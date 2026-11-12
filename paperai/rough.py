# 最後修改：Claude Opus 5.5（PaperAI 維護），20261103
"""粗翻：PyMuPDF 直接取 PDF 的文字與座標，按下 Ctrl 時才送 Google Cloud Translation（Basic v2）。

不經過 Marker，加入檔案後立即可用；翻過的段落存在 library.db，同一段不會重複計費。
掃描頁沒有文字層時，改用本機 OCR（RapidOCR，CPU）取得文字與座標，結果存在 output/<名稱>/ocr/。
金鑰讀自環境變數 GOOGLE_TRANSLATE_API_KEY，或專案根目錄的 .env（不提交 Git）。
"""
import hashlib
import html
import json
import os
import re
import threading
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

from . import config, library
from .glossary import load_user_glossary

FIGURE_GAP = 12           # 點；距離這麼近的圖形視為同一張圖
FIGURE_MIN_WIDTH = 0.15   # 佔頁寬、頁高的最小比例，過濾分數線與表格線
FIGURE_MIN_HEIGHT = 0.07
FIGURE_MAX_AREA = 0.85    # 佔頁面面積的上限
FIGURE_MAX_TEXT = 0.012   # 每平方點的字元數，超過視為表格或文字框
FIGURE_LABEL_CHARS = 30   # 圖旁的短文字（座標軸標籤）一併納入
FIGURE_LABEL_REACH = 24   # 點；標籤最多超出圖框多遠，避免框到頁首
FIGURE_DPI = 220
OCR_DPI = 200             # 再低辨識率會下降；每頁 OCR 約 1–4 秒
OCR_LINE_GAP = 0.8        # 兩行間距超過行高的這個倍數就分段
OCR_INDENT = 0.6          # 首行縮排超過行高的這個倍數就分段
OCR_SHORT_LINE = 2.0      # 上一行比段落右緣短了這麼多個行高，表示段落結束
OCR_FRAGMENT = 0.35       # 比一般行短這麼多、又不從左邊界開始的行是公式碎片，不併入段落
# 版面區塊（RapidLayout 的 CDLA 模型）：數字小的優先；這些類別的文字不翻譯
LAYOUT_PRIORITY = {"title": 0, "figure_caption": 1, "table_caption": 1, "equation": 2,
                   "figure": 3, "table": 3, "header": 4, "footer": 4, "reference": 5, "text": 6}
LAYOUT_SKIP = {"equation", "figure", "table", "header", "footer"}
FIGURE_MAX_PIXELS = 3000
ENDPOINT = "https://translation.googleapis.com/language/translate/v2"
# 公式字型的文字層常對應到錯誤的 Unicode（Computer Modern 的 ρ ν π）
_FONT_FIXES = str.maketrans({"⇢": "ρ", "⌫": "ν", "⇡": "π"})
_FF_RE = re.compile(r"(?<=[A-Za-z])↵(?=[A-Za-z])")  # 同一種字型問題：ff 連字變成 ↵
# PDF 文字層沒有 LaTeX，只能抓含數學符號的片段
_MATH_RE = re.compile(r"\S*[Ͱ-Ͽ∀-⋿=<>^_]\S*")
_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_NUMBER_RE = re.compile(r"\b\d[\d.]*\b")
_SENTENCE_RE = re.compile(r"[a-z)\]]\. +[A-Z(]")  # 小寫字結尾的句號接下一句；目錄的「1. Ion」「II. Detector」不算
_PAGE_REF_RE = re.compile(r"\(\d{2,4}\)")       # 章節摘要裡的頁碼，例如 Wave equation (174)
PROSE_MIN_WORDS = 15


class RoughUnavailable(Exception):
    pass


class QuotaExceeded(RoughUnavailable):
    pass


def _load_key() -> str:
    key = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "").strip()
    env_file = config.PROJECT_ROOT / ".env"
    if not key and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "GOOGLE_TRANSLATE_API_KEY":
                key = value.strip().strip('"')
    return key


def fix_font(text: str) -> str:
    return _FF_RE.sub("ff", text).translate(_FONT_FIXES)


def _clean(text: str) -> str:
    return fix_font(re.sub(r"-\n(?=[a-z])", "", text).replace("\n", " ")).strip()


def _is_translatable(text: str) -> bool:
    """略過頁碼、純公式與目錄（目錄的章節編號和頁碼送翻譯會被打亂）。

    數字多的段落通常是目錄或數據表，但實驗報告的內文也常滿是數值（2.08–2.38 MHz），
    所以有完整句子的長段落仍然保留；章節摘要那種一連串「(173)」的頁碼仍算目錄。
    """
    words = len(_WORD_RE.findall(text))
    if words < 3:
        return False
    if len(_NUMBER_RE.findall(text)) < words * 0.25:
        return True
    sentences = len(_SENTENCE_RE.findall(text)) + text.rstrip().endswith(".")
    return words >= PROSE_MIN_WORDS and sentences >= 1 and len(_PAGE_REF_RE.findall(text)) < 3


@lru_cache(maxsize=512)
def page_blocks(pdf_path: str, page: int, ocr_dir: str = None) -> dict:
    """一頁的文字區塊；座標是頁面寬高的百分比，縮放時不必重算。有 ocr_dir 時，掃描頁改用 OCR。"""
    import fitz
    pdf = fitz.open(pdf_path)
    try:
        target = pdf[page]
        width, height = target.rect.width, target.rect.height
        raw = [(index, b[:4], b[4]) for index, b in enumerate(target.get_text("blocks", sort=False)) if b[6] == 0]
        prefix, lines = str(page), []
        if ocr_dir and not any(text.strip() for _, _, text in raw):
            ocr = _cached_ocr(target, Path(ocr_dir) / f"{page}.json")
            lines = ocr["lines"]
            raw, prefix = _group_lines(lines, ocr["regions"]), f"{page}_ocr"
        blocks = []
        for index, box, raw_text in raw:
            text = _clean(raw_text)
            if not _is_translatable(text):
                continue
            blocks.append({
                "id": f"{prefix}_{index}", "text": text, "lines": raw_text.count("\n") or 1,
                "left": box[0] / width * 100, "top": box[1] / height * 100,
                "width": (box[2] - box[0]) / width * 100, "height": (box[3] - box[1]) / height * 100,
            })
        figures = [{"left": r.x0 / width * 100, "top": r.y0 / height * 100,
                    "width": r.width / width * 100, "height": r.height / height * 100}
                   for r in _figure_rects(target)]
        ocr_lines = [{"text": text, "left": x0 / width * 100, "top": y0 / height * 100,
                      "width": (x1 - x0) / width * 100, "height": (y1 - y0) / height * 100}
                     for x0, y0, x1, y1, text in sorted(lines, key=lambda line: (line[1], line[0]))]
        return {"blocks": blocks, "figures": figures, "aspect": width / height, "ocr_lines": ocr_lines}
    finally:
        pdf.close()


_ocr_lock = threading.Lock()
_ocr_engine = None
_layout_engine = None


def _ocr_lines(image: bytes) -> list:
    """整頁 OCR，回傳 [x0, y0, x1, y1, 文字]，座標是 PDF point。引擎不能多執行緒共用，呼叫端要持有 _ocr_lock。"""
    global _ocr_engine
    from rapidocr import RapidOCR
    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    result = _ocr_engine(image)
    scale = 72 / OCR_DPI
    lines = []
    for box, text in zip(result.boxes if result.txts else [], result.txts or []):
        xs, ys = [float(p[0]) * scale for p in box], [float(p[1]) * scale for p in box]  # numpy float32 不能存 JSON
        lines.append([min(xs), min(ys), max(xs), max(ys), str(text)])
    return lines


def _layout_regions(image: bytes) -> list:
    """版面分析（RapidLayout，CPU 每頁約 0.2 秒），回傳 [x0, y0, x1, y1, 類別]；沒有安裝時回傳空清單。"""
    global _layout_engine
    try:
        from rapid_layout import RapidLayout
    except ImportError:
        return []
    if _layout_engine is None:
        _layout_engine = RapidLayout()
    result = _layout_engine(image)
    scale = 72 / OCR_DPI
    return [[float(box[0]) * scale, float(box[1]) * scale, float(box[2]) * scale, float(box[3]) * scale, str(name)]
            for box, name in zip(result.boxes if result.class_names else [], result.class_names or [])]


def _region_of(line: list, regions: list):
    """文字行中心所在的版面區塊；多個區塊重疊時，標題與公式優先於一般內文。"""
    cx, cy = (line[0] + line[2]) / 2, (line[1] + line[3]) / 2
    inside = [r for r in regions if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]]
    return min(inside, key=lambda r: LAYOUT_PRIORITY.get(r[4], len(LAYOUT_PRIORITY)), default=None)


def _group_lines(lines: list, regions: list = ()) -> list:
    """把 OCR 的文字行併成段落。有版面分析時先依區塊分開（標題不會併進內文），
    公式、圖片、頁首頁尾區塊裡的行不翻譯；區塊內或沒有版面資訊時，再用位置規則分段。"""
    if not lines:
        return []
    widths = sorted(line[2] - line[0] for line in lines)
    typical, margin = widths[len(widths) * 3 // 4], min(line[0] for line in lines)  # 公式碎片多時中位數會偏短
    buckets = {}
    for line in lines:
        region = _region_of(line, regions)
        if region is not None and region[4] in LAYOUT_SKIP:
            continue
        buckets.setdefault(id(region), []).append(line)
    groups = [group for bucket in buckets.values() for group in _split_paragraphs(bucket, typical, margin)]
    groups.sort(key=lambda group: (group[1], group[0]))
    return [(index, group[:4], group[4]) for index, group in enumerate(groups)]


def _split_paragraphs(lines: list, typical: float, margin: float) -> list:
    """依位置分段：行距太大、首行縮排或上一行提早結束就分段。

    公式的碎片（∂t2、(2.1)）自成一段，否則段落框會往上下延伸，譯文蓋住公式。
    """
    groups = []  # [x0, y0, x1, y1, 文字, 上一行的右緣, 是否為碎片]
    for x0, y0, x1, y1, text in sorted(lines, key=lambda line: (line[1], line[0])):
        line_height = y1 - y0
        fragment = x1 - x0 < typical * OCR_FRAGMENT and x0 - margin > line_height * 2
        last = groups[-1] if groups else None
        # OCR 的行框常上下重疊，所以行距容許負值
        if last and not fragment and not last[6] and x0 < last[2] and x1 > last[0] \
                and -line_height * 0.5 <= y0 - last[3] < line_height * OCR_LINE_GAP \
                and x0 - last[0] < line_height * OCR_INDENT \
                and last[2] - last[5] < line_height * OCR_SHORT_LINE:
            last[:4] = [min(last[0], x0), last[1], max(last[2], x1), y1]
            last[4] += "\n" + text
            last[5] = x1
        else:
            groups.append([x0, y0, x1, y1, text, x1, fragment])
    return groups


def _cached_ocr(page, cache: Path) -> dict:
    """掃描頁的 OCR 文字行與版面區塊；結果存檔，同一頁只做一次。沒有安裝 rapidocr 時當作沒有文字。

    早期的快取只有文字行（list），讀到時補做版面分析並改寫。
    """
    saved = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else None
    if isinstance(saved, dict):
        return saved
    image = page.get_pixmap(dpi=OCR_DPI).tobytes("png")
    with _ocr_lock:
        try:
            lines = saved if saved is not None else _ocr_lines(image)
        except ImportError:
            return {"lines": [], "regions": []}
        result = {"lines": lines, "regions": _layout_regions(image)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def _covered_text(rect, text_blocks: list) -> int:
    return sum(len(text) for box, text in text_blocks if rect.contains(box))


def _merge_rects(rects: list, text_blocks: list) -> list:
    """把相鄰的矩形併成一張圖（子圖、向量圖的線條群），但不跨過中間的文字段落。"""
    import fitz
    merged = []
    for rect in map(fitz.Rect, rects):
        changed = True
        while changed:
            changed = False
            for other in merged:
                union = rect | other
                near = (rect + (-FIGURE_GAP, -FIGURE_GAP, FIGURE_GAP, FIGURE_GAP)).intersects(other)
                extra = _covered_text(union, text_blocks) - _covered_text(rect, text_blocks) - _covered_text(other, text_blocks)
                if near and extra <= FIGURE_LABEL_CHARS:
                    rect = union
                    merged.remove(other)
                    changed = True
                    break
        merged.append(rect)
    return merged


def _figure_rects(page) -> list:
    """頁面上的圖：點陣圖加上向量線條群，再納入座標軸標籤這類短文字。"""
    import fitz
    area = page.rect
    text_blocks = [(fitz.Rect(b[:4]), b[4]) for b in page.get_text("blocks") if b[6] == 0]
    candidates = [info["bbox"] for info in page.get_image_info()] + list(page.cluster_drawings())
    figures = []
    for rect in _merge_rects(candidates, text_blocks):
        rect &= area
        size = rect.width * rect.height
        if rect.width < area.width * FIGURE_MIN_WIDTH or rect.height < area.height * FIGURE_MIN_HEIGHT:
            continue
        if size > area.width * area.height * FIGURE_MAX_AREA:  # 掃描書整頁是一張圖
            continue
        if sum(len(text) for box, text in text_blocks if box.intersects(rect)) / size > FIGURE_MAX_TEXT:  # 文字框
            continue
        reach = rect + (-FIGURE_LABEL_REACH, -FIGURE_LABEL_REACH, FIGURE_LABEL_REACH, FIGURE_LABEL_REACH)
        for box, text in text_blocks:
            if len(text) < FIGURE_LABEL_CHARS and reach.contains(box) and (rect + (-8, -8, 8, 8)).intersects(box):
                rect |= box
        figures.append(rect)
    return figures


def figure_png(pdf_path: str, page: int, index: int) -> bytes:
    """從 PDF 重新算圖，比螢幕截圖清楚；放大後過大時降低解析度。"""
    import fitz
    pdf = fitz.open(pdf_path)
    try:
        target = pdf[page]
        rect = _figure_rects(target)[index]
        dpi = min(FIGURE_DPI, int(FIGURE_MAX_PIXELS / max(rect.width, rect.height) * 72))
        return target.get_pixmap(clip=rect, dpi=dpi).tobytes("png")
    finally:
        pdf.close()


def _protect(text: str) -> str:
    escaped = html.escape(text, quote=False)
    return _MATH_RE.sub(lambda m: f'<span translate="no">{m.group(0)}</span>', escaped)


def _unprotect(text: str) -> str:
    return html.unescape(re.sub(r"</?span[^>]*>", "", text))


def _apply_aliases(text: str) -> str:
    """Basic v2 不支援術語表，翻完後把 glossary.json 的 aliases 換成標準譯名。"""
    for entry in load_user_glossary():
        for alias in entry.get("aliases", []):
            text = text.replace(alias, entry["translation"])
    return text


def _call_google(query: str) -> str:
    key = _load_key()
    if not key:
        raise RoughUnavailable("找不到 Google 金鑰：請在 .env 填入 GOOGLE_TRANSLATE_API_KEY")
    body = json.dumps({"q": [query], "source": "en", "target": "zh-TW", "format": "html"}).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=body, headers={
        "Content-Type": "application/json", "X-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return json.load(res)["data"]["translations"][0]["translatedText"]
    except urllib.error.HTTPError as e:
        raise RoughUnavailable(f"Google 翻譯回傳 {e.code}：{e.read().decode('utf-8', 'replace')[:200]}")
    except urllib.error.URLError as e:
        raise RoughUnavailable(f"無法連線到 Google 翻譯：{e.reason}")


def translate(text: str) -> str:
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cached = library.rough_cached(key)
    if cached is not None:
        return _apply_aliases(cached)
    query = _protect(text)
    if not library.reserve_chars(len(query), library.translation_settings()["monthly_chars"]):
        raise QuotaExceeded("本月粗翻額度已用完，這一段要等本機細翻")
    result = _unprotect(_call_google(query))
    library.rough_store(key, result)
    return _apply_aliases(result)

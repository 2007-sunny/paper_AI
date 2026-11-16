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


REBUILT_MARKER = "rebuilt.json"  # ocr 資料夾裡：重新製作過文字層的頁與當時 PDF 的修改時間


def _rebuilt_pages(pdf_path: str, ocr_dir: str) -> set:
    """重新製作過 OCR 的頁；PDF 之後又被換掉（修改時間不同）就不算。"""
    try:
        data = json.loads((Path(ocr_dir) / REBUILT_MARKER).read_text(encoding="utf-8"))
        return set(data["pages"]) if data["mtime"] == int(os.path.getmtime(pdf_path)) else set()
    except (OSError, ValueError, KeyError):
        return set()


@lru_cache(maxsize=512)
def page_blocks(pdf_path: str, page: int, ocr_dir: str = None) -> dict:
    """一頁的文字區塊；座標是頁面寬高的百分比，縮放時不必重算。有 ocr_dir 時，掃描頁改用 OCR。

    重新製作過 OCR 的頁也用 OCR 的分段：文字層雖然是同一份 OCR，但交給 MuPDF 重新分段時，
    圖上的座標軸文字會被併進旁邊的段落，段落框橫跨到圖上（Model 第 2 頁）。
    """
    import fitz
    pdf = fitz.open(pdf_path)
    try:
        target = pdf[page]
        width, height = target.rect.width, target.rect.height
        raw = [(index, b[:4], b[4]) for index, b in enumerate(target.get_text("blocks", sort=False)) if b[6] == 0]
        prefix, lines, words = str(page), [], []
        rebuilt = bool(ocr_dir) and page in _rebuilt_pages(pdf_path, ocr_dir)
        if ocr_dir and (rebuilt or not any(text.strip() for _, _, text in raw)):
            ocr = _cached_ocr(target, Path(ocr_dir) / f"{page}.json")
            if not rebuilt:  # 重新製作過的頁 PDF 本身就有這些字，不再疊一層透明 OCR 文字
                lines, words = ocr["lines"], ocr.get("words") or []
            raw, prefix = _group_lines(ocr["lines"], ocr["regions"]), f"{page}_ocr"
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
        # 透明文字層：每行附上逐字的位置（_line_pieces，單字帶空格、寬度到下一個字），前端逐字放才對得準
        ocr_lines = [{"text": line[4], "left": line[0] / width * 100, "top": line[1] / height * 100,
                      "width": (line[2] - line[0]) / width * 100, "height": (line[3] - line[1]) / height * 100,
                      "pieces": [{"text": text + " " if space else text, "left": x0 / width * 100,
                                  "width": (end - x0) / width * 100}
                                 for x0, _, end, text, space in _line_pieces(line, words[i] if i < len(words) else [])]}
                     for i, line in sorted(enumerate(lines), key=lambda item: (item[1][1], item[1][0]))]
        return {"blocks": blocks, "figures": figures, "aspect": width / height, "ocr_lines": ocr_lines}
    finally:
        pdf.close()


_ocr_lock = threading.Lock()
_ocr_engine = None
_layout_engine = None


def _ocr_lines(image: bytes) -> tuple:
    """整頁 OCR，回傳 (文字行, 單字)，座標是 PDF point。引擎不能多執行緒共用，呼叫端要持有 _ocr_lock。

    文字行是 [x0, y0, x1, y1, 文字]；單字與文字行一一對應，每行是 [[x0, y0, x1, y1, 單字], ...]。
    透明文字層要逐字放：整行拉寬時字型字寬和掃描不同，行中間的字會偏半個字母（選字對不準）。
    """
    global _ocr_engine
    from rapidocr import RapidOCR
    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    result = _ocr_engine(image, return_word_box=True)
    scale = 72 / OCR_DPI

    def rect(box):
        xs, ys = [float(p[0]) * scale for p in box], [float(p[1]) * scale for p in box]  # numpy float32 不能存 JSON
        return [min(xs), min(ys), max(xs), max(ys)]

    lines, words = [], []
    word_results = getattr(result, "word_results", None) or [()] * len(result.txts or [])
    for box, text, line_words in zip(result.boxes if result.txts else [], result.txts or [], word_results):
        lines.append([*rect(box), str(text)])
        pieces = [[*rect(w_box), str(w_text)] for w_text, _, w_box in line_words or () if str(w_text).strip()]
        words.append(_regroup_words(str(text), pieces))
    return lines, words


def _regroup_words(text: str, pieces: list) -> list:
    """依整行文字的空格重新組成單字。行內有中文字元（OCR 把句點認成「。」）時 RapidOCR 改成逐字元回傳，
    直接用的話複製出來會變成「m a x i m u m」。對不上時交回原本的片段。"""
    tokens = text.split()
    if len(tokens) == len(pieces) or "".join(p[4] for p in pieces) != "".join(tokens):
        return pieces
    out, i = [], 0
    for token in tokens:
        group, acc = [], ""
        while i < len(pieces) and len(acc) < len(token):
            acc += pieces[i][4]
            group.append(pieces[i])
            i += 1
        if acc != token:
            return pieces
        out.append([min(p[0] for p in group), min(p[1] for p in group),
                    max(p[2] for p in group), max(p[3] for p in group), token])
    return out


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


SNAP_GAP = 1.2    # 墨跡欄之間空白至少這麼寬（pt）才算字與字之間
SNAP_RANGE = 2.5  # 單字框的邊界最多往旁邊找這麼遠（pt）的墨跡邊緣


def _snap_words(words: list, page) -> list:
    """把 OCR 的單字框左右邊界移到掃描上最近的墨跡邊緣。

    RapidOCR 的單字框是從整行辨識結果推算的，平均偏約 0.8pt（常切進第一個字母），
    對齊墨跡後透明文字層的選取範圍才會剛好落在字與字之間的空白。找不到邊緣就保留原值。
    """
    import fitz
    import numpy as np
    dpi = OCR_DPI
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    ink = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width) < 140
    s = dpi / 72

    def edges(y0, y1, x_from, x_to):
        h = y1 - y0
        band = ink[max(int((y0 + h * 0.1) * s), 0):max(int((y1 - h * 0.1) * s), 0), max(int(x_from * s), 0):max(int(x_to * s), 0)]
        if band.size == 0:
            return [], []
        cols, starts, ends, gap = band.any(axis=0), [], [], 10 ** 6
        for i, on in enumerate(cols):
            if on:
                if gap >= SNAP_GAP * s:
                    starts.append(x_from + i / s)
                    if i:
                        ends.append(x_from + (i - gap) / s)
                gap = 0
            else:
                gap += 1
        if gap < len(cols):
            ends.append(x_from + (len(cols) - gap) / s)
        return starts, ends

    snapped = []
    for line_words in words:
        out = []
        for x0, y0, x1, y1, text in line_words:
            starts, ends = edges(y0, y1, x0 - SNAP_RANGE * 2, x1 + SNAP_RANGE * 2)
            near_start = min((p for p in starts if abs(p - x0) <= SNAP_RANGE), key=lambda p: abs(p - x0), default=x0)
            near_end = min((p for p in ends if abs(p - x1) <= SNAP_RANGE), key=lambda p: abs(p - x1), default=x1)
            out.append([near_start, y0, near_end, y1, text] if near_end > near_start else [x0, y0, x1, y1, text])
        snapped.append(out)
    return snapped


def _cached_ocr(page, cache: Path) -> dict:
    """掃描頁的 OCR 文字行、單字與版面區塊；結果存檔，同一頁只做一次。沒有安裝 rapidocr 時當作沒有文字。

    早期的快取只有文字行（list），讀到時補做版面分析並改寫；沒有 words 的快取照用，透明文字層退回整行放置。
    """
    saved = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else None
    if isinstance(saved, dict):
        return saved
    image = page.get_pixmap(dpi=OCR_DPI).tobytes("png")
    with _ocr_lock:
        try:
            lines, words = (saved, []) if saved is not None else _ocr_lines(image)
        except ImportError:
            return {"lines": [], "regions": []}
        result = {"lines": lines, "words": _snap_words(words, page), "regions": _layout_regions(image)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


SCAN_IMAGE_COVER = 0.5   # 圖片蓋住頁面這麼多才算掃描頁；電子版頁面保留原本的文字
REBUILD_MAX_PAGES = 300  # 每頁約 3 秒，超過就太久了


def _is_scan(page) -> bool:
    import fitz
    area = sum((fitz.Rect(info["bbox"]) & page.rect).get_area() for info in page.get_image_info())
    return page.rotation == 0 and area >= page.rect.get_area() * SCAN_IMAGE_COVER


def _reading_order(ocr: dict) -> list:
    """OCR 文字行依段落順序排列的索引（_group_lines 會分欄）。依 y 排序直接寫入的話，
    MuPDF 會把左右兩欄同一高度的行併成同一段（「were re- The previous work on」）。"""
    lines, order, used = ocr["lines"], [], set()
    for _, box, text in _group_lines(lines, ocr["regions"]):
        members = set(text.split("\n"))
        for i, line in enumerate(lines):
            if i not in used and line[4] in members and box[0] - 1 <= line[0] and line[2] <= box[2] + 1 \
                    and box[1] - 1 <= line[1] and line[3] <= box[3] + 1:
                order.append(i)
                used.add(i)
    return order + [i for i in range(len(lines)) if i not in used]  # 公式、圖、頁首等不分段的行


def _line_pieces(line: list, words: list) -> list:
    """一行文字要逐段放置的 [x0, x1, 下一個字的開頭, 單字, 後面是否接空格]。有單字框時逐字放，
    空格補在字與字之間，複製出來才有空格、選取範圍也剛好蓋住字間；沒有單字框（舊快取）時整行放。"""
    if not words:
        return [[line[0], line[2], line[2], line[4], False]]
    pieces = []
    for j, (x0, _, x1, _, text) in enumerate(words):
        last = j == len(words) - 1
        pieces.append([x0, x1, x1 if last else max(words[j + 1][0], x1), text, not last])
    return pieces


def rebuild_text_layer(pdf_path: str, ocr_dir: str, backup: Path) -> dict:
    """重新 OCR 掃描頁，換掉 PDF 內建的文字層並覆蓋原檔（使用者在設置裡明確要求）。

    第一次覆蓋前把原檔複製到 backup；只動掃描頁，電子版頁面保留原本的文字；
    先寫到暫存檔再取代，中途失敗不會留下壞檔。新文字是隱形的（render_mode=3），畫面不變。
    """
    import shutil
    import fitz
    source = Path(pdf_path)
    pdf = fitz.open(source)
    try:
        scans = [page.number for page in pdf if _is_scan(page)]
        if len(scans) > REBUILD_MAX_PAGES:
            raise ValueError(f"掃描頁有 {len(scans)} 頁，超過 {REBUILD_MAX_PAGES} 頁上限")
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, backup)
        # 字型：英數用內建 Helvetica；希臘字母與數學符號用內嵌的 Arial（Windows 內建）；剩下的中日韓字元用內建 CJK 字型。
        # 內建 CJK 字型不內嵌，PDF.js 沒有字元對照表時讀不出來（τm 複製時會不見），所以只留給 Arial 沒有的字
        fonts = [(fitz.Font("helv"), "helv", None)]
        unicode_file = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
        if unicode_file.is_file():
            fonts.append((fitz.Font(fontfile=str(unicode_file)), "ocru", str(unicode_file)))
        fonts.append((fitz.Font("china-t"), "china-t", None))

        def pick(text):
            if text.isascii():  # Helvetica 只能編碼 ASCII；has_glyph 對 τ 也回報有，寫進去會變成「·」
                return fonts[0]
            for font, name, file in fonts[1:]:
                if all(font.has_glyph(ord(c)) for c in text):
                    return font, name, file
            return fonts[-1]

        def put(page, x0, x1, y0, y1, size, text, font, name):
            length = font.text_length(text, fontsize=size)
            if length <= 0 or x1 <= x0:
                return
            point = fitz.Point(x0, y1 - (y1 - y0) * 0.2)
            page.insert_text(point, text, fontsize=size, fontname=name, render_mode=3,
                             morph=(point, fitz.Matrix((x1 - x0) / length, 1)))  # 寬度對齊掃描的字

        for number in scans:
            page = pdf[number]
            cache = Path(ocr_dir) / f"{number}.json"
            cache.unlink(missing_ok=True)  # 重新製作，不沿用舊的辨識結果
            ocr = _cached_ocr(page, cache)
            page.add_redact_annot(page.rect)  # 只拿掉文字，圖片與線條保留
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                  text=fitz.PDF_REDACT_TEXT_REMOVE)
            all_words = ocr.get("words") or []
            embedded = set()
            for i in _reading_order(ocr):
                line = ocr["lines"][i]
                y0, y1 = line[1], line[3]  # 同一行共用基線與字級
                size = max((y1 - y0) * 0.8, 1)
                for x0, x1, end, text, space in _line_pieces(line, all_words[i] if i < len(all_words) else []):
                    font, name, file = pick(text)
                    if file and name not in embedded:
                        page.insert_font(fontname=name, fontfile=file)
                        embedded.add(name)
                    if name == "helv":  # 空格一起寫，寬度到下一個字
                        put(page, x0, end, y0, y1, size, text + " " if space else text, font, name)
                        continue
                    put(page, x0, x1, y0, y1, size, text, font, name)
                    if space:  # Arial 的空格讀回來是不換行空格（\xa0），空格改用 Helvetica 補
                        put(page, x1, max(end, x1 + 0.5), y0, y1, size, " ", fonts[0][0], "helv")
            page.clean_contents()  # 每次 insert_text 都多一段內容串流，逐字寫入時一頁上百段，合併後檔案小一半以上
        temp = source.with_name(source.name + ".paperai-tmp")
        pdf.subset_fonts()  # 內嵌的 Arial 只留用到的字，檔案才不會多 1MB
        pdf.save(temp, garbage=4, deflate=True, use_objstms=True)
        total = len(pdf)
    finally:
        pdf.close()
    try:
        os.replace(temp, source)
    except OSError:
        temp.unlink(missing_ok=True)
        raise
    marker = Path(ocr_dir) / REBUILT_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"mtime": int(os.path.getmtime(source)), "pages": scans}), encoding="utf-8")
    page_blocks.cache_clear()
    return {"pages": total, "rebuilt": len(scans), "skipped": total - len(scans), "backup": str(backup)}


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

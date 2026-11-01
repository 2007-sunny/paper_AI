# 最後修改：Claude Opus 5.5（PaperAI 維護），20261101
"""粗翻：PyMuPDF 直接取 PDF 的文字與座標，按下 Ctrl 時才送 Google Cloud Translation（Basic v2）。

不經過 Marker，加入檔案後立即可用；翻過的段落存在 library.db，同一段不會重複計費。
金鑰讀自環境變數 GOOGLE_TRANSLATE_API_KEY，或專案根目錄的 .env（不提交 Git）。
"""
import hashlib
import html
import json
import os
import re
import urllib.error
import urllib.request
from functools import lru_cache

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
FIGURE_MAX_PIXELS = 3000
ENDPOINT = "https://translation.googleapis.com/language/translate/v2"
# 公式字型的文字層常對應到錯誤的 Unicode（Computer Modern 的 ρ ν π）
_FONT_FIXES = str.maketrans({"⇢": "ρ", "⌫": "ν", "⇡": "π"})
_FF_RE = re.compile(r"(?<=[A-Za-z])↵(?=[A-Za-z])")  # 同一種字型問題：ff 連字變成 ↵
# PDF 文字層沒有 LaTeX，只能抓含數學符號的片段
_MATH_RE = re.compile(r"\S*[Ͱ-Ͽ∀-⋿=<>^_]\S*")
_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_NUMBER_RE = re.compile(r"\b\d[\d.]*\b")


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
    """略過頁碼、純公式與目錄（目錄的章節編號和頁碼送翻譯會被打亂）。"""
    words = len(_WORD_RE.findall(text))
    return words >= 3 and len(_NUMBER_RE.findall(text)) < words * 0.25


@lru_cache(maxsize=512)
def page_blocks(pdf_path: str, page: int) -> dict:
    """一頁的文字區塊；座標是頁面寬高的百分比，縮放時不必重算。"""
    import fitz
    pdf = fitz.open(pdf_path)
    try:
        target = pdf[page]
        width, height = target.rect.width, target.rect.height
        blocks = []
        for index, b in enumerate(target.get_text("blocks", sort=False)):
            if b[6] != 0:
                continue
            text = _clean(b[4])
            if not _is_translatable(text):
                continue
            blocks.append({
                "id": f"{page}_{index}", "text": text, "lines": b[4].count("\n") or 1,
                "left": b[0] / width * 100, "top": b[1] / height * 100,
                "width": (b[2] - b[0]) / width * 100, "height": (b[3] - b[1]) / height * 100,
            })
        figures = [{"left": r.x0 / width * 100, "top": r.y0 / height * 100,
                    "width": r.width / width * 100, "height": r.height / height * 100}
                   for r in _figure_rects(target)]
        return {"blocks": blocks, "figures": figures, "aspect": width / height}
    finally:
        pdf.close()


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

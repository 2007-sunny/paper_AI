"""把區塊對應到原始 PDF 的頁碼（從 0 起算，與 Marker 的 page_id 相同）。

Marker 的 Markdown 沒有頁碼，依序使用三種線索：
1. 圖片檔名（_page_376_Figure_0.jpeg）
2. meta.json 目錄中標題所在的頁
3. 段落文字與 PDF 各頁文字的單字重疊率
並限制頁碼只能往後（閱讀順序），找不到線索的區塊沿用前一個區塊的頁碼。
"""
import json
import re

_IMAGE_PAGE_RE = re.compile(r"_page_(\d+)_")
_WORD_RE = re.compile(r"[a-z]{3,}")
MATCH_THRESHOLD = 0.5   # 區塊單字出現在該頁的比例
LOOKAHEAD = 4           # 從目前頁往後找幾頁


def _words(text: str) -> set:
    # 去掉 LaTeX 與 HTML，只比對一般英文單字
    text = re.sub(r"\$[^$]*\$|<[^>]+>|\\[a-zA-Z]+", " ", text.lower())
    return set(_WORD_RE.findall(text))


def _block_text(block: dict) -> str:
    if "items" in block:
        return " ".join(block["items"])
    if "units" in block:
        return " ".join(s["en"] for unit in block["units"] for s in unit)
    return block.get("source", "")


def _toc_entries(meta_path) -> list:
    """目錄依出現順序的 (標題單字, 頁碼)；同名標題（如每節的 Exercises）會出現多次，必須依序使用。"""
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            toc = json.load(f).get("table_of_contents", [])
    except Exception:
        return []
    return [(_title_key(t["title"]), t["page_id"]) for t in toc if t.get("title") and "page_id" in t]


def _title_key(title: str) -> str:
    """標題比對用：去掉 Markdown 與標點，保留數字（區分 EXAMPLE 10.1.1 與 10.3.2）。"""
    return re.sub(r"[^a-z0-9]", "", re.sub(r"<[^>]+>|\*", "", title.lower()))


def _take_toc_page(toc: list, key: str, current: int):
    """取出第一個標題相符、且頁碼不早於目前頁的目錄項目。"""
    for i, (title, page) in enumerate(toc):
        if title == key and page >= current:
            del toc[:i + 1]
            return page
    return None


def _page_range(meta_path, blocks) -> list:
    """文件涵蓋的 PDF 頁碼（meta 的 page_stats 最準確）。"""
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            pages = sorted(p["page_id"] for p in json.load(f).get("page_stats", []))
        if pages:
            return pages
    except Exception:
        pass
    found = [int(m) for b in blocks for m in _IMAGE_PAGE_RE.findall(b.get("source", ""))]
    return list(range(min(found), max(found) + 1)) if found else [0]


def _pdf_page_words(pdf_path, pages: list) -> dict:
    import pypdfium2 as pdfium  # Marker 的依賴
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return {p: _words(pdf[p].get_textpage().get_text_bounded()) for p in pages if p < len(pdf)}
    finally:
        pdf.close()


def assign_pages(blocks: list, meta_path, pdf_path=None) -> list:
    """回傳文件涵蓋的頁碼清單，並在每個區塊寫入 block["page"]。"""
    pages = _page_range(meta_path, blocks)
    toc = _toc_entries(meta_path)
    page_words = {}
    if pdf_path:
        try:
            page_words = _pdf_page_words(pdf_path, pages)
        except Exception as e:
            print(f"    [WARNING] 無法讀取 PDF 文字（{e}），頁碼只依圖片與標題判斷")

    current = pages[0]
    for block in blocks:
        text = _block_text(block)
        page = None
        m = _IMAGE_PAGE_RE.search(block.get("source", ""))
        if block["type"] == "image" and m:
            page = int(m.group(1))
        elif block["type"] == "heading" and _title_key(text):
            page = _take_toc_page(toc, _title_key(text), current)
        if page is None and page_words:
            words = _words(text)
            if len(words) >= 3:
                def score(p):
                    return len(words & page_words.get(p, set())) / len(words)
                stay = score(current)
                candidates = [p for p in pages if current < p <= current + LOOKAHEAD]
                # 分數相同時取較近的頁；只有明顯比目前頁更符合才換頁（常見單字到處都有）
                best_score, best_page = max(((score(p), -p) for p in candidates), default=(0, -current))
                if best_score >= MATCH_THRESHOLD and best_score > stay + 0.1:
                    page = -best_page
        if page is not None and page >= current:
            current = page
        block["page"] = current
    return pages

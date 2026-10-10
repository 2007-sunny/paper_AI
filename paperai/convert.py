"""PDF → Markdown（Marker）。

輸出到 output/<名稱>/<名稱>.md。指定頁碼範圍時名稱加上 _p<範圍>，
同一本書的不同章節會成為各自獨立的文件。
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import config

_PAGES_RE = re.compile(r"^\d+(-\d+)?(,\d+(-\d+)?)*$")
# 字型編碼損壞的 PDF 會把 fi/fl 連字抽成 " # ! 等符號（"xed、#ow、satis"es），
# 數學符號也一併遺失；Marker 預設沿用這層文字，必須改用 OCR。
_BROKEN_LIGATURE_RE = re.compile(
    r'["#!](xed|nite|rst|eld|elds|gure|gures|es|ed|ow|ows|uid|uids|nd|nds|nal|nally|ne|ned|ll|lled|t|ts|x|rm|ve|ber)\b',
    re.IGNORECASE)


def doc_name_for(pdf_path: Path, pages: str = None) -> str:
    if not pages:
        return pdf_path.stem
    return f"{pdf_path.stem}_p{pages.replace(',', '_')}"


def normalize_pages(pages: str) -> str:
    """Marker 頁碼從 0 起算，例如 "0-40" 或 "0,5-10"。"""
    pages = (pages or "").replace(" ", "")
    if pages and not _PAGES_RE.match(pages):
        raise ValueError(f"頁碼格式錯誤：{pages}（例如 0-40 或 0,5-10）")
    return pages


def _page_indices(pages: str, total: int) -> list:
    if not pages:
        return list(range(total))
    indices = []
    for part in pages.split(","):
        start, _, end = part.partition("-")
        indices.extend(range(int(start), int(end or start) + 1))
    return [i for i in indices if i < total]


def text_layer_is_broken(pdf_path, pages: str = None, sample: int = 30) -> bool:
    """抽樣檢查 PDF 內嵌文字是否有連字損壞；沒有文字層的 PDF 由 Marker 自行 OCR，回傳 False。"""
    import pypdfium2 as pdfium  # Marker 的依賴，只在轉檔時才需要
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        indices = _page_indices(pages, len(pdf))[:sample]
        text = "\n".join(pdf[i].get_textpage().get_text_bounded() for i in indices)
    finally:
        pdf.close()
    hits = len(_BROKEN_LIGATURE_RE.findall(text))
    return hits >= 5 and hits * 1000 >= len(text.split())


def page_count(pdf_path, pages: str = None) -> int:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(_page_indices(pages, len(pdf)))
    finally:
        pdf.close()


# Marker 以 tqdm 輸出各階段進度，例如 "Recognizing Text:  45%|████ | 9/20 [00:03<00:04, 2.9it/s]"
_TQDM_RE = re.compile(r"^\s*(?P<desc>[^:|\[]{3,60}?):\s*(?P<pct>\d+)%\|[^|]*\|\s*(?P<done>\d+)/(?P<total>\d+)"
                      r"(?:\s*\[(?P<elapsed>[\d:]+)<(?P<remain>[\d:?]+))?")
STAGE_LABELS = {
    "Recognizing Layout": "辨識版面",
    "Running OCR Error Detection": "檢查文字品質",
    "Detecting bboxes": "偵測文字位置",
    "Recognizing Text": "辨識文字",
    "Recognizing Equations": "辨識公式",
    "Recognizing tables": "辨識表格",
    "Recognizing Tables": "辨識表格",
}


def _seconds(clock: str):
    """tqdm 的 "01:23" 或 "1:02:03" → 秒；"?" 時回傳 None。"""
    if not clock or "?" in clock:
        return None
    total = 0
    for part in clock.split(":"):
        total = total * 60 + int(part)
    return total


def _run_marker(command: list, progress) -> tuple:
    """執行 Marker 並即時解析進度；回傳 (exit code, 最後幾行輸出)。"""
    from collections import deque
    tail = deque(maxlen=8)
    proc = subprocess.Popen(command, env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    buffer = ""
    while True:
        chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not chunk:
            break
        buffer += chunk.decode("utf-8", errors="replace")
        # tqdm 以 \r 覆寫同一行，所以 \r 與 \n 都當作分隔
        *lines, buffer = re.split(r"[\r\n]", buffer)
        for line in lines:
            if not line.strip():
                continue
            m = _TQDM_RE.match(line)
            if m:
                desc = m.group("desc").strip()
                progress({"stage": STAGE_LABELS.get(desc, desc), "done": int(m.group("done")),
                          "total": int(m.group("total")), "remaining": _seconds(m.group("remain"))})
            else:
                tail.append(line.strip())
    return proc.wait(), "\n".join(list(tail)[-5:])


def convert_pdf(pdf_path, pages: str = None, log=print, force_ocr: bool = None, progress=None) -> str:
    """轉換 PDF，回傳文件名稱（output/ 下的資料夾名）。

    force_ocr 為 None 時自動判斷：內嵌文字損壞就忽略文字層，整頁重新 OCR。
    progress(dict) 會收到 {"pages", "ocr"}（開始時）與各階段的 {"stage", "done", "total", "remaining"}。
    """
    progress = progress or (lambda info: None)
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"找不到 PDF：{pdf_path}")
    pages = normalize_pages(pages)
    name = doc_name_for(pdf_path, pages)
    target = config.OUTPUT_ROOT / name
    if (target / f"{name}.md").exists():
        raise FileExistsError(f"已轉換過：{name}（要重新轉換請先刪除 output/{name}/）")

    # Marker 以 PDF 檔名命名輸出，先轉到暫存資料夾再改名，避免不同頁碼範圍互相覆蓋
    tmp_root = config.OUTPUT_ROOT / ".converting"
    shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir(parents=True)
    command = ["marker_single", str(pdf_path), "--output_dir", str(tmp_root)]
    if pages:
        command.extend(["--page_range", pages])
    if force_ocr is None:
        force_ocr = text_layer_is_broken(pdf_path, pages)
        if force_ocr:
            log("PDF 內嵌文字損壞（連字與數學符號遺失），改用 OCR 辨識")
    if force_ocr:
        command.append("--force_ocr")
    log(f"正在轉換：{pdf_path.name}" + (f"（頁 {pages}）" if pages else "") + ("，OCR 模式" if force_ocr else ""))
    progress({"pages": page_count(pdf_path, pages), "ocr": bool(force_ocr)})
    # 自動選擇裝置；可透過 TORCH_DEVICE 環境變數指定。
    returncode, tail = _run_marker(command, progress)
    produced = tmp_root / pdf_path.stem
    if returncode != 0 or not (produced / f"{pdf_path.stem}.md").exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise RuntimeError(f"Marker 轉換失敗：{tail}")

    target.mkdir(parents=True, exist_ok=True)
    for item in produced.iterdir():
        new_name = item.name.replace(pdf_path.stem, name, 1) if item.name.startswith(pdf_path.stem) else item.name
        shutil.move(str(item), str(target / new_name))
    shutil.rmtree(tmp_root, ignore_errors=True)
    log(f"完成：output/{name}/{name}.md")
    return name

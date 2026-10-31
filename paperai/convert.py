"""PDF → Markdown：依閱讀位置分段呼叫常駐的 Marker（marker_server.py）。

每段存成 output/<名稱>/chunks/<起始頁>.md。Marker 模型只載入一次，閒置後結束以釋放 VRAM。
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

from . import config
from .marker_server import RESULT_PREFIX

CHUNK_PAGES = 3    # 每段幾頁；Marker 每頁可能要 1–12 秒，段太大會拖慢第一次顯示
OVERLAP_PAGES = 1  # 多轉的頁數，讓跨段落的段落保持完整；這一頁的區塊不保留
_PAGES_RE = re.compile(r"^\d+(-\d+)?(,\d+(-\d+)?)*$")
# 字型編碼損壞的 PDF 會把 fi/fl 連字抽成 " # ! 等符號（"xed、#ow、satis"es），
# 數學符號也一併遺失；Marker 預設沿用這層文字，必須改用 OCR。
_BROKEN_LIGATURE_RE = re.compile(
    r'["#!](xed|nite|rst|eld|elds|gure|gures|es|ed|ow|ows|uid|uids|nd|nds|nal|nally|ne|ned|ll|lled|t|ts|x|rm|ve|ber)\b',
    re.IGNORECASE)


def normalize_pages(pages: str) -> str:
    """頁碼從 0 起算，例如 "0-40" 或 "0,5-10"。"""
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


def text_layer_is_broken(pdf_path, pages: list) -> bool:
    """檢查這幾頁的內嵌文字是否有連字損壞；沒有文字層的 PDF 由 Marker 自行 OCR，回傳 False。"""
    import fitz
    pdf = fitz.open(pdf_path)
    try:
        text = "\n".join(pdf[i].get_text() for i in pages if i < len(pdf))
    finally:
        pdf.close()
    hits = len(_BROKEN_LIGATURE_RE.findall(text))
    return hits >= 3 and hits * 1000 >= len(text.split())


class ChunkTimeout(Exception):
    pass


def chunk_start(page: int) -> int:
    return page - page % CHUNK_PAGES


class MarkerService:
    """管理常駐的 Marker 程序；只由 Worker 執行緒呼叫 convert。"""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.last_used = 0.0

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def convert(self, pdf_path, pages: list) -> str:
        with self.lock:
            if not self.running:
                self.proc = subprocess.Popen(
                    [sys.executable, "-m", "paperai.marker_server"], cwd=config.PROJECT_ROOT,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace",
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
            job = {"pdf": str(pdf_path), "pages": pages, "force_ocr": text_layer_is_broken(pdf_path, pages)}
            self.proc.stdin.write(json.dumps(job, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            # Marker 無法中途取消，逾時只能結束程序；下一段會重新載入模型
            watchdog = threading.Timer(config.CHUNK_TIMEOUT_SECONDS, self.proc.kill)
            watchdog.start()
            try:
                for line in self.proc.stdout:
                    if line.startswith(RESULT_PREFIX):
                        self.last_used = time.time()
                        result = json.loads(line[len(RESULT_PREFIX):])
                        if "error" in result:
                            raise RuntimeError(f"Marker 轉換失敗：{result['error']}")
                        return result["markdown"]
            finally:
                timed_out = not watchdog.is_alive()
                watchdog.cancel()
            if timed_out:
                self.proc.wait(timeout=10)
                self.proc = None
                raise ChunkTimeout()
            raise RuntimeError("Marker 程序意外結束（請確認已安裝 requirements-pdf.txt）")

    def stop_if_idle(self):
        if self.running and time.time() - self.last_used > config.MARKER_IDLE_SECONDS and self.lock.acquire(False):
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
            finally:
                self.proc = None
                self.lock.release()

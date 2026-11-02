# 最後修改：Claude Opus 5.5（PaperAI 維護），20261103
"""PDF → Markdown：依閱讀位置分段呼叫常駐的解析引擎。

優先用 MinerU（.venv-mineru，本機服務），沒有安裝時用 Marker（marker_server.py）。
每段存成 output/<名稱>/chunks/<起始頁>.md。模型只載入一次，閒置後結束以釋放 VRAM。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import config, library
from .marker_server import RESULT_PREFIX

CHUNK_PAGES = 3    # 每段幾頁；段太大會拖慢第一次顯示
OVERLAP_PAGES = 1  # 多轉的頁數，讓跨段落的段落保持完整；這一頁的區塊不保留
IMAGE_DPI = 200
IMAGE_QUALITY = 90
HEADER_CHARS = 70  # 比這短且緊鄰頁碼的文字視為書眉
START_ATTEMPTS = 4  # 服務剛啟動時還不能解析，最多試幾次
START_RETRY_SECONDS = 10
# 把雲端網址指向關閉的本機埠：不論設定如何都不會把文件傳到 mineru.net
_BLOCKED_REMOTE = "http://127.0.0.1:9"
_PAGE_MARK_RE = re.compile(r"<!-- page \d+ of \d+ -->")
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


class ChunkCancelled(Exception):
    pass


def chunk_start(page: int) -> int:
    return page - page % CHUNK_PAGES


def _image_pdf(pdf_path, pages: list, target: Path) -> None:
    """只留頁面影像：很多教科書的文字層是壞的，MinerU 看到文字層會直接沿用（θ 變成 \\$）。"""
    import fitz
    source = fitz.open(pdf_path)
    output = fitz.open()
    try:
        for index in pages:
            if index < len(source):
                page = source[index]
                image = page.get_pixmap(dpi=IMAGE_DPI).tobytes("jpeg", jpg_quality=IMAGE_QUALITY)
                output.new_page(width=page.rect.width, height=page.rect.height).insert_image(page.rect, stream=image)
        output.save(target)
    finally:
        output.close()
        source.close()


def _clean_page(page: str) -> str:
    """去掉頁碼、書眉與 MinerU 的圖片引用；它們夾在內文中間會被當成段落翻譯。"""
    parts = [p.strip() for p in page.split("\n\n") if p.strip() and not p.strip().startswith("![")]
    head = next((i for i, part in enumerate(parts[:2]) if part.isdigit()), None)
    if head is not None:
        title = parts[1] if head == 0 and len(parts) > 1 else ""
        keep_title = not title or len(title) > HEADER_CHARS or title[0] in "#$"
        parts = parts[head + 1 if keep_title else 2:]
    if parts and parts[-1].isdigit():
        parts.pop()
    return "\n\n".join(parts)


def clean_mineru(markdown: str) -> str:
    return "\n\n".join(filter(None, map(_clean_page, _PAGE_MARK_RE.split(markdown))))


class MarkerService:
    """管理常駐的 Marker 程序；只由 Worker 執行緒呼叫 convert。"""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.last_used = 0.0
        self.cancelled = False

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def convert(self, pdf_path, pages: list) -> str:
        with self.lock:
            self.cancelled = False
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
            watchdog = threading.Timer(library.translation_settings()["chunk_timeout_minutes"] * 60, self.proc.kill)
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
            if timed_out or self.cancelled:
                self.proc.wait(timeout=10)
                self.proc = None
                raise ChunkCancelled() if self.cancelled else ChunkTimeout()
            raise RuntimeError("Marker 程序意外結束（請確認已安裝 requirements-pdf.txt）")

    def cancel(self):
        """由其他執行緒呼叫：結束程序立刻釋放 VRAM，正在轉的這段之後要重做。"""
        proc = self.proc
        if proc is not None and proc.poll() is None:
            self.cancelled = True
            proc.kill()

    def stop_if_idle(self):
        idle_seconds = library.translation_settings()["marker_idle_minutes"] * 60
        if self.running and time.time() - self.last_used > idle_seconds and self.lock.acquire(False):
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
            finally:
                self.proc = None
                self.lock.release()

    def reset(self):
        pass


class MineruService:
    """管理 MinerU 的本機服務；只由 Worker 執行緒呼叫 convert，只用本機解析。"""

    def __init__(self):
        self.running = False
        self.proc = None  # 正在等待結果的 mineru parse
        self.lock = threading.Lock()
        self.last_used = 0.0
        self.cancelled = False

    def _run(self, *args, timeout: float = 60) -> subprocess.CompletedProcess:
        config.MINERU_WORK.mkdir(parents=True, exist_ok=True)
        return subprocess.run([str(config.MINERU_EXE), *args], capture_output=True, text=True, timeout=timeout,
                              cwd=config.MINERU_WORK, encoding="utf-8", errors="replace",
                              env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "NO_COLOR": "1"})

    def _start(self):
        """每次啟動都重新鎖定本機模式，確認無誤才解析。"""
        self._run("server", "start", timeout=120)
        self._run("telemetry", "disable")
        self._run("config", "set", "parse_server.remote.url", _BLOCKED_REMOTE)
        self._run("config", "set", "parse_server.local.mode", "managed")
        settings = self._run("config", "show").stdout
        if "managed" not in settings or _BLOCKED_REMOTE not in settings:
            self._stop()
            raise RuntimeError("無法把 MinerU 鎖定為本機模式，已停止解析")
        self.running = True

    def _stop(self):
        try:
            self._run("server", "stop", timeout=30)
        except subprocess.TimeoutExpired:
            pass
        self.running = False
        shutil.rmtree(config.MINERU_WORK, ignore_errors=True)

    def _parse(self, source: Path, target: Path, timeout: int) -> None:
        self.proc = subprocess.Popen(
            [str(config.MINERU_EXE), "parse", str(source), "--tier", "standard", "--pages", "all", "--force",
             "--wait", str(timeout), "--output", str(target)],
            cwd=config.MINERU_WORK, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "NO_COLOR": "1"})
        try:
            output = self.proc.communicate(timeout=timeout + 10)[0]
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            self._stop()  # 服務裡的工作還在跑，不結束就不會釋放 GPU
            raise ChunkTimeout()
        if self.cancelled:
            raise ChunkCancelled()
        if self.proc.returncode or not target.is_file():
            raise RuntimeError(f"MinerU 解析失敗：{output[-300:]}")

    def convert(self, pdf_path, pages: list) -> str:
        with self.lock:
            self.cancelled = False
            if not self.running:
                self._start()
            timeout = library.translation_settings()["chunk_timeout_minutes"] * 60
            with tempfile.TemporaryDirectory() as folder:
                source, target = Path(folder) / "chunk.pdf", Path(folder) / "chunk.md"
                _image_pdf(pdf_path, pages, source)
                for attempt in range(START_ATTEMPTS):
                    try:
                        self._parse(source, target, timeout)
                        break
                    except RuntimeError:
                        if attempt == START_ATTEMPTS - 1:
                            raise
                        time.sleep(START_RETRY_SECONDS)
                        # MinerU 服務是全機共用的；另一個 PaperAI（例如測試用的 8001）啟動時會把它停掉，所以重新啟動
                        self._start()
                self.last_used = time.time()
                return clean_mineru(target.read_text(encoding="utf-8"))

    def cancel(self):
        """由其他執行緒呼叫：結束服務立刻釋放 VRAM，正在轉的這段之後要重做。"""
        if not self.running:
            return
        self.cancelled = True
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
        self._stop()

    def stop_if_idle(self):
        idle_seconds = library.translation_settings()["marker_idle_minutes"] * 60
        if self.running and time.time() - self.last_used > idle_seconds and self.lock.acquire(False):
            try:
                self._stop()
            finally:
                self.lock.release()

    def reset(self):
        """服務重啟時，收掉上次被強制關閉而留下的 MinerU。"""
        self._stop()


def make_parser():
    return MineruService() if config.MINERU_EXE.is_file() else MarkerService()

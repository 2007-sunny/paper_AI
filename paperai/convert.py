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


def convert_pdf(pdf_path, pages: str = None, log=print) -> str:
    """轉換 PDF，回傳文件名稱（output/ 下的資料夾名）。"""
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
    log(f"正在轉換：{pdf_path.name}" + (f"（頁 {pages}）" if pages else ""))
    # 自動選擇裝置；可透過 TORCH_DEVICE 環境變數指定。
    result = subprocess.run(command, env=os.environ.copy(), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    produced = tmp_root / pdf_path.stem
    if result.returncode != 0 or not (produced / f"{pdf_path.stem}.md").exists():
        tail = "\n".join((result.stderr or result.stdout or "").strip().splitlines()[-5:])
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise RuntimeError(f"Marker 轉換失敗：{tail}")

    target.mkdir(parents=True, exist_ok=True)
    for item in produced.iterdir():
        new_name = item.name.replace(pdf_path.stem, name, 1) if item.name.startswith(pdf_path.stem) else item.name
        shutil.move(str(item), str(target / new_name))
    shutil.rmtree(tmp_root, ignore_errors=True)
    log(f"完成：output/{name}/{name}.md")
    return name

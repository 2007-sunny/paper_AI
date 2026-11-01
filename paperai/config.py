# 最後修改：Claude Opus 5.5（PaperAI 維護），20261101
"""全域設定與文件路徑。"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = PROJECT_ROOT / "output"
# MinerU 裝在獨立環境，避免和 Marker 的套件版本衝突；沒有安裝時改用 Marker
MINERU_EXE = PROJECT_ROOT / ".venv-mineru" / "Scripts" / "mineru.exe"
MINERU_WORK = OUTPUT_ROOT / "mineru_work"  # 服務在這裡暫存收到的 PDF，停止時刪除
# 跨文件共用、可手動維護的術語表；優先於自動生成的術語表
USER_GLOSSARY_PATH = PROJECT_ROOT / "glossary.json"

MODEL_NAME = "gemma4:latest"  # 需與 `ollama list` 顯示的名稱一致
# 思考型模型的 thinking 會大幅拖慢翻譯，預設關閉。
THINK = False
NUM_CTX = 16384      # 需容納術語擷取的 25000 字元採樣
NUM_PREDICT = 4096   # 單次回覆上限，避免失控生成
ROUGH_MONTHLY_CHARS = 450_000  # 每月送 Google 的字元上限，防止意外超額
ROUGH_MAX_CHARS = 500_000      # Google 每月免費額度，設定頁不能超過
FINE_MODES = ("auto", "ctrl", "off")  # 讀到就翻／按 Ctrl 才翻／關閉（不用 GPU）
FINE_MODE = "auto"
# 設定頁「進階」的預設值，使用者調整後存在 library.db
ADVANCED_DEFAULTS = {
    "lookahead": 1,                 # 目前頁之後預先細翻幾頁
    "model": MODEL_NAME,
    "monthly_chars": ROUGH_MONTHLY_CHARS,
    "marker_idle_minutes": 5,       # Marker 閒置多久後關閉以釋放 VRAM
    "chunk_timeout_minutes": 4,     # 一段超過這個時間就放棄，公式密集的頁面可能要十分鐘以上
}


class Doc:
    """一份文件在 output/<BASE_NAME>/ 下的所有路徑。"""

    def __init__(self, input_name: str):
        given = Path(input_name)
        self.base_name = given.stem if given.suffix == ".md" else given.name
        self.output_dir = OUTPUT_ROOT / self.base_name
        # 可傳入 output/ 下的檔名，或任意位置的 .md 路徑
        self.md_path = given if given.is_file() else self.output_dir / f"{self.base_name}.md"
        os.makedirs(self.output_dir, exist_ok=True)

    def path(self, suffix: str) -> Path:
        """例如 path("_glossary.json") → output/<名稱>/<名稱>_glossary.json"""
        return self.output_dir / f"{self.base_name}{suffix}"

    def load_markdown(self) -> str:
        if not self.md_path.is_file():
            raise FileNotFoundError(f"找不到 Markdown：{self.md_path}（文件還沒轉檔完成）")
        return self.md_path.read_text(encoding="utf-8")

"""全域設定與文件路徑。"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = PROJECT_ROOT / "output"
# 跨文件共用、可手動維護的術語表；優先於自動生成的術語表
USER_GLOSSARY_PATH = PROJECT_ROOT / "glossary.json"

MODEL_NAME = "gemma4:latest"  # 需與 `ollama list` 顯示的名稱一致
# 思考型模型的 thinking 會大幅拖慢翻譯，預設關閉。
THINK = False
NUM_CTX = 16384      # 需容納術語擷取的 25000 字元採樣
NUM_PREDICT = 4096   # 單次回覆上限，避免失控生成


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
            raise FileNotFoundError(f"找不到 Markdown：{self.md_path}（請先執行 pdftomd.py）")
        return self.md_path.read_text(encoding="utf-8")

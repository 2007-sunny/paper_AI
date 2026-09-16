"""使用 Marker 將 input/ 的 PDF 轉成 output/ 的 Markdown。"""
import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
INPUT_FILENAME = "1804.03318v2.pdf"
# None 表示完整文件；例如 "0-2" 只轉換前 3 頁。
PAGE_RANGE = None


def main():
    pdf_path = PROJECT_ROOT / "input" / INPUT_FILENAME
    output_dir = PROJECT_ROOT / "output"
    if not pdf_path.is_file():
        raise FileNotFoundError(f"找不到 PDF：{pdf_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    command = ["marker_single", str(pdf_path), "--output_dir", str(output_dir)]
    if PAGE_RANGE:
        command.extend(["--page_range", PAGE_RANGE])
    print(f"正在轉換：{pdf_path}")
    # 自動選擇裝置；可透過 TORCH_DEVICE 環境變數指定。
    subprocess.run(command, env=os.environ.copy(), check=True)
    print(f"完成！請檢查 {output_dir / pdf_path.stem}。")


if __name__ == "__main__":
    main()

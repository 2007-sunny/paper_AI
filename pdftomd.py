"""使用 Marker 將 PDF 轉成 output/<名稱>/<名稱>.md。

PDF 可放在任何位置；只給檔名時會到 input/ 尋找。
範例：
python pdftomd.py "D:/papers/1804.03318v2.pdf"
python pdftomd.py "Fundamentals of Photonics.pdf" --pages 0-40
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent
INPUT_FILENAME = "1804.03318v2.pdf"


def main():
    parser = argparse.ArgumentParser(description="PDF → Markdown（Marker）")
    parser.add_argument("pdf", nargs="?", default=INPUT_FILENAME, help=f"PDF 路徑或 input/ 下的檔名（預設：{INPUT_FILENAME}）")
    parser.add_argument("--pages", help='頁碼範圍（從 0 起算），例如 "0-2" 或 "10-40"；不指定則轉換完整文件')
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        pdf_path = PROJECT_ROOT / "input" / args.pdf
    if not pdf_path.is_file():
        raise FileNotFoundError(f"找不到 PDF：{args.pdf}")

    output_dir = PROJECT_ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = ["marker_single", str(pdf_path.resolve()), "--output_dir", str(output_dir)]
    if args.pages:
        command.extend(["--page_range", args.pages])
    print(f"正在轉換：{pdf_path}")
    # 自動選擇裝置；可透過 TORCH_DEVICE 環境變數指定。
    subprocess.run(command, env=os.environ.copy(), check=True)
    print(f"完成！接著執行：python translate.py --input \"{pdf_path.stem}.md\"")


if __name__ == "__main__":
    main()

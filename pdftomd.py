"""使用 Marker 將 PDF 轉成 output/<名稱>/<名稱>.md。

PDF 可放在任何位置；只給檔名時會到 input/ 尋找。指定 --pages 時，文件名稱會加上 _p<範圍>。
範例：
python pdftomd.py "D:/papers/1804.03318v2.pdf"
python pdftomd.py "Fundamentals of Photonics.pdf" --pages 0-40
"""
import argparse
import sys
from pathlib import Path

from paperai import config
from paperai.convert import convert_pdf

sys.stdout.reconfigure(encoding="utf-8")

INPUT_FILENAME = "1804.03318v2.pdf"


def main():
    parser = argparse.ArgumentParser(description="PDF → Markdown（Marker）")
    parser.add_argument("pdf", nargs="?", default=INPUT_FILENAME, help=f"PDF 路徑或 input/ 下的檔名（預設：{INPUT_FILENAME}）")
    parser.add_argument("--pages", help='頁碼範圍（從 0 起算），例如 "0-2" 或 "10-40"；不指定則轉換完整文件')
    ocr = parser.add_mutually_exclusive_group()
    ocr.add_argument("--force-ocr", dest="force_ocr", action="store_const", const=True,
                     help="忽略 PDF 內嵌文字，整頁 OCR（預設自動判斷文字層是否損壞）")
    ocr.add_argument("--no-force-ocr", dest="force_ocr", action="store_const", const=False,
                     help="一律使用 PDF 內嵌文字")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        pdf_path = config.PROJECT_ROOT / "input" / args.pdf
    name = convert_pdf(pdf_path, args.pages, force_ocr=args.force_ocr)
    print(f"接著執行：python translate.py --input \"{name}.md\"，或用 python server.py 在書庫中開啟")


if __name__ == "__main__":
    main()

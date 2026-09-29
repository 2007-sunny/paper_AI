"""PaperAI 本機服務入口。

python server.py            # 啟動並自動開啟瀏覽器
python server.py --no-browser
uvicorn server:app --host 127.0.0.1 --port 8000   # 也可以這樣啟動
"""
import argparse
import sys
import threading
import webbrowser

from paperai.app import app  # noqa: F401  uvicorn server:app 需要

sys.stdout.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="PaperAI 本機服務")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="不要自動開啟瀏覽器")
    args = parser.parse_args()

    import uvicorn
    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"PaperAI 已啟動：{url}（Ctrl+C 結束）")
    # 只綁定本機；靜態閱讀器的選字查詢預設連 port 8000
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

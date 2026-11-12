# 最後修改：Claude Opus 5.5（PaperAI 維護），20261103
"""PaperAI 本機服務入口。

python server.py            # 啟動並自動開啟瀏覽器
python server.py --no-browser
pythonw server.py --no-browser   # 沒有主控台（例如由引用 MCP 背景啟動）時，輸出寫到 output/server.log
uvicorn server:app --host 127.0.0.1 --port 8000   # 也可以這樣啟動
"""
import argparse
import sys
import threading
import webbrowser
from pathlib import Path

# pythonw 或背景程序沒有主控台時 sys.stdout／stderr 是 None，print 與 reconfigure 會直接讓服務結束。
# 必須在匯入 paperai 之前處理，載入模型與 uvicorn 的紀錄也會寫到這裡。
if sys.stdout is None or sys.stderr is None:
    _log_dir = Path(__file__).resolve().parent / "output"
    _log_dir.mkdir(exist_ok=True)
    _log = open(_log_dir / "server.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

from paperai.app import app  # noqa: E402,F401  uvicorn server:app 需要

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
    # 只綁定本機
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

@echo off
chcp 65001 >nul
title PaperAI 本機服務
cd /d "%~dp0"

rem 服務已在執行時只開啟閱讀器，不重複啟動
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/state -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
    start "" http://127.0.0.1:8000/
    exit /b 0
)

rem 有虛擬環境就用虛擬環境的 Python
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo 正在啟動 PaperAI，瀏覽器會自動開啟閱讀器 http://127.0.0.1:8000/
echo 關閉這個視窗就會停止服務（轉檔與翻譯也會中斷）。
echo.
"%PY%" server.py
if errorlevel 1 (
    echo.
    echo 啟動失敗。請確認已安裝 Python 與 requirements.txt，並已開啟 Ollama。
    pause
)

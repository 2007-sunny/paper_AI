# PaperAI — Agent Guide

## 正式流程
PDF → Marker → Markdown → 切區塊 → 逐句翻譯 → knowledge JSON → HTML 閱讀器。兩種入口共用 paperai/ 套件：
- 本機服務 server.py（paperai/app.py + session.py）：書庫、邊讀邊翻、即時推送、術語修正、SQLite 生字本。
- 命令列 translate.py / pdftomd.py：一次處理整份文件，輸出靜態 HTML。
模組：config（設定與路徑）、llm（Ollama）、convert（Marker）、blocks（切區塊）、sentences（斷句）、glossary（摘要與術語）、translator（翻譯與快取）、pipeline（共用組裝）、render + templates/（HTML）、library（SQLite 書庫）、session（背景排程）、app（FastAPI 路由）。
所有推論使用本機 Ollama。experiments/ 的分析流程不參與正式閱讀器生成。

## 路徑與設定
- 生成結果放在 output/{BASE_NAME}/，書庫資料庫為 output/library.db，皆不提交 Git。PDF 可放在任何位置，書庫只記錄路徑。
- 路徑一律由 paperai/config.py 計算（PROJECT_ROOT、Doc），不得寫死使用者電腦路徑。
- 模型、thinking、num_ctx 等設定在 paperai/config.py；預設 gemma4:latest（名稱須與 ollama list 一致，不加 ollama/ 前綴）。
- 術語表兩層：output/{BASE_NAME}/{BASE_NAME}_glossary.json（自動生成，可手改）與專案根目錄 glossary.json（使用者維護、跨文件、優先；閱讀器修正譯名時會寫入）。
- 翻譯快取 {BASE_NAME}_translation_cache_v2.json 的 key 包含提示詞版本、模型、句子與本段術語；改提示詞時遞增 translator.PROMPT_VERSION。
- 核心依賴見 requirements.txt；Marker 見 requirements-pdf.txt；原型另需 experiments/requirements.txt。
- spaCy 需要 en_core_web_sm；缺少時會自動嘗試下載。
- Windows 輸出需注意 UTF-8：入口腳本設定 sys.stdout.reconfigure(encoding="utf-8")。

## 執行（從根目錄）
python server.py [--no-browser] [--port 8000]
python pdftomd.py "<PDF 路徑>" [--pages 0-40]
python translate.py --input "<名稱>.md" [--limit N] [--model M] [--render-only]
python tools/bench_models.py --input "<名稱>.md" --models <模型...>
python experiments/analyze_v4.py

## 開發慣例
- 使用者介面與新增提示文字使用繁體中文（台灣）。
- 解析 LLM JSON 時使用 llm.safe_json_parse；需要固定結構時用 Ollama 的 format=<JSON schema>。翻譯輸出含 LaTeX，解析前需經 translator._escape_latex_in_json（否則 \b \f \t 等會被當成 JSON 跳脫）。
- HTML 模板在 paperai/templates/（reader.html/.css/.js、library.html），以 __PLACEHOLDER__ 取代，不用 f-string。reader.js 同時支援靜態檔（file://）與本機服務模式（config.serverMode）。
- MathJax：行內 $...$，區塊 $$...$$；閱讀器從 CDN 載入 MathJax。渲染時公式以 QQQLATEXBLOCKZZZ placeholder 保護。
- 句子切割使用 QQQSPLITBLOCKZZZ、___EG___ 等 placeholder 保護縮寫、LaTeX 與上下標，不得移除。
- 本機服務只綁定 127.0.0.1；非 GET 請求會檢查 Origin，新增寫入類 API 不需另外處理，但不要放寬這個檢查。
- 目前沒有自動化測試、linter 或 type checker；至少做 Python 語法檢查，並實際跑一次（見 CLAUDE.md 的驗證方式）。
- docs/maintenance.md 提供函式與介面維護索引；進度.md 記錄進度與計畫。

## 檔案分類
- server.py、translate.py、pdftomd.py：正式入口。
- paperai/：正式流程、本機服務與網頁模板。
- glossary.json：使用者術語表（提交 Git）。
- tools/bench_models.py：模型速度、對齊率與翻譯品質比較。
- experiments/analyze*.py：v1–v4 實驗分析流程。
- experiments/translate_v2.py：舊版翻譯副本。
- experiments/process.py：PyMuPDF4LLM 原型。

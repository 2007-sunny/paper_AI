# PaperAI — Agent Guide

## 正式流程
PDF 閱讀器只有一個入口：本機服務 server.py（paperai/app.py + session.py）。
- 粗翻：PyMuPDF 取每頁文字與座標，使用者按 Ctrl 時才呼叫 Google Cloud Translation（rough.py）。
- 細翻：MinerU（沒有安裝時用 Marker）背景轉檔 → 切區塊 → 只在閱讀到該頁時用本機 Ollama 逐句翻譯，完成後依單字重疊取代粗翻。
模組：config（設定與路徑）、llm（Ollama）、convert（Marker）、blocks（切區塊）、sentences（斷句）、glossary（摘要與術語）、translator（細翻與快取）、pipeline（共用組裝）、rough（粗翻與段落座標）、render（Markdown 轉 HTML）、templates/（單頁前端）、library（SQLite）、session（背景排程）、app（FastAPI 路由）、pages（區塊對應 PDF 頁碼）、claude_assist（直接問 Claude）。
只有三種情況會把內容傳到外部：按 Ctrl 的段落與查詢的單字傳給 Google，使用者按「直接問 Claude」時呼叫 Anthropic API（模型 claude-opus-5-5）。細翻一律使用本機 Ollama。experiments/ 不參與正式流程。

## 路徑與設定
- 生成結果放在 output/{BASE_NAME}/，書庫資料庫為 output/library.db，皆不提交 Git。PDF 可放在任何位置，書庫只記錄路徑。
- 路徑一律由 paperai/config.py 計算（PROJECT_ROOT、Doc），不得寫死使用者電腦路徑。
- 模型、thinking、num_ctx 等設定在 paperai/config.py；預設 gemma4:latest（名稱須與 ollama list 一致，不加 ollama/ 前綴）。閱讀器的「設置」可調整的項目存在 library.db，預設值是 config.ADVANCED_DEFAULTS；程式取模型用 llm.active_model，不要直接讀 config.MODEL_NAME。
- 術語表兩層：output/{BASE_NAME}/{BASE_NAME}_glossary.json（自動生成，可手改）與專案根目錄 glossary.json（跨文件、優先；aliases 也會套用到粗翻）。
- Google 金鑰放在根目錄 .env 的 GOOGLE_TRANSLATE_API_KEY（不提交 Git，不要寫進程式或貼到對話）；每月上限的預設值是 config.ROUGH_MONTHLY_CHARS。
- 細翻快取 {BASE_NAME}_translation_cache_v2.json 的 key 包含提示詞版本、模型、句子與本段術語；改提示詞時遞增 translator.PROMPT_VERSION。
- 核心依賴見 requirements.txt；Marker 見 requirements-pdf.txt；MinerU 裝在 .venv-mineru，見 requirements-mineru.txt（模型在 ~/.mineru，只用本機解析，不得使用 --remote）；原型另需 experiments/requirements.txt。
- spaCy 需要 en_core_web_sm；缺少時會自動嘗試下載。
- Windows 輸出需注意 UTF-8：入口腳本設定 sys.stdout.reconfigure(encoding="utf-8")。

## 執行（從根目錄）
python server.py [--no-browser] [--port 8000]
pythonw server.py --no-browser   # 背景啟動（沒有主控台），輸出寫到 output/server.log
python tools/bench_models.py --input "<名稱>.md" --models <模型...>
python experiments/analyze_v4.py

## 開發慣例
- 使用者介面與新增提示文字使用繁體中文（台灣）。
- 解析 LLM JSON 時使用 llm.safe_json_parse；需要固定結構時用 Ollama 的 format=<JSON schema>。翻譯輸出含 LaTeX，解析前需經 translator._escape_latex_in_json（否則 \b \f \t 等會被當成 JSON 跳脫）。
- 前端是單一頁面 paperai/templates/app.html、app.css、app.js，由 app.py 內嵌 CSS 與 JS（__CSS__、__JS__）。
- MathJax：行內 $...$，區塊 $$...$$；從 CDN 載入。Markdown 渲染時公式以 QQQLATEXBLOCKZZZ placeholder 保護。
- 句子切割使用 QQQSPLITBLOCKZZZ、___EG___ 等 placeholder 保護縮寫、LaTeX 與上下標，不得移除。
- 本機服務只綁定 127.0.0.1；所有請求都檢查 Host（防 DNS rebinding），非 GET 請求另外檢查 Origin。新增 API 不需另外處理，但不要放寬這兩項檢查。
- 目前沒有自動化測試、linter 或 type checker；至少做 Python 語法檢查，並實際跑一次（見 CLAUDE.md 的驗證方式）。
- docs/maintenance.md 提供函式與介面維護索引；進度.md 記錄進度與計畫。

## 檔案分類
- server.py：正式入口。
- paperai/：正式流程、本機服務與網頁模板。
- glossary.json：使用者術語表（提交 Git）。
- tools/bench_models.py：模型速度、對齊率與翻譯品質比較。
- experiments/analyze*.py：v1–v4 實驗分析流程。
- experiments/translate_v2.py：舊版翻譯副本。
- experiments/process.py：PyMuPDF4LLM 原型。

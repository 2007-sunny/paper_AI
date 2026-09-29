# PaperAI — Agent Guide

## 正式流程
PDF → pdftomd.py（Marker）→ Markdown → translate.py → 英文／繁體中文 HTML 閱讀器。
translate.py 是命令列入口，邏輯在 paperai/ 套件：
文件摘要與術語（glossary.py）→ Markdown 切區塊（blocks.py）→ 逐句翻譯（translator.py，用到 sentences.py）→ 知識 JSON → HTML（render.py + templates/）。
所有推論使用本機 Ollama（llm.py）。experiments/ 的分析流程不參與正式閱讀器生成。

## 路徑與設定
- 生成結果放在 output/{BASE_NAME}/，內容不提交 Git。PDF 可放在任何位置（pdftomd.py 接受路徑），input/ 只是預設位置。
- 路徑一律由 paperai/config.py 計算（PROJECT_ROOT、Doc），不得寫死使用者電腦路徑。
- 模型、thinking、num_ctx 等設定在 paperai/config.py；預設 gemma4:latest（名稱須與 ollama list 一致，不加 ollama/ 前綴）。server.py 沿用同一個預設值。
- 術語表兩層：output/{BASE_NAME}/{BASE_NAME}_glossary.json（自動生成，可手改）與專案根目錄 glossary.json（使用者維護、跨文件、優先）。
- 翻譯快取 {BASE_NAME}_translation_cache_v2.json 的 key 包含提示詞版本、模型、句子與本段術語；改提示詞時遞增 translator.PROMPT_VERSION。
- 核心依賴見 requirements.txt；Marker 見 requirements-pdf.txt；原型另需 experiments/requirements.txt。
- spaCy 需要 en_core_web_sm；缺少時會自動嘗試下載。
- Windows 輸出需注意 UTF-8：入口腳本設定 sys.stdout.reconfigure(encoding="utf-8")。

## 執行（從根目錄）
python pdftomd.py "<PDF 路徑>" [--pages 0-40]
python translate.py --input "<名稱>.md" [--limit N] [--model M] [--render-only]
python tools/bench_models.py --input "<名稱>.md" --models <模型...>
uvicorn server:app --reload --host 127.0.0.1 --port 8000
python experiments/analyze_v4.py

## 開發慣例
- 使用者介面與新增提示文字使用繁體中文（台灣）。
- 解析 LLM JSON 時使用 llm.safe_json_parse；需要固定結構時用 Ollama 的 format=<JSON schema>。
- HTML 模板在 paperai/templates/（reader.html/.css/.js），以 __PLACEHOLDER__ 取代，不用 f-string。
- MathJax：行內 $...$，區塊 $$...$$；閱讀器從 CDN 載入 MathJax。渲染時公式以 QQQLATEXBLOCKZZZ placeholder 保護。
- 句子切割使用 QQQSPLITBLOCKZZZ、___EG___ 等 placeholder 保護縮寫、LaTeX 與上下標，不得移除。
- 目前沒有自動化測試、linter 或 type checker；至少做 Python 語法檢查，並用 --limit 預覽實際跑一次。
- docs/maintenance.md 提供函式與介面維護索引；進度.md 記錄進度與計畫。

## 檔案分類
- translate.py、pdftomd.py、server.py：正式入口。
- paperai/：正式流程的程式碼與閱讀器模板。
- glossary.json：使用者術語表（提交 Git）。
- tools/bench_models.py：模型速度、對齊率與翻譯品質比較。
- experiments/analyze*.py：v1–v4 實驗分析流程。
- experiments/translate_v2.py：舊版翻譯副本。
- experiments/process.py：PyMuPDF4LLM 原型。

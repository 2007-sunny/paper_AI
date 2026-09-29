# PaperAI — Agent Guide

## 正式流程
PDF → pdftomd.py（Marker）→ Markdown → translate.py → 英文／繁體中文 HTML 閱讀器。
六個階段：文件摘要、術語擷取、段落翻譯、句子切割與對齊、知識 JSON、HTML。
所有推論使用本機 Ollama。experiments/ 的分析流程不參與正式閱讀器生成。

## 路徑與設定
- PDF 放在 input/；生成結果放在 output/{BASE_NAME}/。兩者內容不提交 Git。
- 輸出路徑以 __file__ 計算專案根目錄，不得寫死使用者電腦路徑。
- pdftomd.py 的 INPUT_FILENAME 與 translate.py 的輸入（INPUT_FILENAME 或 --input）必須對應同一文件。
- pdftomd.py 的 PAGE_RANGE 預設 None，表示完整文件。Marker 自動選擇裝置，可用 TORCH_DEVICE 覆寫。
- translate.py 的 MODEL_NAME 與 server.py 的 downloaded_models 目前預設 gemma4:latest（名稱須與 ollama list 一致，不加 ollama/ 前綴）。thinking 預設關閉（THINK / --think）。
- 翻譯快取每段寫入 {BASE_NAME}_translation_cache.json；術語表存於 {BASE_NAME}_glossary.json 並重複使用。
- 核心依賴見 requirements.txt；Marker 見 requirements-pdf.txt；原型另需 experiments/requirements.txt。
- spaCy 需要 en_core_web_sm；翻譯腳本會在缺少時嘗試下載。
- Windows 輸出需注意 UTF-8。translate.py、experiments/translate_v2.py 與 experiments/analyze_v4.py 設定 stdout encoding。

## 執行（從根目錄）
python pdftomd.py
python translate.py --input "<名稱>.md" [--limit N]
python tools/bench_models.py --input "<名稱>.md" --models <模型...>
uvicorn server:app --reload --host 127.0.0.1 --port 8000
python experiments/analyze_v4.py
python tools/check_latex.py

## 開發慣例
- 使用者介面與新增提示文字使用繁體中文（台灣）。
- 解析 LLM JSON 時使用 safe_json_parse，處理 Markdown fences 與雜訊。
- f-string HTML 模板使用雙大括號 {{ / }}。
- MathJax：行內 $...$，區塊 $$...$$；閱讀器從 CDN 載入 MathJax。
- 句子切割使用 QQQSPLITBLOCKZZZ、___EG___ 等 placeholder 保護縮寫與 LaTeX，不得移除。
- 目前沒有自動化測試、linter 或 type checker；至少做 Python 語法檢查。
- docs/maintenance.md 提供函式與介面維護索引。

## 檔案分類
- translate.py、pdftomd.py、server.py：正式入口。
- experiments/analyze*.py：v1–v4 實驗分析流程。
- experiments/translate_v2.py：舊版翻譯副本。
- experiments/process.py：PyMuPDF4LLM 原型。
- tools/check_latex.py：HTML 除錯工具，並非自動化測試。
- tools/bench_models.py：模型速度與翻譯品質比較，共用 translate.build_translation_prompt。

# PaperAI

使用本機 Ollama 將學術文件轉成英文／繁體中文對照的 HTML 閱讀器，支援術語標示、生字本、句子對齊與 MathJax 公式顯示。

## 專案結構

| 路徑 | 用途 |
|---|---|
| pdftomd.py | PDF → Markdown |
| translate.py | Markdown → 雙語閱讀器（正式入口） |
| server.py | 即時單字解釋 API |
| input/ | 本機 PDF，不提交 |
| output/ | Markdown、圖片、快取與閱讀器，不提交 |
| experiments/ | 歷代分析流程、翻譯副本與原型 |
| tools/check_latex.py | 已生成 HTML 的除錯工具 |
| docs/maintenance.md | 程式維護索引 |

## 安裝

建議使用 Python 3.10 以上版本與獨立虛擬環境。以下指令從專案根目錄執行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-pdf.txt
python -m spacy download en_core_web_sm
```

若已有 Markdown，只需安裝 requirements.txt。依賴目前未鎖定版本。

另需安裝並啟動 Ollama，下載欲使用的模型。修改 translate.py 的 MODEL_NAME 與 server.py 的 downloaded_models，使其對應 ollama list 中可用的模型；目前程式預設為 ollama/qwythos:latest。

## 產生閱讀器

1. 將 PDF 放入 input/，修改 pdftomd.py 的 INPUT_FILENAME。預設轉換完整文件，可用 PAGE_RANGE 指定頁面。
2. 執行 python pdftomd.py，產生 output/<文件名稱>/<文件名稱>.md。
3. 將 translate.py 的 INPUT_FILENAME 改成相同文件的 .md 檔名。
4. 執行 python translate.py。
5. 在瀏覽器開啟 output/<文件名稱>/<文件名稱>_reader.html。

兩個入口目前的預設文件不同，執行前請依上述步驟設成同一份文件。輸出路徑由程式位置決定，不依賴特定電腦的絕對路徑。

翻譯流程依序建立文件摘要、術語表、段落翻譯、英中句子對齊、知識 JSON，最後產生 HTML。spaCy 語言模型若未安裝，翻譯程式會嘗試下載。推論使用本機 Ollama；MathJax 由 CDN 載入，因此公式顯示仍可能需要網路。

即時單字解釋功能需另外啟動：

```powershell
uvicorn server:app --reload --host 127.0.0.1 --port 8000
```

## 實驗程式

experiments/analyze.py、analyze_v2.py、analyze_v3.py、analyze_v4.py 是獨立分析流程，不參與正式閱讀器生成。各檔案頂端保留自己的文件與模型設定。

```powershell
python experiments/analyze_v4.py
python tools/check_latex.py
```

experiments/translate_v2.py 保留舊翻譯副本；experiments/process.py 是使用 PyMuPDF4LLM 的早期原型，需另安裝 python -m pip install -r experiments/requirements.txt。

## 版本控制

Git 僅追蹤程式、文件與依賴清單。原始 PDF、生成結果、快取、虛擬環境、編輯器設定及 .env 均保留在本機並由 .gitignore 排除。目前沒有自動化測試套件。

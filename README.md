# PaperAI

使用本機 Ollama 將學術文件轉成英文／繁體中文對照的 HTML 閱讀器，支援逐句對照、術語標示、生字本與 MathJax 公式顯示。

## 專案結構

| 路徑 | 用途 |
|---|---|
| pdftomd.py | PDF → Markdown（Marker） |
| translate.py | Markdown → 中英對照閱讀器（正式入口） |
| server.py | 閱讀器選字查詢用的本機 API |
| paperai/ | 翻譯流程與閱讀器模板 |
| glossary.json | 使用者術語表（跨文件共用） |
| tools/bench_models.py | 模型速度與翻譯品質比較 |
| output/ | Markdown、圖片、快取與閱讀器，不提交 |
| experiments/ | 歷代分析流程與原型 |
| docs/maintenance.md | 程式維護索引 |
| 進度.md | 進度與後續計畫 |

## 安裝

建議使用 Python 3.10 以上版本與獨立虛擬環境。以下指令從專案根目錄執行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-pdf.txt
python -m spacy download en_core_web_sm
```

若已有 Markdown，只需安裝 requirements.txt。依賴目前未鎖定版本。

另需安裝並啟動 Ollama，下載欲使用的模型。預設模型設定在 paperai/config.py（目前為 gemma4:latest），名稱須與 ollama list 顯示的完全一致。

## 產生閱讀器

```powershell
python pdftomd.py "D:/papers/xxx.pdf"                  # PDF 可放在任何位置；大型書籍可加 --pages 0-40
python translate.py --input "xxx.md" --limit 20        # 先翻前 20 個區塊預覽
python translate.py --input "xxx.md"                   # 完整翻譯
```

結果在 output/xxx/：xxx_reader.html（預覽為 xxx_preview_reader.html），直接用瀏覽器開啟即可。

- **中斷可接續**：每個區塊翻完就寫入快取，重跑時已翻過的區塊會直接沿用。
- **修正術語**：譯名有誤時，把正確譯名加入根目錄的 glossary.json（格式見檔案內說明），再重跑 translate.py；只有含該術語的區塊會重新翻譯。自動生成的術語表在 output/xxx/xxx_glossary.json，也可以直接修改。
- **只改版面**：修改 paperai/templates/ 後，用 `python translate.py --input "xxx.md" --render-only` 重新產生 HTML，不會呼叫模型。
- 其他參數：--model 指定模型、--think 啟用思考模式（很慢）。

翻譯方式：Markdown 先切成區塊，只翻譯標題、段落與清單；公式、表格、圖片原樣保留。每個段落切成英文句子後一次送給模型，並以 JSON schema 要求回傳同樣數量的譯文，因此中英句子一對一對齊。模型未能對齊時會改為整段翻譯，閱讀器中以虛框標示。

MathJax 由 CDN 載入，公式顯示需要網路。

閱讀器的選字查詢功能需另外啟動：

```powershell
uvicorn server:app --reload --host 127.0.0.1 --port 8000
```

## 測試模型速度與品質

```powershell
python tools/bench_models.py --input "1804.03318v2.md"
python tools/bench_models.py --input "1804.03318v2.md" --models gemma4:latest qwythos:latest --paragraphs 5
```

從文件挑出幾個內文段落，用與正式流程相同的方式翻譯。終端機會列出每個模型的 GPU 載入比例、每段秒數、tok/s 與對齊成功率；逐句並排的對照報告存在 output/<文件名稱>/bench/。GPU 比例低於 100% 表示模型部分在 CPU 上執行，速度會明顯下降。

## 實驗程式

experiments/analyze.py、analyze_v2.py、analyze_v3.py、analyze_v4.py 是獨立分析流程，不參與正式閱讀器生成。各檔案頂端保留自己的文件與模型設定。experiments/translate_v2.py 保留舊翻譯副本；experiments/process.py 是使用 PyMuPDF4LLM 的早期原型。執行前需另外安裝 `python -m pip install -r experiments/requirements.txt`。

## 版本控制

Git 僅追蹤程式、文件、使用者術語表與依賴清單。原始 PDF、生成結果、快取、虛擬環境、編輯器設定及 .env 均保留在本機並由 .gitignore 排除。目前沒有自動化測試套件。

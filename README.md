# PaperAI

使用本機 Ollama 閱讀英文學術文件的中英對照閱讀器：書庫管理、邊讀邊翻、逐句對照、術語標示與修正、生字本、MathJax 公式顯示。

## 快速開始

```powershell
python server.py
```

瀏覽器會自動開啟書庫（http://127.0.0.1:8000/）：

1. **加入 PDF**：在「監看資料夾」加入放 PDF 的資料夾（例如 D:\papers），書庫會列出裡面所有 PDF；也可以直接貼上單一 PDF 的路徑。PDF 不會被複製。
2. **轉換**：按「轉換」用 Marker 轉成 Markdown。大型書籍可以填頁碼（從 0 起算，例如 0-40）分章轉換，每一章會成為獨立文件。
3. **閱讀**：點文件標題開啟。第一次開啟會先生成摘要與術語表，接著**從你正在看的位置開始翻譯**，譯文完成後自動出現在頁面上；捲到哪裡就優先翻哪裡。
4. **修正術語**：點擊內文中藍色虛線的術語，可以加入生字本或修改譯名。修改會存到 glossary.json（所有文件共用），含該術語的段落自動重新翻譯。
5. **生字本**：選取任意英文單字，按浮動按鈕請本機 AI 解釋並加入生字本；生字本存在 output/library.db，所有文件共用，並記錄來源文件與句子。

排程規則：只有一張 GPU，轉檔與翻譯依序執行。轉檔優先，其次是正在閱讀的文件；關閉閱讀頁後，該文件會在背景繼續翻譯 30 分鐘。全部翻完時會同時輸出可離線開啟的靜態 HTML（output/<名稱>/<名稱>_reader.html）。

## 專案結構

| 路徑 | 用途 |
|---|---|
| server.py | 本機服務入口（書庫、閱讀器、API） |
| translate.py | 命令列翻譯整份文件，輸出靜態 HTML |
| pdftomd.py | 命令列 PDF → Markdown（Marker） |
| paperai/ | 翻譯流程、本機服務與網頁模板 |
| glossary.json | 使用者術語表（跨文件共用） |
| tools/bench_models.py | 模型速度與翻譯品質比較 |
| output/ | Markdown、圖片、快取、閱讀器與書庫資料庫，不提交 |
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

## 命令列用法

不開服務也可以直接處理整份文件：

```powershell
python pdftomd.py "D:/papers/xxx.pdf" --pages 0-40     # PDF 可放在任何位置
python translate.py --input "xxx.md" --limit 20        # 先翻前 20 個區塊預覽
python translate.py --input "xxx.md"                   # 完整翻譯
python translate.py --input "xxx.md" --render-only     # 只重新產生 HTML（改版面時用）
```

命令列與本機服務共用同一份翻譯快取與 knowledge JSON，兩邊的進度可以互相接續。其他參數：--model 指定模型、--think 啟用思考模式（很慢）。

翻譯方式：Markdown 先切成區塊，只翻譯標題、段落與清單；公式、表格、圖片原樣保留。每個段落切成英文句子後一次送給模型，並以 JSON schema 要求回傳同樣數量的譯文，因此中英句子一對一對齊。模型未能對齊時會改為整段翻譯，閱讀器中以虛框標示。

MathJax 由 CDN 載入，公式顯示需要網路。

## 測試模型速度與品質

```powershell
python tools/bench_models.py --input "1804.03318v2.md"
python tools/bench_models.py --input "1804.03318v2.md" --models gemma4:latest qwythos:latest --paragraphs 5
```

從文件挑出幾個內文段落，用與正式流程相同的方式翻譯。終端機會列出每個模型的 GPU 載入比例、每段秒數、tok/s 與對齊成功率；逐句並排的對照報告存在 output/<文件名稱>/bench/。GPU 比例低於 100% 表示模型部分在 CPU 上執行，速度會明顯下降。

## 實驗程式

experiments/analyze.py、analyze_v2.py、analyze_v3.py、analyze_v4.py 是獨立分析流程，不參與正式閱讀器生成。experiments/translate_v2.py 保留舊翻譯副本；experiments/process.py 是使用 PyMuPDF4LLM 的早期原型。執行前需另外安裝 `python -m pip install -r experiments/requirements.txt`。

## 版本控制

Git 僅追蹤程式、文件、使用者術語表與依賴清單。原始 PDF、生成結果、快取、書庫資料庫、虛擬環境、編輯器設定及 .env 均保留在本機並由 .gitignore 排除。目前沒有自動化測試套件。

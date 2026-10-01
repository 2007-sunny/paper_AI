# PaperAI

用**本機 AI** 閱讀英文學術論文與教科書的中英對照閱讀器。PDF 留在原本的位置，打開就能讀，翻譯從你正在看的段落開始，逐句對照、公式完整保留，搭配術語表、生字本與間隔複習，把「讀英文文獻」變成可以累積的學習。

翻譯全部在自己的電腦上用 [Ollama](https://ollama.com/) 執行，不需要網路服務帳號，也不會把文件上傳到任何地方。

## 功能

- **書庫**：指定放 PDF 的資料夾（可以是 Zotero 的 storage 資料夾），自動列出所有 PDF；PDF 不會被複製。大型書籍可以只轉換需要的頁碼範圍，每一章成為獨立文件。
- **邊讀邊翻**：開啟文件立即顯示原文，譯文完成後自動出現；捲到哪裡就優先翻哪裡，不必等整本翻完。
- **逐句對照**：滑鼠移到句子上，會同時標示對應的另一種語言。提供左右對照、上下交錯、只看英文、只看中文四種檢視。
- **公式與表格**：LaTeX 公式以 MathJax 顯示並原樣保留；表格只翻譯文字儲存格，數字與單位不動。
- **原版面**：左邊是原始 PDF，右邊是目前這一頁的譯文，適合公式與圖表多的書籍。
- **術語表**：自動擷取每份文件的核心術語並標示在內文中。譯名不對時，點擊術語直接修改，所有文件共用，含該術語的段落會自動重新翻譯。
- **生字本與複習**：點術語或選取任意單字，由本機 AI 解釋後加入生字本（附原文例句與出處）；以間隔重複方式複習，也可匯出到 Anki。
- **問 AI**：遇到看不懂的推導或圖片，一鍵複製整理好的提示詞（含原始 LaTeX、目前譯文與文件背景）貼到 Claude.ai 等網頁版 AI。設定 Anthropic API 金鑰後也可以在閱讀器內直接取得回答（選用）。
- **離線閱讀**：全部翻完時同時輸出單一 HTML 檔，雙擊即可開啟。

## 需求

- Windows（目前只在 Windows 11 測試過），Python 3.10 以上
- [Ollama](https://ollama.com/download)，以及一個翻譯用模型
- 建議有 NVIDIA 顯示卡。開發環境是 RTX 4060 Laptop（8GB），預設模型 `gemma4:latest` 約 5 秒翻譯一段
- 轉換 PDF 使用 [Marker](https://github.com/datalab-to/marker)，第一次執行會下載辨識模型
- 公式（MathJax）與原版面（PDF.js）由 CDN 載入，顯示時需要網路

## 安裝

```powershell
git clone https://github.com/2007-sunny/paper_AI.git
cd paper_AI
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-pdf.txt
python -m spacy download en_core_web_sm
ollama pull gemma4
```

`requirements-pdf.txt` 包含 Marker；如果只需要翻譯已經轉好的 Markdown，安裝 `requirements.txt` 即可。

換模型時修改 `paperai/config.py` 的 `MODEL_NAME`，名稱須與 `ollama list` 顯示的完全一致。可以先用 `tools/bench_models.py`（見下方）比較不同模型在你電腦上的速度與品質。

## 使用

```powershell
python server.py
```

瀏覽器會自動開啟書庫 <http://127.0.0.1:8000/>：

1. **加入 PDF**：在「監看資料夾」加入放 PDF 的資料夾，或在「新增 PDF」貼上單一檔案的路徑。
2. **轉換**：按「轉換」。大型書籍建議填頁碼（從 0 起算，例如 `0-40`）只轉需要的章節。PDF 的文字層損壞時（常見於舊版掃描書，連字變成 `"xed`、`#ow` 等亂碼）會自動改用 OCR，速度較慢。
3. **閱讀**：點文件標題開啟。第一次開啟會先生成摘要與術語表，接著從你正在看的位置開始翻譯。
4. **術語**：點藍色虛線的術語，可以加入生字本或修改譯名。
5. **生字**：選取任何英文單字，按浮動按鈕查詢並加入生字本；書庫右上角可進入「生字複習」或「匯出 Anki」。
6. **問 AI**：滑鼠移到段落、表格或圖片上，按「問 AI」→ 選擇要問什麼 →「複製提示詞」→ 貼到網頁版 AI。圖片先按「複製圖片」貼上，再貼提示詞。

服務只綁定本機（127.0.0.1），其他電腦無法連線。只有一張 GPU，所以轉檔與翻譯依序進行：轉檔優先，其次是正在閱讀的文件；關閉閱讀頁後會在背景繼續翻譯 30 分鐘。

### 直接問 Claude（選用）

設定環境變數 `ANTHROPIC_API_KEY` 並安裝 `anthropic` 套件後重新啟動 `server.py`，「問 AI」對話框會多出「直接問 Claude（API）」按鈕。每次查詢會把該段原文或圖片傳送到 Anthropic，並依用量計費。一般的逐段翻譯仍然只使用本機模型。

## 命令列

不開服務也可以一次處理整份文件：

```powershell
python pdftomd.py "D:/papers/xxx.pdf" --pages 0-40     # PDF → Markdown，PDF 可放在任何位置
python translate.py --input "xxx.md" --limit 20        # 先翻前 20 個區塊預覽
python translate.py --input "xxx.md"                   # 完整翻譯，輸出靜態 HTML
python translate.py --input "xxx.md" --render-only     # 不呼叫模型，只重新產生 HTML
```

其他參數：`pdftomd.py --force-ocr`／`--no-force-ocr` 手動指定是否 OCR；`translate.py --model` 指定模型、`--think` 啟用思考模式（很慢）。命令列與本機服務共用同一份翻譯快取，兩邊的進度可以互相接續。

## 運作方式

```
PDF ──Marker──▶ Markdown ──切區塊──▶ 標題／段落／清單／表格（翻譯）
                                     公式／圖片／程式碼（原樣保留）
                                          │
                     文件摘要 + 術語表（自動擷取 + glossary.json）
                                          │
            每段切成英文句子 → 本機模型以 JSON schema 回傳等長譯文陣列
                                          │
                       knowledge JSON（含快取）→ HTML 閱讀器
```

- **對齊在翻譯時就成立**：每段的英文句子編號後一次送給模型，並以 JSON schema 要求回傳同樣數量的譯文，因此中英句子一對一對應。模型做不到時改為整段翻譯，閱讀器中以虛框標示。
- **翻譯失控的偵測**：重複迴圈、原始 JSON、模型的說明文字等輸出會被偵測並重試，仍失敗就標示「翻譯失敗」，不會把錯誤輸出當成譯文。
- **快取**：以（提示詞版本、模型、句子、本段術語）為 key，每段翻完就存檔。中斷後重跑會從中斷處接續；修改某個術語只會重翻含該術語的段落。
- **術語表**有兩層：每份文件自動擷取的術語，以及專案根目錄的 `glossary.json`（使用者維護、所有文件共用、優先採用）。`glossary.json` 已附上一些常見的兩岸用語修正，例如雷射／激光、電漿／等離子體。

## 測試模型速度與品質

```powershell
python tools/bench_models.py --input "xxx.md" --models gemma4:latest qwen3.5:9b --paragraphs 5
```

從文件挑出幾個內文段落，用與正式流程相同的方式翻譯。終端機會列出每個模型的 GPU 載入比例、每段秒數、tok/s 與對齊成功率；逐句並排的對照報告存在 `output/<文件名稱>/bench/`。GPU 比例低於 100% 代表模型有一部分在 CPU 上執行，速度會明顯下降。開發時的實測紀錄見 [進度.md](進度.md)。

## 已知限制

- 本機 7–9B 模型偶爾會譯錯專業術語或改寫公式；遇到時在閱讀器中修正術語，或用「問 AI」檢查。
- 公式很多的段落較容易無法逐句對齊，會改為整段翻譯。
- 原版面檢視中，PDF 上不能選字查詢（右側譯文可以）。
- 掃描品質差或文字層損壞的 PDF 依賴 OCR，轉換較慢，公式辨識也可能出錯。
- 目前沒有自動化測試。

## 專案結構

| 路徑 | 用途 |
|---|---|
| `server.py` | 本機服務入口（書庫、閱讀器、API） |
| `translate.py` | 命令列翻譯整份文件，輸出靜態 HTML |
| `pdftomd.py` | 命令列 PDF → Markdown |
| `paperai/` | 翻譯流程、本機服務與網頁模板 |
| `glossary.json` | 使用者術語表（所有文件共用） |
| `tools/bench_models.py` | 模型速度與翻譯品質比較 |
| `output/` | 轉換結果、翻譯快取、閱讀器與書庫資料庫（不提交） |
| `experiments/` | 早期的分析流程與原型，不參與正式流程 |
| `docs/maintenance.md` | 程式維護索引 |
| `進度.md` | 開發進度、模型實測與後續計畫 |

`AGENTS.md`、`CLAUDE.md` 與 `.claude/` 是給 AI 程式助理（Claude Code 等）看的開發說明。

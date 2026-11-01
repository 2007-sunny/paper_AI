# PaperAI

以 PDF 為中心的英文論文與教科書閱讀器。打開就回到上次讀到的地方，滑鼠移到看不懂的段落，按一下 Ctrl，譯文就直接蓋在原文上；再按一次就消失。

## 功能

- **PDF 閱讀**：左側欄像 VS Code 一樣開啟檔案或資料夾，資料夾以樹狀顯示，可逐層展開。右側直接顯示原始 PDF，不會複製檔案，只記錄路徑。重新開啟時回到上次的檔案、頁碼與縮放。
- **歷史與刪除**：看過的文件都留在左下角齒輪的「歷史管理」。刪除分三步：確認首頁與標題、閱讀永久刪除說明、按下確認。只會刪除翻譯資料，不會刪除原始 PDF。
- **Ctrl 段落翻譯**：單獨按下並放開 Ctrl，切換滑鼠所在段落的譯文。Ctrl+C、Ctrl+滾輪不會觸發。
- **兩層翻譯**：
  - 粗翻：按 Ctrl 時才呼叫 Google Cloud Translation，約 0.7 秒；翻過的段落存在本機，不會重複計費。
  - 細翻：讀到某一頁時，MinerU 只解析這附近 3 頁，本機模型再細翻這一頁和下一頁，完成後自動換掉粗翻，公式以 MathJax 顯示。可在「翻譯設置」改成按 Ctrl 才翻或關閉。
- **翻譯設置**：滑鼠移到左下角齒輪，選「翻譯設置」。細翻模式有三種，可套用到所有文件或只套用到目前文件：
  - 自動：讀到哪裡，就解析並細翻到哪裡。
  - 按 Ctrl 才翻：先顯示粗翻，同時只細翻這一段。
  - 關閉：省電，不用 GPU，停止解析、細翻和單字的本機補充。已完成的細翻照常顯示；還沒解析的頁面擷取內容時只有 PDF 文字。
  - 「進階」可調整預先細翻頁數、本機模型、粗翻每月上限、Marker 閒置時間和解析逾時，也能還原預設值。
- **單字查詢**：在 PDF 或譯文上選字，按「查詢」先看到 Google 譯名，本機模型再依上下文補充。
- **擷取圖片**：游標移到圖上會自動框出整張圖，按「擷取圖片」就以高解析度複製到剪貼簿，按鈕平時淡化。點陣圖與向量圖都支援；整頁掃描的書無法自動認圖。
- **擷取內容**：選取一段範圍後按「擷取內容」，取得內文與 LaTeX 公式（包含夾在中間的獨立公式），複製後貼到任何 AI，不必截圖。安裝 `anthropic` 套件並設定環境變數 `ANTHROPIC_API_KEY` 後，也可以在閱讀器內直接問 Claude（選用）。
- **每月上限**：粗翻預設每月最多送出 45 萬字元，可在「翻譯設置」調整，最高 50 萬；用量顯示在左下角。達到上限後不再呼叫 Google，等待本機細翻。

## 需求

- Windows（目前只在 Windows 11 測試過），Python 3.10 以上
- [Ollama](https://ollama.com/download)，以及一個翻譯用模型
- 建議有 NVIDIA 顯示卡。開發環境是 RTX 4060 Laptop（8GB）
- 解析 PDF 使用 [MinerU](https://github.com/opendatalab/MinerU)，裝在獨立環境，模型約 2GB；沒有安裝時改用 [Marker](https://github.com/datalab-to/marker)。只在本機解析，不會把文件傳到 MinerU 的雲端服務
- Google Cloud Translation API 金鑰（粗翻與單字查詢使用）
- 公式（MathJax）與 PDF 顯示（PDF.js）由 CDN 載入，需要網路

## 安裝

```powershell
git clone https://github.com/2007-sunny/paper_AI.git
cd paper_AI
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-pdf.txt
python -m spacy download en_core_web_sm
ollama pull gemma4
python -m venv .venv-mineru
.venv-mineru\Scripts\python -m pip install -r requirements-mineru.txt
.venv-mineru\Scripts\mineru-kit models download --tier standard
```

在專案根目錄建立 `.env`（不會提交 Git）：

```
GOOGLE_TRANSLATE_API_KEY=你的金鑰
```

金鑰到 Google Cloud Console 啟用 **Cloud Translation API** 後建立，並把 API 限制設成只允許這個 API。本機模型可在閱讀器的「翻譯設置 → 進階」切換，清單來自 `ollama list`。

## 使用

雙擊 **`PaperAI.bat`**，或在終端機執行 `python server.py`，瀏覽器會開啟 <http://127.0.0.1:8000/>。

1. 按「開啟檔案」（可多選）或「開啟資料夾」。打開後立即可讀，讀到哪裡才解析到哪裡。按 × 關閉只會移到歷史，不會刪除資料。
2. 滑鼠移到段落上，按一下 Ctrl 看譯文，再按一次隱藏。淡黃框是粗翻，綠框是細翻。
3. 選取文字後，用浮動選單查詢單字或擷取內容。只選幾個字時會擷取整段；那幾頁還在解析時先顯示 PDF 文字，完成後自動換成 LaTeX。
4. 游標移到圖上，按「擷取圖片」複製，再貼到 AI。
5. Ctrl+滾輪或右上角按鈕可以縮放。
6. 要刪除翻譯資料時，把滑鼠移到左下角齒輪，選「歷史管理」，再按文件旁的「刪除」。
7. 要省電或調整細翻時，到同一個選單的「翻譯設置」，改了立即生效。

**外傳的資料**：按 Ctrl 翻譯的段落與查詢的單字會傳送到 Google；使用 API 問 Claude 時，該段內容會傳送到 Anthropic。細翻只在本機執行。

## 運作方式

```
PDF ─PyMuPDF─▶ 每頁文字與座標 ─按 Ctrl─▶ Google 粗翻 ─▶ 覆蓋在原段落
 │
 └─讀到該頁─▶ MinerU 解析附近 3 頁 ─▶ 切區塊與術語 ─▶ 本機細翻 ─▶ 依單字重疊換掉粗翻
```

只處理正在閱讀的文件。自動模式下，目前頁缺解析就先解析，再細翻，讀到一段的最後一頁時才處理下一段。解析結果存在 `output/<文件名稱>/chunks/`，重新開啟不必再轉。解析前會先把頁面轉成純圖片，避開損壞的文字層，暫存檔在解析服務停止時刪除。解析模型常駐，閒置 5 分鐘後釋放 VRAM；解析時會先卸載本機翻譯模型，之後再自動載入。粗翻不佔 GPU。分段轉檔之前產生的文件沿用原本的完整 Markdown。

本機細翻以英文句子為單位，用 JSON schema 要求回傳等長譯文，結果依（提示詞版本、模型、句子、本段術語）快取。術語在 `glossary.json`，其中的 `aliases` 也會套用到粗翻，把常見的中國用語改成台灣用語。

## 測試模型速度與品質

```powershell
python tools/bench_models.py --input "xxx.md" --models gemma4:latest qwen3.5:9b --paragraphs 5
```

`--input` 需要完整的 Markdown，也就是分段轉檔之前產生的文件；分段文件沒有這個檔案。GPU 比例低於 100% 代表模型有一部分在 CPU 上執行，速度會明顯下降。逐句對照報告存在 `output/<文件名稱>/bench/`，實測紀錄見 [進度.md](進度.md)。

## 已知限制

- 粗翻的公式來自 PDF 文字層，下標與上標會失真；正確公式要等細翻。
- 沒有文字層的掃描頁無法粗翻，要等解析後的細翻。
- 一段解析超過 4 分鐘會放棄，維持粗翻，重啟服務後再試。實測 MinerU 每段約 25 秒，第一次載入約 1 分鐘。
- 跳到別頁時要等目前這段處理完或逾時，粗翻不受影響。切到「關閉」會直接結束解析，那一段之後要重做。
- 擷取內容的獨立公式是 LaTeX；行內公式取決於 PDF，部分文件只會得到近似文字。圖說不會隨圖片一起複製，要另外擷取。
- 有框線的表格也會被認成圖，少數版面的圖片裁切會帶到一點圖說。
- 解析模型與本機翻譯模型同時載入時 VRAM 接近 8GB 上限，所以翻譯模型會在解析前後反覆載入，每次約 15 秒。
- 跨欄或跨頁的段落目前分開翻譯，粗翻可能從半句開始；細翻時兩邊可能顯示同一段譯文。
- 分段解析看不到整份文件，頁首頁尾與標題層級的辨識可能比整份轉檔差。
- 譯文太長放不下時會縮小字級，到 10px 後改為框內捲動。
- 解析模型與本機翻譯模型第一次載入約需 1 分鐘，這段時間只有粗翻與 Google 查詢結果。
- 目前沒有自動化測試。

## 專案結構

| 路徑 | 用途 |
|---|---|
| `PaperAI.bat` | 雙擊啟動本機服務（Windows） |
| `server.py` | 本機服務入口 |
| `paperai/` | 翻譯流程、本機服務與網頁模板 |
| `glossary.json` | 術語表（所有文件共用） |
| `tools/bench_models.py` | 模型速度與翻譯品質比較 |
| `output/` | 轉換結果、翻譯快取與資料庫（不提交） |
| `experiments/` | 早期的分析流程與原型，不參與正式流程 |
| `docs/maintenance.md` | 程式維護索引 |
| `進度.md` | 開發進度、模型實測與後續計畫 |

`AGENTS.md`、`CLAUDE.md` 與 `.claude/` 是給 AI 程式助理看的開發說明。

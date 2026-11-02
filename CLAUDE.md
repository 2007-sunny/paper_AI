# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

專案規範、執行指令與檔案分類以 AGENTS.md 為準（以下匯入），本檔只補充架構重點。目前進度與重構計畫見 進度.md，完成階段性工作後請更新該檔。函式層級的索引見 docs/maintenance.md。

@AGENTS.md

## 架構重點（跨檔案才看得出來的部分）

- **分段轉檔**：`POST /api/docs` 只記錄路徑。`DocSession._next_step` 依目前頁與下一頁決定工作，缺分段才呼叫解析引擎，每段 3 頁加 1 頁重疊（重疊頁的區塊丟棄）。分段區塊 id 是 `c<起始頁>b<n>`。`library.doc_converted`（有完整 `.md`）的舊文件走 `whole` 路徑，不分段。摘要與術語表用 PyMuPDF 全文，不必等 Marker。
- **文件名稱不一定等於檔名**：不同位置的同名 PDF 由 `library.doc_name` 加上路徑 hash；要用它取名，不要用 `Path.stem`。
- **關閉不等於刪除**：側邊欄的 × 只改 `state.files`；`DELETE /api/docs/<名稱>` 才會刪 `output/<名稱>/`，且必須先 `Worker.close_document`，正在轉檔時回 409。絕不刪除原始 PDF。
- **粗翻與細翻的切法不同**：粗翻用 PyMuPDF 的文字區塊（也是畫面上的覆蓋範圍），細翻用 Marker 的 Markdown 區塊，兩者沒有共同 id。`DocSession.fine_translation` 在相鄰頁內比對單字重疊，所以既有文件不用重新轉檔。
- **掃描頁的粗翻**：`rough.page_blocks` 在整頁沒有文字層時改用 `_ocr_blocks`（RapidOCR，CPU，不佔 VRAM），`_group_lines` 先用 RapidLayout 的版面區塊（`_layout_regions`，CPU 每頁約 0.2 秒）把文字行分到標題、內文、公式等區塊，`LAYOUT_SKIP` 類別（公式、圖、頁首頁尾）不翻譯；區塊內再依行距、縮排與上一行長度用 `_split_paragraphs` 分段，短且不在左邊界的行（公式碎片）自成一段。快取格式是 `{"lines", "regions"}`，舊的 list 格式讀到時補做版面分析並改寫。區塊 id 是 `<頁>_ocr_<n>`，結果存在 `output/<名稱>/ocr/<頁>.json`。`page_blocks` 另外回傳 `ocr_lines`，前端 `renderOcrLayer` 用它建透明文字層，掃描頁才能選取、查詢與擷取。前端 `ensureOcrLayer` 只在捲動停下後（`settledPage`）替目前頁與前後各一頁取 `/pages/<頁>`，按 Ctrl 前通常已完成。不要改回「頁面一渲染就取」：快速捲動會讓上百頁排隊 OCR（引擎以 `_ocr_lock` 序列化，每頁約 3 秒），佔滿 FastAPI 的工作執行緒，按 Ctrl 的翻譯請求會卡好幾分鐘。只有文字層損壞（不是沒有）的頁面不會走 OCR。
- **引用跳轉（給引用 MCP）**：`/?doc=<library.doc_name 名稱>&page=<1 起算>&bbox=x0,y0,x1,y1` 由 app.js 啟動時的 `readJump` 讀取，讀完用 `history.replaceState` 清掉。`jumpTo` 開文件並捲到該頁，`markCitation` 用 PDF.js 第 1 倍 viewport 的寬高把 bbox（point，相對 PyMuPDF `page.rect`）換成比例畫在 overlay。跳轉期間 `holdPosition` 讓 `reportPosition` 只回報 `/focus`、不寫回記住的位置，使用者滾輪、點擊或按鍵後才恢復。參數只用 `textContent` 顯示，不要放進 `innerHTML`。`?pdf=<完整路徑>`（同時有 `doc` 時以 `doc` 為準）先呼叫 `GET /api/check-pdf`：只檢查路徑（不含 `..`、`resolve(strict=True)` 後是 `.pdf`、位於 `state.folders` 內），不讀 PDF 內容；已在書庫直接跳，否則 `jumpToPdf` 顯示確認框，按「加入並開啟」才 `POST /api/docs`。
- **粗翻只在按 Ctrl 時計費**：`rough.translate` 先查 `rough_cache`，未命中才用 `reserve_chars` 記帳後呼叫 Google。不要新增背景批次粗翻。
- **細翻模式（設定存在 library.db 的 `translation`，每一步重新讀取）**：`auto` 跟著閱讀位置，`/focus` 回報目前頁，`set_focus` 把 `focus` 換成這一頁和後面 `lookahead` 頁，離開後未開始的區塊不再翻。`ctrl` 只處理 `requests`：`/translate` 未命中細翻時 `request_fine` 排入段落，`_request_step` 先補分段再用 `_match_block` 找區塊；擷取內容缺分段時排入文字為空的請求。`off` 不排任何 GPU 工作，`Worker.settings_changed` 立刻結束 Marker，手上的區塊完成後卸載模型，前端也不呼叫 `/api/explain`。`library.fine_mode` 先看 `doc_modes` 的單份文件設定，再看全域的 `fine_mode`。
- **解析引擎**：`convert.make_parser` 優先用 `MineruService`（`.venv-mineru`），沒有安裝時用 `MarkerService`。MinerU 一律先轉成純圖片 PDF（`_image_pdf`），因為它會直接沿用壞掉的文字層（θ 變成 `\$`）；輸出經 `clean_mineru` 去掉頁碼、書眉與圖片引用。`_start` 每次都把雲端網址指向 127.0.0.1:9、關閉遙測並確認是本機模式，確認失敗就停止；不要加上 `--remote`。服務會把收到的 PDF 暫存在工作目錄，所以固定在 `config.MINERU_WORK` 執行，`_stop` 時刪除。Marker 把第 126–129 頁放在同一批會卡住超過 7 分鐘，分開跑不到 2 分鐘，所以不要加大它的分段。
- **排程（session.Worker）**：單一執行緒，只處理有 SSE 連線的文件。解析前 `_convert_chunk` 會卸載本機模型：gemma4 佔著 VRAM 時 MinerU 從 26 秒變成約 100 秒；反過來 MinerU 閒置時 gemma4 可以重新載入並維持 100% GPU。切頁要等目前這段；`chunk_timeout_minutes` 到了會結束，該段記入 `skipped`。
- **對齊在翻譯時建立，不是事後推算**：`translate_block` 先用 spaCy 切英文句子，再以 JSON schema（minItems/maxItems = 句數）要求等長陣列。句數不符重試一次，仍失敗就整段翻譯並設 `aligned=False`。
- **LaTeX 與 JSON 跳脫的衝突**：模型在 JSON 字串裡寫 `\bar`、`\text`、`\frac` 時，`\b \f \t` 會被解析成控制字元，MathJax 多半不報錯（`\text{He}` 會顯示成「extHe」）。`_escape_latex_in_json` 在解析前補反斜線，`_repair_control_chars` 修復舊快取。檢查輸出時要搜尋控制字元，不能只看 `mjx-merror`。
- **細翻快取 key**：（`PROMPT_VERSION`、模型、英文句子、本段術語的「英文→譯名」對）。修改提示詞必須遞增 `PROMPT_VERSION`。
- **頁碼是 PDF 的 0 起算索引**：與 Marker 的 page_id、圖片檔名一致；頁碼範圍文件（`_p374-423`）也用整本 PDF 的絕對頁碼。`pages.assign_pages` 必須在複製 `originals` 之前執行。

## 驗證方式

沒有測試套件。改動後的驗證順序：

1. 語法檢查：`python -m py_compile server.py paperai/*.py tools/bench_models.py`
2. 本機服務：用 `.claude/launch.json` 的 `paperai-server` 啟動（port 8000）；使用者自己的服務若正在 8000 轉檔，改用 `paperai-test`（port 8001，共用同一個 output/ 與 library.db）。注意 MinerU 服務是全機共用的：測試服務啟動時 `parser.reset` 會把它停掉，使用者的服務下次解析失敗時會自動重新啟動（`MineruService.convert` 的重試），但那一段會多等十幾秒。面板隱藏時視窗大小是 0×0，需要先用 resize_window 設定大小，頁面渲染也比較慢。
3. Ctrl 可用 JS 派送 `mousemove` 與 Control 的 keydown／keyup 模擬；粗翻會消耗真實額度，測試時只翻少量段落。
4. 修改提示詞或換模型：`python tools/bench_models.py --input "1804.03318v2.md" --models <模型...>`，看對齊率與 `output/<名稱>/bench/` 的逐句對照報告。

硬體是 RTX 4060 Laptop 8GB。模型要 100% 載入 GPU 才夠快，bench 輸出的 GPU 比例可以判斷。

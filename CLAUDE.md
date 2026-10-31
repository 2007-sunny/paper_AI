# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

專案規範、執行指令與檔案分類以 AGENTS.md 為準（以下匯入），本檔只補充架構重點。目前進度與重構計畫見 進度.md，完成階段性工作後請更新該檔。函式層級的索引見 docs/maintenance.md。

@AGENTS.md

## 架構重點（跨檔案才看得出來的部分）

- **分段轉檔**：`POST /api/docs` 只記錄路徑。`DocSession._next_step` 依目前頁與下一頁決定工作，缺分段才呼叫常駐的 Marker，每段 3 頁加 1 頁重疊（重疊頁的區塊丟棄）。分段區塊 id 是 `c<起始頁>b<n>`。`library.doc_converted`（有完整 `.md`）的舊文件走 `whole` 路徑，不分段。摘要與術語表用 PyMuPDF 全文，不必等 Marker。
- **文件名稱不一定等於檔名**：不同位置的同名 PDF 由 `library.doc_name` 加上路徑 hash；要用它取名，不要用 `Path.stem`。
- **關閉不等於刪除**：側邊欄的 × 只改 `state.files`；`DELETE /api/docs/<名稱>` 才會刪 `output/<名稱>/`，且必須先 `Worker.close_document`，正在轉檔時回 409。絕不刪除原始 PDF。
- **粗翻與細翻的切法不同**：粗翻用 PyMuPDF 的文字區塊（也是畫面上的覆蓋範圍），細翻用 Marker 的 Markdown 區塊，兩者沒有共同 id。`DocSession.fine_translation` 在相鄰頁內比對單字重疊，所以既有文件不用重新轉檔。
- **粗翻只在按 Ctrl 時計費**：`rough.translate` 先查 `rough_cache`，未命中才用 `reserve_chars` 記帳後呼叫 Google。不要新增背景批次粗翻。
- **細翻只跟著閱讀位置**：`/focus` 回報目前頁，`set_focus` 把 `focus` 換成這一頁和下一頁。離開後未開始的區塊不再翻。
- **排程（session.Worker）**：單一執行緒，只處理有 SSE 連線的文件。Marker 與 gemma4 可同時留在 8GB VRAM（實測 7.9GB，gemma4 維持 100% GPU），所以不互相卸載。Marker 無法中途取消，切頁要等目前這段；`CHUNK_TIMEOUT_SECONDS` 到了會結束程序，該段記入 `skipped`。
- **對齊在翻譯時建立，不是事後推算**：`translate_block` 先用 spaCy 切英文句子，再以 JSON schema（minItems/maxItems = 句數）要求等長陣列。句數不符重試一次，仍失敗就整段翻譯並設 `aligned=False`。
- **LaTeX 與 JSON 跳脫的衝突**：模型在 JSON 字串裡寫 `\bar`、`\text`、`\frac` 時，`\b \f \t` 會被解析成控制字元，MathJax 多半不報錯（`\text{He}` 會顯示成「extHe」）。`_escape_latex_in_json` 在解析前補反斜線，`_repair_control_chars` 修復舊快取。檢查輸出時要搜尋控制字元，不能只看 `mjx-merror`。
- **細翻快取 key**：（`PROMPT_VERSION`、模型、英文句子、本段術語的「英文→譯名」對）。修改提示詞必須遞增 `PROMPT_VERSION`。
- **頁碼是 PDF 的 0 起算索引**：與 Marker 的 page_id、圖片檔名一致；頁碼範圍文件（`_p374-423`）也用整本 PDF 的絕對頁碼。`pages.assign_pages` 必須在複製 `originals` 之前執行。

## 驗證方式

沒有測試套件。改動後的驗證順序：

1. 語法檢查：`python -m py_compile server.py paperai/*.py tools/bench_models.py`
2. 本機服務：用 `.claude/launch.json` 的 `paperai-server` 啟動（port 8000）；使用者自己的服務若正在 8000 轉檔，改用 `paperai-test`（port 8001，共用同一個 output/ 與 library.db）。面板隱藏時視窗大小是 0×0，需要先用 resize_window 設定大小，頁面渲染也比較慢。
3. Ctrl 可用 JS 派送 `mousemove` 與 Control 的 keydown／keyup 模擬；粗翻會消耗真實額度，測試時只翻少量段落。
4. 修改提示詞或換模型：`python tools/bench_models.py --input "1804.03318v2.md" --models <模型...>`，看對齊率與 `output/<名稱>/bench/` 的逐句對照報告。

硬體是 RTX 4060 Laptop 8GB。模型要 100% 載入 GPU 才夠快，bench 輸出的 GPU 比例可以判斷。

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

專案規範、執行指令與檔案分類以 AGENTS.md 為準（以下匯入），本檔只補充架構重點。目前進度與重構計畫見 進度.md，完成階段性工作後請更新該檔。函式層級的索引見 docs/maintenance.md。

@AGENTS.md

## 架構重點（跨檔案才看得出來的部分）

- **兩個入口共用同一份狀態**：CLI（translate.py）和本機服務（session.py）都讀寫 `_translation_cache_v2.json` 與 `_translation_knowledge.json`。服務開啟文件時用 `pipeline.restore_translations`，依區塊 `src`（原文 hash）套回既有譯文，因此兩邊的進度可以互相接續。
- **對齊在翻譯時建立，不是事後推算**：`translate_block` 先用 spaCy 把區塊切成英文句子，再整批送給模型，並以 JSON schema（minItems/maxItems = 句數）要求回傳等長陣列。句數不符會重試一次，仍失敗就整段翻譯並設 `aligned=False`。因此英文斷句的品質直接決定對齊的品質。
- **LaTeX 與 JSON 跳脫的衝突**：模型在 JSON 字串裡寫 `\bar`、`\text`、`\frac` 時，`\b \f \t` 會被 JSON 解析成控制字元，而且 MathJax 多半不會報錯（`\text{He}` 只會靜靜顯示成「extHe」）。`_escape_latex_in_json` 在解析前補反斜線，`_repair_control_chars` 修復舊快取。檢查輸出時要搜尋控制字元，不能只看 `mjx-merror`。
- **快取 key 的組成**：（`PROMPT_VERSION`、模型、英文句子、本段術語的「英文→譯名」對）。修改 glossary.json 某個譯名後，只有含該術語的區塊會重新翻譯；修改提示詞必須遞增 `PROMPT_VERSION`。
- **術語比對只有一個實作**：`glossary.term_pattern`。翻譯時挑出本段術語、aliases 改正、閱讀器標示、服務端判斷哪些區塊受術語修改影響，全部用它。
- **服務的排程（session.Worker）**：單一執行緒處理所有 GPU 工作。優先順序依序是轉檔、有人正在看的文件、30 分鐘內看過的文件。閱讀頁用 IntersectionObserver 回報可見區塊（`/focus`），`DocSession._pick` 先翻這些，再從該位置往後。結果以 SSE 推送 `block`／`progress`／`status`／`meta`／`stale` 事件；reader.js 的 `handlers` 對應處理。
- **未翻譯區塊保留 `items`，已翻譯的有 `units`**：`render_block` 依此決定輸出等待中或對照版本。服務在 `DocSession.originals` 保留原始區塊，術語修改後才能重翻。
- **閱讀器 HTML 同一份模板兩種模式**：`render_reader_html(server_mode=...)`。靜態檔的生字本存 localStorage、選字查詢打 `http://127.0.0.1:8000`；服務模式的生字本存 SQLite，並可修正術語。`.server-only`／`.static-only` 元素由 JS 切換顯示。

## 驗證方式

沒有測試套件。改動後的驗證順序：

1. 語法檢查：`python -m py_compile server.py translate.py pdftomd.py paperai/*.py tools/bench_models.py`
2. CLI 端對端：`python translate.py --input "1804.03318v2.md" --limit 25`（輸出 `_preview_*`，不覆蓋完整版）。1804.03318v2 是篇短論文，Markdown 已經轉好；快取命中時幾秒內完成。
3. 本機服務：用 `.claude/launch.json` 的 `paperai-server` 啟動（port 8000），在瀏覽器面板開 `/` 與 `/read/<名稱>/`。面板隱藏時視窗大小是 0×0，IntersectionObserver 不會觸發、區塊高度也會異常；需要先用 resize_window 設定大小，或直接 fetch `/api/docs/<名稱>/focus` 模擬捲動。
4. 只改模板或渲染：`python translate.py --input "xxx.md" --render-only`；服務模式則重新整理頁面即可。
5. 修改提示詞或換模型：`python tools/bench_models.py --input "1804.03318v2.md" --models <模型...>`，看對齊率與 `output/<名稱>/bench/` 的逐句對照報告。

硬體是 RTX 4060 Laptop 8GB。模型要 100% 載入 GPU 才夠快，bench 輸出的 GPU 比例可以判斷。Marker 轉檔前服務會先卸載 Ollama 模型以釋放 VRAM。

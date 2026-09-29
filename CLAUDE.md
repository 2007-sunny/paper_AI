# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

專案規範、執行指令與檔案分類以 AGENTS.md 為準（以下匯入），本檔只補充架構重點。目前進度與重構計畫見 進度.md，完成階段性工作後請更新該檔。

@AGENTS.md

## 架構重點（跨檔案才看得出來的部分）

- **資料流與中間檔**：所有產物都在 `output/<BASE_NAME>/`，檔名以 BASE_NAME 為前綴。`_paper_map.json` 與 `_glossary.json` 存在即沿用（刪除才重新生成）；`_translation_cache.json` 以「原始段落的 md5」為 key，每段寫入一次。因此修改翻譯 prompt 或換模型後，要刪除快取才會重新翻譯；只改 HTML 渲染則會全部命中快取，重跑很快。
- **`_translation_knowledge.json` 是閱讀器的唯一資料來源**：`render_html_reader` 只讀這份 JSON，沒有其他輸入。未來拆分前後端時，以它作為前後端之間的資料介面。
- **句子對齊在翻譯之後**：英文用 spaCy 切句，中文用標點切句，再依句數硬湊（不足補空字串，過多併入最後一句）。對齊錯誤多半源自這一步，而不是 `align_sentences`；該函式目前 main() 沒有使用。
- **渲染是逐句進行的**：每句各自經過 `render_inline_markdown`，再放進 `<span>`／`<p>`。表格、清單、區塊公式、`###` 標題因此會被拆散，這是 HTML 跑版的主因。同一份內容會輸出成 4 種檢視的 DOM。
- **設定是模組層級的全域變數**：`configure()` 會重新計算 BASE_NAME／OUTPUT_DIR 等路徑，其他腳本 `import translate` 之後要先呼叫它。`call_ai` 讀取執行當下的 MODEL_NAME／THINK。
- **即時查詢是另一條路徑**：閱讀器的 JS 直接 fetch `http://127.0.0.1:8000/api/explain`（server.py），並把前端選到的模型名稱傳過去。生字本與 AI 查詢紀錄只存在瀏覽器的 localStorage。

## 驗證方式

沒有測試套件。改動後的驗證順序：

1. 語法檢查：`python -m py_compile translate.py server.py tools/bench_models.py`
2. 端對端預覽：`python translate.py --input "1804.03318v2.md" --limit 5`，產出 `_preview_reader.html`，不會覆蓋完整版。1804.03318v2 是篇短論文，Markdown 已經轉好，適合當測試文件。
3. 修改 prompt 或換模型時：`python tools/bench_models.py --input "1804.03318v2.md" --models <模型...>`，比較 `output/<名稱>/bench/` 裡的並排報告。

硬體是 RTX 4060 Laptop 8GB。模型要 100% 載入 GPU 才夠快，bench 輸出的 GPU 比例可以判斷。

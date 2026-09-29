# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

專案規範、執行指令與檔案分類以 AGENTS.md 為準（以下匯入），本檔只補充架構重點。目前進度與重構計畫見 進度.md，完成階段性工作後請更新該檔。函式層級的索引見 docs/maintenance.md。

@AGENTS.md

## 架構重點（跨檔案才看得出來的部分）

- **對齊在翻譯時建立，不是事後推算**：`translate_block` 先用 spaCy 把區塊切成英文句子，再整批送給模型，並以 JSON schema（minItems/maxItems = 句數）要求回傳等長陣列。句數不符會重試一次，仍失敗就整段翻譯並設 `aligned=False`（閱讀器以虛框標示）。因此英文斷句的品質直接決定對齊的品質。
- **區塊類型決定翻不翻**：只有 `TRANSLATABLE_TYPES`（heading/paragraph/list）會翻譯；公式、表格、圖片、程式碼原樣輸出，並在對照檢視中佔滿整列。Marker 常把公式編號或「and」接在 `$$` 後面，`render_block` 會把這段文字當作編號顯示。
- **快取 key 的組成**：（`PROMPT_VERSION`、模型、英文句子、本段術語的「英文→譯名」對）。修改 glossary.json 某個譯名後，只有含該術語的區塊會重新翻譯；修改提示詞必須遞增 `PROMPT_VERSION`，否則會一直命中舊快取。
- **術語比對只有一個實作**：`glossary.term_pattern`（不分大小寫、允許複數、連字號等同空白）。翻譯時挑出本段術語、aliases 改正、閱讀器標示都用它，修改比對規則會同時影響三處，也會改變快取 key。
- **knowledge JSON 是渲染的唯一輸入**：`render_reader` 只讀 `_translation_knowledge.json`，因此 `--render-only` 可在不呼叫模型的情況下重新產生 HTML。未來拆成本機服務時，以它作為前後端之間的介面（格式見 docs/maintenance.md）。
- **閱讀器是單一 HTML 檔**：CSS/JS 在生成時內嵌，雙擊即可開啟。選字查詢呼叫 `http://127.0.0.1:8000/api/explain`（server.py）；生字本、查詢紀錄、檢視模式與主題存在瀏覽器的 localStorage。

## 驗證方式

沒有測試套件。改動後的驗證順序：

1. 語法檢查：`python -m py_compile translate.py server.py pdftomd.py paperai/*.py tools/bench_models.py`
2. 端對端預覽：`python translate.py --input "1804.03318v2.md" --limit 25`，產出 `_preview_reader.html`，不會覆蓋完整版。1804.03318v2 是篇短論文，Markdown 已經轉好，適合當測試文件；快取命中時幾秒內完成。
3. 只改模板或渲染：`--render-only` 重新產生 HTML 後，用瀏覽器開啟檢查。瀏覽器面板無法開啟 file://，可用 `.claude/launch.json` 的 `output-static`（`python -m http.server` 提供 output/）。檢查項目：`mjx-merror` 數量、中英 `.sentence` 數量相同、頁面沒有橫向溢出。
4. 修改提示詞或換模型：`python tools/bench_models.py --input "1804.03318v2.md" --models <模型...>`，看對齊率與 `output/<名稱>/bench/` 的逐句對照報告。

硬體是 RTX 4060 Laptop 8GB。模型要 100% 載入 GPU 才夠快，bench 輸出的 GPU 比例可以判斷。

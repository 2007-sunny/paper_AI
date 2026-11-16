# PaperAI 維護索引

入口：server.py（本機服務）。流程程式在 paperai/，網頁模板在 paperai/templates/（app.html、app.css、app.js）。

## 本機服務

| 功能 | 位置 |
|---|---|
| 路由與 Origin 檢查 | `app.py`（`reject_cross_site_writes`） |
| 文件清單、閱讀狀態、粗翻快取與用量（SQLite） | `library.py`，資料庫 `output/library.db` |
| 選擇檔案對話框（tkinter 子程序，路由 `/api/pick`） | `picker.pick` |
| 開啟 PDF | 路由 `POST /api/docs` → `Worker.submit_convert` |
| 側邊欄工作區（開著的檔案、資料夾） | 路由 `/api/workspace`、`/api/folder`；`library.save_workspace`、`folder_pdfs` |
| 文件命名（同名 PDF 分開、搬走的檔案接回舊資料） | `library.doc_name` |
| 刪除翻譯資料 | 路由 `DELETE /api/docs/<名稱>` → `Worker.close_document`、`library.delete_document`（拒絕刪除放在翻譯資料夾內的 PDF；粗翻快取依內容共用，不隨文件刪除） |
| 分段轉檔（文字層損壞時自動 OCR） | `convert.MarkerService`、`text_layer_is_broken`、`CHUNK_PAGES`、`OVERLAP_PAGES`；常駐程序 `marker_server.py`；存在 `output/<名稱>/chunks/<起始頁>.md` |
| 解析引擎 | `convert.make_parser`、`MineruService`（`_start` 鎖定本機模式）、`MarkerService`、`_image_pdf`、`clean_mineru` |
| 分段載入與跳過 | `DocSession._convert_chunk`、`_load_chunk`、`skipped`；設定 `chunk_timeout_minutes`、`marker_idle_minutes` |
| 每頁文字與座標 | `rough.page_blocks`（路由 `/api/docs/<名稱>/pages/<頁>`），過濾規則 `_is_translatable` |
| 掃描頁 OCR | `rough.page_blocks`（`ocr_dir`、回傳 `ocr_lines`）、`_cached_ocr`、`_ocr_lines`、`_layout_regions`、`_region_of`、`_group_lines`、`_split_paragraphs`；`LAYOUT_PRIORITY`、`LAYOUT_SKIP`、`OCR_DPI`、`OCR_LINE_GAP`、`OCR_INDENT`、`OCR_SHORT_LINE`、`OCR_FRAGMENT`；`app._ocr_dir`；前端 `ensureOcrLayer`（`settledPage`）、`renderOcrLayer`、`pageData`（失敗不快取） |
| 粗翻與每月上限 | `rough.translate`、`library.reserve_chars`、設定 `monthly_chars` |
| 粗翻用語修正 | `rough._apply_aliases`，讀 `glossary.json` 的 `aliases` |
| 段落翻譯路由 | `POST /api/docs/<名稱>/translate`：先查細翻，再粗翻 |
| 擷取內容 | `DocSession.extract`（`context` 定位短選取、`whole`）、`_extract_blocks`、`_trim_to_selection`、`_md_words`；路由 `/api/docs/<名稱>/extract`；前端 `loadExtract`、`ask-whole` 按鈕（`askWhole` 預設 true＝整段） |
| 粗細翻對應 | `DocSession.fine_translation`（`retried`）、`_match_blocks`（單一區塊：`MATCH_PARAGRAPH`、`MATCH_BLOCK`；連續區塊：`MATCH_RUN_BLOCK`、`RUN_TRIM`、`EDGE_ANCHOR`）、`_retry`；沒翻成中文：`translator.untranslated`；切換：前端 `switchSource`、`fillOverlay(el, page, source)`、`/translate` 的 `source` |
| 翻譯設定 | `library.translation_settings`、`save_translation_settings`、`save_doc_mode`、`fine_mode`；路由 `/api/settings`、`/api/models`、`/api/docs/<名稱>/mode`；預設值 `config.ADVANCED_DEFAULTS` |
| 細翻模式排程 | `DocSession._next_step`（`_focus_step`、`_request_step`、`request_fine`）；`Worker.settings_changed`、`parser.cancel`、`llm.unload_model` |
| 背景排程優先順序 | `session.Worker._next` |
| 細翻範圍（目前頁與預先頁數） | 路由 `/focus` → `DocSession.set_focus`、`_focus_step` |
| 區塊對應 PDF 頁碼 | `pages.assign_pages` |
| 表格翻譯 | `translator.translate_table` |
| 單字查詢 | 路由 `/api/lookup`（Google）、`/api/explain`（本機） |
| 擷取內容 | 路由 `/extract` → `DocSession.extract`（`EDGE_WORDS`）、`_source_text`，字型修正 `rough.fix_font`；前端 `loadExtract` |
| 自動認圖與擷取 | `rough._figure_rects`、`figure_png`（`FIGURE_*` 參數）；路由 `/pages/<頁>/figures/<序號>.png`；前端 `updateFigureButton` |
| 直接問 Claude | `claude_assist.ask`（路由 `/ask`） |

SSE 事件（`/api/docs/<名稱>/events`）：`fine`（page）、`progress`（done、total、pending）、`status`（text）。

## 前端（templates/app.js）

| 功能 | 位置 |
|---|---|
| 側邊欄與輪詢 | `renderSidebar`（`sideView`：files／history）、`refresh` |
| 引用跳轉（網址參數） | `readJump`、`jumpTo`、`jumpToPdf`、`markCitation`、`parseBbox`、`holdPosition`；路由 `/api/check-pdf`；`openDoc(name, startPage)`；CSS `.citation-box` |
| 縮放與渲染 | 前端 `fitPages`（批次寫入、保持 1/3 判斷線位置）、`renderPage`（`renderGen`／`renderTask` 取消舊渲染）、`setZoom`（按鈕，0.1 一階）、滾輪／觸控板縮放的 wheel 處理與 `commitPinch`（手勢中 CSS scale，結束才重排）、`fitOverlay`（二分搜尋放得進框的最大字級，上限為頁高千分比 `overlay_font_max`、下限 `overlay_font_min` px，不用區塊行數）、`lazy` 觀察器 |
| 重新製作 OCR（覆蓋原始 PDF） | `rough.rebuild_text_layer`、`_is_scan`、`_reading_order`、`_line_pieces`、`_snap_words`（`SNAP_GAP`、`SNAP_RANGE`）、`_regroup_words`、`_rebuilt_pages`（`REBUILT_MARKER`）；前端 `renderOcrLayer`（逐字 pieces）、`PDF_OPTIONS`（cMapUrl）（`SCAN_IMAGE_COVER`、`REBUILD_MAX_PAGES`）；路由 `POST /api/docs/<名稱>/rebuild-ocr`；`/info` 的 `version`；前端 `rebuild-ocr` 按鈕（`ocrArmed`、`ocrRunning`） |
| 書籤與目錄 | `library.list_bookmarks`、`add_bookmark`、`rename_bookmark`、`delete_bookmark`；路由 `/api/docs/<名稱>/bookmarks`、`/api/bookmarks/<id>`；前端 `loadBookmarks`、`loadOutline`、`bookmarkRows`、`outlineRows`、`addBookmark`、`scrollToSpot`；自訂目錄 `library.get_outline`、`save_outline`、`reset_outline`，路由 `/api/docs/<名稱>/outline`，前端 `linkOutline`、`saveOutline`、`editOutline`、`insertOutline`、`outlineHead` |
| 標記與註解 | `library.list_annotations`、`add_annotation`、`update_annotation`、`delete_annotation`；路由 `/api/docs/<名稱>/annotations`、`/api/annotations/<id>`；前端 `loadAnnotations`、`renderAnnotations`、`selectionRects`、`createAnnotations`、框選的 `setDrawing`／`textInBox`、`showAnnotationMenu`、`annotationRows` |
| 設定選單與設置 | `showFlyout`、`renderSettings`、`saveSettings`、`loadModels`、`currentMode` |
| 資料夾樹（次級資料夾預設收合） | `folderTree`、`treeRows` |
| 刪除流程 | `startDelete`、`delete-next`、`delete-confirm` |
| PDF 顯示與文字層 | `openDoc`、`renderPage`、`fitPages`（PDF.js 3.11 由 cdnjs 載入） |
| 閱讀位置回報 | `reportPosition` |
| Ctrl 判定 | `ctrlAlone` 與其 keydown／keyup 監聽 |
| 譯文覆蓋 | `toggleParagraph`、`fillOverlay`、`fitOverlay`（`MIN_FONT`） |
| 選字選單、查詢、擷取內容 | `selection-menu` 相關監聽 |

## 細翻流程與對應模組

| 階段 | 模組／函式 | 產出 |
|---|---|---|
| 文件摘要 | `glossary.get_paper_map` | `{BASE_NAME}_paper_map.json`（存在即沿用） |
| 術語擷取 | `glossary.generate_glossary` | `{BASE_NAME}_glossary.json`（存在即沿用） |
| 術語合併 | `glossary.build_glossary`、`load_user_glossary` | 根目錄 glossary.json 覆蓋同名自動術語 |
| 切區塊 | `blocks.parse_blocks` | heading/paragraph/list 帶 `items`；table/math/image/code/html 帶 `source` |
| 斷句 | `sentences.split_en_sentences` | spaCy + placeholder 保護 |
| 翻譯 | `translator.translate_block` → `translate_sentences` | 區塊的 `units`（中英句對）、`aligned`、`terms` |
| 快取 | `translator.TranslationCache` | `{BASE_NAME}_translation_cache_v2.json` |

## 常見修改位置

- 換模型、thinking、context 長度、粗翻上限：`paperai/config.py`。
- 翻譯提示詞：`translator.build_translation_prompt`。修改後遞增 `PROMPT_VERSION`，舊快取才會失效。
- 對齊失敗處理：`translator.translate_sentences`。
- 失控輸出判斷：`translator.translation_problem`；舊譯文沿用前以 `cached_is_valid`、`units_are_valid` 檢查。
- 術語比對規則：`glossary.term_pattern`。
- 錯譯自動改正：細翻用 `glossary.apply_aliases`，粗翻用 `rough._apply_aliases`。
- 公式渲染保護、`%` 跳脫：`render._protect_math`、`_restore_math`。
- 字型造成的文字層錯字：`rough._FONT_FIXES`、`_FF_RE`。

## knowledge JSON（version 2）

```json
{
  "version": 2,
  "metadata": {"title": "...", "model": "...", "preview": false, "stats": {...}},
  "paper_map": {...},
  "glossary": [{"term": "...", "translation": "...", "category": "...", "aliases": [], "source": "auto|user"}],
  "blocks": [
    {"id": "b3", "type": "paragraph", "aligned": true, "terms": ["..."],
     "units": [[{"id": "s1", "en": "...", "zh": "..."}]]},
    {"id": "b4", "type": "math", "source": "$$...$$ (1)"}
  ]
}
```

`units` 是二維陣列：段落與標題只有一個 unit，清單每個項目是一個 unit。已翻譯區塊另有 `src`（原文 hash），重新開啟文件時用來判斷譯文是否仍對應原文（`pipeline.restore_translations`）。未翻譯的區塊保留 `items`。

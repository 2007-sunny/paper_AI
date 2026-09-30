# PaperAI 維護索引

入口：server.py（本機服務）、translate.py（命令列翻譯）、pdftomd.py（命令列轉檔）。流程程式在 paperai/，網頁模板在 paperai/templates/。

## 本機服務

| 功能 | 位置 |
|---|---|
| 路由與 Origin 檢查 | `app.py`（`reject_cross_site_writes`） |
| 書庫清單、監看資料夾、生字本（SQLite） | `library.py`，資料庫 `output/library.db` |
| PDF 轉檔（含頁碼範圍命名、文字層損壞時自動 OCR） | `convert.convert_pdf`、`text_layer_is_broken` |
| 背景排程優先順序 | `session.Worker._next` |
| 閱讀位置優先 | `DocSession.set_focus`、`_pick` |
| 翻譯一個區塊並推送 | `DocSession._translate` |
| 術語修改後重翻 | `glossary.upsert_user_term` → `Worker.glossary_changed` → `DocSession.on_glossary_changed` |
| 背景翻譯時間、存檔頻率 | `session.BACKGROUND_WINDOW`、`SAVE_INTERVAL` |
| 書庫頁 | `templates/library.html`（內含 JS） |
| 閱讀頁即時更新 | `templates/reader.js` 的 `if (serverMode)` 區段 |

SSE 事件（`/api/docs/<名稱>/events`）：`block`（id、html）、`progress`（done、total、pending）、`status`（text）、`meta`（標題、摘要、術語表列）、`stale`（ids）。

## 流程與對應模組

| 階段 | 模組／函式 | 產出 |
|---|---|---|
| 文件摘要 | `glossary.get_paper_map` | `{BASE_NAME}_paper_map.json`（存在即沿用） |
| 術語擷取 | `glossary.generate_glossary` | `{BASE_NAME}_glossary.json`（存在即沿用） |
| 術語合併 | `glossary.build_glossary`、`load_user_glossary` | 根目錄 glossary.json 覆蓋同名自動術語 |
| 切區塊 | `blocks.parse_blocks` | heading/paragraph/list 帶 `items`；table/math/image/code/html 帶 `source` |
| 斷句 | `sentences.split_en_sentences` | spaCy + placeholder 保護；`_merge_after_abbreviations` 修正縮寫後斷句 |
| 翻譯 | `translator.translate_block` → `translate_sentences` | 區塊的 `units`（中英句對）、`aligned`、`terms` |
| 快取 | `translator.TranslationCache` | `{BASE_NAME}_translation_cache_v2.json` |
| 渲染 | `render.render_reader` | `{BASE_NAME}_reader.html`；`--limit` 時加 `_preview` |

## 常見修改位置

- 換模型、thinking、context 長度：`paperai/config.py`（或 `--model`、`--think`）。
- 翻譯提示詞：`translator.build_translation_prompt`。修改後遞增 `PROMPT_VERSION`，舊快取才會失效。
- 對齊失敗處理（重試、整段以純文字退回）：`translator.translate_sentences`。
- 失控輸出判斷（重複、原始 JSON、說明文字、過長）：`translator.translation_problem`；舊譯文沿用前以 `cached_is_valid`、`units_are_valid` 檢查。
- 術語比對規則（大小寫、複數、連字號）：`glossary.term_pattern`，翻譯與渲染共用。
- 錯譯自動改正：`glossary.apply_aliases`（不動公式內容）。
- Markdown 區塊判斷（例如新增區塊類型）：`blocks.parse_blocks`；可翻譯類型列在 `TRANSLATABLE_TYPES`。
- 區塊的 HTML 結構：`render.render_block`、`_render_lang`。
- 公式渲染保護、`%` 跳脫：`render._protect_math`、`_restore_math`。
- 版面、配色、檢視模式：`templates/reader.css`（`[data-view=...]` 規則控制四種檢視）。
- 互動（句子對照、生字本、選字查詢、AI 紀錄）：`templates/reader.js`。
- 只調整版面時，用 `python translate.py --input "xxx.md" --render-only` 重新產生 HTML。

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

`units` 是二維陣列：段落與標題只有一個 unit，清單每個項目是一個 unit。句子 id 在區塊內唯一，閱讀器以 `data-block` + `data-sid` 配對中英句子。

已翻譯區塊另有 `src`（原文 hash），重新開啟文件時用來判斷譯文是否仍對應原文（`pipeline.restore_translations`）。未翻譯的區塊保留 `items`。

## 選字查詢

`/api/explain` 在 `app.py`，預設模型取自 paperai/config.py。以 file:// 開啟的靜態閱讀器也能呼叫（CORS 只對此路徑允許 Origin: null）。

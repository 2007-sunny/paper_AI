# PaperAI Developer Maintenance Guide

PaperAI is a static bilingual paper reader pipeline centered on `translate.py`.
The current flow converts a Markdown paper into a translation knowledge JSON,
an initial vocabulary JSON, and an interactive bilingual HTML reader.

## Pipeline Passes

- **Pass 0: Paper Map**
  - Function: `get_paper_map`
  - Purpose: reuse or generate the paper title/domain/topics/summary metadata.
  - Main outputs: `{BASE_NAME}_paper_map.json`

- **Pass 1: Terminology Agent**
  - Function: `run_terminology_agent`
  - Purpose: extract a unified glossary of important paper terms.
  - Maintain here when changing term categories, glossary count, or glossary prompt style.

- **Pass 2: Paragraph Translator**
  - Functions: `translate_paragraph`, `glossary_consistency_correction`
  - Purpose: translate each paragraph and enforce glossary consistency.
  - Maintain here when changing translation tone, bilingual style, or terminology correction rules.

- **Pass 3: Sentence Splitter**
  - Functions: `split_en_sentences`, `split_zh_sentences`, `align_sentences`
  - Purpose: split English/Chinese text and align sentence pairs for hover highlighting.
  - Maintain here when sentence alignment looks wrong.

- **Pass 4: Translation Knowledge Builder**
  - Functions: `find_matching_terms`, the processing loop in `main`
  - Purpose: assemble paragraph objects, sentence pairs, glossary terms, and initial vocab JSON.
  - Main outputs: `{BASE_NAME}_translation_knowledge.json`, `{BASE_NAME}_vocab.json`

- **Pass 5: HTML Renderer**
  - Function: `render_html_reader`
  - Purpose: generate the final static bilingual reader HTML.
  - Maintain here for UI, layout, CSS, JavaScript interaction, vocabulary drawer, selection popup, glossary table, and view switching.

## UI Maintenance Map

- Color theme: edit the CSS variables in `render_html_reader` under `:root` and `[data-theme="dark"]`.
- Paragraph layout: edit `.reader-content`, `.article-paragraph`, `.reader-paragraph`, and `.view-parallel`.
- Vocabulary drawer: edit `.side-drawer`, `.drawer-backdrop`, `openDrawer`, `closeDrawer`, and `renderVocab`.
- Selection-to-vocab interaction: edit `selection-popup`, `showSelectionPopupFromSelection`, the `dblclick` listener, and `selectionPopup.addEventListener('click', ...)`.
- Glossary term click behavior: edit `.term-highlight` and the `document.querySelectorAll('.term-highlight')` listener.

## Translation Logic Maintenance Map

- Change global model: edit `MODEL_NAME`.
- Change input paper: edit `INPUT_FILENAME`.
- Change paragraph splitting: edit `split_paragraphs`.
- Change glossary extraction prompt: edit `run_terminology_agent`.
- Change paragraph translation prompt: edit `translate_paragraph`.
- Add deterministic terminology fixes: edit `glossary_consistency_correction`.

## 即時單字解釋後端

根目錄 server.py 已提供 /api/explain。執行方式及依賴請參閱 README.md。

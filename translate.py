# -*- coding: utf-8 -*-
"""
PaperAI Translation Pipeline v1
--------------------------------
Flow:
PDF (via Marker) -> Markdown
  ??Pass 0: Reuse or Generate Paper Map (JSON)
  ??Pass 1: Terminology Agent (Extract Glossary JSON)
  ??Pass 2: Paragraph Translator (Translate paragraph-by-paragraph with Glossary Consistency Correction)
  ??Pass 3: Sentence Splitter (spaCy sentence splitting with abbreviation protection and alignment)
  ??Pass 4: Translation Knowledge File Builder (Assemble final JSON + initial Vocab list)
  ??Pass 5: HTML Renderer (Programmatic Bilingual Reader HTML generation)
"""

import os
import re
import json
import sys
import html
import ollama
import spacy
import markdown
import time


# Force UTF-8 stdout for Windows consoles
sys.stdout.reconfigure(encoding='utf-8')

# ============== Configuration ==============
MODEL_NAME = "ollama/qwythos:latest"
INPUT_FILENAME = "Fundamentals of Photonics-Wiley-Blackwell (2019).md"
BASE_NAME = INPUT_FILENAME.replace(".md", "")
OUTPUT_DIR = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "output"), BASE_NAME)

os.makedirs(OUTPUT_DIR, exist_ok=True)
MD_PATH = os.path.join(OUTPUT_DIR, INPUT_FILENAME)
FALLBACK_MD_PATH = INPUT_FILENAME

# Load spaCy English model
print("Loading spaCy English model...")
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    print("Warning: en_core_web_sm not found. Trying to download or load default.")
    import subprocess
    subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"], check=True)
    nlp = spacy.load("en_core_web_sm")

# Scientific abbreviations to protect from sentence splitting
ABBREVIATIONS = {
    "e.g.": "___EG___",
    "i.e.": "___IE___",
    "Fig.": "___FIG___",
    "Figs.": "___FIGS___",
    "Dr.": "___DR___",
    "Eq.": "___EQ___",
    "Eqs.": "___EQS___",
    "Vol.": "___VOL___",
    "al.": "___AL___",
    "No.": "___NO___",
    "cf.": "___CF___",
    "ca.": "___CA___"
}

# ============== Utility Functions ==============


def load_markdown(path: str, fallback_path: str) -> str:
    """Reads the Markdown text of the paper."""
    print("Reading paper Markdown...")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    elif os.path.exists(fallback_path):
        with open(fallback_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        raise FileNotFoundError(f"Could not locate Markdown file at {path} or {fallback_path}")

def call_ai(prompt: str, model_name: str = MODEL_NAME) -> str:
    """Wrapper for Ollama chat."""
    response = ollama.chat(model=model_name, messages=[
        {'role': 'user', 'content': prompt}
    ])
    return response['message']['content']

def safe_json_parse(raw_text: str, fallback: dict) -> dict:
    """Safely extracts and parses JSON from raw LLM output."""
    text = raw_text.strip()
    text = re.sub(r"^```json", "", text.strip(), flags=re.IGNORECASE).strip()
    text = re.sub(r"^```", "", text.strip()).strip()
    text = re.sub(r"```$", "", text.strip()).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        # Fallback to direct json.loads in case no brackets were used
        try:
            return json.loads(text)
        except Exception:
            print("    [WARNING] Could not locate valid JSON bounds. Using fallback.")
            return fallback

    candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        print("    [WARNING] JSON decoding failed. Using fallback.")
        return fallback

# ============== Helper ==============

def _sample_text(full_text: str, max_chars: int = 25000) -> str:
    """從開頭、中段、結尾三點採樣，避免只看前段漏掉後半論文的術語。"""
    if len(full_text) <= max_chars:
        return full_text
    chunk = max_chars // 3
    mid_start = len(full_text) // 2 - chunk // 2
    head = full_text[:chunk]
    mid  = full_text[mid_start : mid_start + chunk]
    tail = full_text[-chunk:]
    return f"{head}\n\n[... 中間省略 ...]\n\n{mid}\n\n[... 中間省略 ...]\n\n{tail}"


# ============== Pass 0: Paper Map ==============

def get_paper_map(full_text: str, output_path: str) -> dict:
    """Reuses the existing paper map or generates one if missing."""
    if os.path.exists(output_path):
        print("[Pass 0] Reusing existing paper_map from disk...")
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"    [WARNING] Error reading paper_map.json: {e}. Re-generating...")

    print("[Pass 0] Generating new paper_map...")
    preview = full_text[:6000]
    prompt = f"""你是一位專業學術論文分析專家。請閱讀以下論文開頭，提取關鍵資訊並建立 Paper Map。
輸出必須是乾淨的 JSON，不要包含 ```json 標籤或任何說明文字。

JSON 格式如下：
{{
  "title": "論文的完整英文標題",
  "author": "第一作者姓名（若無法判斷填 Unknown）",
  "domain": "論文所屬學術領域",
  "topics": ["主題關鍵字1", "主題關鍵字2", "主題關鍵字3"],
  "summary": "論文核心摘要，100-200字，使用流暢的繁體中文"
}}

【論文內容預覽】
{preview}

JSON："""

    raw_output = call_ai(prompt)
    fallback = {
        "title": "Unknown",
        "author": "Unknown",
        "domain": "Unknown",
        "topics": [],
        "summary": "（摘要生成失敗）"
    }
    paper_map = safe_json_parse(raw_output, fallback)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(paper_map, f, ensure_ascii=False, indent=2)
        print(f"    Saved paper_map to {output_path}")
    except Exception as e:
        print(f"    [WARNING] Failed to write paper_map.json: {e}")

    return paper_map


# ============== Pass 1: Terminology Agent ==============

def run_terminology_agent(paper_map: dict, full_text: str) -> dict:
    """Extracts a unified glossary of key scientific terms for the entire paper."""
    print("[Pass 1] Terminology Agent generating glossary...")
    preview = _sample_text(full_text, max_chars=25000)

    prompt = f"""你是專業的 Terminology Agent，負責為學術論文建立術語對齊詞彙表。
請從以下論文內文中挑選 15 到 25 個核心術語，包含專有名詞、儀器名稱及學科核心概念。

【任務規則】
1. 只挑選學術與技術性術語，排除一般英文單字。
2. aliases 欄位：列出小型語言模型可能產生的常見錯譯或同義替換（若無則填空陣列 []）。
3. 輸出必須是乾淨的 JSON，不使用 Markdown 標籤，不包含任何說明文字。

【輸出格式】
{{
  "glossary": [
    {{
      "term": "positive ray",
      "translation": "正射線",
      "category": "physics",
      "aliases": ["陽極射線", "正電射線", "正離子射線"]
    }},
    {{
      "term": "mass spectrograph",
      "translation": "質譜儀",
      "category": "instrument",
      "aliases": ["質量攝譜儀", "質量譜儀"]
    }}
  ]
}}

【論文背景】
標題：{paper_map.get('title')}
領域：{paper_map.get('domain')}
摘要：{paper_map.get('summary')}

【論文內文】
{preview}

JSON："""

    raw_output = call_ai(prompt)
    fallback = {
        "glossary": [
            {"term": "positive ray",     "translation": "正射線", "category": "physics",     "aliases": ["陽極射線", "正電射線"]},
            {"term": "mass spectrograph","translation": "質譜儀", "category": "instrument",  "aliases": ["質量攝譜儀"]},
        ]
    }
    glossary = safe_json_parse(raw_output, fallback)
    print(f"    Glossary generated with {len(glossary.get('glossary', []))} terms.")
    return glossary


# ============== Pass 2: Paragraph Translator ==============

def translate_paragraph(paragraph: str, paper_map: dict, glossary: dict) -> str:
    """Translates a single paragraph using the context and glossary."""
    glossary_list = "\n".join(
        f"- {g['term']} -> {g['translation']} ({g.get('category', 'general')})"
        for g in glossary.get("glossary", [])
    )

    prompt = f"""你是一位專業學術論文翻譯專家，專門翻譯科學文獻為台灣學術界使用的繁體中文。

【論文背景】
標題：{paper_map.get('title')}
領域：{paper_map.get('domain')}

【術語詞彙表】（必須嚴格遵循）
{glossary_list}

【翻譯規則】
1. 術語翻譯：詞彙表中的術語使用對應翻譯；未收錄的術語，第一次出現格式為「中譯（英文原文）」，後續僅用中譯。
2. 禁止翻譯以下類型，保持英文原文不動：
   - 人名與機構名（如 Einstein, Stanford）
   - 軟體、模型、資料集名稱（如 BERT, PyTorch）
   - 引用標記（如 [1], [Smith et al., 2023]）
   - 數學式（如 $E=mc^2$, $$\\sum_{{i}}$$）
   - 程式碼區塊（``` 包圍的內容）
   - 圖表標號（Figure 1, Table 2, Eq. 3）
3. Markdown 結構：完整保留所有 Markdown 標記（#, ##, **, *, -, > 等），僅翻譯文字內容。
4. 文體：正式學術語體，適合台灣理工科閱讀。
5. 只輸出翻譯結果，不加任何前言、解釋或說明。

【待翻譯段落】
{paragraph}

【繁體中文翻譯】
"""
    return call_ai(prompt).strip()


# ============== Pass Post-Processor: Glossary Consistency Correction ==============

def glossary_consistency_correction(original: str, translation: str, glossary: dict) -> str:
    """Post-processor to enforce glossary consistency via data-driven aliases."""
    corrected = translation
    for item in glossary.get("glossary", []):
        en_term = item["term"]
        zh_translation = item["translation"]
        aliases = item.get("aliases", [])  # 從詞彙表資料讀取，不再硬編碼

        pattern = re.compile(r'\b' + re.escape(en_term) + r'\b', re.IGNORECASE)
        if not pattern.search(original):
            continue

        # 1. 英文殘留 → 替換成標準中文譯詞
        if pattern.search(corrected):
            corrected = pattern.sub(zh_translation, corrected)

        # 2. 錯譯同義詞 → 替換成標準譯詞（找到第一個匹配即停止）
        if zh_translation not in corrected:
            for alias in aliases:
                if alias in corrected:
                    corrected = corrected.replace(alias, zh_translation)
                    break

    return corrected

def split_paragraphs(markdown_text: str) -> list:
    """
    Splits the Markdown text into paragraphs based on double newlines, filtering out empty ones.
    """
    if not markdown_text:
        return []

    # 依照標準 Markdown 規範，雙換行（\n\n）代表段落切分。
    # 使用 split('\n\n') 可以相容 Windows 與 Linux 的換行符號切割。
    paragraphs = [p.strip() for p in markdown_text.split('\n\n') if p.strip()]
    return paragraphs

# ============== Pass 3: Sentence Splitter ==============

def replace_abbreviations(text: str) -> str:
    temp_text = text
    for abbr, placeholder in ABBREVIATIONS.items():
        pattern = re.compile(r'\b' + re.escape(abbr), re.IGNORECASE)
        temp_text = pattern.sub(placeholder, temp_text)
    return temp_text

def restore_abbreviations(text: str) -> str:
    restored = text
    for abbr, placeholder in ABBREVIATIONS.items():
        restored = restored.replace(placeholder, abbr)
        restored = restored.replace(placeholder.lower(), abbr)
        restored = restored.replace(placeholder.upper(), abbr)
    return restored

import re

def _protect_and_split(text: str, split_func) -> list:
    """
    精準防禦版保護核心：在斷句前保護 LaTeX 與 Markdown 語法。
    嚴格限制匹配範圍，防止將多個公式或跨句文字誤判為單一區塊。
    """
    if not text:
        return []

    protected_blocks = []

    def replace_to_placeholder(match):
        protected_blocks.append(match.group(0))
        return f" QQQSPLITBLOCKZZZ{len(protected_blocks)-1}QQQ "

    # 🌟 升級版安全正則表達式：
    # 1. (\$\$[\s\S]*?\$\$) -> 匹配區塊公式，允許換行
    # 2. (\\\[[\s\S]*?\\\]) -> 匹配區塊公式，允許換行
    # 3. (\$[^\$\n]+?\$) -> 🌟 行內公式限制：中間絕對不能包含另一個 $ 或 換行符號
    # 4. (!\[[^\]]*?\]\([^ \)]+?\)) -> 🌟 圖片限制：! 與 [ 必須緊連，且路徑內不能有空格
    # 5. (\*\*[^\*\n]+?\*\*) -> 粗體限制：不能跨行
    # 6. (\*[^\*\n]+?\*) -> 斜體限制：不能跨行
    pattern = r"(\$\$[\s\S]*?\$\$) | (\\\[[\s\S]*?\\\]) | (\$[^\$\n]+?\$) | (!\[[^\]]*?\]\([^ \)]+?\)) | (\*\*[^\*\n]+?\*\*) | (\*[^\*\n]+?\*)"

    # 為了防止有些 Markdown 轉出來的圖片驚嘆號跟中括號有空格 (如 ! [](...))，先做極簡化清洗
    cleaned_text = re.sub(r'!\s+\[', '![', text)

    # 第一次跑：安全打包保護
    temp_text = re.compile(pattern, re.VERBOSE).sub(replace_to_placeholder, cleaned_text)

    # 執行斷句 (英文或中文)
    raw_sentences = split_func(temp_text)

    # 第二次跑：逐一還原
    final_sentences = []
    for sent in raw_sentences:
        for idx, original_content in enumerate(protected_blocks):
            sent = sent.replace(f"QQQSPLITBLOCKZZZ{idx}QQQ", original_content)

        sent = re.sub(r'\s+', ' ', sent).strip()
        if sent:
            final_sentences.append(sent)

    return final_sentences


def split_en_sentences(text: str) -> list:
    """升級版：切分英文句子，同時保護科學縮寫、LaTeX、Markdown 粗體、圖片與列表標記"""
    def _en_core(temp_text):
        # 1. 執行你原本的縮寫保護
        temp_text = replace_abbreviations(temp_text)

        # 2. 🌟 新增：預處理 Markdown 的清單橫線阻斷
        # 如果發現有類似 " - 4. " 這種在一行內串聯清單的狀況，將橫線換成句點，強迫 spaCy 斷句
        temp_text = re.sub(r'\s+-\s+(\d+\.)', r'. \1', temp_text)

        # 3. 🌟 新增：保護清單開頭的數字標號（例如 "1. ", "2. "），防止標號與內容被切斷
        # 我們把 "數字." 暫時換成特殊標記
        list_markers = []
        def protect_marker(match):
            list_markers.append(match.group(0))
            return f"___LIST_MARKER_{len(list_markers)-1}___ "

        # 匹配行首或空格後的 "數字." 或是橫線清單 "- "
        temp_text = re.sub(r'(?:^|\s)(\d+\.|\-)\s', protect_marker, temp_text)

        # 4. 呼叫 spaCy 進行自然語言斷句
        doc = nlp(temp_text)
        sentences = [sent.text.strip() for sent in doc.sents if sent.text.strip()]

        # 5. 還原縮寫與清單標號
        restored = []
        for s in sentences:
            s = restore_abbreviations(s)
            # 還原數字標號
            for idx, marker in enumerate(list_markers):
                s = s.replace(f"___LIST_MARKER_{idx}___", marker.strip())
            restored.append(s)

        return restored

    # 透過我們上一輪寫好的通用保護機制（_protect_and_split）執行
    return _protect_and_split(text, _en_core)

def split_zh_sentences(text: str) -> list:
    """升級版：依據中文標點斷句，同時確保內嵌的英文、LaTeX 與 Markdown 格式不被切爛"""
    def _zh_core(temp_text):
        # 你原本的正則斷句邏輯
        pattern = re.compile(r'([^。！？；\n]+[。！？；\n]?)')
        sentences = [m.group(1).strip() for m in pattern.finditer(temp_text)]
        sentences = [s for s in sentences if s]
        if not sentences and temp_text.strip():
            return [temp_text.strip()]
        return sentences

    # 透過通用保護機制執行
    return _protect_and_split(text, _zh_core)

def align_sentences(en_sents: list, zh_sents: list) -> list:
    """Aligns English and Chinese sentences, merging overflows to preserve indices."""
    aligned = []
    n = len(en_sents)
    m = len(zh_sents)

    if n == 0 and m == 0:
        return []
    if n == 0:
        return [{"id": f"s{i+1}", "en": "", "zh": s} for i, s in enumerate(zh_sents)]
    if m == 0:
        return [{"id": f"s{i+1}", "en": s, "zh": ""} for i, s in enumerate(en_sents)]

    if n == m:
        for i, (e, z) in enumerate(zip(en_sents, zh_sents)):
            aligned.append({"id": f"s{i+1}", "en": e, "zh": z})
    elif n > m:
        # Merge remaining English sentences into the last one
        for i in range(m - 1):
            aligned.append({"id": f"s{i+1}", "en": en_sents[i], "zh": zh_sents[i]})
        remaining_en = " ".join(en_sents[m-1:])
        aligned.append({"id": f"s{m}", "en": remaining_en, "zh": zh_sents[-1]})
    else:
        # Merge remaining Chinese sentences into the last one
        for i in range(n - 1):
            aligned.append({"id": f"s{i+1}", "en": en_sents[i], "zh": zh_sents[i]})
        remaining_zh = " ".join(zh_sents[n-1:])
        aligned.append({"id": f"s{n}", "en": en_sents[-1], "zh": remaining_zh})

    return aligned

# ============== Pass 4: Translation Knowledge File Builder ==============

def find_matching_terms(original_text: str, glossary: dict) -> list:
    """Finds which glossary terms are present in the paragraph."""
    matching_terms = []
    for item in glossary.get("glossary", []):
        term = item["term"]
        pattern = re.compile(r'\b' + re.escape(term) + r'\b', re.IGNORECASE)
        if pattern.search(original_text):
            matching_terms.append(term)
    return matching_terms

# ============== Pass 5: HTML Renderer ==============

def html_text(value) -> str:
    """Escapes text for HTML body content."""
    return html.escape(str(value or ""), quote=False)

def html_attr(value) -> str:
    """Escapes text for HTML attributes."""
    return html.escape(str(value or ""), quote=True)

def js_string(value) -> str:
    """Serializes a string as a safe JavaScript literal."""
    return json.dumps(str(value or ""), ensure_ascii=False)

def css_token(value) -> str:
    """Keeps generated CSS class suffixes predictable."""
    token = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "general").strip().lower())
    return token or "general"

def highlight_terms_in_text(text: str, glossary_terms: list, glossary_dict: dict) -> str:
    """Wraps English terms in highlight tags, sorted by length descending."""
    sorted_terms = sorted(glossary_terms, key=len, reverse=True)
    highlighted = html_text(text)
    for term in sorted_terms:
        item = glossary_dict.get(term.lower())
        if not item:
            continue
        translation = item["translation"]
        category = item.get("category", "general")

        # Replace term with interactive HTML span, case-insensitive
        pattern = re.compile(r'\b(' + re.escape(term) + r')\b', re.IGNORECASE)
        def replacement(match):
            matched = match.group(1)
            return (
                f'<span class="term-highlight" data-term="{html_attr(matched)}" '
                f'data-translation="{html_attr(translation)}" '
                f'data-category="{html_attr(category)}">{html_text(matched)}</span>'
            )
        highlighted = pattern.sub(replacement, highlighted)
    return highlighted

def highlight_chinese_terms_in_text(text: str, glossary_terms: list, glossary_dict: dict) -> str:
    """Wraps Chinese translations in highlight tags, sorted by length descending."""
    sorted_terms = sorted(glossary_terms, key=len, reverse=True)
    highlighted = html_text(text)
    for term in sorted_terms:
        item = glossary_dict.get(term.lower())
        if not item:
            continue
        translation = item["translation"]
        category = item.get("category", "general")

        pattern = re.compile(re.escape(translation))
        replacement = (
            f'<span class="term-highlight" data-term="{html_attr(term)}" '
            f'data-translation="{html_attr(translation)}" '
            f'data-category="{html_attr(category)}">{html_text(translation)}</span>'
        )
        highlighted = pattern.sub(replacement, highlighted)
    return highlighted

def render_inline_markdown(text: str) -> str:
    """將行內的 Markdown 轉換為 HTML，使用無底線 Token 完美防禦 markdown 套件的誤判"""
    if not text:
        return ""

    # 1. 處理標題記號 (## 或 #)
    is_heading = False
    if text.startswith("## "):
        text = text.replace("## ", "", 1)
        is_heading = True
    elif text.startswith("# "):
        text = text.replace("# ", "", 1)
        is_heading = True

    # 2. 🌟 移除所有底線，改用絕對安全的純英文 Token
    latex_blocks = []
    def replace_latex(match):
        actual_match = match.group(0)
        latex_blocks.append(actual_match)
        # 用 QQQLATEXBLOCKZZZ 作為前綴，後面接索引
        return f" QQQLATEXBLOCKZZZ{len(latex_blocks)-1}QQQ "

    # 匹配雙錢、中括號、單錢公式
    pattern = r"\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\$[^\$\n]+?\$"
    protected_text = re.sub(pattern, replace_latex, text)

    # 3. 解析 Markdown 粗體、斜體
    html_output = markdown.markdown(protected_text, extensions=['tables', 'fenced_code'])

    # 移除 python-markdown 自動包裹的外層 <p>
    if html_output.startswith("<p>") and html_output.endswith("</p>"):
        html_output = html_output[3:-4]

    # 4. 🌟 完美還原 LaTeX 公式
    for idx, latex_content in enumerate(latex_blocks):
        target_token = f"QQQLATEXBLOCKZZZ{idx}QQQ"

        # 由於 markdown 解析可能會壓縮空白，我們做精準匹配與帶空格匹配的雙重防禦
        if target_token in html_output:
            html_output = html_output.replace(target_token, latex_content)
        else:
            # 模糊匹配：防止前後空格被轉譯或變動
            fuzzy_pattern = rf"QQQLATEXBLOCKZZZ{idx}\s*QQQ"
            html_output = re.sub(fuzzy_pattern, latex_content, html_output)

    # 5. 如果是標題，包上一層自訂樣式
    if is_heading:
        html_output = f'<strong style="font-size: 1.25em; color: var(--primary-color, #1a1a1a); display: inline-block; margin-top: 5px;">{html_output}</strong>'

    return html_output

def render_html_reader(knowledge_data: dict, output_html_path: str):
    """Generates the interactive bilingual HTML reader."""
    print("[Pass 5] Programmatically rendering bilingual HTML reader...")

    # Pre-map glossary for quick highlighting lookup
    glossary = knowledge_data.get("glossary", [])
    glossary_dict = {g["term"].lower(): g for g in glossary}

    # Generate Glossary Table HTML
    glossary_rows = ""
    for g in glossary:
        category = g.get('category', 'general')
        glossary_rows += f"""
        <tr class="glossary-row">
            <td><strong>{html_text(g['term'])}</strong></td>
            <td>{html_text(g['translation'])}</td>
            <td><span class="badge badge-{css_token(category)}">{html_text(category)}</span></td>
            <td>
                <button class="action-btn-sm" onclick='addWordToVocab({js_string(g["term"])}, {js_string(g["translation"])}, {js_string(category)})'>
                    + Add to Vocab
                </button>
            </td>
        </tr>
        """

    # Generate Paragraphs Content HTML
    paragraphs_html = ""
    for p in knowledge_data.get("paragraphs", []):
        p_id = p["id"]
        original_text = p["original"]
        translated_text = p["translation"]
        p_terms = p.get("terms", [])

        # Build sentences
        en_html = ""
        zh_html = ""
        for s in p.get("sentences", []):
            s_id = s["id"]
            en_sent = s["en"]
            zh_sent = s["zh"]

            # Apply term highlights (保持原本的術語高亮機制)
            en_highlighted = highlight_terms_in_text(en_sent, p_terms, glossary_dict)
            zh_highlighted = highlight_chinese_terms_in_text(zh_sent, p_terms, glossary_dict)

            # 🌟 新增：將高亮後的文字再丟進 Markdown 渲染器處理粗體、標題與圖片
            en_final = render_inline_markdown(en_highlighted)
            zh_final = render_inline_markdown(zh_highlighted)

            # 將處理完的最終 HTML 塞入 span
            en_html += f'<span class="sentence sentence-en" data-paragraph-id="{p_id}" data-sentence-id="{s_id}" data-lang="en">{en_final} </span>'
            zh_html += f'<span class="sentence sentence-zh" data-paragraph-id="{p_id}" data-sentence-id="{s_id}" data-lang="zh">{zh_final} </span>'

        # Parallel block layout
        paragraphs_html += f"""
        <article class="article-paragraph" id="card-{html_attr(p_id)}">
            <div class="view-parallel">
                <div class="column en-column">
                    <p class="reader-paragraph">{en_html}</p>
                </div>
                <div class="column zh-column">
                    <p class="reader-paragraph">{zh_html}</p>
                </div>
            </div>

            <div class="view-interleaved">
                <p class="reader-paragraph en-text">{en_html}</p>
                <p class="reader-paragraph zh-text">{zh_html}</p>
            </div>

            <div class="view-english-only">
                <p class="reader-paragraph">{en_html}</p>
            </div>

            <div class="view-chinese-only">
                <p class="reader-paragraph">{zh_html}</p>
            </div>
        </article>
        """

# HTML Template
    html_content = f"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html_text(knowledge_data['metadata']['title'])} - Bilingual Reader</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Outfit:wght@500;600;700;800&display=swap" rel="stylesheet">

    <script>
      window.MathJax = {{
        tex: {{
          inlineMath: [['$', '$']], // 支援單個 $ 作為行內公式
          displayMath: [['$$', '$$']] // 支援雙 $$ 作為獨立區塊公式
        }}
      }};
    </script>

    <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js" async></script>

    <style>
        :root {{
            /* 背景色：全面拉高亮度，打造高透光、無邊界的極簡純白感 */
            --bg-primary: #ffffff;       /* 直接使用絕對純白，讓網頁底色徹底亮起來 */
            --bg-secondary: #ffffff;     /* 卡片保持純白，靠陰影營造浮空層次 */
            --bg-panel: #f8fafc;         /* 控制面板改用最淡的 Slate 白，營造乾淨的微弱對比 */

            /* 邊框：使用極高透明度的灰，讓它像光影折射一樣若隱若現 */
            --border-color: rgba(226, 232, 240, 0.6); /* 超淡半透明邊框，消除線條生硬感 */

            /* 文字：稍微調亮，告別沉悶死黑，提升長時間閱讀的舒適度 */
            --text-primary: #1e293b;     /* 清晰、明亮的石墨黑 Slate-800 */
            --text-secondary: #64748b;   /* 輕盈的霧灰 Slate-500 */
            --text-muted: #cbd5e1;       /* 低調的銀灰 Slate-300 */

            /* 主色調：換上更高飽和度、更清澈透亮的陽光藍 */
            --accent-primary: #3b82f6;   /* 更有活力的清澈藍 Sky/Blue-500 */
            --accent-secondary: #2563eb; /* 稍深的點綴藍 */
            --accent-hover: #1d4ed8;     /* 懸停時的深藍 */

            /* 高亮區塊：將藍色底色調得更淡、更透，像水彩一樣乾淨 */
            --highlight-bg: #f0f7ff;     /* 極致輕薄的微亮水藍底 */
            --highlight-border: #60a5fa; /* 清爽不刺眼的高亮引導線 */

            /* 陰影：進一步調淡透明度，拋棄所有重影，只留下空氣般的柔和折射 */
            --card-shadow: 0 4px 20px rgba(0, 0, 0, 0.03), 0 1px 3px rgba(0, 0, 0, 0.01);
        }}

        [data-theme="dark"] {{
            /* 深色背景維持極致沉穩的高級暗夜感 */
            --bg-primary: #0b0f19;
            --bg-secondary: #161b26;
            --bg-panel: #1e2533;
            --border-color: #242c3d;
            --text-primary: #f8fafc;
            --text-secondary: #cbd5e1;
            --text-muted: #64748b;
            --accent-primary: #60a5fa;
            --accent-secondary: #93c5fd;
            --accent-hover: #3b82f6;
            --highlight-bg: rgba(59, 130, 246, 0.08);
            --highlight-border: #3b82f6;
            --card-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.4), 0 10px 10px -5px rgba(0, 0, 0, 0.3);
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            /* 升級字體：英文採用傳統論文經典 Times New Roman，繁中優選精緻黑體 */
            font-family: 'Times New Roman', Times, 'PingFang TC', 'Noto Sans TC', 'Microsoft JhengHei', serif;
            /* 升級字級：由原本的 16px 放大 15% 到 1.15rem，大幅減輕眼睛負擔 */
            font-size: 1.15rem;
            /* 寬敞行高：調整至 1.8，釋放版面空氣感 */
            line-height: 1.8;

            background-color: var(--bg-primary);
            color: var(--text-primary);
            padding: 2rem 1.5rem;
            max-width: 1240px;
            margin: 0 auto;
            /* 僅針對色彩進行平滑過渡，避免排版元件因 transition * 產生載入抖動 */
            transition: background-color 0.3s ease, color 0.3s ease;
        }}

        /* 標題同步改為優雅的襯線字體，並等比例放大、規劃底邊留白 */
        h1 {{ font-size: 2.5rem;   margin-bottom: 1.5rem; }}
        h2 {{ font-size: 1.85rem;  margin-bottom: 1.25rem; }}
        h3 {{ font-size: 1.4rem;   margin-bottom: 1rem; }}

        h1, h2, h3, h4 {{
            font-family: 'Times New Roman', Times, 'PingFang TC', 'Noto Sans TC', 'Microsoft JhengHei', serif;
            font-weight: 700;
            line-height: 1.3;
        }}

        /* 確保內文段落有乾淨的間距 */
        p {{
            margin-bottom: 1.25rem;
        }}

        /* Header block */
        .header {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 2rem 2.25rem;
            margin-bottom: 2rem;
            position: relative;
            overflow: hidden;
            box-shadow: var(--card-shadow);
        }}

        .header::before {{
            content: none;
        }}

        .header-title {{
            font-size: 2rem;
            margin-bottom: 1rem;
            color: var(--accent-secondary);
            font-weight: 700;
        }}

        .header-meta {{
            display: flex;
            gap: 1.5rem;
            flex-wrap: wrap;
            margin-bottom: 1.5rem;
            font-size: 0.9rem;
        }}

        .meta-item {{
            background-color: var(--bg-panel);
            border: 1px solid var(--border-color);
            padding: 0.4rem 1rem;
            border-radius: 6px;
            color: var(--text-secondary);
        }}

        .meta-label {{
            font-weight: 600;
            color: var(--accent-secondary);
            margin-right: 0.5rem;
        }}

        .header-summary {{
            font-size: 1.05rem;
            color: var(--text-secondary);
            max-width: 1000px;
            border-left: 3px solid var(--accent-primary);
            padding-left: 1.2rem;
        }}

        /* Toolbar controls */
        .control-panel {{
            background-color: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 1rem;
            box-shadow: var(--card-shadow);
        }}

        .tab-group {{
            display: flex;
            background-color: var(--bg-panel);
            border: 1px solid var(--border-color);
            padding: 0.3rem;
            border-radius: 6px;
            flex-wrap: wrap;
        }}

        .tab-btn {{
            background: none;
            border: none;
            color: var(--text-secondary);
            padding: 0.5rem 1.2rem;
            font-size: 0.9rem;
            font-weight: 600;
            cursor: pointer;
            border-radius: 4px;
        }}

        .tab-btn.active {{
            background-color: var(--accent-primary);
            color: #ffffff;
        }}

        .toolbar-actions {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
        }}

        .theme-toggle, .drawer-toggle {{
            background-color: var(--bg-panel);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            padding: 0.6rem 1.2rem;
            border-radius: 6px;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}

        .theme-toggle:hover, .drawer-toggle:hover {{
            background-color: var(--border-color);
        }}

        /* Reader Content */
        .reader-content {{
            background-color: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 2.5rem 3rem;
            box-shadow: var(--card-shadow);
        }}

        .article-paragraph {{
            margin: 0 0 1.5rem;
        }}

        .article-paragraph:last-child {{
            margin-bottom: 0;
        }}

        .reader-paragraph {{
            margin: 0;
            font-size: 1rem;
            line-height: 1.85;
        }}

        .view-parallel {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 2.5rem;
            align-items: start;
        }}

        .column {{
            overflow-wrap: break-word;
        }}

        .en-column {{
            padding-right: 0.25rem;
        }}

        /* Interactive highlighted sentences */
        .sentence {{
            padding: 2px 4px;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .highlighted-sentence {{
            background-color: var(--highlight-bg);
            border-bottom: 2px solid var(--highlight-border);
        }}

        /* Interactive glossary terms */
        .term-highlight {{
            color: var(--accent-primary);
            font-weight: 600;
            cursor: pointer;
            border-bottom: 1.5px dashed var(--accent-primary);
            position: relative;
            padding: 0 2px;
            border-radius: 2px;
        }}

        .term-highlight:hover {{
            background-color: var(--highlight-bg);
            color: var(--text-primary);
        }}

        /* Tooltip style */
        .term-highlight::after {{
            content: attr(data-translation) " (" attr(data-category) ")";
            position: absolute;
            bottom: 125%;
            left: 50%;
            transform: translateX(-50%);
            background-color: var(--accent-secondary);
            color: #ffffff;
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 0.85rem;
            white-space: nowrap;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s ease, transform 0.2s ease;
            box-shadow: 0 4px 12px rgba(0,0,0,0.5);
            border: 1px solid var(--accent-primary);
            z-index: 10;
        }}

        .term-highlight:hover::after {{
            opacity: 1;
            transform: translateX(-50%) translateY(-4px);
        }}

        /* View Mode visibility switches */
        [data-view="parallel"] .view-interleaved,
        [data-view="parallel"] .view-english-only,
        [data-view="parallel"] .view-chinese-only {{
            display: none !important;
        }}

        [data-view="interleaved"] .view-parallel,
        [data-view="interleaved"] .view-english-only,
        [data-view="interleaved"] .view-chinese-only {{
            display: none !important;
        }}

        [data-view="english"] .view-parallel,
        [data-view="english"] .view-interleaved,
        [data-view="english"] .view-chinese-only {{
            display: none !important;
        }}

        [data-view="chinese"] .view-parallel,
        [data-view="chinese"] .view-interleaved,
        [data-view="chinese"] .view-english-only {{
            display: none !important;
        }}

        .view-interleaved .en-text {{
            color: var(--text-primary);
            margin-bottom: 0.4rem;
        }}

        .view-interleaved .zh-text {{
            color: var(--text-secondary);
            margin-bottom: 1.5rem;
            font-size: 0.95rem;
        }}

        /* Drawer Widgets */
        .drawer-backdrop {{
            position: fixed;
            inset: 0;
            background: rgba(15, 23, 42, 0.35);
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s ease;
            z-index: 80;
        }}

        .drawer-backdrop.open {{
            opacity: 1;
            pointer-events: auto;
        }}

        .side-drawer {{
            position: fixed;
            top: 0;
            right: 0;
            width: min(420px, 92vw);
            height: 100vh;
            background: var(--bg-secondary);
            border-left: 1px solid var(--border-color);
            box-shadow: -16px 0 34px rgba(15, 23, 42, 0.18);
            padding: 1.5rem;
            transform: translateX(100%);
            transition: transform 0.24s ease;
            z-index: 90;
            overflow-y: auto;
        }}

        .side-drawer.open {{
            transform: translateX(0);
        }}

        .drawer-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 1rem;
            margin-bottom: 1rem;
        }}

        .drawer-close {{
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            width: 2rem;
            height: 2rem;
            border-radius: 6px;
            cursor: pointer;
        }}

        .widget {{
            background-color: var(--bg-panel);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 1.5rem;
            margin-bottom: 1rem;
        }}

        .widget-title {{
            font-size: 1.2rem;
            margin-bottom: 1rem;
            border-bottom: 2px solid var(--border-color);
            padding-bottom: 0.5rem;
            color: var(--accent-secondary);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        /* Vocabulary list */
        .vocab-list {{
            max-height: 45vh;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 0.8rem;
            margin-bottom: 1rem;
        }}

        .vocab-item {{
            background-color: var(--bg-secondary);
            border: 1px solid var(--border-color);
            padding: 0.6rem 0.8rem;
            border-radius: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.85rem;
        }}

        .vocab-details {{
            display: flex;
            flex-direction: column;
            gap: 0.2rem;
        }}

        .vocab-term {{
            font-weight: 600;
            color: var(--text-primary);
        }}

        .vocab-translation {{
            color: var(--text-secondary);
        }}

        .vocab-category {{
            font-size: 0.75rem;
            color: var(--accent-secondary);
            text-transform: uppercase;
        }}

        .delete-btn {{
            background: none;
            border: none;
            color: #ef4444;
            font-size: 1.2rem;
            cursor: pointer;
            padding: 0.2rem;
            line-height: 1;
        }}

        .action-btn {{
            width: 100%;
            background-color: var(--accent-primary);
            color: #ffffff;
            border: none;
            padding: 0.8rem;
            border-radius: 6px;
            font-weight: 600;
            cursor: pointer;
        }}

        .action-btn:hover {{
            background-color: var(--accent-hover);
        }}

        .action-btn-sm {{
            background-color: var(--bg-secondary);
            color: var(--text-primary);
            border: 1px solid var(--border-color);
            padding: 0.3rem 0.6rem;
            border-radius: 4px;
            font-size: 0.8rem;
            cursor: pointer;
        }}

        .action-btn-sm:hover {{
            background-color: var(--border-color);
            border-color: var(--accent-primary);
        }}

        /* Glossary Table */
        .glossary-widget {{
            grid-column: 1 / -1;
            margin-top: 2rem;
        }}

        .glossary-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 1rem;
            font-size: 0.9rem;
        }}

        .glossary-table th, .glossary-table td {{
            padding: 0.8rem 1rem;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }}

        .glossary-table th {{
            color: var(--text-secondary);
            font-weight: 600;
        }}

        .badge {{
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
        }}

        .badge-physics {{ background-color: rgba(59, 130, 246, 0.2); color: #60a5fa; }}
        .badge-instrument {{ background-color: rgba(16, 185, 129, 0.2); color: #34d399; }}
        .badge-chemistry {{ background-color: rgba(245, 158, 11, 0.2); color: #fbbf24; }}
        .badge-general {{ background-color: rgba(107, 114, 128, 0.16); color: var(--text-secondary); }}

        .notification {{
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            background-color: var(--accent-primary);
            color: #ffffff;
            padding: 1rem 1.5rem;
            border-radius: 8px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.3);
            display: none;
            z-index: 100;
            font-weight: 600;
        }}

        .empty-state {{
            color: var(--text-secondary);
            font-style: italic;
            text-align: center;
            padding: 1rem 0;
        }}

        .selection-popup {{
            position: fixed;
            display: none;
            z-index: 120;
            background: var(--accent-primary);
            color: #ffffff;
            border: none;
            border-radius: 6px;
            padding: 0.45rem 0.75rem;
            font-size: 0.85rem;
            font-weight: 700;
            cursor: pointer;
            box-shadow: 0 8px 20px rgba(15, 23, 42, 0.22);
        }}

        @media (max-width: 820px) {{
            body {{
                padding: 1rem;
            }}

            .header {{
                padding: 1.5rem;
            }}

            .header-title {{
                font-size: 1.5rem;
            }}

            .reader-content {{
                padding: 1.5rem;
            }}

            .view-parallel {{
                grid-template-columns: 1fr;
                gap: 0.75rem;
            }}
        }}
    </style>
</head>

<body data-view="parallel">

    <div class="header">
        <h1 class="header-title">{html_text(knowledge_data['metadata']['title'])}</h1>
        <div class="header-meta">
            <div class="meta-item"><span class="meta-label">Domain:</span>{html_text(knowledge_data['paper_map'].get('domain', 'Scientific'))}</div>
            <div class="meta-item"><span class="meta-label">Topics:</span>{html_text(', '.join(knowledge_data['paper_map'].get('topics', [])))}</div>
            <div class="meta-item"><span class="meta-label">Author:</span>{html_text(knowledge_data['metadata'].get('author', 'J.J. Thomson'))}</div>
        </div>
        <div class="header-summary">
            {html_text(knowledge_data['paper_map'].get('summary', ''))}
        </div>
    </div>

    <div class="control-panel">
        <div class="tab-group">
            <button class="tab-btn active" data-view-btn="parallel" onclick="switchView('parallel')">Parallel View</button>
            <button class="tab-btn" data-view-btn="interleaved" onclick="switchView('interleaved')">Interleaved View</button>
            <button class="tab-btn" data-view-btn="english" onclick="switchView('english')">English Only</button>
            <button class="tab-btn" data-view-btn="chinese" onclick="switchView('chinese')">Chinese Only</button>
        </div>
        <div class="toolbar-actions">
            <button class="drawer-toggle" onclick="openDrawer()">Vocabulary</button>
            <button class="drawer-toggle" onclick="openDrawer('help')">Reading Help</button>
            <button class="theme-toggle" onclick="toggleTheme()">Toggle Theme</button>
        </div>
    </div>

    <main class="reader-content">
        {paragraphs_html}
    </main>

    <div class="drawer-backdrop" id="drawer-backdrop" onclick="closeDrawer()"></div>
        <aside class="side-drawer" id="side-drawer" aria-hidden="true">
            <div class="drawer-header">
                <h2 class="widget-title" style="border-bottom: 0; padding-bottom: 0; margin-bottom: 0;">Reader Tools</h2>
                <button class="drawer-close" onclick="closeDrawer()" aria-label="Close reader tools">&times;</button>
            </div>
            <div class="widget" id="vocab-panel">
                <h3 class="widget-title">Unknown Vocabulary</h3>
                <div class="vocab-list" id="vocab-list-container">
                    </div>
                <button class="action-btn" onclick="downloadVocabJson()">Download vocab.json</button>
            </div>
            <div class="widget" id="help-panel" style="font-size: 0.9rem; color: var(--text-secondary);">
                <h3 class="widget-title">Reading Help</h3>
                <p>Hover over English or Chinese sentences to highlight aligned translations. Click highlighted terminology, or select any word and use the floating button, to save it to your vocabulary list.</p>
            </div>

            <div class="widget" id="model-panel" style="margin-top: 15px;">
                <h3 class="widget-title">AI Model Selector</h3>
                <select id="ai-model-select" style="width: 100%; padding: 8px; border-radius: 6px; border: 1px solid var(--border-color, #ccc); background: var(--card-bg, #fff); color: var(--text-color, #333); font-size: 14px; cursor: pointer;">
                    <option value="ollama/qwythos:latest" selected>Qwythos (Default)</option>
                </select>
            </div>

            <div class="widget" id="ai-history-panel" style="margin-top: 20px;">
                <h3 class="widget-title">AI Translation History</h3>
                <div id="ai-history-container" style="max-height: 300px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px;">
                </div>
                <button class="action-btn" onclick="clearAllAiHistory()" style="margin-top: 10px; background: #d9383a; color: white;">Clear All History</button>
            </div>
        </aside>

    <div class="widget glossary-widget">
        <h3 class="widget-title">Unified Glossary</h3>
        <table class="glossary-table">
            <thead>
                <tr>
                    <th>Term</th>
                    <th>Standard Translation</th>
                    <th>Category</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                {glossary_rows}
            </tbody>
        </table>
    </div>

    <div class="notification" id="notif-bar">Word added!</div>
    <button class="selection-popup" id="selection-popup" type="button">Add to Vocabulary</button>

    <script>
        // 1. 初始化 AI 歷史紀錄陣列與讀取快取
        let aiHistoryList = [];
        try {{
            aiHistoryList = JSON.parse(localStorage.getItem('ai_translation_history')) || [];
        }} catch(e) {{
            aiHistoryList = [];
        }}

        // 2. 發送請求函式（傳遞前端指定的模型名稱）
        async function explainSelectedWord(term, context = "") {{
            const selectedModel = document.getElementById('ai-model-select').value;

            try {{
                const response = await fetch("http://127.0.0.1:8000/api/explain", {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{
                        term: term,
                        context: context,
                        model_name: selectedModel
                    }})
                }});
                if (!response.ok) throw new Error("API 請求失敗");
                return await response.json();
            }} catch (error) {{
                console.error("錯誤:", error);
                return {{
                    term: term,
                    translation: "翻譯失敗",
                    explanation: "請確認本地 server.py 是否正在運行 (port 8000)",
                    category: "Error",
                    sentences: [],
                    model_used: "None"
                }};
            }}
        }}

        // 3. 處理 API 數據、彈出視窗，並寫入歷史紀錄面板
        async function addWordWithBackend(term) {{
            const selection = window.getSelection();
            let context = "";
            if (selection && selection.anchorNode) {{
                context = selection.anchorNode.parentElement?.innerText || "";
            }}
            if (!context) {{
                context = document.body.innerText.slice(0, 1000);
            }}

            showNotification(`Ollama 正在翻譯 "${{term}}"...`);

            // 初始化彈窗狀態
            document.getElementById('modalTitle').innerText = term;
            document.getElementById('modalTranslation').innerText = "讀取中...";
            document.getElementById('modalCategory').innerText = "-";
            document.getElementById('modalExplanation').innerText = "AI 正在分析學術上下文，請稍候...";
            document.getElementById('modalSentences').innerHTML = "<li>載入中...</li>";
            document.getElementById('modalModel').innerText = "連線中...";
            document.getElementById('aiTranslationModal').style.display = 'flex';

            const result = await explainSelectedWord(term, context);

            if (result.category !== "Error") {{
                document.getElementById('modalTranslation').innerText = result.translation;
                document.getElementById('modalCategory').innerText = result.category.toUpperCase();
                document.getElementById('modalExplanation').innerText = result.explanation;
                document.getElementById('modalModel').innerText = result.model_used;

                // 渲染示範例句 (防禦衝突版)
                const sentencesList = document.getElementById('modalSentences');
                sentencesList.innerHTML = '';
                if (result.sentences && result.sentences.length > 0) {{
                    result.sentences.forEach(function(item) {{
                        const li = document.createElement('li');
                        li.style.marginBottom = "8px";
                        li.innerHTML = '<strong>EN:</strong> ' + item.en + '<br><span style="color:var(--text-secondary, #666);"><strong>ZH:</strong> ' + item.zh + '</span>';
                        sentencesList.appendChild(li);
                    }});
                }} else {{
                    sentencesList.innerHTML = '<li>無示範例句</li>';
                }}

                // 儲存並更新歷史紀錄面板 (已修正註解)
                saveToAiHistory(result);

            }} else {{
                document.getElementById('modalTranslation').innerText = "錯誤";
                document.getElementById('modalExplanation').innerText = result.explanation;
                document.getElementById('modalSentences').innerHTML = "";
                document.getElementById('modalModel').innerText = "Error";
            }}
        }}

        // 4. AI 歷史紀錄的管理與渲染函式群
        function saveToAiHistory(result) {{
            aiHistoryList = aiHistoryList.filter(item => item.term.toLowerCase() !== result.term.toLowerCase());
            aiHistoryList.unshift(result);
            localStorage.setItem('ai_translation_history', JSON.stringify(aiHistoryList));
            renderAiHistory();
        }}

        function renderAiHistory() {{
            const container = document.getElementById('ai-history-container');
            if (!container) return;
            container.innerHTML = '';

            if (aiHistoryList.length === 0) {{
                container.innerHTML = '<p style="color:#888; font-size:12px; margin:0;">No query history yet.</p>';
                return;
            }}

            aiHistoryList.forEach(function(item, index) {{
                const card = document.createElement('div');
                card.style.cssText = "background:var(--bg-hover, #f5f5f7); padding:10px; border-radius:6px; border-left:3px solid var(--primary-color, #007aff); position:relative; font-size:13px; line-height:1.4; margin-bottom:8px;";

                card.innerHTML = '<div><strong style="color:var(--primary-color); cursor:pointer;" onclick="reopenModalFromHistory(' + index + ')">' + item.term + '</strong> : ' + item.translation + '</div>' +
                                 '<div style="font-size:11px; color:#888; margin-top:4px;">Model: ' + item.model_used + ' | ' + item.category + '</div>' +
                                 '<button onclick="deleteAiHistoryItem(event, ' + index + ')" style="position:absolute; top:4px; right:6px; background:none; border:none; color:#888; cursor:pointer; font-size:16px;">&times;</button>';

                container.appendChild(card);
            }});
        }}

        function deleteAiHistoryItem(event, index) {{
            event.stopPropagation();
            aiHistoryList.splice(index, 1);
            localStorage.setItem('ai_translation_history', JSON.stringify(aiHistoryList));
            renderAiHistory();
            showNotification("History item deleted.");
        }}

        function clearAllAiHistory() {{
            if(confirm("Are you sure you want to clear all AI translation history?")) {{
                aiHistoryList = [];
                localStorage.removeItem('ai_translation_history');
                renderAiHistory();
            }}
        }}

        function reopenModalFromHistory(index) {{
            const data = aiHistoryList[index];
            document.getElementById('modalTitle').innerText = data.term;
            document.getElementById('modalTranslation').innerText = data.translation;
            document.getElementById('modalCategory').innerText = data.category.toUpperCase();
            document.getElementById('modalExplanation').innerText = data.explanation;
            document.getElementById('modalModel').innerText = data.model_used;

            const sentencesList = document.getElementById('modalSentences');
            sentencesList.innerHTML = '';
            if (data.sentences && data.sentences.length > 0) {{
                data.sentences.forEach(function(item) {{
                    const li = document.createElement('li');
                    li.style.marginBottom = "8px";
                    li.innerHTML = '<strong>EN:</strong> ' + item.en + '<br><span style="color:var(--text-secondary, #666);"><strong>ZH:</strong> ' + item.zh + '</span>';
                    sentencesList.appendChild(li);
                }});
            }} else {{
                sentencesList.innerHTML = '<li>無示範例句</li>';
            }}
            document.getElementById('aiTranslationModal').style.display = 'flex';
        }}

        // Set view mode
        function switchView(mode) {{
            document.body.setAttribute('data-view', mode);
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelector(`[data-view-btn="${{mode}}"]`).classList.add('active');
        }}

        // Toggle Dark/Light Theme
        function toggleTheme() {{
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'light' ? 'dark' : 'light';
            document.documentElement.setAttribute('data-theme', newTheme);
        }}

        function openDrawer(section = 'vocab') {{
            document.getElementById('side-drawer').classList.add('open');
            document.getElementById('side-drawer').setAttribute('aria-hidden', 'false');
            document.getElementById('drawer-backdrop').classList.add('open');
            const target = section === 'help' ? document.getElementById('help-panel') : document.getElementById('vocab-panel');
            if (target) {{
                setTimeout(() => target.scrollIntoView({{ block: 'start' }}), 80);
            }}
        }}

        function closeDrawer() {{
            document.getElementById('side-drawer').classList.remove('open');
            document.getElementById('side-drawer').setAttribute('aria-hidden', 'true');
            document.getElementById('drawer-backdrop').classList.remove('open');
        }}

        // Set up sentence hovering alignment
        document.querySelectorAll('.sentence').forEach(sent => {{
            sent.addEventListener('mouseenter', () => {{
                const pid = sent.getAttribute('data-paragraph-id');
                const sid = sent.getAttribute('data-sentence-id');
                const targetLang = sent.getAttribute('data-lang') === 'en' ? 'zh' : 'en';

                sent.classList.add('highlighted-sentence');

                const partner = document.querySelector(`.sentence[data-paragraph-id="${{pid}}"][data-sentence-id="${{sid}}"][data-lang="${{targetLang}}"]`);
                if (partner) {{
                    partner.classList.add('highlighted-sentence');
                }}
            }});

            sent.addEventListener('mouseleave', () => {{
                document.querySelectorAll('.sentence').forEach(s => s.classList.remove('highlighted-sentence'));
            }});
        }});

        // Set up term adding vocabulary
        let vocabList = [];
        try {{
            vocabList = JSON.parse(localStorage.getItem('vocab_list')) || [];
        }} catch (err) {{
            vocabList = [];
            localStorage.removeItem('vocab_list');
        }}

        function escapeHtml(value) {{
            return String(value || '').replace(/[&<>"']/g, (ch) => ({{
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;'
            }}[ch]));
        }}

        function renderVocab() {{
            const container = document.getElementById('vocab-list-container');
            if (!container) return;
            container.innerHTML = '';
            if (vocabList.length === 0) {{
                container.innerHTML = '<p class="empty-state">No terms added yet.</p>';
                return;
            }}
            vocabList.forEach((item, index) => {{
                const div = document.createElement('div');
                div.className = 'vocab-item';
                div.innerHTML = `
                    <div class="vocab-details">
                        <span class="vocab-term">${{escapeHtml(item.term)}}</span>
                        <span class="vocab-translation">${{escapeHtml(item.translation)}}</span>
                        <span class="vocab-category">${{escapeHtml(item.category)}}</span>
                    </div>
                    <button class="delete-btn" onclick="removeWord(${{index}})">&times;</button>
                `;
                container.appendChild(div);
            }});
        }}

        function addWordToVocab(term, translation, category) {{
            if (!vocabList.some(item => item.term.toLowerCase() === term.toLowerCase())) {{
                vocabList.push({{ term, translation, category }});
                localStorage.setItem('vocab_list', JSON.stringify(vocabList));
                renderVocab();
                showNotification(`Added "${{term}}" to vocabulary.`);
            }} else {{
                showNotification(`"${{term}}" is already in your vocabulary.`);
            }}
        }}

        function removeWord(index) {{
            vocabList.splice(index, 1);
            localStorage.setItem('vocab_list', JSON.stringify(vocabList));
            renderVocab();
        }}

        // 下載 JSON 檔案
        function downloadVocabJson() {{
            const rawObj = {{ unknown_words: vocabList.map(item => item.term) }};
            const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(rawObj, null, 2));
            const downloadAnchor = document.createElement('a');
            downloadAnchor.setAttribute("href", dataStr);
            downloadAnchor.setAttribute("download", "{BASE_NAME}_vocab.json");
            document.body.appendChild(downloadAnchor);
            downloadAnchor.click();
            downloadAnchor.remove();
        }}

        function showNotification(msg) {{
            const bar = document.getElementById('notif-bar');
            if (!bar) return;
            bar.textContent = msg;
            bar.style.display = 'block';
            setTimeout(() => {{
                bar.style.display = 'none';
            }}, 2500);
        }}

        document.querySelectorAll('.term-highlight').forEach(el => {{
            el.addEventListener('click', (e) => {{
                e.stopPropagation();
                const term = el.getAttribute('data-term');
                const trans = el.getAttribute('data-translation');
                const cat = el.getAttribute('data-category');
                addWordToVocab(term, trans, cat);
            }});
        }});

        const selectionPopup = document.getElementById('selection-popup');
        let selectedTextForVocab = '';

        function getCleanSelectionText() {{
            const selection = window.getSelection();
            const text = selection ? selection.toString().trim() : '';
            return text.replace(/^[\\s.,;:!?()[\\]{{}}"'Input指標]+|[\\s.,;:!?()[\\]{{}}"'Input指標]+$/g, '').slice(0, 80);
        }}

        function showSelectionPopupFromSelection() {{
            selectedTextForVocab = getCleanSelectionText();
            if (!selectedTextForVocab || !selectionPopup) {{
                if (selectionPopup) selectionPopup.style.display = 'none';
                return;
            }}
            const selection = window.getSelection();
            if (!selection || selection.rangeCount === 0) return;
            const rect = selection.getRangeAt(0).getBoundingClientRect();
            selectionPopup.style.left = `${{Math.min(rect.left + rect.width / 2, window.innerWidth - 170)}}px`;
            selectionPopup.style.top = `${{Math.max(rect.top - 44, 8)}}px`;
            selectionPopup.style.display = 'block';
        }}

        document.addEventListener('mouseup', () => {{
            setTimeout(showSelectionPopupFromSelection, 0);
        }});

        document.addEventListener('dblclick', (event) => {{
            const range = document.caretRangeFromPoint ? document.caretRangeFromPoint(event.clientX, event.clientY) : null;
            if (range && range.startContainer.nodeType === Node.TEXT_NODE) {{
                const text = range.startContainer.textContent;
                const offset = range.startOffset;
                const left = text.slice(0, offset).search(/[\\w'-]+$/);
                const rightMatch = text.slice(offset).match(/^[\\w'-]+/);
                if (left >= 0 && rightMatch) {{
                    selectedTextForVocab = text.slice(left, offset) + rightMatch[0];
                    if (selectionPopup) {{
                        selectionPopup.style.left = `${{Math.min(event.clientX + 8, window.innerWidth - 170)}}px`;
                        selectionPopup.style.top = `${{Math.max(event.clientY - 44, 8)}}px`;
                        selectionPopup.style.display = 'block';
                    }}
                }}
            }} else {{
                setTimeout(showSelectionPopupFromSelection, 0);
            }}
        }});

        if (selectionPopup) {{
            selectionPopup.addEventListener('click', async () => {{
                if (selectedTextForVocab) {{
                    openDrawer('vocab');
                    await addWordWithBackend(selectedTextForVocab);
                }}
                selectionPopup.style.display = 'none';
                window.getSelection()?.removeAllRanges();
            }});
        }}

        document.addEventListener('mousedown', (event) => {{
            if (selectionPopup && event.target !== selectionPopup) {{
                selectionPopup.style.display = 'none';
            }}
        }});

        // 執行初始渲染
        renderVocab();
        renderAiHistory();
    </script>

    <div id="aiTranslationModal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); z-index:99999; align-items:center; justify-content:center; font-family: system-ui, -apple-system, sans-serif;">
        <div style="background:var(--card-bg, #ffffff); color:var(--text-color, #333333); padding:24px; border-radius:12px; max-width:520px; width:90%; box-shadow:0 10px 25px rgba(0,0,0,0.2); position:relative; max-height: 85vh; overflow-y: auto;">

            <h3 id="modalTitle" style="margin-top:0; font-size: 20px; color: var(--primary-color, #007aff); border-bottom: 2px solid var(--border-color, #eee); padding-bottom: 8px;">單字解析</h3>

            <p style="margin: 12px 0;"><strong>繁體翻譯：</strong> <span id="modalTranslation" style="font-size: 16px; font-weight: bold; color: #d9383a;"></span></p>
            <p style="margin: 12px 0;"><strong>專業領域：</strong> <span id="modalCategory" style="background:#e1f5fe; color:#0288d1; padding:2px 8px; border-radius:4px; font-size:12px;"></span></p>

            <p style="margin: 12px 0 4px 0;"><strong>詳細解釋：</strong></p>
            <div id="modalExplanation" style="background:var(--bg-hover, #f5f5f7); padding:12px; border-radius:6px; margin-bottom:14px; line-height: 1.5; font-size: 14px;"></div>

            <p style="margin: 0 0 6px 0;"><strong>學術示範例句：</strong></p>
            <ul id="modalSentences" style="padding-left:20px; font-size:13px; margin: 0; line-height: 1.6; color: var(--text-color, #333);"></ul>

            <hr style="border:0; border-top:1px solid var(--border-color, #eee); margin: 20px 0 10px 0;">
            <div style="font-size:11px; color:#888; display: flex; justify-content: space-between; align-items: center;">
                <span>AI 模型：<span id="modalModel"></span></span>
                <button onclick="document.getElementById('aiTranslationModal').style.display='none'" style="background: #e0e0e0; color: #333; border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer;">關閉</button>
            </div>
        </div>
    </div>

</body>
</html>
"""

    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"    Interactive HTML reader generated successfully at: {output_html_path}")

# ============== Pipeline Execution ==============

def main():
    print("=== PaperAI Translation Pipeline v1 ===")

    # Load Markdown input
    full_markdown = load_markdown(MD_PATH, FALLBACK_MD_PATH)
    print(f"Loaded Markdown length: {len(full_markdown)} characters.")
    print(f"choosing model: {MODEL_NAME}")
    # Pass 0: Paper Map (Reuse or Generate)
    paper_map_path = os.path.join(OUTPUT_DIR, f"{BASE_NAME}_paper_map.json")
    paper_map = get_paper_map(full_markdown, paper_map_path)

    # Pass 1: Terminology Agent
    glossary = run_terminology_agent(paper_map, full_markdown)

    # Split Markdown into paragraphs
    raw_paragraphs = split_paragraphs(full_markdown)
    print(f"Total paragraphs detected: {len(raw_paragraphs)}")

    # Pass 2 & 3 & 4: Paragraph Translation, Sentence Splitting, Terms Mapping
    processed_paragraphs = []

    # Load translation cache/checkpoint
    cache_path = os.path.join(OUTPUT_DIR, f"{BASE_NAME}_translation_cache.json")
    translation_cache = {}
    if os.path.exists(cache_path):
        print(f"Loading translation cache from {cache_path}...")
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                translation_cache = json.load(f)
            print(f"    Loaded {len(translation_cache)} cached translations.")
        except Exception as e:
            print(f"    [WARNING] Error loading cache: {e}")
    start_time = time.time()
    timeperchar = 0
    timeperpara = 0

    estimated_time_left = 0
    # Process paragraph by paragraph.
    for index, raw_p in enumerate(raw_paragraphs, 1):
        timeperchar = (time.time() - start_time) / len(raw_p) if len(raw_p) > 0 else 0
        timeperpara = (time.time() - start_time) / index if index > 0 else 0
        estimated_time_left = timeperpara * (len(raw_paragraphs) - index)
        p_id = f"p{index}"
        print(f"Processing Paragraph {index}/{len(raw_paragraphs)} (Length: {len(raw_p)} chars)...")
        print(f"time per char: {timeperchar:.4f} sec | time per paragraph: {timeperpara:.2f} sec |", end=" ")
        estimated_time_left_hour = estimated_time_left // 3600
        estimated_time_left_min = (estimated_time_left % 3600) // 60
        estimated_time_left_sec = estimated_time_left % 60
        print(f"Estimated time left: {estimated_time_left_hour:.0f}h {estimated_time_left_min:.0f}m {estimated_time_left_sec:.0f}s")
        # 🌟 【新架構核心 1】Pass 3 提前：在翻譯前，先對絕對正確的原始英文進行斷句與標記
        # 這時候的英文是最乾淨的，保證不會有任何翻譯帶來的標點符號干擾
        en_sentences = split_en_sentences(raw_p)

        # 檢查快取
        import hashlib
        p_hash = hashlib.md5(raw_p.encode('utf-8')).hexdigest()

        if p_hash in translation_cache:
            print(f"    Reusing cached translation for Paragraph {index}")
            corrected_p = translation_cache[p_hash]
        else:
            # Pass 2: 段落翻譯
            translated_p = translate_paragraph(raw_p, paper_map, glossary)

            # Pass Post Processor: 術語一致性修正
            corrected_p = glossary_consistency_correction(raw_p, translated_p, glossary)

            # 儲存快取
            translation_cache[p_hash] = corrected_p
            # (此處省略寫入快取檔案的 try-except...)

        # 🌟 【新架構核心 2】智慧型中文斷句與強迫對齊
        # 不要再用正則去盲猜翻譯後的中文怎麼斷句了！
        # 我們直接根據「英文切出了幾句」，來動態決定中文怎麼對應
        zh_sentences = split_zh_sentences(corrected_p)

        # ⚠️ 防禦機制：如果中英文句子數量不一致，
        # 這是因為 LLM 把兩句翻譯成了一句，或者多給了句點。
        # 如果對不齊，我們不再強行使用可能出錯的 align_sentences，而是進行安全平鋪或強迫等長處理
        if len(en_sentences) != len(zh_sentences):
            print(f"    [WARNING] Sentence count mismatch (EN: {len(en_sentences)}, ZH: {len(zh_sentences)}). Adjusting...")
            # 如果中文太少，用空字串補齊；如果中文太多，把多餘的合併到最後一句
            if len(zh_sentences) < len(en_sentences):
                zh_sentences += [""] * (len(en_sentences) - len(zh_sentences))
            else:
                main_zh = zh_sentences[:len(en_sentences)-1]
                last_zh = " ".join(zh_sentences[len(en_sentences)-1:])
                zh_sentences = main_zh + [last_zh]

        # 🌟 【新架構核心 3】絕對安全的結構化組裝
        # 因為長度保證百分之百一樣，對齊絕對不會錯位
        aligned_sentences = []
        for i in range(len(en_sentences)):
            aligned_sentences.append({
                "id": f"s{i+1}",
                "en": en_sentences[i],
                "zh": zh_sentences[i]
            })

        # Pass 4: 尋找術語
        matching_terms = find_matching_terms(raw_p, glossary)

        processed_paragraphs.append({
            "id": p_id,
            "original": raw_p,
            "translation": corrected_p,
            "sentences": aligned_sentences,
            "terms": matching_terms
        })

    # Pass 4: Translation Knowledge File compilation
    knowledge_data = {
        "metadata": {
            "title": paper_map.get("title", BASE_NAME),
            "author": paper_map.get("author", "Unknown")
        },
        "paper_map": paper_map,
        "glossary": glossary.get("glossary", []),
        "paragraphs": processed_paragraphs
    }

    # Save Knowledge File
    knowledge_path = os.path.join(OUTPUT_DIR, f"{BASE_NAME}_translation_knowledge.json")
    with open(knowledge_path, "w", encoding="utf-8") as f:
        json.dump(knowledge_data, f, ensure_ascii=False, indent=2)
    print(f"\n[Pass 4] Created translation knowledge database: {knowledge_path}")

    # Save initial vocabulary list
    vocab_path = os.path.join(OUTPUT_DIR, f"{BASE_NAME}_vocab.json")
    initial_vocab = {
        "unknown_words": [g["term"] for g in glossary.get("glossary", [])]
    }
    with open(vocab_path, "w", encoding="utf-8") as f:
        json.dump(initial_vocab, f, ensure_ascii=False, indent=2)
    print(f"[Pass 4] Created initial vocab list: {vocab_path}")

    # Pass 5: HTML Reader rendering
    html_reader_path = os.path.join(OUTPUT_DIR, f"{BASE_NAME}_reader.html")
    render_html_reader(knowledge_data, html_reader_path)

    print("\n Translation Pipeline Version 1 Finished Successfully!")
    print(f"Bilingual Knowledge DB: {knowledge_path}")
    print(f"Bilingual Interactive Reader: {html_reader_path}")


if __name__ == "__main__":
    main()

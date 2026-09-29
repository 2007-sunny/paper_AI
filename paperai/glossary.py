"""文件摘要（paper map）、術語表的生成、合併與比對。

術語表有兩層：
- output/<名稱>/<名稱>_glossary.json：模型自動生成，每份文件一份，可手動修改。
- 專案根目錄 glossary.json：使用者維護、跨文件共用，同名術語優先採用。
"""
import json
import os
import re

from . import config
from .llm import call_ai, safe_json_parse, save_json_atomic


def _load_json(path, label: str):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"    [WARNING] 無法讀取 {label}（{e}），將重新生成。")
    return None


def _sample_text(full_text: str, max_chars: int = 25000) -> str:
    """從開頭、中段、結尾三點採樣，避免只看前段漏掉後半論文的術語。"""
    if len(full_text) <= max_chars:
        return full_text
    chunk = max_chars // 3
    mid_start = len(full_text) // 2 - chunk // 2
    head = full_text[:chunk]
    mid = full_text[mid_start: mid_start + chunk]
    tail = full_text[-chunk:]
    return f"{head}\n\n[... 中間省略 ...]\n\n{mid}\n\n[... 中間省略 ...]\n\n{tail}"


def get_paper_map(full_text: str, output_path) -> dict:
    """沿用既有 paper map，不存在時才生成。"""
    cached = _load_json(output_path, "paper_map")
    if cached is not None:
        print("[文件摘要] 沿用既有 paper_map")
        return cached

    print("[文件摘要] 生成 paper_map...")
    prompt = f"""你是一位專業學術論文分析專家。請閱讀以下論文開頭，提取關鍵資訊並建立 Paper Map。
輸出必須是乾淨的 JSON，不要包含 ```json 標籤或任何說明文字。

JSON 格式如下：
{{
  "title": "論文的完整英文標題",
  "author": "第一作者姓名（若無法判斷填 Unknown）",
  "domain": "論文所屬學術領域",
  "topics": ["主題關鍵字1", "主題關鍵字2", "主題關鍵字3"],
  "summary": "論文核心摘要，100-200字，使用流暢的台灣繁體中文"
}}

【論文內容預覽】
{full_text[:6000]}

JSON："""
    fallback = {"title": "Unknown", "author": "Unknown", "domain": "Unknown",
                "topics": [], "summary": "（摘要生成失敗）"}
    paper_map = safe_json_parse(call_ai(prompt), fallback)
    if paper_map is not fallback:
        save_json_atomic(output_path, paper_map)
    return paper_map


def generate_glossary(paper_map: dict, full_text: str, output_path) -> list:
    """沿用既有自動術語表，不存在時才請模型擷取。刪除檔案即可重新生成。"""
    cached = _load_json(output_path, "glossary")
    if cached is not None:
        print("[術語擷取] 沿用既有自動術語表")
        return cached.get("glossary", [])

    print("[術語擷取] 生成自動術語表...")
    prompt = f"""你是專業的 Terminology Agent，負責為學術論文建立術語對齊詞彙表。
請從以下論文內文中挑選 15 到 25 個核心術語，包含專有名詞、儀器名稱及學科核心概念。

【任務規則】
1. 只挑選學術與技術性術語，排除一般英文單字。
2. term 使用論文中出現的原形（單數），不要加括號縮寫。
3. translation 使用台灣學術界通用的譯名（以國家教育研究院學術名詞為準），不可使用中國大陸用語或簡體字。
4. aliases 欄位：列出小型語言模型可能產生的常見錯譯或中國大陸用語（若無則填空陣列 []）。
5. 輸出必須是乾淨的 JSON，不使用 Markdown 標籤，不包含任何說明文字。

【輸出格式】
{{
  "glossary": [
    {{"term": "plasma", "translation": "電漿", "category": "physics", "aliases": ["等離子體", "等離子"]}},
    {{"term": "mass spectrograph", "translation": "質譜儀", "category": "instrument", "aliases": ["質量攝譜儀"]}}
  ]
}}

【論文背景】
標題：{paper_map.get('title')}
領域：{paper_map.get('domain')}
摘要：{paper_map.get('summary')}

【論文內文】
{_sample_text(full_text)}

JSON："""
    fallback = {"glossary": []}
    result = safe_json_parse(call_ai(prompt), fallback)
    terms = [g for g in result.get("glossary", []) if g.get("term") and g.get("translation")]
    print(f"    擷取 {len(terms)} 個術語")
    if terms:
        save_json_atomic(output_path, {"glossary": terms})
    return terms


def load_user_glossary() -> list:
    data = _load_json(config.USER_GLOSSARY_PATH, "glossary.json")
    return (data or {}).get("glossary", [])


def upsert_user_term(term: str, translation: str, old_translation: str = "", category: str = "general") -> dict:
    """在使用者術語表新增或修改術語；舊譯名自動加入 aliases，已翻譯的舊譯文也會被改正。"""
    term, translation = term.strip(), translation.strip()
    if not term or not translation:
        raise ValueError("術語與譯名不可空白")
    data = _load_json(config.USER_GLOSSARY_PATH, "glossary.json") or {"glossary": []}
    entries = data.setdefault("glossary", [])
    entry = next((g for g in entries if g["term"].lower() == term.lower()), None)
    if entry is None:
        entry = {"term": term, "translation": translation, "category": category or "general", "aliases": []}
        entries.append(entry)
    previous = entry.get("translation", "")
    entry["translation"] = translation
    aliases = entry.setdefault("aliases", [])
    for old in (old_translation, previous):
        old = (old or "").strip()
        if old and old != translation and old not in aliases:
            aliases.append(old)
    entry["aliases"] = [a for a in aliases if a != translation]
    save_json_atomic(config.USER_GLOSSARY_PATH, data)
    return entry


def term_pattern(term: str) -> re.Pattern:
    """英文術語比對：不分大小寫，允許複數詞尾，連字號與空白視為相同。"""
    words = re.split(r"[-\s]+", term.strip())
    body = r"[-\s]+".join(re.escape(w) for w in words)
    return re.compile(r"\b" + body + r"(?:s|es)?\b", re.IGNORECASE)


def build_glossary(auto_terms: list, user_terms: list, full_text: str) -> list:
    """合併術語表：使用者術語覆蓋同名自動術語，且只保留本文件出現的使用者術語。"""
    merged = {}
    for item in auto_terms:
        merged[item["term"].lower()] = {**item, "source": "auto"}
    for item in user_terms:
        if term_pattern(item["term"]).search(full_text):
            merged[item["term"].lower()] = {**item, "source": "user"}
    return list(merged.values())


def find_matching_terms(text: str, glossary: list) -> list:
    """回傳在 text 中出現的術語（長的優先，避免短術語搶先比對）。"""
    found = [g for g in glossary if term_pattern(g["term"]).search(text)]
    return sorted(found, key=lambda g: len(g["term"]), reverse=True)


_MATH_RE = re.compile(r"(\$\$[\s\S]*?\$\$|\$[^$\n]+?\$)")


def apply_aliases(en: str, zh: str, terms: list) -> str:
    """把譯文中的已知錯譯（aliases）換成標準譯名；不動公式內容。"""
    if not zh:
        return zh
    parts = _MATH_RE.split(zh)
    for item in terms:
        translation = item["translation"]
        aliases = [a for a in item.get("aliases", []) if a and a not in translation]
        if not aliases or not term_pattern(item["term"]).search(en):
            continue
        for idx in range(0, len(parts), 2):  # 偶數索引為公式以外的文字
            for alias in sorted(aliases, key=len, reverse=True):
                parts[idx] = parts[idx].replace(alias, translation)
    return "".join(parts)

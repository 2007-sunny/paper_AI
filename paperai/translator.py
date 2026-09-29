"""逐句翻譯：送入編號英文句子，以 JSON schema 要求回傳等長譯文陣列。

對齊在翻譯時就成立；模型回傳句數不符時重試一次，仍失敗則整段翻譯並標記 aligned=False。
"""
import hashlib
import json
import os
import re
from dataclasses import dataclass

from . import config
from .glossary import apply_aliases, find_matching_terms
from .llm import chat, safe_json_parse, save_json_atomic
from .sentences import split_en_sentences

# 修改提示詞或輸出格式時遞增，讓舊快取自動失效
# 3：修正 LaTeX 反斜線被 JSON 解析成控制字元（\bar、\text、\frac…）
PROMPT_VERSION = 3

_BACKSLASH_RUN = re.compile(r"(\\+)(?=[A-Za-z]{2})")
_UNICODE_ESCAPE = re.compile(r"u[0-9a-fA-F]{4}")
# JSON 合法跳脫 \b \f \t \r 被解析後的控制字元 → 還原成 LaTeX 反斜線
_CONTROL_TO_LATEX = {"\b": "\\b", "\f": "\\f", "\t": "\\t", "\r": "\\r"}


def _escape_latex_in_json(raw: str) -> str:
    """模型常在 JSON 字串中寫單一反斜線的 LaTeX（如 \\bar），補成 \\\\bar 再解析。

    奇數個反斜線後接兩個以上字母視為未跳脫的 LaTeX 指令；\\uXXXX 例外。
    """
    def fix(m):
        run = m.group(1)
        if len(run) % 2 == 0 or _UNICODE_ESCAPE.match(raw, m.end()):
            return run
        return run + "\\"
    return _BACKSLASH_RUN.sub(fix, raw)


def _repair_control_chars(text: str) -> str:
    for ch, latex in _CONTROL_TO_LATEX.items():
        text = text.replace(ch, latex)
    return text


@dataclass
class TranslationResult:
    zh: list
    aligned: bool
    tokens: int = 0
    eval_seconds: float = 0.0


def _glossary_lines(terms: list) -> str:
    if not terms:
        return "（本段無指定術語）"
    return "\n".join(f"- {g['term']} → {g['translation']}" for g in terms)


def build_translation_prompt(sentences: list, paper_map: dict, terms: list) -> str:
    numbered = "\n".join(f"[{i}] {s}" for i, s in enumerate(sentences, 1))
    return f"""你是一位專業學術論文翻譯專家，將科學文獻翻譯為台灣學術界使用的繁體中文。

【論文背景】
標題：{paper_map.get('title')}
領域：{paper_map.get('domain')}

【本段術語】（必須使用指定譯名）
{_glossary_lines(terms)}

【翻譯規則】
1. 以下共 {len(sentences)} 句英文，逐句翻譯，輸出 {len(sentences)} 個譯文，順序一一對應，不可合併、拆分或省略。
2. 上列術語使用指定譯名；其他專業術語第一次出現時寫成「中譯（英文原文）」。
3. 保持原文不翻譯：人名與機構名、軟體與資料集名稱、引用標記（如 [1]）、圖表與公式編號（如 Fig. 2、Eq. 3）。
4. 數學式（$...$）、HTML 標籤（如 <sup>1</sup>）與 Markdown 標記（**、*）原樣保留。
5. 使用台灣用語與正體字，不可使用中國大陸用語或簡體字。
6. 只翻譯，不加任何說明。

【英文句子】
{numbered}

請輸出 JSON：{{"translations": ["第 1 句譯文", ...]}}"""


def _schema(n: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "translations": {"type": "array", "items": {"type": "string"}, "minItems": n, "maxItems": n}
        },
        "required": ["translations"],
    }


_NUMBER_PREFIX_RE = re.compile(r"^\s*\[\d+\]\s*")


def _output_budget(sentences: list) -> int:
    """依原文長度限制輸出 token 數；JSON 模式偶爾會無限輸出空白，需及早截斷。"""
    return 256 + sum(len(s) for s in sentences)


def translate_sentences(sentences: list, paper_map: dict, terms: list, model: str = None) -> TranslationResult:
    prompt = build_translation_prompt(sentences, paper_map, terms)
    budget = _output_budget(sentences)
    tokens, seconds = 0, 0.0
    for attempt in range(2):
        res = chat(prompt, model, fmt=_schema(len(sentences)), num_predict=budget,
                   temperature=0.2 if attempt else None)
        tokens += res.tokens
        seconds += res.eval_seconds
        zh = safe_json_parse(_escape_latex_in_json(res.content), {}).get("translations")
        if isinstance(zh, list) and len(zh) == len(sentences) and all(isinstance(z, str) for z in zh):
            zh = [_NUMBER_PREFIX_RE.sub("", z).strip() for z in zh]
            return TranslationResult(zh, True, tokens, seconds)

    # 對齊失敗：整段翻譯，保證至少有譯文
    whole = [" ".join(sentences)]
    res = chat(build_translation_prompt(whole, paper_map, terms), model, fmt=_schema(1), num_predict=budget)
    zh = safe_json_parse(_escape_latex_in_json(res.content), {}).get("translations") or [res.content]
    zh = _NUMBER_PREFIX_RE.sub("", str(zh[0])).strip()
    return TranslationResult([zh], False, tokens + res.tokens, seconds + res.eval_seconds)


class TranslationCache:
    """以（提示詞版本、模型、句子、本段術語）為 key；每次寫入都存檔。"""

    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                print(f"    [WARNING] 快取讀取失敗（{e}），將重新翻譯。")

    @staticmethod
    def key(model: str, sentences: list, terms: list) -> str:
        payload = [PROMPT_VERSION, model, sentences, [(g["term"], g["translation"]) for g in terms]]
        return hashlib.md5(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: dict):
        self.data[key] = value
        save_json_atomic(self.path, self.data)


def source_hash(items: list) -> str:
    """區塊原文的 hash；重新開啟文件時用來判斷既有譯文是否仍對應同一段原文。"""
    return hashlib.md5("\n".join(items).encode("utf-8")).hexdigest()[:12]


def translate_block(block: dict, paper_map: dict, glossary: list, cache: TranslationCache,
                    model: str = None) -> bool:
    """翻譯 heading/paragraph/list 區塊，將 items 轉為 units（每個 unit 是一串中英句對）。

    回傳是否實際呼叫了模型（False 表示命中快取）。
    """
    model = model or config.MODEL_NAME
    items = block.pop("items")
    block["src"] = source_hash(items)
    unit_sentences = [split_en_sentences(text) or [text] for text in items]
    flat = [s for unit in unit_sentences for s in unit]
    terms = find_matching_terms(" ".join(items), glossary)

    key = TranslationCache.key(model, flat, terms)
    cached = cache.get(key)
    called = cached is None
    if called:
        result = translate_sentences(flat, paper_map, terms, model)
        cached = {"zh": result.zh, "aligned": result.aligned}
        cache.set(key, cached)

    units, sid = [], 0
    if cached["aligned"]:
        pos = 0
        for unit in unit_sentences:
            pairs = []
            for en in unit:
                sid += 1
                pairs.append({"id": f"s{sid}", "en": en, "zh": apply_aliases(en, _repair_control_chars(cached["zh"][pos]), terms)})
                pos += 1
            units.append(pairs)
    else:
        # 整段譯文無法拆回各項目，合併成單一句對
        en = " ".join(flat)
        units = [[{"id": "s1", "en": en, "zh": apply_aliases(en, _repair_control_chars(cached["zh"][0]), terms)}]]

    block["units"] = units
    block["aligned"] = cached["aligned"]
    block["terms"] = [g["term"] for g in terms]
    return called

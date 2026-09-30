"""CLI（translate.py）與本機服務共用的流程組裝。"""
import json
import os
from datetime import datetime

from . import config
from .blocks import TRANSLATABLE_TYPES
from .glossary import apply_aliases, build_glossary, generate_glossary, get_paper_map, load_user_glossary
from .translator import source_hash, units_are_valid


def prepare(doc: config.Doc, markdown_text: str):
    """生成或沿用 paper map 與術語表，回傳 (paper_map, 合併後的術語表)。"""
    paper_map = get_paper_map(markdown_text, doc.path("_paper_map.json"))
    auto_terms = generate_glossary(paper_map, markdown_text, doc.path("_glossary.json"))
    glossary = build_glossary(auto_terms, load_user_glossary(), markdown_text)
    # 摘要由模型另外生成，同樣套用錯譯改正（例如 激光 → 雷射）；只影響顯示，不改檔案
    if paper_map.get("summary"):
        paper_map = {**paper_map, "summary": apply_aliases(markdown_text, paper_map["summary"], glossary)}
    return paper_map, glossary


def restore_translations(blocks: list, knowledge_path) -> int:
    """把既有 knowledge JSON 中、原文未變的譯文套回剛切好的區塊，回傳套用數量。

    含失控輸出（重複迴圈、原始 JSON 等）的舊譯文不沿用，讓它重新翻譯。
    """
    if not os.path.exists(knowledge_path):
        return 0
    try:
        with open(knowledge_path, "r", encoding="utf-8") as f:
            saved = {b["id"]: b for b in json.load(f).get("blocks", []) if "units" in b and b.get("src")}
    except Exception:
        return 0
    restored = 0
    for block in blocks:
        old = saved.get(block["id"])
        if block["type"] in TRANSLATABLE_TYPES and "items" in block and old \
                and old["src"] == source_hash(block["items"]) and units_are_valid(old["units"]):
            block.pop("items")
            for key in ("units", "aligned", "terms", "src"):
                block[key] = old[key]
            restored += 1
    return restored


def build_knowledge(doc: config.Doc, paper_map: dict, glossary: list, blocks: list, preview: bool = False) -> dict:
    translatable = [b for b in blocks if b["type"] in TRANSLATABLE_TYPES]
    translated = [b for b in translatable if "units" in b]
    return {
        "version": 2,
        "metadata": {
            "title": paper_map.get("title", doc.base_name),
            "author": paper_map.get("author", "Unknown"),
            "model": config.MODEL_NAME,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "preview": preview,
            "stats": {
                "blocks": len(blocks),
                "translatable_blocks": len(translatable),
                "translated_blocks": len(translated),
                "unaligned_blocks": sum(1 for b in translated if not b.get("aligned", True)),
            },
        },
        "paper_map": paper_map,
        "glossary": glossary,
        "blocks": blocks,
    }

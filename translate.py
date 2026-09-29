# -*- coding: utf-8 -*-
"""PaperAI 翻譯流程：Markdown → 中英對照閱讀器。

1. 文件摘要（paper map）  2. 術語擷取並合併使用者 glossary.json
3. Markdown 切成區塊      4. 逐區塊、逐句翻譯（結構化輸出保證對齊，結果寫入快取）
5. 輸出 knowledge JSON    6. 產生 HTML 閱讀器

範例：
python translate.py --input "1804.03318v2.md"
python translate.py --input "1804.03318v2.md" --limit 20
"""
import argparse
import sys
import time
from datetime import datetime

from paperai import config
from paperai.blocks import TRANSLATABLE_TYPES, parse_blocks
from paperai.glossary import build_glossary, generate_glossary, get_paper_map, load_user_glossary
from paperai.llm import save_json_atomic
from paperai.render import render_reader
from paperai.translator import TranslationCache, translate_block

# Windows 主控台預設編碼不是 UTF-8
sys.stdout.reconfigure(encoding="utf-8")

# 未指定 --input 時使用的文件（output/<名稱>/<名稱>.md）
INPUT_FILENAME = "1804.03318v2.md"


def parse_args():
    parser = argparse.ArgumentParser(description="PaperAI：Markdown → 中英對照閱讀器")
    parser.add_argument("--input", default=INPUT_FILENAME,
                        help=f"output/<名稱>/ 下的 .md 檔名，或任意 .md 路徑（預設：{INPUT_FILENAME}）")
    parser.add_argument("--model", help=f"Ollama 模型名稱（預設：{config.MODEL_NAME}）")
    parser.add_argument("--limit", type=int, default=0,
                        help="只處理前 N 個區塊，輸出 *_preview_reader.html，用於快速檢查")
    parser.add_argument("--think", action="store_true", help="啟用模型 thinking（較慢）")
    parser.add_argument("--render-only", action="store_true",
                        help="不呼叫模型，直接用既有 knowledge JSON 重新產生 HTML（調整版面時使用）")
    return parser.parse_args()


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}h {seconds % 3600 // 60}m {seconds % 60}s" if seconds >= 3600 \
        else f"{seconds // 60}m {seconds % 60}s"


def main():
    args = parse_args()
    if args.model:
        config.MODEL_NAME = args.model
    if args.think:
        config.THINK = True

    doc = config.Doc(args.input)
    suffix = "_preview" if args.limit > 0 else ""
    knowledge_path = doc.path(f"{suffix}_translation_knowledge.json")
    reader_path = doc.path(f"{suffix}_reader.html")

    if args.render_only:
        import json
        knowledge = json.loads(knowledge_path.read_text(encoding="utf-8"))
        render_reader(knowledge, reader_path, knowledge["metadata"].get("model", config.MODEL_NAME), doc.base_name)
        print(f"已重新產生：{reader_path}")
        return

    print(f"=== PaperAI：{doc.base_name}（模型：{config.MODEL_NAME}） ===")
    markdown_text = doc.load_markdown()

    paper_map = get_paper_map(markdown_text, doc.path("_paper_map.json"))
    auto_terms = generate_glossary(paper_map, markdown_text, doc.path("_glossary.json"))
    glossary = build_glossary(auto_terms, load_user_glossary(), markdown_text)
    print(f"[術語] 共 {len(glossary)} 個（使用者 {sum(g['source'] == 'user' for g in glossary)} 個）")

    blocks = parse_blocks(markdown_text)
    if args.limit > 0:
        blocks = blocks[:args.limit]
        print(f"[預覽] 只處理前 {len(blocks)} 個區塊")

    cache = TranslationCache(doc.path("_translation_cache_v2.json"))
    todo = [b for b in blocks if b["type"] in TRANSLATABLE_TYPES]
    print(f"[翻譯] {len(blocks)} 個區塊，其中 {len(todo)} 個需要翻譯")

    start = time.time()
    model_calls = 0
    for n, block in enumerate(todo, 1):
        called = translate_block(block, paper_map, glossary, cache)
        model_calls += called
        if called or n == len(todo):
            elapsed = time.time() - start
            eta = elapsed / model_calls * (len(todo) - n) if model_calls else 0
            flag = "" if block["aligned"] else "  ⚠ 未逐句對齊"
            print(f"  [{n}/{len(todo)}] {block['id']} {block['type']}，已用 {format_duration(elapsed)}，"
                  f"預估剩餘 {format_duration(eta)}{flag}")
    print(f"[翻譯] 完成：呼叫模型 {model_calls} 次，其餘 {len(todo) - model_calls} 個命中快取")

    unaligned = sum(1 for b in todo if not b["aligned"])
    knowledge = {
        "version": 2,
        "metadata": {
            "title": paper_map.get("title", doc.base_name),
            "author": paper_map.get("author", "Unknown"),
            "model": config.MODEL_NAME,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "preview": args.limit > 0,
            "stats": {"blocks": len(blocks), "translated_blocks": len(todo), "unaligned_blocks": unaligned},
        },
        "paper_map": paper_map,
        "glossary": glossary,
        "blocks": blocks,
    }
    save_json_atomic(knowledge_path, knowledge)
    render_reader(knowledge, reader_path, config.MODEL_NAME, doc.base_name)

    print(f"\n知識 JSON：{knowledge_path}")
    print(f"閱讀器：{reader_path}")
    if unaligned:
        print(f"注意：{unaligned} 個區塊未能逐句對齊（閱讀器中以虛框標示）")


if __name__ == "__main__":
    main()

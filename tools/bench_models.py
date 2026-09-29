"""比較 Ollama 模型的翻譯速度、對齊成功率與品質。

從文件挑幾個內文段落，用與 translate.py 相同的逐句翻譯流程（translate_sentences），
輸出速度統計與並排對照報告（output/<名稱>/bench/）。不讀寫翻譯快取。

範例：
python tools/bench_models.py --input "1804.03318v2.md"
python tools/bench_models.py --input "1804.03318v2.md" --models gemma4:latest qwythos:latest --paragraphs 5
python tools/bench_models.py --input "1804.03318v2.md" --models qwythos:latest --think both
"""
import argparse
import html
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ollama  # noqa: E402

from paperai import config  # noqa: E402
from paperai.blocks import parse_blocks  # noqa: E402
from paperai.glossary import build_glossary, find_matching_terms, load_user_glossary  # noqa: E402
from paperai.sentences import split_en_sentences  # noqa: E402
from paperai.translator import translate_sentences  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


def pick_paragraphs(markdown_text: str, count: int) -> list:
    """挑選長度適中、以文字為主的段落，平均分布於全文。"""
    candidates = [
        b["items"][0] for b in parse_blocks(markdown_text)
        if b["type"] == "paragraph" and 300 <= len(b["items"][0]) <= 1500
        # 排除作者名單、參考文獻等：需有足夠的一般英文單字與句子
        and len(re.findall(r"\b[a-z]{3,}\b", b["items"][0])) >= 40 and b["items"][0].count(". ") >= 2
    ]
    if len(candidates) <= count:
        return candidates
    step = len(candidates) / count
    return [candidates[int(i * step)] for i in range(count)]


def load_json(path, fallback):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return fallback


def gpu_share(model: str) -> str:
    """回報模型載入後在 GPU 上的比例；低於 100% 代表部分跑在 CPU，速度會明顯下降。"""
    try:
        for m in ollama.ps().models:
            if m.model == model or m.name == model:
                if not m.size:
                    return "?"
                return f"{m.size_vram / m.size:.0%} GPU ({m.size / 1024**3:.1f} GB)"
    except Exception as e:
        return f"無法取得（{e}）"
    return "未載入"


def main():
    parser = argparse.ArgumentParser(description="比較 Ollama 模型翻譯速度與品質")
    parser.add_argument("--input", required=True, help="output/<名稱>/ 下的 .md 檔名")
    parser.add_argument("--models", nargs="+", default=[config.MODEL_NAME])
    parser.add_argument("--paragraphs", type=int, default=3)
    parser.add_argument("--think", choices=["off", "on", "both"], default="off")
    args = parser.parse_args()

    doc = config.Doc(args.input)
    markdown_text = doc.load_markdown()
    paragraphs = pick_paragraphs(markdown_text, args.paragraphs)
    if not paragraphs:
        sys.exit("找不到合適的測試段落。")

    # 沿用已生成的 paper_map／術語表，讓測試條件與正式流程一致
    paper_map = load_json(doc.path("_paper_map.json"), {"title": doc.base_name, "domain": "Unknown"})
    auto_terms = load_json(doc.path("_glossary.json"), {"glossary": []}).get("glossary", [])
    glossary = build_glossary(auto_terms, load_user_glossary(), markdown_text)
    cases = [(split_en_sentences(p), find_matching_terms(p, glossary)) for p in paragraphs]

    think_modes = {"off": [False], "on": [True], "both": [False, True]}[args.think]
    results = {}

    for model in args.models:
        for think in think_modes:
            config.THINK = think
            label = f"{model} (think {'on' if think else 'off'})"
            print(f"\n=== {label} ===")
            load_start = time.time()
            try:
                ollama.generate(model=model, prompt="hi", options={"num_ctx": config.NUM_CTX, "num_predict": 1})
            except Exception as e:
                print(f"    無法載入：{e}")
                continue
            load_s = time.time() - load_start
            share = gpu_share(model)
            print(f"    載入 {load_s:.1f}s | {share}")
            rows = []
            for i, (sentences, terms) in enumerate(cases, 1):
                start = time.time()
                r = translate_sentences(sentences, paper_map, terms, model)
                seconds = time.time() - start
                tps = r.tokens / r.eval_seconds if r.eval_seconds else 0
                print(f"    段落 {i}: {seconds:.1f}s, {tps:.1f} tok/s, {'對齊' if r.aligned else '未對齊'}")
                rows.append({"seconds": seconds, "tok_per_s": tps, "aligned": r.aligned, "zh": r.zh})
            results[label] = {"load_s": load_s, "gpu": share, "rows": rows}

    if not results:
        sys.exit("沒有模型成功執行。")

    print("\n" + "=" * 80)
    print(f"{'模型':<36}{'GPU':<18}{'平均秒/段':>9}{'tok/s':>9}{'對齊':>8}")
    for label, r in results.items():
        n = len(r["rows"])
        avg_s = sum(x["seconds"] for x in r["rows"]) / n
        avg_t = sum(x["tok_per_s"] for x in r["rows"]) / n
        aligned = sum(x["aligned"] for x in r["rows"])
        print(f"{label:<36}{r['gpu']:<18}{avg_s:>9.1f}{avg_t:>9.1f}{f'{aligned}/{n}':>8}")

    # 並排對照報告：每個句子一列
    bench_dir = os.path.join(doc.output_dir, "bench")
    os.makedirs(bench_dir, exist_ok=True)
    report_path = os.path.join(bench_dir, f"bench_{time.strftime('%Y%m%d_%H%M%S')}.html")
    labels = list(results)
    head = "".join(f"<th>{html.escape(l)}<br><small>{html.escape(results[l]['gpu'])}</small></th>" for l in labels)
    body = ""
    for i, (sentences, _) in enumerate(cases):
        meta = "".join(
            f"<td class=meta>{results[l]['rows'][i]['seconds']:.1f}s · "
            f"{'對齊' if results[l]['rows'][i]['aligned'] else '未對齊（整段）'}</td>" for l in labels)
        body += f"<tr class=para><td class=meta>段落 {i + 1}</td>{meta}</tr>"
        for j, en in enumerate(sentences):
            cells = ""
            for l in labels:
                row = results[l]["rows"][i]
                zh = row["zh"][j] if row["aligned"] else (row["zh"][0] if j == 0 else "")
                cells += f"<td>{html.escape(zh)}</td>"
            body += f"<tr><td>{html.escape(en)}</td>{cells}</tr>"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>模型比較 - {html.escape(doc.base_name)}</title>
<style>body{{font-family:system-ui,sans-serif;margin:16px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:6px 8px;vertical-align:top;font-size:14px}}
th{{background:#f3f4f6;position:sticky;top:0}}.meta{{color:#666;font-size:12px;background:#fafafa}}</style>
</head><body><h2>模型翻譯比較：{html.escape(doc.base_name)}</h2>
<table><tr><th>原文</th>{head}</tr>{body}</table></body></html>""")
    print(f"對照報告：{report_path}")


if __name__ == "__main__":
    main()

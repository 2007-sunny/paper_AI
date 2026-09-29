"""比較 Ollama 模型的翻譯速度與品質。

從指定文件挑幾個代表性段落，用與 translate.py 相同的提示詞逐一翻譯，
輸出速度統計與並排對照報告（output/<名稱>/bench/）。

範例：
python tools/bench_models.py --input "J.J.Thomson-1917_Thomson-Parabola.md"
python tools/bench_models.py --input "1804.03318v2.md" --models qwythos:latest gemma3:4b --paragraphs 5
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
import translate as tr  # noqa: E402


def pick_paragraphs(markdown_text: str, count: int) -> list:
    """挑選長度適中、以文字為主的段落，平均分布於全文。"""
    candidates = [
        p for p in tr.split_paragraphs(markdown_text)
        if 300 <= len(p) <= 1500 and not re.match(r"^\s*(!\[|\||\$\$|#|```)", p)
        # 排除作者名單、參考文獻等：需有足夠的一般英文單字與句子
        and len(re.findall(r"\b[a-z]{3,}\b", p)) >= 40 and p.count(". ") >= 2
    ]
    if len(candidates) <= count:
        return candidates
    step = len(candidates) / count
    return [candidates[int(i * step)] for i in range(count)]


def load_json(path: str, fallback: dict) -> dict:
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


def run_one(model: str, prompt: str, think: bool) -> dict:
    start = time.time()
    resp = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        think=think,
        options={"num_ctx": tr.NUM_CTX, "num_predict": tr.NUM_PREDICT},
    )
    wall = time.time() - start
    eval_count = resp.get("eval_count") or 0
    eval_ns = resp.get("eval_duration") or 0
    text = re.sub(r"<think>[\s\S]*?</think>", "", resp["message"]["content"]).strip()
    return {
        "seconds": wall,
        "tokens": eval_count,
        "tok_per_s": eval_count / (eval_ns / 1e9) if eval_ns else 0,
        "text": text,
    }


def main():
    parser = argparse.ArgumentParser(description="比較 Ollama 模型翻譯速度與品質")
    parser.add_argument("--input", required=True, help="output/<名稱>/ 下的 .md 檔名")
    parser.add_argument("--models", nargs="+", default=[tr.MODEL_NAME])
    parser.add_argument("--paragraphs", type=int, default=3)
    parser.add_argument("--think", choices=["off", "on", "both"], default="off")
    args = parser.parse_args()

    tr.configure(args.input)
    markdown_text = tr.load_markdown(tr.MD_PATH, tr.FALLBACK_MD_PATH)
    paragraphs = pick_paragraphs(markdown_text, args.paragraphs)
    if not paragraphs:
        sys.exit("找不到合適的測試段落。")

    # 沿用已生成的 paper_map／術語表，讓測試條件與正式流程一致
    paper_map = load_json(os.path.join(tr.OUTPUT_DIR, f"{tr.BASE_NAME}_paper_map.json"),
                          {"title": tr.BASE_NAME, "domain": "Unknown"})
    glossary = load_json(os.path.join(tr.OUTPUT_DIR, f"{tr.BASE_NAME}_glossary.json"),
                         {"glossary": []})
    prompts = [tr.build_translation_prompt(p, paper_map, glossary) for p in paragraphs]

    think_modes = {"off": [False], "on": [True], "both": [False, True]}[args.think]
    runs = [(m, t) for m in args.models for t in think_modes]
    results = {}

    for model, think in runs:
        label = f"{model} (think {'on' if think else 'off'})"
        print(f"\n=== {label} ===")
        load_start = time.time()
        try:
            ollama.generate(model=model, prompt="hi", options={"num_ctx": tr.NUM_CTX, "num_predict": 1})
        except Exception as e:
            print(f"    無法載入：{e}")
            continue
        load_s = time.time() - load_start
        share = gpu_share(model)
        print(f"    載入 {load_s:.1f}s | {share}")
        rows = []
        for i, prompt in enumerate(prompts, 1):
            r = run_one(model, prompt, think)
            print(f"    段落 {i}: {r['seconds']:.1f}s, {r['tok_per_s']:.1f} tok/s")
            rows.append(r)
        results[label] = {"load_s": load_s, "gpu": share, "rows": rows}

    if not results:
        sys.exit("沒有模型成功執行。")

    # 摘要表
    print("\n" + "=" * 72)
    print(f"{'模型':<36}{'GPU':<18}{'平均秒/段':>9}{'tok/s':>9}")
    for label, r in results.items():
        avg_s = sum(x["seconds"] for x in r["rows"]) / len(r["rows"])
        avg_t = sum(x["tok_per_s"] for x in r["rows"]) / len(r["rows"])
        print(f"{label:<36}{r['gpu']:<18}{avg_s:>9.1f}{avg_t:>9.1f}")
    total = len(tr.split_paragraphs(markdown_text))
    print(f"\n全文共 {total} 段；以最快模型估計全文約需 "
          f"{min(sum(x['seconds'] for x in r['rows']) / len(r['rows']) for r in results.values()) * total / 60:.0f} 分鐘。")

    # 並排對照報告
    bench_dir = os.path.join(tr.OUTPUT_DIR, "bench")
    os.makedirs(bench_dir, exist_ok=True)
    report_path = os.path.join(bench_dir, f"bench_{time.strftime('%Y%m%d_%H%M%S')}.html")
    labels = list(results)
    head = "".join(f"<th>{html.escape(l)}<br><small>{html.escape(results[l]['gpu'])}</small></th>" for l in labels)
    body = ""
    for i, p in enumerate(paragraphs):
        cells = "".join(
            f"<td>{html.escape(results[l]['rows'][i]['text'])}"
            f"<div class=meta>{results[l]['rows'][i]['seconds']:.1f}s · "
            f"{results[l]['rows'][i]['tok_per_s']:.1f} tok/s</div></td>"
            for l in labels
        )
        body += f"<tr><td>{html.escape(p)}</td>{cells}</tr>"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">
<title>模型比較 - {html.escape(tr.BASE_NAME)}</title>
<style>body{{font-family:system-ui,sans-serif;margin:16px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:8px;vertical-align:top;white-space:pre-wrap;font-size:14px}}
th{{background:#f3f4f6;position:sticky;top:0}}.meta{{color:#888;font-size:12px;margin-top:6px}}</style>
</head><body><h2>模型翻譯比較：{html.escape(tr.BASE_NAME)}</h2>
<table><tr><th>原文</th>{head}</tr>{body}</table></body></html>""")
    print(f"對照報告：{report_path}")


if __name__ == "__main__":
    main()

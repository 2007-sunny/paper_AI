"""knowledge JSON → 單一 HTML 閱讀器。

每個區塊只輸出一份 DOM，檢視模式（對照／交錯／純英／純中）由 CSS 切換。
模板在 paperai/templates/，CSS 與 JS 於生成時內嵌，產出的 HTML 可直接雙擊開啟。
"""
import html
import json
import re
from pathlib import Path

from markdown_it import MarkdownIt

from .glossary import term_pattern

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_md = MarkdownIt("commonmark", {"html": True}).enable("table")
_MATH_RE = re.compile(r"\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\$[^$\n]+?\$")
_MATH_TOKEN = "QQQLATEXBLOCKZZZ{}QQQ"
_TAG_SPLIT_RE = re.compile(r"(<[^>]+>)")


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _protect_math(text: str):
    blocks = []

    def repl(m):
        blocks.append(m.group(0))
        return _MATH_TOKEN.format(len(blocks) - 1)

    return _MATH_RE.sub(repl, text), blocks


def _restore_math(rendered: str, blocks: list) -> str:
    for idx, latex in enumerate(blocks):
        # 模型常在公式內寫出未跳脫的 %（如 \text{ %}），在 TeX 中是註解，會讓整個公式失效
        latex = re.sub(r"(?<!\\)%", r"\\%", latex)
        # LaTeX 需跳脫 < > &，MathJax 讀取的是文字內容，不受影響
        rendered = rendered.replace(_MATH_TOKEN.format(idx), html.escape(latex, quote=False))
    return rendered


def _highlighter(terms: list, glossary: dict, lang: str):
    """建立術語標示函式；單一 regex 一次比對，避免短術語插進長術語的標籤裡。"""
    items = [glossary[t.lower()] for t in terms if t.lower() in glossary]
    if not items:
        return None
    if lang == "en":
        pattern = re.compile("|".join(f"(?:{term_pattern(g['term']).pattern})" for g in items), re.IGNORECASE)
        lookup = lambda m: next((g for g in items if term_pattern(g["term"]).fullmatch(m.group(0))), None)
    else:
        pattern = re.compile("|".join(re.escape(g["translation"]) for g in
                                      sorted(items, key=lambda g: len(g["translation"]), reverse=True)))
        lookup = lambda m: next((g for g in items if g["translation"] == m.group(0)), None)

    def wrap(m):
        g = lookup(m)
        if not g:
            return m.group(0)
        return (f'<span class="term" data-term="{_esc(g["term"])}" data-translation="{_esc(g["translation"])}" '
                f'data-category="{_esc(g.get("category", "general"))}">{m.group(0)}</span>')

    def apply(rendered_html: str) -> str:
        parts = _TAG_SPLIT_RE.split(rendered_html)
        return "".join(p if p.startswith("<") else pattern.sub(wrap, p) for p in parts)

    return apply


def render_inline(text: str, highlight=None) -> str:
    protected, blocks = _protect_math(text)
    rendered = _md.renderInline(protected)
    if highlight:
        rendered = highlight(rendered)
    return _restore_math(rendered, blocks)


def render_markdown(text: str) -> str:
    protected, blocks = _protect_math(text)
    return _restore_math(_md.render(protected), blocks)


def _sentence_spans(block: dict, unit: list, lang: str, highlight) -> str:
    spans = []
    for s in unit:
        inner = render_inline(s[lang], highlight) if s[lang] else ""
        spans.append(f'<span class="sentence" data-block="{block["id"]}" data-sid="{s["id"]}" '
                     f'data-lang="{lang}">{inner}</span>')
    return (" " if lang == "en" else "").join(spans)


def _render_lang(block: dict, lang: str, highlight) -> str:
    kind = block["type"]
    if kind == "heading":
        level = min(block.get("level", 1) + 1, 6)
        return f"<h{level}>{_sentence_spans(block, block['units'][0], lang, highlight)}</h{level}>"
    if kind == "list" and block.get("aligned", True):
        tag = "ol" if block.get("ordered") else "ul"
        start = f' start="{block["start"]}"' if block.get("ordered") and block.get("start", 1) != 1 else ""
        items = "".join(f"<li>{_sentence_spans(block, u, lang, highlight)}</li>" for u in block["units"])
        return f"<{tag}{start}>{items}</{tag}>"
    return "".join(f"<p>{_sentence_spans(block, u, lang, highlight)}</p>" for u in block["units"])


def render_block(block: dict, glossary: dict) -> str:
    kind, bid = block["type"], block["id"]
    if "units" in block:
        terms = block.get("terms", [])
        classes = f"block pair block-{kind}" + ("" if block.get("aligned", True) else " unaligned")
        title = "" if block.get("aligned", True) else ' title="此段未能逐句對齊，顯示整段譯文"'
        return (f'<section class="{classes}" id="{bid}"{title}>'
                f'<div class="lang en" lang="en">{_render_lang(block, "en", _highlighter(terms, glossary, "en"))}</div>'
                f'<div class="lang zh" lang="zh-Hant">{_render_lang(block, "zh", _highlighter(terms, glossary, "zh"))}</div>'
                f"</section>")

    src = block.get("source", "")
    if kind == "math":
        m = re.match(r"(\$\$[\s\S]*?\$\$)\s*(.*)$", src, re.DOTALL)
        latex, tag = (m.group(1), m.group(2).strip()) if m else (src, "")
        tag_html = f'<span class="eq-num">{_esc(tag)}</span>' if tag else ""
        body = f'<div class="math-body">{html.escape(latex, quote=False)}</div>{tag_html}'
    elif kind == "image":
        body = f"<figure>{render_inline(src)}</figure>"
    elif kind == "table":
        body = f'<div class="table-wrap">{render_markdown(src)}</div>'
    elif kind == "code":
        body = f"<pre><code>{html.escape(src)}</code></pre>"
    else:  # html
        body = src
    return f'<section class="block full block-{kind}" id="{bid}">{body}</section>'


def render_reader(knowledge: dict, output_path, model_name: str, base_name: str) -> None:
    glossary_list = knowledge.get("glossary", [])
    glossary = {g["term"].lower(): g for g in glossary_list}
    paper_map = knowledge.get("paper_map", {})
    meta = knowledge.get("metadata", {})

    content = "\n".join(render_block(b, glossary) for b in knowledge.get("blocks", []))
    glossary_rows = "\n".join(
        f'<tr><td><strong>{_esc(g["term"])}</strong></td><td>{_esc(g["translation"])}</td>'
        f'<td><span class="badge">{_esc(g.get("category", "general"))}</span></td>'
        f'<td>{"使用者" if g.get("source") == "user" else "自動"}</td>'
        f'<td><button class="action-btn-sm" data-add-term="{_esc(g["term"])}" '
        f'data-add-translation="{_esc(g["translation"])}" data-add-category="{_esc(g.get("category", "general"))}">'
        f"加入生字本</button></td></tr>"
        for g in glossary_list
    )
    stats = meta.get("stats", {})
    status = f'{stats.get("translated_blocks", 0)} 個區塊'
    if stats.get("unaligned_blocks"):
        status += f'，{stats["unaligned_blocks"]} 個未逐句對齊'
    if meta.get("preview"):
        status += "（預覽：僅部分內容）"

    page_config = {"baseName": base_name, "defaultModel": model_name,
                   "apiUrl": "http://127.0.0.1:8000/api/explain"}
    replacements = {
        "__TITLE__": _esc(meta.get("title") or base_name),
        "__DOMAIN__": _esc(paper_map.get("domain", "")),
        "__TOPICS__": _esc("、".join(paper_map.get("topics", []))),
        "__AUTHOR__": _esc(meta.get("author", "Unknown")),
        "__SUMMARY__": _esc(paper_map.get("summary", "")),
        "__MODEL__": _esc(meta.get("model", model_name)),
        "__STATUS__": _esc(status),
        "__CONTENT__": content,
        "__GLOSSARY_ROWS__": glossary_rows,
        "__CSS__": (TEMPLATE_DIR / "reader.css").read_text(encoding="utf-8"),
        "__JS__": (TEMPLATE_DIR / "reader.js").read_text(encoding="utf-8"),
        # 防止 JSON 內容提前結束 <script>
        "__CONFIG__": json.dumps(page_config, ensure_ascii=False).replace("</", "<\\/"),
    }
    page = (TEMPLATE_DIR / "reader.html").read_text(encoding="utf-8")
    # 依序以單次掃描取代，避免內容中剛好出現 __XXX__ 字樣被二次取代
    page = re.sub("|".join(map(re.escape, replacements)), lambda m: replacements[m.group(0)], page)
    Path(output_path).write_text(page, encoding="utf-8")

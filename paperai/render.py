"""Markdown → HTML（細翻譯文與 Claude 回答），公式留給 MathJax。"""
import html
import re

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": True}).enable("table")
_MATH_RE = re.compile(r"\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\$[^$\n]+?\$")
_MATH_TOKEN = "QQQLATEXBLOCKZZZ{}QQQ"


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


def render_markdown(text: str) -> str:
    protected, blocks = _protect_math(text)
    return _restore_math(_md.render(protected), blocks)

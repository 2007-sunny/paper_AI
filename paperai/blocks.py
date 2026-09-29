"""把 Marker 產生的 Markdown 切成區塊。

只有 heading / paragraph / list 需要翻譯（items 為待翻譯的文字單位）；
table / math / image / code / html 原樣保留在 source，渲染時整塊輸出。
"""
import re

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark").enable("table")

TRANSLATABLE_TYPES = {"heading", "paragraph", "list"}

# Marker 在標題與段落前插入的頁面錨點，閱讀器用不到
_ANCHOR_RE = re.compile(r'<span id="[^"]*"></span>')
_IMAGE_ONLY_RE = re.compile(r"^!\[[^\]]*\]\([^)]*\)$")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def _source(lines: list, token) -> str:
    return "\n".join(lines[token.map[0]:token.map[1]]).strip()


def _join_lines(text: str) -> str:
    return re.sub(r"\s*\n\s*", " ", text).strip()


def _list_items(tokens: list, start: int, lines: list) -> list:
    """收集清單第一層項目的文字（去掉項目符號）；巢狀清單併入所屬項目。"""
    items = []
    for tok in tokens[start + 1:]:
        if tok.level == 0:  # 清單結束
            break
        if tok.type == "list_item_open" and tok.level == 1 and tok.map:
            raw = _source(lines, tok)
            text = " ".join(_LIST_MARKER_RE.sub("", ln) for ln in raw.split("\n"))
            if text.strip():
                items.append(_join_lines(text))
    return items


def parse_blocks(markdown_text: str) -> list:
    text = _ANCHOR_RE.sub("", markdown_text)
    lines = text.split("\n")
    tokens = _md.parse(text)
    blocks = []

    for i, tok in enumerate(tokens):
        if tok.level != 0 or tok.map is None:
            continue
        src = _source(lines, tok)
        if not src:
            continue
        kind = tok.type

        if kind == "heading_open":
            title = re.sub(r"^#{1,6}\s*", "", src.split("\n")[0]).strip()
            if title:
                blocks.append({"type": "heading", "level": int(tok.tag[1]), "items": [title]})
        elif kind in ("paragraph_open", "blockquote_open"):
            if kind == "blockquote_open":
                src = re.sub(r"^\s*>\s?", "", src, flags=re.MULTILINE)
            if src.startswith("$$"):
                blocks.append({"type": "math", "source": src})
            elif _IMAGE_ONLY_RE.match(src):
                blocks.append({"type": "image", "source": src})
            else:
                blocks.append({"type": "paragraph", "items": [_join_lines(src)]})
        elif kind in ("bullet_list_open", "ordered_list_open"):
            items = _list_items(tokens, i, lines)
            if items:
                block = {"type": "list", "ordered": kind == "ordered_list_open", "items": items}
                if block["ordered"]:
                    block["start"] = int(tok.attrGet("start") or 1)
                blocks.append(block)
        elif kind == "table_open":
            blocks.append({"type": "table", "source": src})
        elif kind in ("fence", "code_block"):
            blocks.append({"type": "code", "source": tok.content})
        elif kind == "html_block":
            blocks.append({"type": "html", "source": src})
        # hr 等其他區塊略過

    for n, block in enumerate(blocks, 1):
        block["id"] = f"b{n}"
    return blocks

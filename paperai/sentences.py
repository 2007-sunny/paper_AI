"""英文斷句。

以 placeholder（QQQSPLITBLOCKZZZ、___EG___ 等）暫時保護 LaTeX、Markdown 與縮寫，
再交給 spaCy 斷句，最後還原。這些 placeholder 不得移除。
"""
import re
import subprocess
import sys

_nlp = None


def _get_nlp():
    """延遲載入 spaCy，避免只 import 模組就花時間載入模型。"""
    global _nlp
    if _nlp is None:
        import spacy
        try:
            _nlp = spacy.load("en_core_web_sm")
        except OSError:
            print("Warning: en_core_web_sm not found. Downloading...")
            subprocess.run([sys.executable, "-m", "spacy", "download", "en_core_web_sm"], check=True)
            _nlp = spacy.load("en_core_web_sm")
    return _nlp


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
    # 7. (<(sup|sub)>...</sup|sub>) -> Marker 輸出的上下標，避免 spaCy 在標籤附近斷句
    pattern = r"(\$\$[\s\S]*?\$\$) | (\\\[[\s\S]*?\\\]) | (\$[^\$\n]+?\$) | (!\[[^\]]*?\]\([^ \)]+?\)) | (\*\*[^\*\n]+?\*\*) | (\*[^\*\n]+?\*) | (<(?:sup|sub)>[^<]*</(?:sup|sub)>)"

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
        doc = _get_nlp()(temp_text)
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
    return _merge_after_abbreviations(_protect_and_split(text, _en_core))


_ABBR_END = re.compile(r"(?:\b(?:" + "|".join(re.escape(a) for a in ABBREVIATIONS) + r"))$", re.IGNORECASE)


def _merge_after_abbreviations(sentences: list) -> list:
    """spaCy 仍可能在縮寫後斷句（如 "e.g." 後），此時併回下一句；並移除標點前多餘空白。"""
    merged = []
    for sent in sentences:
        sent = re.sub(r"\s+([.,;:!?])(?=\s|$)", r"\1", sent)
        if merged and _ABBR_END.search(merged[-1]):
            merged[-1] = f"{merged[-1]} {sent}"
        else:
            merged.append(sent)
    return merged

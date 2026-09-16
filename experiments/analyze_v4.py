# -*- coding: utf-8 -*-
"""
論文自動分析流水線 v4（11-Pass 多代理整合版）
------------------------------------------------
v4 設計：
結合 Claude 的架構（有上下文控制、Cross-Link 關聯地圖、安全 JSON 解析）
以及 Gemini 的 Prompt 風格（簡潔實用，Reviewer/Director 更穩定），並加入兩個歷史與教學新角色：
  - Supervisor      [Pass 0] 負責看全文預覽，建立「論文知識地圖」(Paper Knowledge Map)
  - Chunk Analyzer  [Pass 5] 深度展開學習任務
  - Reviewer        [Pass 6] (簡化版) 挑出每個區塊的「新貢獻」與「限制」，提高本地小模型穩定度
  - Cross-Link Builder [Pass 7] 建立區塊之間的內部邏輯關聯，形成 Knowledge Graph
  - Historian       [Pass 8] (新角色) 梳理論文在科學史中的位置、前人工作、里程碑突破與後續影響，生成演進脈絡
  - Teacher         [Pass 9] (新角色) 專為大二學生設計，指出最難懂的 3 件事與必備的先備知識
  - Director & Editor [Pass 10] (合併角色) 重新組織全文，依「背景 -> 問題 -> 突破 -> 證據 -> 影響」大綱一次性寫成並潤飾好深度導讀文章。

十一個 Pass：
  Pass 0：Supervisor   → 建立 Paper Knowledge Map（全局地圖）
  Pass 1：Heading Split → 依標題做原始切割
  Pass 2：Merge        → 太短的區塊自動合併
  Pass 3：Long Split    → 太長的區塊依段落邊界二次切分
  Pass 4：Task Generator → 每個區塊（帶著全局地圖）自己決定要幾個任務
  Pass 5：Chunk Analyzer → 逐任務深度展開（帶著全局地圖）
  Pass 6：Reviewer      → 審查區塊的 新貢獻 與 限制（簡化且穩定的 Prompt）
  Pass 7：Cross-Link Builder → 綜合區塊摘要，找出區塊之間的邏輯關聯
  Pass 8：Historian      → 生成科學史定位與歷史演進脈絡（新角色）
  Pass 9：Teacher        → 生成大二物理教學指南（先備知識與難點拆解，新角色）
  Pass 10：Director (兼 Editor) → 熔煉所有素材，直接寫成並潤飾好完整深度導讀文章
"""

import os
import re
import json
import sys
import ollama

# 解決 Windows 主控台 cp950 編碼問題，強制使用 utf-8 輸出
sys.stdout.reconfigure(encoding='utf-8')

# ============== 可調參數區 ==============

MERGE_THRESHOLD_CHARS = 150       # 區塊字數低於這個門檻，就自動跟下一塊合併
SPLIT_THRESHOLD_CHARS = 5000      # 區塊字數超過這個門檻，就依段落邊界二次切分
models = ["ollama/qwythos:latest"]

MODEL_NAME = models[0]

SUPERVISOR_PREVIEW_CHARS = 600    # 餵給 Supervisor 看每個區塊時，只取前 N 字當預覽
DIRECTOR_BLOCK_CHARS = 1200       # 餵給 Director 時，每個區塊的「展開分析」最多截取的字數
CROSSLINK_SUMMARY_CHARS = 400     # 餵給 Cross-Link Builder 時，每個區塊摘要的最多字數

# ============== 檔案路徑設定 ==============

filename = "J.J.Thomson-1917_Thomson-Parabola.md"
base_name = filename.replace(".md", "")
md_path = os.path.join(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"), base_name, filename
)


def load_markdown(path: str, fallback_filename: str) -> str:
    """讀取 Marker 轉換好的 Markdown 全文。"""
    print("正在讀取 Marker 轉換好的 Markdown 論文...")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        with open(fallback_filename, "r", encoding="utf-8") as f:
            return f.read()


def call_ai(prompt: str, model_name: str = MODEL_NAME) -> str:
    """統一包裝 ollama.chat 呼叫，方便之後要換 retry / log 機制時只改一個地方。"""
    response = ollama.chat(model=model_name, messages=[
        {'role': 'user', 'content': prompt}
    ])
    return response['message']['content']


def safe_json_parse(raw_text: str, fallback: dict) -> dict:
    """
    把 AI 回傳的文字嘗試解析成 JSON。
    """
    text = raw_text.strip()
    text = re.sub(r"^```json", "", text.strip(), flags=re.IGNORECASE).strip()
    text = re.sub(r"^```", "", text.strip()).strip()
    text = re.sub(r"```$", "", text.strip()).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        print("    [警告] 回傳內容找不到合法 JSON，使用預設值代替。")
        return fallback

    candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        print("    [警告] JSON 解析失敗，使用預設值代替。")
        return fallback


# ============== Pass 0：Supervisor，建立 Paper Knowledge Map ==============

def build_paper_map(full_text: str, raw_blocks: list, model_name: str = MODEL_NAME) -> dict:
    """
    Supervisor 建立全局知識地圖，使用標題＋首尾簡短預覽。
    """
    print("[Pass 0] Supervisor 正在建立 Paper Knowledge Map...")

    preview_sections = []
    for b in raw_blocks:
        preview = b["content"][:SUPERVISOR_PREVIEW_CHARS]
        preview_sections.append(f"### {b['title']}\n{preview}")
    condensed_text = "\n\n".join(preview_sections)

    prompt = f"""
你是一位資深的科學史與物理學論文審稿人，現在擔任 Supervisor 角色。
你的工作不是解釋細節，而是「看完整篇論文的標題與摘要」後，建立一份全局知識地圖。

請仔細閱讀以下這份論文的「各章節標題與內容預覽」，然後輸出一份 JSON，格式如下，不要有任何其他文字、不要用 Markdown 程式碼框包住：

{{
  "paper_topic": "這篇論文的主題（一句話）",
  "main_goal": "這篇論文要解決的核心問題或要驗證的核心假說",
  "method_summary": "整體研究方法的一句話摘要",
  "key_conclusion": "整篇論文最重要的結論",
  "sections": [
    {{
      "title": "與輸入的章節標題完全一致",
      "role": "background | method | evidence | interpretation | conclusion | transition 其中一種"
    }}
  ]
}}

【論文章節標題與內容預覽開始】
{condensed_text}
【論文章節標題與內容預覽結束】
"""
    raw_output = call_ai(prompt, model_name)
    fallback = {
        "paper_topic": "未知（Supervisor 解析失敗）",
        "main_goal": "未知",
        "method_summary": "未知",
        "key_conclusion": "未知",
        "sections": [{"title": b["title"], "role": "unknown"} for b in raw_blocks],
    }
    paper_map = safe_json_parse(raw_output, fallback)
    print(f"    Supervisor 判斷主題為：{paper_map.get('paper_topic', '未知')}")
    return paper_map


def paper_map_to_prompt_text(paper_map: dict) -> str:
    """把 paper_map 這個 dict 轉成一段精簡的文字，方便塞進其他 Agent 的 prompt 裡。"""
    sections_text = "\n".join(
        f"  - {s.get('title', '?')}（角色：{s.get('role', '?')}）"
        for s in paper_map.get("sections", [])
    )
    return f"""論文主題：{paper_map.get('paper_topic', '未知')}
核心問題：{paper_map.get('main_goal', '未知')}
研究方法摘要：{paper_map.get('method_summary', '未知')}
核心結論：{paper_map.get('key_conclusion', '未知')}
章節角色分布：
{sections_text}"""


# ============== Pass 1：依標題做原始切割 ==============

def split_by_headings(full_text: str):
    """把全文依任何 '#' 開頭的行切成區塊。"""
    lines = full_text.split("\n")
    blocks = []
    current_title = "（文件開頭，無標題段落）"
    current_content_lines = []

    heading_pattern = re.compile(r"^#{1,6}\s+(.*)")

    for line in lines:
        match = heading_pattern.match(line.strip())
        if match:
            if current_content_lines:
                blocks.append({
                    "title": current_title,
                    "content": "\n".join(current_content_lines).strip()
                })
            current_title = match.group(1).strip()
            current_content_lines = []
        else:
            current_content_lines.append(line)

    if current_content_lines:
        blocks.append({
            "title": current_title,
            "content": "\n".join(current_content_lines).strip()
        })

    blocks = [b for b in blocks if len(b["content"]) > 0]
    return blocks


# ============== Pass 2：太短的區塊自動合併 ==============

def merge_short_blocks(blocks, threshold=MERGE_THRESHOLD_CHARS):
    """區塊字數低於 threshold，就跟下一個區塊合併。"""
    if not blocks:
        return blocks

    merged = []
    buffer = None

    for block in blocks:
        if buffer is None:
            buffer = block
        else:
            buffer["content"] = buffer["content"] + "\n\n" + block["content"]
            buffer["title"] = f"{buffer['title']}、{block['title']}"

        if len(buffer["content"]) >= threshold:
            merged.append(buffer)
            buffer = None

    if buffer is not None:
        if merged:
            merged[-1]["content"] += "\n\n" + buffer["content"]
            merged[-1]["title"] += f"、{buffer['title']}"
        else:
            merged.append(buffer)

    return merged


# ============== Pass 3：太長的區塊依段落邊界二次切分 ==============

def split_long_block(block, threshold=SPLIT_THRESHOLD_CHARS):
    """超過 threshold 字數的區塊，依空行切成子區塊。"""
    content = block["content"]
    if len(content) <= threshold:
        return [block]

    paragraphs = re.split(r"\n\s*\n", content)
    sub_blocks = []
    current_chunk = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if current_len + para_len > threshold and current_chunk:
            sub_blocks.append("\n\n".join(current_chunk))
            current_chunk = []
            current_len = 0
        current_chunk.append(para)
        current_len += para_len

    if current_chunk:
        sub_blocks.append("\n\n".join(current_chunk))

    total = len(sub_blocks)
    return [
        {"title": f"{block['title']} (Part {i+1}/{total})", "content": chunk}
        for i, chunk in enumerate(sub_blocks)
    ]


# ============== Pass 4：每個區塊（帶著全局地圖）決定任務數量 ==============

def generate_tasks_for_block(block, paper_map_text: str, model_name: str = MODEL_NAME):
    """判斷內容密度並決定 1~3 個學習任務。"""
    prompt = f"""
你是一位嚴謹的物理與科學學術審稿人與系統架構師。

【論文全局資訊】
{paper_map_text}

請閱讀以下論文的其中一個段落（標題：{block['title']}），並參考上方的全局資訊判斷這段在全文中的角色。

【任務目標】：
請先判斷這個段落的「內容密度」，再決定要產出幾個學習任務：
- 內容單薄（過渡句、純粹章節銜接）→ 只給 1 個任務。
- 內容中等豐富（背景說明或單一概念）→ 給 2 個任務。
- 內容資訊量大（核心公式、關鍵實驗設計、重要創新點）→ 給 3 個任務。
- 上限 3 個，不要為了湊數硬生不必要的任務。

每個任務必須是以下其中一種類型：
1. 背景／核心概念導讀
2. 數學公式或物理模型推導解析（若無公式則為實驗核心觀念解析）
3. 專業生字精準翻譯與物理語境解析（列出 1~3 個最關鍵的生字）
4. 實驗儀器、架構或圖表解析（僅在段落確實提及時才產出）

請嚴格按照以下格式輸出，不要包含任何多餘的客套話：
---TASK_LIST_START---
- [TASK_1]: ...
- [TASK_2]: ...（如果不需要，整行省略，不要留空）
- [TASK_3]: ...（如果不需要，整行省略，不要留空）
- ---TASK_LIST_END---

【段落內容開始】
{block['content']}
【段落內容結束】
"""
    return call_ai(prompt, model_name)


def parse_tasks(ai_output: str):
    """從 AI 輸出的格式化文字中，把任務逐條抓出來。"""
    lines = ai_output.split("\n")
    tasks = []
    is_task_zone = False

    for line in lines:
        if "---TASK_LIST_START---" in line:
            is_task_zone = True
            continue
        if "---TASK_LIST_END---" in line:
            is_task_zone = False
            continue
        if is_task_zone and line.strip().startswith("-"):
            # 移除前導 "- [TASK_X]: " 等標記
            clean_line = re.sub(r"^-\s*\[TASK_\d+\]\s*:\s*", "", line.strip())
            # 如果還是以 - 開頭則再次清理
            if clean_line.startswith("-"):
                clean_line = clean_line.lstrip("-").strip()
            tasks.append(clean_line)

    return tasks


# ============== Pass 5：Chunk Analyzer，依任務深度展開 ==============

def expand_task(task_text: str, block, paper_map_text: str, model_name: str = MODEL_NAME):
    """結合全局資訊，為大二學生寫出詳細解析。"""
    prompt = f"""
你是一位極為專業且耐心的物理系教授。

【論文全局資訊】
{paper_map_text}

現在請你針對以下這個【指定任務】，結合【段落參考文本】與上方的【論文全局資訊】，
為大二學生寫出一份非常詳細、好懂、且具有學術深度的延伸解析報告。
報告最後請務必加一小段「在全文脈絡中的作用」，說明這個任務／這段內容如何呼應論文的核心問題或結論。
如果是翻譯任務，請給出單字的字根、物理含意與 2 個物理學科的例句。
如果是公式或背景，請詳細展開說明。

【指定任務】：{task_text}

【段落參考文本】：
{block['content']}
"""
    return call_ai(prompt, model_name)


# ============== Pass 6：Reviewer，簡化版（新貢獻／限制） ==============

def review_block(block, paper_map_text: str, model_name: str = MODEL_NAME) -> str:
    """
    Reviewer 站在審稿人角度，專注在「新貢獻」與「限制」，提高本地模型穩定度。
    """
    prompt = f"""
你現在扮演一位嚴謹但公正的 Nature 等級學術審稿人（Reviewer）。

【論文全局資訊】
{paper_map_text}

請閱讀以下這個區塊（標題：{block['title']}），並針對它回答以下兩點，盡量簡潔、條列式，不要重述內容本身：
1. 此段對全文的新貢獻（New Contribution）是什麼？
2. 此段是否存在實驗、理論或論述上的限制（Limitations）？（若沒有，請明確說「無明顯限制」）

【段落內容開始】
{block['content']}
【段落內容結束】
"""
    return call_ai(prompt, model_name)


# ============== Pass 7：Cross-Link Builder，建立關聯 ==============

def build_cross_links(blocks, paper_map_text: str, model_name: str = MODEL_NAME) -> dict:
    """
    找出區塊之間的內部邏輯關聯，並輸出 JSON。
    """
    print("[Pass 7] Cross-Link Builder 正在尋找區塊之間的關聯...")

    summaries = []
    for b in blocks:
        preview = b["content"][:CROSSLINK_SUMMARY_CHARS]
        summaries.append(f"### {b['title']}\n{preview}")
    condensed = "\n\n".join(summaries)

    prompt = f"""
你是一位負責建立論文內部邏輯關聯的分析師（Cross-Link Builder）。

【論文全局資訊】
{paper_map_text}

請閱讀以下各區塊的標題與內容預覽，找出區塊與區塊之間「有意義」的關聯
（例如：呼應前文提出的問題、提供前文假設的證據、修正或推翻前文觀點、延伸前文的方法等）。
不需要把每個區塊兩兩都湊出關聯，只列出真正有意義的關聯即可，沒有就不要硬湊。

請只輸出 JSON，不要有其他文字或 Markdown 程式碼框，格式如下：
{{
  "links": [
    {{"from": "區塊標題A", "to": "區塊標題B", "relation": "一句話說明兩者的關聯"}}
  ]
}}

【各區塊標題與內容預覽開始】
{condensed}
【各區塊標題與內容預覽結束】
"""
    raw_output = call_ai(prompt, model_name)
    cross_links = safe_json_parse(raw_output, {"links": []})
    print(f"    找到 {len(cross_links.get('links', []))} 條區塊關聯")
    return cross_links


def cross_links_to_prompt_text(cross_links: dict) -> str:
    links = cross_links.get("links", [])
    if not links:
        return "（未找到明顯的跨區塊關聯）"
    return "\n".join(
        f"  - 「{l.get('from', '?')}」→「{l.get('to', '?')}」：{l.get('relation', '?')}"
        for l in links
    )


# ============== Pass 8：Historian (新角色) ==============

def run_historian(paper_map_text: str, cross_links_text: str, model_name: str = MODEL_NAME) -> str:
    """
    Historian 負責分析論文在科學史中的位置與脈絡。
    """
    print("[Pass 8] Historian 正在分析科學史脈絡...")
    prompt = f"""
你現在扮演一位博古通今的科學史與物理學史學家（Historian）。
你的任務是分析這篇論文在科學史中的位置，並為讀者梳理其歷史脈絡。

請根據以下【論文全局資訊】與【跨區塊關聯】，回答以下四點：
1. 這篇論文在整個科學發展史（或物理學史）中的位置與座標是什麼？
2. 在這篇論文之前，前人（先驅者們）做了哪些關鍵工作或鋪墊？
3. 這篇論文取得了什麼里程碑式的突破？
4. 這篇論文對後世的科學研究、技術應用或後續理論帶來了什麼深遠的影響？

最後，請梳理並輸出一個清晰的「科學史演進脈絡圖」（例如：法拉第電磁感應 -> 克魯克斯陰極射線管 -> 湯姆森發現電子 -> 阿斯頓質譜儀）。

【論文全局資訊】
{paper_map_text}

【跨區塊關聯】
{cross_links_text}

請直接輸出繁體中文的歷史分析內容，使用 markdown 格式，不要有任何客套話。
"""
    return call_ai(prompt, model_name)


# ============== Pass 9：Teacher (新角色) ==============

def run_teacher(paper_map_text: str, cross_links_text: str, block_materials: list, model_name: str = MODEL_NAME) -> str:
    """
    Teacher 負責針對大二學生設計先備知識與難點解析。
    """
    print("[Pass 9] Teacher 正在編寫教學指南...")

    materials_summary_parts = []
    for m in block_materials:
        analysis_preview = m["analysis_text"][:600]
        materials_summary_parts.append(f"### {m['title']}\n{analysis_preview}")
    materials_summary_text = "\n\n".join(materials_summary_parts)

    prompt = f"""
你現在扮演一位極具教學熱忱、擅長將複雜概念具象化的物理系教授（Teacher）。
你的目標是幫助大二學生徹底搞懂這篇論文的核心內容。

請根據以下提供的論文全局資訊、跨區塊關聯以及各區塊分析素材，整理出以下兩大部分：
1. 【先備知識】：大二學生在閱讀這篇論文前，應該先懂哪些物理概念或基礎知識？（請列出 3-5 個關鍵先備知識點並做簡短的物理學科說明，例如：電磁場、荷質比等）
2. 【最難懂的 3 件事】：這篇論文中最難懂、最抽象或最容易產生誤解的 3 個核心概念（或公式推導、實驗設計）是什麼？請用極為好懂、生動且具學術深度的語言為學生詳細拆解這 3 件事。

【論文全局資訊】
{paper_map_text}

【跨區塊關聯】
{cross_links_text}

【各區塊深度分析摘錄】
{materials_summary_text}

請直接輸出繁體中文的教學指南，使用 markdown 格式，不要有任何客套話。
"""
    return call_ai(prompt, model_name)


# ============== Pass 10：Director (兼 Editor，熔煉與潤飾) ==============

def run_director(paper_map_text: str, cross_links_text: str, block_materials: list,
                 historian_text: str, teacher_text: str, model_name: str = MODEL_NAME) -> str:
    """
    Director 兼任 Editor，將所有素材熔煉並潤飾成一篇高水準的深度解讀文章。
    """
    print("[Pass 10] Director 正在熔煉全文，重寫並潤飾為完整導讀文章...")

    materials_text_parts = []
    for m in block_materials:
        analysis_preview = m["analysis_text"][:DIRECTOR_BLOCK_CHARS]
        materials_text_parts.append(
            f"### 原始區塊：{m['title']}\n"
            f"【深度分析摘錄】\n{analysis_preview}\n\n"
            f"【審查意見】\n{m['review_text']}"
        )
    materials_text = "\n\n---\n\n".join(materials_text_parts)

    prompt = f"""
你現在同時扮演 Director（總編劇／總編輯）與 Editor（文字潤飾）的角色。
你的任務是把底下提供的所有論文素材重新熔煉，親自撰寫並潤飾出一篇有清楚故事線、邏輯嚴密且語氣流暢的完整深度解讀文章，專供大學物理系學生閱讀。

寫作要求與大綱架構：
1. 請嚴格依照以下五大章節重新組織文章，並使用繁體中文撰寫，字句必須嚴謹但易懂，展現教授口吻：
   - ## 一、背景 (Background)：說明本研究領域的歷史背景與前人研究狀態。
   - ## 二、問題 (Problem)：指出當時面臨的核心科學痛點或尚未解決的關鍵難題。
   - ## 三、突破 (Breakthrough)：詳細闡述這篇論文提出的核心創新概念、新理論模型或獨特的實驗設計。
   - ## 四、證據 (Evidence)：分析論文中提供的實驗數據、圖表、或數學推導過程，說明它們如何強烈支持前述突破。
   - ## 五、影響 (Influence)：總結本研究在科學史上的位置、對後世的深遠啟發與局限性（適度融入審查意見）。
2. 在撰寫過程中，請自然串聯起各章節的邏輯鏈條，而非孤立地條列筆記。
3. 整合【歷史學家分析】與【教學指南】的洞察，使文章具備深度學術視野與教學引導作用。
4. 同時執行編輯（Editor）職責：統一全文物理學術名詞的翻譯與語氣，修順章節銜接，並保留核心公式與重要數據。

【論文全局資訊】
{paper_map_text}

【跨區塊關聯】
{cross_links_text}

【歷史學家分析（Historian）】
{historian_text}

【教學指南（Teacher）】
{teacher_text}

【各區塊分析與審查素材】
{materials_text}

請直接輸出重新熔煉並潤飾後的完整深度導讀文章本文，不要輸出任何客套話。
"""
    return call_ai(prompt, model_name)


# ============== 主流程：把十一個 Pass 串起來 ==============

def main():
    full_markdown = load_markdown(md_path, f"{base_name}.md")

    # Pass 1：依標題切割
    raw_blocks = split_by_headings(full_markdown)
    print(f"[Pass 1] 依標題切出 {len(raw_blocks)} 個原始區塊")

    # Pass 0：Supervisor 建立全局地圖
    paper_map = build_paper_map(full_markdown, raw_blocks)
    paper_map_text = paper_map_to_prompt_text(paper_map)

    # Pass 2：太短的自動合併
    merged_blocks = merge_short_blocks(raw_blocks)
    print(f"[Pass 2] 合併過短區塊後，剩下 {len(merged_blocks)} 個區塊")

    # Pass 3：太長的依段落二次切分
    final_blocks = []
    for b in merged_blocks:
        final_blocks.extend(split_long_block(b))
    print(f"[Pass 3] 二次切分過長區塊後，總共 {len(final_blocks)} 個最終區塊\n")

    block_materials = []   # 給 Director/Teacher 用的素材
    notes_report = ""      # 保留逐區塊筆記，作為附錄

    for block_index, block in enumerate(final_blocks, 1):
        print(f"=== 處理區塊 {block_index}/{len(final_blocks)}：{block['title']} ===")
        print(f"    區塊長度：{len(block['content'])} 字")

        # Pass 4：AI 決定任務數量
        ai_task_output = generate_tasks_for_block(block, paper_map_text)
        tasks = parse_tasks(ai_task_output)
        print(f"    AI 判斷後產出 {len(tasks)} 個任務")

        block_note = f"\n\n## 區塊 {block_index}：{block['title']}\n"
        block_analysis_text = ""

        # Pass 5：逐任務深度展開
        for task_index, task in enumerate(tasks, 1):
            print(f"      -> 正在深度展開任務 {task_index}/{len(tasks)}：{task}")
            detail = expand_task(task, block, paper_map_text)
            block_note += f"\n### 任務 {task_index}：{task}\n{detail}\n"
            block_analysis_text += f"\n[{task}]\n{detail}\n"

        # Pass 6：Reviewer 審查區塊（簡化版）
        print("      -> Reviewer 正在審查此區塊...")
        review_text = review_block(block, paper_map_text)
        block_note += f"\n### 審查意見（Reviewer）\n{review_text}\n"

        notes_report += block_note
        block_materials.append({
            "title": block["title"],
            "analysis_text": block_analysis_text.strip(),
            "review_text": review_text.strip(),
        })
        print()

    # Pass 7：Cross-Link Builder 找出區塊關聯
    cross_links = build_cross_links(final_blocks, paper_map_text)
    cross_links_text = cross_links_to_prompt_text(cross_links)

    # Pass 8：Historian 科學史定位
    historian_text = run_historian(paper_map_text, cross_links_text)

    # Pass 9：Teacher 大二物理教學指南
    teacher_text = run_teacher(paper_map_text, cross_links_text, block_materials)

    # Pass 10：Director 熔煉與潤飾全文
    final_article = run_director(paper_map_text, cross_links_text, block_materials, historian_text, teacher_text)

    # ============== 輸出最終報告 ==============
    output_report_path = os.path.join(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"),
        f"{base_name}_final_report_v4.md"
    )

    with open(output_report_path, "w", encoding="utf-8") as f:
        f.write("# 論文高精度深度解讀與教學報告（v4 多代理整合版）\n\n")
        f.write("## 論文知識地圖（Supervisor）\n")
        f.write(f"```\n{paper_map_text}\n```\n\n")
        f.write("## 區塊關聯（Cross-Link Builder）\n")
        f.write(f"{cross_links_text}\n\n")
        f.write("---\n\n")
        f.write("# 第一部分：科學史脈絡（Historian）\n")
        f.write(historian_text)
        f.write("\n\n---\n\n")
        f.write("# 第二部分：大二物理教學指南（Teacher）\n")
        f.write(teacher_text)
        f.write("\n\n---\n\n")
        f.write("# 第三部分：論文深度導讀文章（Director & Editor）\n")
        f.write(final_article)
        f.write("\n\n---\n\n")
        f.write("# 第四部分：附錄 — 各區塊逐項分析與審查筆記（原始素材）\n")
        f.write(notes_report)

    # 同時也在 output/<base_name> 目錄下存一份備份
    backup_dir = os.path.dirname(md_path)
    backup_report_path = os.path.join(backup_dir, f"{base_name}_final_report_v4.md")
    try:
        with open(backup_report_path, "w", encoding="utf-8") as f:
            f.write("# 論文高精度深度解讀與教學報告（v4 多代理整合版）\n\n")
            f.write("## 論文知識地圖（Supervisor）\n")
            f.write(f"```\n{paper_map_text}\n```\n\n")
            f.write("## 區塊關聯（Cross-Link Builder）\n")
            f.write(f"{cross_links_text}\n\n")
            f.write("---\n\n")
            f.write("# 第一部分：科學史脈絡（Historian）\n")
            f.write(historian_text)
            f.write("\n\n---\n\n")
            f.write("# 第二部分：大二物理教學指南（Teacher）\n")
            f.write(teacher_text)
            f.write("\n\n---\n\n")
            f.write("# 第三部分：論文深度導讀文章（Director & Editor）\n")
            f.write(final_article)
            f.write("\n\n---\n\n")
            f.write("# 第四部分：附錄 — 各區塊逐項分析與審查筆記（原始素材）\n")
            f.write(notes_report)
    except Exception as e:
        print(f"    [提示] 無法寫入備份報告：{e}")

    print(f"🎉 完成！報告已儲存至：\n{output_report_path}")


if __name__ == "__main__":
    main()

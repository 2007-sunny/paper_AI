# -*- coding: utf-8 -*-
"""
論文自動分析流水線 v3（9-Pass 多代理架構）
------------------------------------------------
v2 的問題：每個區塊各自獨立丟給 AI，模型不知道「全局脈絡」，
所以產出像是一堆互相不認識的筆記，而不是一篇有故事線的文章。

v3 解法：在 Chunk Analyzer 之外，新增四個角色：
  - Supervisor      負責讀全文，建立「論文知識地圖」(Paper Knowledge Map)
  - Reviewer        負責挑出每個區塊的創新點、限制、與前文差異
  - Cross-Link Builder  負責找出區塊之間的關聯（呼應、推翻、延伸...）
  - Director        負責拿著以上所有素材，重新「寫成」一篇有章節的完整文章
  - Editor          負責統一全文語氣與用詞，做最後潤飾

九個 Pass：
  Pass 0：Supervisor   → 建立 Paper Knowledge Map（全局地圖）
  Pass 1：Heading Split → 依標題做原始切割
  Pass 2：Merge        → 太短的區塊自動合併
  Pass 3：Long Split    → 太長的區塊依段落邊界二次切分
  Pass 4：Task Generator → 每個區塊（帶著全局地圖）自己決定要幾個任務
  Pass 5：Chunk Analyzer → 逐任務深度展開（帶著全局地圖）
  Pass 6：Reviewer      → 針對每個區塊找創新點／限制／與前文差異／推理跳躍
  Pass 7：Cross-Link Builder → 综合所有區塊摘要，找出區塊之間的關聯
  Pass 8：Director      → 拿著地圖＋分析＋審查＋關聯，重寫成完整文章（分章節）
  Pass 9：Editor        → 對 Director 寫出的全文做語氣統一與潤飾
"""

import os
import re
import json
import ollama

# ============== 可調參數區 ==============

MERGE_THRESHOLD_CHARS = 150       # 區塊字數低於這個門檻，就自動跟下一塊合併
SPLIT_THRESHOLD_CHARS = 5000      # 區塊字數超過這個門檻，就依段落邊界二次切分
MODEL_NAME = "ollama/qwythos:latest"

SUPERVISOR_PREVIEW_CHARS = 600    # 餵給 Supervisor 看每個區塊時，只取前 N 字當預覽（避免地圖建立階段就塞爆上下文）
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
    本地小模型常常會在 JSON 外面包一層 ```json ... ``` 或加一些客套話，
    所以這裡會先把常見的雜訊去掉，再嘗試抓出第一個 { 到最後一個 } 之間的內容解析。
    解析失敗就回傳 fallback，讓流程可以繼續跑下去而不是整個崩潰。
    """
    text = raw_text.strip()
    text = re.sub(r"^```json", "", text.strip(), flags=re.IGNORECASE).strip()
    text = re.sub(r"^```", "", text.strip()).strip()
    text = re.sub(r"```$", "", text.strip()).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        print("    [警告] Supervisor/CrossLink 回傳內容找不到合法 JSON，使用預設值代替。")
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
    Supervisor 不負責解釋細節內容，只負責「看完全文、建立全局地圖」。
    為了不在這一步就塞爆本地模型的上下文，這裡不會把全文整篇丟進去，
    而是用「每個標題 + 該區塊前 N 字預覽」組成一份精簡版全文摘要。
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
    """把全文依任何 '#' 開頭的行切成區塊（沿用 v2 邏輯，未變動）。"""
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
    """區塊字數低於 threshold，就跟下一個區塊合併（沿用 v2 邏輯，未變動）。"""
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
    """超過 threshold 字數的區塊，依空行切成子區塊（沿用 v2 邏輯，未變動）。"""
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
    """跟 v2 相比，這裡多塞了「全局地圖」進去，讓 AI 判斷任務時也考慮這段在全文中的角色。"""
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
---TASK_LIST_END---

【段落內容開始】
{block['content']}
【段落內容結束】
"""
    return call_ai(prompt, model_name)


def parse_tasks(ai_output: str):
    """從 AI 輸出的格式化文字中，把任務逐條抓出來（沿用 v2 邏輯，未變動）。"""
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
            tasks.append(line.strip())

    return tasks


# ============== Pass 5：Chunk Analyzer，依任務逐一深度展開（帶著全局地圖） ==============

def expand_task(task_text: str, block, paper_map_text: str, model_name: str = MODEL_NAME):
    """跟 v2 相比，這裡要求 AI 額外說明「這個任務在整篇論文中的作用」，避免分析變成孤立筆記。"""
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


# ============== Pass 6：Reviewer，找創新點／限制／差異／推理跳躍 ==============

def review_block(block, paper_map_text: str, model_name: str = MODEL_NAME) -> str:
    """
    Reviewer 不重新解釋內容，而是站在「Nature 審稿人」的角度挑毛病、找亮點。
    這份輸出之後會被 Director 拿去當「批判性視角」的素材。
    """
    prompt = f"""
你現在扮演一位嚴謹但公正的 Nature 等級學術審稿人（Reviewer）。

【論文全局資訊】
{paper_map_text}

請閱讀以下這個區塊（標題：{block['title']}），並針對它回答以下四點，盡量簡潔、條列式，不要重述內容本身：
1. 此段對全文的新貢獻是什麼？
2. 此段與全局地圖中其他章節相比，有什麼差異或銜接關係？
3. 此段是否存在推理跳躍或邏輯不夠嚴謹之處？（若沒有，請明確說「無明顯推理跳躍」）
4. 此段是否存在實驗或論述上的限制？（若沒有，請明確說「無明顯限制」）

【段落內容開始】
{block['content']}
【段落內容結束】
"""
    return call_ai(prompt, model_name)


# ============== Pass 7：Cross-Link Builder，建立區塊之間的關聯 ==============

def build_cross_links(blocks, paper_map_text: str, model_name: str = MODEL_NAME) -> dict:
    """
    把所有區塊的標題＋簡短內容預覽彙整起來，一次性請 AI 找出區塊之間的關聯，
    例如「區塊 3 的實驗設計呼應區塊 1 提出的問題」「區塊 5 的結果推翻了區塊 2 的假設」。
    回傳格式：{"links": [{"from": "標題A", "to": "標題B", "relation": "說明"}]}
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


# ============== Pass 8：Director，重寫成完整文章 ==============

def run_director(paper_map_text: str, cross_links_text: str, block_materials: list,
                  model_name: str = MODEL_NAME) -> str:
    """
    Director 拿到：全局地圖 + 跨區塊關聯 + 每個區塊的（任務分析＋審查意見，截斷過的版本），
    重新組織、改寫成一篇有章節結構、有故事線的完整文章，而不是逐區塊的筆記堆疊。
    """
    print("[Pass 8] Director 正在彙整全文，重寫成完整文章...")

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
你現在扮演 Director（總編劇／總編輯）的角色。
你的任務不是逐段翻譯或摘要，而是把底下提供的所有素材「重新組織」，
寫成一篇有清楚章節、有故事線、邏輯連貫的完整深度解讀文章，給大學物理系學生閱讀。

寫作要求：
1. 自行規劃合理的章節（例如：研究背景、待解決的問題、實驗方法與裝置、數學模型推導、結果與證據、結論與後續影響），
   不需要照抄原始區塊順序，而是依「故事線」重新安排。
2. 每一章請說明「為什麼這一步是必要的」，串連起前後章節的邏輯，而不是孤立條列。
3. 適度引用【審查意見】中的批判性觀點（創新點、限制、推理跳躍），讓文章不只是複述，而帶有分析深度。
4. 適度運用【跨區塊關聯】，在文章中明確指出某個結果如何呼應或修正了前文的假設。
5. 使用繁體中文撰寫，語氣是嚴謹但易懂的教授口吻，可以使用 Markdown 章節標題（##）。

【論文全局資訊】
{paper_map_text}

【跨區塊關聯】
{cross_links_text}

【各區塊素材開始】
{materials_text}
【各區塊素材結束】

請直接輸出完整文章本文，不要輸出任何「以下是文章」之類的客套話。
"""
    return call_ai(prompt, model_name)


# ============== Pass 9：Editor，統一語氣與最後潤飾 ==============

def run_editor(director_article: str, paper_map_text: str, model_name: str = MODEL_NAME) -> str:
    """Editor 負責最後一輪潤飾：統一用詞、修順語句銜接、確保章節間語氣一致。"""
    print("[Pass 9] Editor 正在進行最後潤飾...")

    prompt = f"""
你現在扮演 Editor（文字編輯）的角色。
以下是 Director 寫好的完整文章草稿，請你進行最後一輪潤飾：
1. 統一全文用詞與語氣（例如同一個物理量前後翻譯要一致）。
2. 修順章節之間的銜接句，讓讀者讀起來像一篇連貫的文章，而不是拼接的段落。
3. 保留所有 Markdown 章節標題（##）與技術內容、公式、數據，不要刪減實質內容。
4. 不要新增原文沒有的事實或數據。

【論文全局資訊（僅供參考，確保潤飾後仍貼合主題）】
{paper_map_text}

【Director 草稿開始】
{director_article}
【Director 草稿結束】

請直接輸出潤飾後的完整文章本文，不要輸出任何「以下是潤飾後文章」之類的客套話。
"""
    return call_ai(prompt, model_name)


# ============== 主流程：把九個 Pass 串起來 ==============

def main():
    full_markdown = load_markdown(md_path, f"{base_name}.md")

    # Pass 1：依標題切割（先切一次，給 Supervisor 看全局用）
    raw_blocks = split_by_headings(full_markdown)
    print(f"[Pass 1] 依標題切出 {len(raw_blocks)} 個原始區塊")

    # Pass 0：Supervisor 建立全局地圖（用 Pass 1 切好的區塊當輸入，比直接塞全文更省 token）
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

    block_materials = []   # 給 Director 用的素材（每塊一份分析摘錄 + 審查意見）
    notes_report = ""      # 保留原本逐區塊筆記，當作附錄

    for block_index, block in enumerate(final_blocks, 1):
        print(f"=== 處理區塊 {block_index}/{len(final_blocks)}：{block['title']} ===")
        print(f"    區塊長度：{len(block['content'])} 字")

        # Pass 4：AI 決定這個區塊要幾個任務（帶全局地圖）
        ai_task_output = generate_tasks_for_block(block, paper_map_text)
        tasks = parse_tasks(ai_task_output)
        print(f"    AI 判斷後產出 {len(tasks)} 個任務")

        block_note = f"\n\n## 區塊 {block_index}：{block['title']}\n"
        block_analysis_text = ""

        # Pass 5：逐任務深度展開（帶全局地圖）
        for task_index, task in enumerate(tasks, 1):
            print(f"      -> 正在深度展開任務 {task_index}/{len(tasks)}：{task}")
            detail = expand_task(task, block, paper_map_text)
            block_note += f"\n### 任務 {task_index}：{task}\n{detail}\n"
            block_analysis_text += f"\n[{task}]\n{detail}\n"

        # Pass 6：Reviewer 審查這個區塊
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

    # Pass 7：Cross-Link Builder 找出區塊之間的關聯
    cross_links = build_cross_links(final_blocks, paper_map_text)
    cross_links_text = cross_links_to_prompt_text(cross_links)

    # Pass 8：Director 重寫成完整文章
    director_article = run_director(paper_map_text, cross_links_text, block_materials)

    # Pass 9：Editor 最後潤飾
    final_article = run_editor(director_article, paper_map_text)

    # ============== 輸出最終報告 ==============
    output_report_path = os.path.join(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"),
        f"{base_name}_final_report_v3.md"
    )

    with open(output_report_path, "w", encoding="utf-8") as f:
        f.write("# 論文高精度深度解讀報告（v3 多代理整合版）\n\n")
        f.write("## 論文知識地圖（Supervisor）\n")
        f.write(f"```\n{paper_map_text}\n```\n\n")
        f.write("## 區塊關聯（Cross-Link Builder）\n")
        f.write(f"{cross_links_text}\n\n")
        f.write("---\n\n")
        f.write("# 第一部分：完整深度解讀文章（Director + Editor）\n")
        f.write(final_article)
        f.write("\n\n---\n\n")
        f.write("# 第二部分：附錄 — 各區塊逐項分析筆記（原始素材）\n")
        f.write(notes_report)

    print(f"🎉 完成！報告已儲存至：\n{output_report_path}")


if __name__ == "__main__":
    main()

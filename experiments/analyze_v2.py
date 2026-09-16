# -*- coding: utf-8 -*-
"""
論文自動分析流水線 v2
------------------------------------------------
跟 v1 最大的差異：
v1 永遠固定產出 4 個任務、永遠只看前 3000 字。
v2 會先把整篇論文依「標題」切成多個區塊，
   再讓 AI 針對「每一個區塊」自己判斷該產出幾個任務（1~3 個，寧少而精），
   內容太單薄的區塊會先在程式層自動合併，避免浪費 AI 呼叫。

整體流程（六個 Pass）：
  Pass 1：依標題符號（# 開頭）做「原始切割」
  Pass 2：太短的區塊自動跟下一塊合併（避免浪費 AI 呼叫處理過渡段）
  Pass 3：太長的區塊依空行（段落邊界）再切一次（避免塞爆模型上下文）
  Pass 4：每個處理好的區塊丟給 AI，AI 自己決定產出 1~3 個任務
  Pass 5：依任務逐一呼叫 AI 做「深度展開」（沿用 v1 的 Loop 邏輯）
  Pass 6：依區塊順序把所有結果彙整成最終報告
"""

import os
import re
import ollama

# ============== 可調參數區（之後想微調就改這裡就好） ==============

MERGE_THRESHOLD_CHARS = 150      # 區塊字數低於這個門檻，就自動跟下一塊合併
SPLIT_THRESHOLD_CHARS = 5000     # 區塊字數超過這個門檻，就依段落邊界二次切分
MODEL_NAME = "ollama/qwythos:latest"

# ============== 檔案路徑設定 ==============

filename = "J.J.Thomson-1917_Thomson-Parabola.md"
base_name = filename.replace(".md", "")
md_path = os.path.join(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"), base_name, filename
)


def load_markdown(path: str, fallback_filename: str) -> str:
    """讀取 Marker 轉換好的 Markdown 全文（不只是前 3000 字了）。"""
    print("正在讀取 Marker 轉換好的 Markdown 論文...")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        with open(fallback_filename, "r", encoding="utf-8") as f:
            return f.read()


# ============== Pass 1：依標題做原始切割 ==============

def split_by_headings(full_text: str):
    """
    把全文依任何 '#' 開頭的行切成區塊。
    因為 Marker 產出的標題層級不一定穩定（有時 Introduction 是 ##，
    有時可能跑掉變成 ###，甚至偶爾抓不到），所以這裡採取「寬容策略」：
    只要是 # 開頭的行就當作候選標題，不specifically要求是 ## 還是 #。

    回傳格式：[{"title": "標題文字", "content": "這個標題底下的內文"}, ...]
    """
    lines = full_text.split("\n")
    blocks = []
    current_title = "（文件開頭，無標題段落）"
    current_content_lines = []

    heading_pattern = re.compile(r"^#{1,6}\s+(.*)")

    for line in lines:
        match = heading_pattern.match(line.strip())
        if match:
            # 遇到新標題，先把累積的內容存成一個區塊
            if current_content_lines:
                blocks.append({
                    "title": current_title,
                    "content": "\n".join(current_content_lines).strip()
                })
            current_title = match.group(1).strip()
            current_content_lines = []
        else:
            current_content_lines.append(line)

    # 收尾：把最後一塊也存進去
    if current_content_lines:
        blocks.append({
            "title": current_title,
            "content": "\n".join(current_content_lines).strip()
        })

    # 過濾掉內容完全是空白的區塊（例如連續兩個標題中間什麼都沒有）
    blocks = [b for b in blocks if len(b["content"]) > 0]
    return blocks


# ============== Pass 2：太短的區塊自動合併 ==============

def merge_short_blocks(blocks, threshold=MERGE_THRESHOLD_CHARS):
    """
    區塊字數低於 threshold，就跟「下一個」區塊合併，
    標題改成兩者用「、」連接，避免浪費一次 AI 呼叫處理過渡段。
    """
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

        # 如果累積到的內容已經夠長，就把 buffer 收進結果，重新開始累積
        if len(buffer["content"]) >= threshold:
            merged.append(buffer)
            buffer = None

    # 收尾：如果最後還有沒收進去的 buffer（代表結尾幾塊都太短），
    # 就併到前一個區塊裡，而不是讓它孤零零地留著
    if buffer is not None:
        if merged:
            merged[-1]["content"] += "\n\n" + buffer["content"]
            merged[-1]["title"] += f"、{buffer['title']}"
        else:
            merged.append(buffer)

    return merged


# ============== Pass 3：太長的區塊依段落邊界二次切分 ==============

def split_long_block(block, threshold=SPLIT_THRESHOLD_CHARS):
    """
    如果單一區塊字數超過 threshold，依「空行」（段落邊界）切成多塊子區塊，
    並標記成 標題 (Part 1/2)、標題 (Part 2/2) 這樣的子標題，
    避免破壞語意完整的句子或公式。
    """
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
        {
            "title": f"{block['title']} (Part {i+1}/{total})",
            "content": chunk
        }
        for i, chunk in enumerate(sub_blocks)
    ]


# ============== Pass 4：每個區塊讓 AI 自己決定任務數量 ==============

def generate_tasks_for_block(block, model_name=MODEL_NAME):
    """
    針對單一區塊呼叫 AI，請它根據「這個區塊本身的內容密度」
    自己決定要產出 1~3 個任務，內容單薄就少給，內容豐富才多給。
    """
    prompt = f"""
你是一位嚴謹的物理與科學學術審稿人與系統架構師。
請閱讀以下論文的其中一個段落（標題：{block['title']}）。

【任務目標】：
請先判斷這個段落的「內容密度」，再決定要產出幾個學習任務：
- 如果這段內容很單薄（例如只是過渡句、引言、純粹的章節銜接），請只給 1 個任務即可。
- 如果這段內容中等豐富（有一些背景說明或單一概念），請給 2 個任務。
- 如果這段內容資訊量很大（有核心公式、關鍵實驗設計、或重要創新點），請給 3 個任務。
- 任務數量的上限是 3 個，不要為了湊數硬生出不必要的任務。

每個任務必須是以下其中一種類型：
1. 背景／核心概念導讀
2. 數學公式或物理模型推導解析（若無公式則為實驗核心觀念解析）
3. 專業生字精準翻譯與物理語境解析（請直接列出 1~3 個你判斷最關鍵的生字）
4. 實驗儀器、架構或圖表解析（僅在段落確實提及時才產出此類任務）

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
    response = ollama.chat(model=model_name, messages=[
        {'role': 'user', 'content': prompt}
    ])
    return response['message']['content']


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
            tasks.append(line.strip())

    return tasks


# ============== Pass 5：依任務逐一呼叫 AI 做深度展開 ==============

def expand_task(task_text: str, block, model_name=MODEL_NAME):
    """針對單一任務，結合所屬區塊的原文，請 AI 寫出詳細解析報告。"""
    loop_prompt = f"""
你是一位極為專業且耐心的物理系教授。
現在請你針對以下這個【指定任務】，結合【段落參考文本】，為大二學生寫出一份非常詳細、好懂、且具有學術深度的延伸解析報告。
如果是翻譯任務，請給出單字的字根、物理含意與 2 個物理學科的例句。
如果是公式或背景，請詳細展開說明。

【指定任務】：{task_text}

【段落參考文本】：
{block['content']}
"""
    response = ollama.chat(model=model_name, messages=[
        {'role': 'user', 'content': loop_prompt}
    ])
    return response['message']['content']


# ============== 主流程：把六個 Pass 串起來 ==============

def main():
    full_markdown = load_markdown(md_path, f"{base_name}.md")

    # Pass 1：依標題切割
    raw_blocks = split_by_headings(full_markdown)
    print(f"[Pass 1] 依標題切出 {len(raw_blocks)} 個原始區塊")

    # Pass 2：太短的自動合併
    merged_blocks = merge_short_blocks(raw_blocks)
    print(f"[Pass 2] 合併過短區塊後，剩下 {len(merged_blocks)} 個區塊")

    # Pass 3：太長的依段落二次切分
    final_blocks = []
    for b in merged_blocks:
        final_blocks.extend(split_long_block(b))
    print(f"[Pass 3] 二次切分過長區塊後，總共 {len(final_blocks)} 個最終區塊\n")

    final_report = ""

    for block_index, block in enumerate(final_blocks, 1):
        print(f"=== 處理區塊 {block_index}/{len(final_blocks)}：{block['title']} ===")
        print(f"    區塊長度：{len(block['content'])} 字")

        # Pass 4：AI 自己決定這個區塊要幾個任務
        ai_task_output = generate_tasks_for_block(block)
        tasks = parse_tasks(ai_task_output)
        print(f"    AI 判斷後產出 {len(tasks)} 個任務")

        block_report = f"\n\n## 區塊 {block_index}：{block['title']}\n"

        # Pass 5：逐任務深度展開
        for task_index, task in enumerate(tasks, 1):
            print(f"      -> 正在深度展開任務 {task_index}/{len(tasks)}：{task}")
            detail = expand_task(task, block)
            block_report += f"\n### 任務 {task_index}：{task}\n{detail}\n"

        final_report += block_report
        print()

    # Pass 6：彙整輸出
    output_report_path = os.path.join(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"),
        f"{base_name}_final_report.md"
    )

    with open(output_report_path, "w", encoding="utf-8") as f:
        f.write("# 論文高精度深度解讀報告（動態任務分配版）\n")
        f.write(final_report)

    print(f"🎉 完成！報告已儲存至：\n{output_report_path}")


if __name__ == "__main__":
    main()

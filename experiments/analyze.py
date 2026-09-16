import os
import ollama

# 1. 定義 marker 剛剛產出的 markdown 檔案路徑
# 根據你的資料夾結構，轉換後的 md 檔案應該躺在 output/test 資料夾內（或者就在目前目錄，請依你實際的檔案位置微調）
# 1. 定義基礎名稱
filename = "J.J.Thomson-1917_Thomson-Parabola.md"  # marker 轉換後的原始 md 檔案名稱
# 透過字串處理，把 ".md" 拿掉，只留下 "test"，方便後面串接新檔名
base_name = filename.replace(".md", "")  # base_name 會變成 "test"

# 2. 用 os.path.join 自動處理反斜線，這比用 "+" 號去拼字串安全 100 倍！
# 這會精準指向：C:\Users\User\Desktop\sunny\code\PaperAI\output\test\test.md
md_path = os.path.join(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"), base_name, filename)

# 嘗試讀取這個 Markdown 檔案
print("正在讀取 Marker 轉換好的 Markdown 論文...")
try:
    with open(md_path, "r", encoding="utf-8") as f:
        full_markdown = f.read()
except FileNotFoundError:
    # 預防路徑有些微落差，嘗試直接讀取目前目錄下的 test 檔
    with open("test.md", "r", encoding="utf-8") as f:
        full_markdown = f.read()

# 為了安全與速度，我們先截取前 3000 個字（大約是 Abstract 到 Introduction 早期）
sample_text = full_markdown[:3000]

# 2. 設計你對論文的品味與要求（提示詞）
prompt = f"""
你是一位嚴謹的物理與科學學術審稿人與系統架構師。
請閱讀以下由 Marker 解析出來的論文 Markdown 文本。

【任務目標】：
請將這段論文內容拆解為「大二物理系學生學習專用」的具體微型任務清單（工作需求表單）。
請嚴格按照以下規定的格式輸出，不要包含任何多餘的客套話。

【輸出格式規範】：
---TASK_LIST_START---
- [TASK_1]: 導讀此段落的研究背景與核心科學痛點。
- [TASK_2]: 詳細推導或解析此段落中出現的關鍵數學公式或物理模型（若無公式，則改為深入解析其實驗核心觀念）。
- [TASK_3]: 針對以下專業生字進行精準翻譯與上下文物理物理意義解析：[在這裡列出你從文本中挑選出的 3-5 個關鍵生字，用逗號隔開]
- [TASK_4]: 解析此段落提及的實驗儀器、架構或光路設計（特別對應論文中的插圖）。
---TASK_LIST_END---

【論文 Markdown 文本開始】
{sample_text}
【論文 Markdown 文本結束】
"""



# 3. 呼叫本地大腦進場接力（因為 Marker 已經關閉，此時 RTX 4060 100% 屬於 Ollama）
# ollama pull qwen2.5:7b
# ollama pull llama3.1:8b
# ollama pull deepseek-r1:8b
model_name = "ollama/qwythos:latest"
print("正在呼叫本地 " + model_name + " 進行學術分析與翻譯...")
response = ollama.chat(model=model_name, messages=[
    {
        'role': 'user',
        'content': prompt,
    },
])

# （接續原本呼叫第一次 ollama.chat 的程式碼...）
# 假設第一次 AI 吐出的表單內容存在 response['message']['content'] 中
stage1_output = response['message']['content']

print("\n[階段一完成] 成功生成工作需求表單！")
print(stage1_output)

# ================= 階段二：依據表單執行 Loop =================
print("\n[階段二啟動] 程式正在解析表單，準備執行迭代深挖...")

# 1. 透過 Python 的文字處理，把任務清單每一行抓出來
lines = stage1_output.split("\n")
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

# 2. 開始執行 Loop，把每一個小任務單獨餵給 AI 放大成詳細內容
print(f"共偵測到 {len(tasks)} 個核心任務，開始逐一呼叫 RTX 4060 進行深度解析...\n")

final_report = ""

for index, task in enumerate(tasks, 1):
    print(f"正在執行第 {index}/{len(tasks)} 個任務: {task}")

    # 設計深度放大用的提示詞
    loop_prompt = f"""
    你是一位極為專業且耐心的物理系教授。
    現在請你針對以下這個【指定任務】，結合【論文參考文本】，為大二學生寫出一份非常詳細、好懂、且具有學術深度的延伸解析報告。
    如果是翻譯任務，請給出單字的字根、物理含意與 2 個物理學科的例句。
    如果是公式或背景，請詳細展開說明。

    【指定任務】：{task}

    【論文參考文本】：
    {sample_text}
    """

    # 再次呼叫本地 AI（這就是 Loop 核心）
    loop_response = ollama.chat(model=model_name, messages=[
        {'role': 'user', 'content': loop_prompt}
    ])

    # 把每次 Loop 產出的詳細內容拼接到最終報告裡
    task_result = loop_response['message']['content']
    final_report += f"\n\n### 任務 {index} 深度解析結果：\n{task_result}\n"
    final_report += "-"*50

# 3. 將最終極度詳細的成果存成一份新的 Markdown 檔案
# 3. 定義最終報告的輸出路徑
# 這會精準輸出成：C:\Users\User\Desktop\sunny\code\PaperAI\output\test_final_report.md
output_report_path = os.path.join(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"), f"{base_name}_final_report.md")

# ============== 測試印出路徑是否正確 ==============
print("讀取路徑:", md_path)
print("輸出路徑:", output_report_path)

with open(output_report_path, "w", encoding="utf-8") as f:
    f.write("# 論文高精度深度解讀報告\n")
    f.write(final_report)

print(f"\n🎉 完美破關！更詳細的內容已自動生成，並儲存至：\n{output_report_path}")

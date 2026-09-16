import os
import pymupdf4llm
import ollama

# 1. 將 PDF 轉換為 Markdown 文字
print("正在讀取 PDF 並轉換為 Markdown 格式...")
md_text = pymupdf4llm.to_markdown(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "input", "test.pdf"))

# 為了測試，我們先抓取前 2000 個字，避免一次餵太多字導致記憶體爆掉
sample_text = md_text[:2000]

# 2. 設計你對論文的品味與要求（提示詞）
prompt = f"""
你是一位嚴謹的物理與科學學術審稿人。請閱讀以下論文的 Markdown 文本片段，並用繁體中文執行以下任務：
1. 簡述這段內容的研究背景（Background）。
2. 提取出這段內容的核心重要觀念（Key Concepts）。
3. 如果裡面有對於「大二物理系學生」而言較為陌生的專業英文單字，請在下方建立一個「客製化生字本」，給出精準的物理或科學上下文翻譯。

【論文片段開始】
{sample_text}
【論文片段結束】
"""

# 3. 呼叫你本地的 Qwen 模型
print("正在啟動本地 AI 進行分析（這會完全消耗你的 RTX 4060 算力）...")
response = ollama.chat(model='qwen2.5:7b', messages=[
    {
        'role': 'user',
        'content': prompt,
    },
])

# 4. 印出結果
print("\n============== 本地 AI 論文分析結果 ==============\n")
print(response['message']['content'])

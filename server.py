from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import ollama
import json

from paperai.config import MODEL_NAME

# =====使用方法=====#
"""
開啟終端機執行：
uvicorn server:app --reload --port 8000
"""

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 可用模型列表
# 預設與 translate.py 相同（paperai/config.py），前端可另外指定
model_name = MODEL_NAME
print(f"目前選擇使用的模型：{model_name}")

class ExplainRequest(BaseModel):
    term: str
    context: str | None = None
    model_name: str | None = None  # 🌟 新增：接收前端傳來的模型名稱

@app.post("/api/explain")
def explain_word(payload: ExplainRequest):
    # 🌟 動態判定：如果前端有傳指定模型，就用前端的；沒有就用預設的後端變數
    active_model = payload.model_name if payload.model_name else model_name
    print(f"【收到請求】查詢單字: {payload.term}，指定呼叫的 Ollama 模型為: {active_model}")
    # 更新提示詞：加入 "sentences" 的需求，格式為包含英中對照的陣列
    system_prompt = (
        "You are an expert bilingual scientific paper reading assistant. "
        "Your task is to translate and explain the requested term based on the provided context, "
        "and provide 1-2 high-quality academic example sentences using this term.\n\n"
        "You must respond ONLY with a raw JSON object matching this schema, without any markdown formatting:\n"
        "{\n"
        '  "translation": "繁體中文翻譯",\n'
        '  "explanation": "針對該詞彙在學術上下文中的簡短中文解釋",\n'
        '  "category": "術語類別 (例如: tech, math, physics, general)",\n'
        '  "sentences": [\n'
        '    {"en": "Example sentence in English.", "zh": "對應的繁體中文翻譯"},\n'
        '    {"en": "Another example sentence.", "zh": "另一個對應的繁體中文翻譯"}\n'
        '  ]\n'
        "}"
    )

    user_content = f"Term: {payload.term}\n"
    if payload.context:
        user_content += f"Context: {payload.context}"

    try:
        # 呼叫本地的 Ollama (改成傳入 active_model)
        response = ollama.chat(
            model=active_model,  # 👈 換成動態變數
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_content}
            ],
            format='json',
            think=False,  # 思考型模型關閉 thinking，查詢才會即時回應
            options={'temperature': 0.3}
        )

        reply = response['message']['content'].strip()


        # 🌟 新增這兩行：在控制台印出最顯眼的邊界，讓我們看清楚第 8 行到底發生了什麼事
        print("\n" + "="*50 + "\n【Ollama 實際吐出的 JSON 原始內容】:")
        print(reply)
        print("="*50 + "\n")


        result_data = json.loads(reply)

        return {
            "term": payload.term,
            "translation": result_data.get("translation", "翻譯失敗"),
            "explanation": result_data.get("explanation", "無法提供解釋"),
            "category": result_data.get("category", "custom"),
            "sentences": result_data.get("sentences", []),
            "model_used": active_model  # 👈 回傳真正使用的模型名稱
        }
    except Exception as e:
        # ... 錯誤回傳也對應改成 active_model ...
        return {
            "term": payload.term,
            "translation": "錯誤",
            "explanation": f"呼叫本地 AI 時發生錯誤: {str(e)}",
            "category": "error",
            "sentences": [],
            "model_used": active_model
        }

"""Ollama 呼叫與 LLM JSON 解析。"""
import json
import os
import re
from dataclasses import dataclass

import ollama

from . import config


@dataclass
class ChatResult:
    content: str
    tokens: int = 0
    eval_seconds: float = 0.0


def chat(prompt: str, model: str = None, fmt=None, temperature: float = None,
         num_predict: int = None) -> ChatResult:
    """呼叫 Ollama。fmt 可為 None、"json" 或 JSON schema（結構化輸出）。"""
    options = {"num_ctx": config.NUM_CTX, "num_predict": num_predict or config.NUM_PREDICT}
    if temperature is not None:
        options["temperature"] = temperature
    resp = ollama.chat(
        model=model or config.MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        think=config.THINK,
        format=fmt,
        options=options,
    )
    # 部分模型即使關閉 thinking 仍可能在內文輸出 <think> 區塊
    content = re.sub(r"<think>[\s\S]*?</think>", "", resp["message"]["content"]).strip()
    return ChatResult(
        content=content,
        tokens=resp.get("eval_count") or 0,
        eval_seconds=(resp.get("eval_duration") or 0) / 1e9,
    )


def call_ai(prompt: str, model: str = None) -> str:
    return chat(prompt, model).content


def safe_json_parse(raw_text: str, fallback: dict) -> dict:
    """Safely extracts and parses JSON from raw LLM output."""
    text = raw_text.strip()
    text = re.sub(r"^```json", "", text.strip(), flags=re.IGNORECASE).strip()
    text = re.sub(r"^```", "", text.strip()).strip()
    text = re.sub(r"```$", "", text.strip()).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        try:
            return json.loads(text)
        except Exception:
            print("    [WARNING] Could not locate valid JSON bounds. Using fallback.")
            return fallback

    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        print("    [WARNING] JSON decoding failed. Using fallback.")
        return fallback


def save_json_atomic(path, data) -> None:
    """先寫入暫存檔再取代，避免中斷時留下損毀的 JSON。"""
    path = str(path)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)

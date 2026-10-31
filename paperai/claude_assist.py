"""把擷取的段落交給 Claude 解說（選用；需要 Anthropic API 憑證，環境變數 ANTHROPIC_API_KEY）。

每次查詢會把該段內容傳送到 Anthropic，並依用量計費。不用 API 時，閱讀器的「擷取內容」可以直接複製。
"""
MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000

SYSTEM_PROMPT = (
    "你是協助台灣大學生閱讀英文理工教科書與論文的助教。一律使用台灣繁體中文與台灣學術用語回答。"
    "數學式用 LaTeX，行內寫成 $...$，獨立公式寫成 $$...$$。"
    "回答要具體：指出關鍵概念、補上原文省略的推導步驟、說明符號意義；不確定時直接說明，不要編造。"
)


class ClaudeUnavailable(Exception):
    pass


def api_available() -> bool:
    """有沒有設定 API 憑證；沒有時閱讀器只提供複製。"""
    import os
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def ask(text: str, question: str = "", context: dict = None) -> dict:
    """回傳 {"answer": Markdown 文字, "model": 實際回答的模型}。"""
    context = context or {}
    parts = [question.strip() or "請解說下面這段內容，補上省略的推導步驟。"]
    if context.get("title"):
        parts.append(f"【文件】{context['title']}（{context.get('domain', '')}）")
    parts.append(f"【原文】\n{text}")

    try:
        import anthropic
    except ImportError:
        raise ClaudeUnavailable("尚未安裝 anthropic 套件（python -m pip install anthropic）")

    client = anthropic.Anthropic()
    try:
        # 已安裝的 SDK 版本較舊，沒有 output_config／fallbacks 參數，改由 extra_body 傳送。
        # fallbacks="default"：Claude 因安全分類器拒答時，伺服器自動改用其他模型回答。
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "\n\n".join(parts)}],
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default", "output_config": {"effort": "medium"}},
        )
    except anthropic.AuthenticationError:
        raise ClaudeUnavailable("沒有可用的 Anthropic API 憑證：請設定環境變數 ANTHROPIC_API_KEY 後重新啟動 server.py")
    except anthropic.PermissionDeniedError as e:
        raise ClaudeUnavailable(f"API 金鑰沒有權限：{e.message}")
    except anthropic.RateLimitError:
        raise ClaudeUnavailable("Claude 目前請求過多，請稍後再試")
    except anthropic.APIStatusError as e:
        raise ClaudeUnavailable(f"Claude API 錯誤（{e.status_code}）：{e.message}")
    except anthropic.APIConnectionError:
        raise ClaudeUnavailable("無法連線到 Anthropic（請確認網路）")
    except TypeError as e:  # 沒有任何憑證時，SDK 在建立請求前就會失敗
        raise ClaudeUnavailable(f"沒有可用的 Anthropic API 憑證：請設定環境變數 ANTHROPIC_API_KEY（{e}）")

    if response.stop_reason == "refusal":
        raise ClaudeUnavailable("Claude 拒絕回答這個請求")
    answer = "\n\n".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
    if response.stop_reason == "max_tokens":
        answer += "\n\n（回答過長，已截斷）"
    return {"answer": answer or "（沒有回答）", "model": getattr(response, "model", MODEL)}

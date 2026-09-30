"""把困難段落、推導與圖片交給 Claude 解說（選用；大量逐段翻譯仍用本機模型）。

兩種用法：
- 預設：build_prompt 組出提示詞，由閱讀器複製到剪貼簿，使用者自行貼到網頁版 AI（不需要 API）。
- 有 Anthropic API 憑證（環境變數 ANTHROPIC_API_KEY）時，ask 可直接在閱讀器中取得回答；
  每次查詢會把該段原文（或圖片）傳送到 Anthropic，並依用量計費。
"""
import base64
import mimetypes
from pathlib import Path

MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000

SYSTEM_PROMPT = (
    "你是協助台灣大學生閱讀英文理工教科書與論文的助教。一律使用台灣繁體中文與台灣學術用語回答。"
    "數學式用 LaTeX，行內寫成 $...$，獨立公式寫成 $$...$$。"
    "回答要具體：指出關鍵概念、補上原文省略的推導步驟、說明符號意義；不確定時直接說明，不要編造。"
)

MODES = {
    "explain": "請解說下面這段內容：先用兩三句話說明重點，再逐步補上推導或論證中省略的步驟，最後列出需要的先備知識。",
    "translate": "請檢查下面的機器翻譯：指出錯譯、漏譯或不自然的用語，並提供修正後的完整譯文。",
    "figure": "請解說這張圖：圖中呈現什麼、座標軸與符號的意義、讀者應該注意的趨勢或特徵，以及它和上下文的關係。",
}


class ClaudeUnavailable(Exception):
    pass


def api_available() -> bool:
    """有沒有設定 API 憑證；沒有時閱讀器只提供「複製提示詞」給網頁版 AI。"""
    import os
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def build_prompt(mode: str, text: str = "", translation: str = "", question: str = "",
                 context: dict = None, for_web: bool = False) -> str:
    """組出提示詞。for_web=True 時把角色說明放在開頭，方便直接貼到 claude.ai 等網頁版 AI。"""
    context = context or {}
    parts = [SYSTEM_PROMPT] if for_web else []
    parts.append(MODES.get(mode, MODES["explain"]))
    if for_web and mode == "figure":
        parts.append("（圖片已另外貼上）")
    if context.get("title"):
        parts.append(f"【文件】{context['title']}（{context.get('domain', '')}）")
    if text:
        parts.append(f"【原文】\n{text}")
    if translation:
        parts.append(f"【目前的機器翻譯】\n{translation}")
    if question.strip():
        parts.append(f"【讀者的問題】\n{question.strip()}")
    return "\n\n".join(parts)


def ask(mode: str, text: str = "", translation: str = "", question: str = "",
        image_path: Path = None, context: dict = None) -> dict:
    """回傳 {"answer": Markdown 文字, "model": 實際回答的模型}。"""
    try:
        import anthropic
    except ImportError:
        raise ClaudeUnavailable("尚未安裝 anthropic 套件（python -m pip install anthropic）")

    parts = [build_prompt(mode, text, translation, question, context)]

    content = []
    if image_path:
        media_type = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        data = base64.standard_b64encode(Path(image_path).read_bytes()).decode("ascii")
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
    content.append({"type": "text", "text": "\n\n".join(parts)})

    client = anthropic.Anthropic()
    try:
        # 已安裝的 SDK 版本較舊，沒有 output_config／fallbacks 參數，改由 extra_body 傳送。
        # fallbacks="default"：Claude 因安全分類器拒答時，伺服器自動改用其他模型回答。
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
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

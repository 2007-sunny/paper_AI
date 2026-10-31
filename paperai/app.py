"""PaperAI 本機服務：PDF 閱讀器、Ctrl 即時粗翻、本機細翻、單字查詢與問 Claude。

啟動：python server.py（會自動開啟瀏覽器），或 uvicorn server:app --host 127.0.0.1 --port 8000
"""
import asyncio
import html
import json
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import ollama
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import claude_assist, config, library, picker, rough
from .convert import _page_indices, normalize_pages
from .render import render_markdown
from .session import Worker

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
worker = Worker()


@asynccontextmanager
async def lifespan(_app):
    worker.start()
    yield


app = FastAPI(title="PaperAI", lifespan=lifespan)


@app.middleware("http")
async def reject_cross_site_writes(request: Request, call_next):
    """服務只綁定本機，但任何網頁都能對 127.0.0.1 發送請求；非本站來源的寫入一律拒絕。"""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin not in (None, f"http://{request.headers.get('host', '')}"):
            return JSONResponse({"detail": "不允許的來源"}, status_code=403)
    return await call_next(request)


def _pdf(name: str) -> Path:
    pdf = library.source_pdf(name)
    if not pdf:
        raise HTTPException(404, f"找不到這份文件的 PDF：{name}")
    return pdf


# ---------- 應用程式狀態 ----------

@app.get("/", response_class=HTMLResponse)
def index():
    page = (TEMPLATE_DIR / "app.html").read_text(encoding="utf-8")
    return (page.replace("__CSS__", (TEMPLATE_DIR / "app.css").read_text(encoding="utf-8"))
            .replace("__JS__", (TEMPLATE_DIR / "app.js").read_text(encoding="utf-8")))


@app.get("/api/state")
def app_state():
    return {"docs": library.list_documents(), "state": library.load_state(),
            "usage": library.rough_usage(), "limit": config.ROUGH_MONTHLY_CHARS,
            "claude_api": claude_assist.api_available(), "model": config.MODEL_NAME}


class StateRequest(BaseModel):
    last: str | None = None
    page: int | None = None
    zoom: float | None = None


@app.post("/api/state")
def save_state(req: StateRequest):
    library.save_state(req.last, req.page, req.zoom)
    return {"ok": True}


class WorkspaceRequest(BaseModel):
    files: list[str]
    folders: list[str]


@app.post("/api/workspace")
def save_workspace(req: WorkspaceRequest):
    library.save_workspace(req.files, req.folders)
    return {"ok": True}


@app.get("/api/folder")
def folder_files(path: str):
    if not Path(path).is_dir():
        raise HTTPException(404, f"找不到資料夾：{path}")
    sources = library.list_sources()
    return {"pdfs": [{**p, "name": library.doc_name(p["path"], sources)} for p in library.folder_pdfs(path)]}


class PickRequest(BaseModel):
    kind: str = "pdf"  # pdf | folder


@app.post("/api/pick")
def pick_path(req: PickRequest):
    """在這台電腦上開啟原生的選擇對話框；瀏覽器本身拿不到檔案的完整路徑。"""
    try:
        return {"paths": picker.pick(req.kind, "")}
    except subprocess.TimeoutExpired:
        raise HTTPException(408, "對話框逾時，請重試")
    except Exception as e:
        raise HTTPException(500, f"無法開啟對話框：{e}")


class AddRequest(BaseModel):
    path: str


@app.post("/api/docs")
def add_document(req: AddRequest):
    """加入 PDF：只記錄路徑，立即可讀可粗翻；閱讀到哪裡才分段轉檔與細翻。"""
    path = Path(req.path.strip().strip('"'))
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise HTTPException(400, f"找不到 PDF：{req.path}")
    path = path.resolve()
    name = library.doc_name(path)
    library.record_source(name, str(path))
    return {"name": name}


# ---------- 文件 ----------

def _known_doc(name: str) -> None:
    if not any(d["name"] == name for d in library.list_documents()):
        raise HTTPException(404, f"找不到文件：{name}")


@app.get("/api/docs/{name}/storage")
def doc_storage(name: str):
    _known_doc(name)
    return library.document_storage(name)


@app.delete("/api/docs/{name}")
def delete_document(name: str):
    """永久刪除 output/<名稱>/ 的翻譯資料與書庫紀錄；不會刪除原始 PDF。"""
    _known_doc(name)
    if not worker.close_document(name):
        raise HTTPException(409, "這份文件正在處理中，請稍候再刪除")
    try:
        library.delete_document(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(500, f"部分檔案無法刪除：{e}")
    return {"ok": True}


@app.get("/api/docs/{name}/info")
def doc_info(name: str):
    import fitz
    pdf = fitz.open(_pdf(name))
    try:
        total = len(pdf)
    finally:
        pdf.close()
    pages = next((d["pages"] for d in library.list_documents() if d["name"] == name), "")
    return {"name": name, "pages": _page_indices(normalize_pages(pages), total)}


@app.get("/api/docs/{name}/source.pdf")
def doc_pdf(name: str):
    """只提供書庫記錄的那個檔案（支援 Range，大型書籍可分段載入）。"""
    return FileResponse(_pdf(name), media_type="application/pdf")


@app.get("/api/docs/{name}/pages/{page}")
def doc_page(name: str, page: int):
    try:
        return rough.page_blocks(str(_pdf(name)), page)
    except IndexError:
        raise HTTPException(404, "沒有這一頁")


@app.get("/api/docs/{name}/pages/{page}/figures/{index}.png")
def doc_figure(name: str, page: int, index: int):
    try:
        return Response(rough.figure_png(str(_pdf(name)), page, index), media_type="image/png")
    except IndexError:
        raise HTTPException(404, "沒有這張圖")


class TranslateRequest(BaseModel):
    page: int
    block: str


def _block_text(name: str, page: int, block_id: str) -> str:
    blocks = rough.page_blocks(str(_pdf(name)), page)["blocks"]
    block = next((b for b in blocks if b["id"] == block_id), None)
    if block is None:
        raise HTTPException(404, "找不到這個段落")
    return block["text"]


@app.post("/api/docs/{name}/translate")
def translate_paragraph(name: str, req: TranslateRequest):
    """按下 Ctrl 時呼叫：有細翻就用細翻，否則即時粗翻。"""
    text = _block_text(name, req.page, req.block)
    fine = worker.session(name).fine_translation(req.page, text)
    if fine:
        return {"html": render_markdown(fine), "source": "fine"}
    try:
        return {"html": f"<p>{html.escape(rough.translate(text))}</p>", "source": "rough"}
    except rough.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except rough.RoughUnavailable as e:
        raise HTTPException(503, str(e))


class FocusRequest(BaseModel):
    page: int


@app.post("/api/docs/{name}/focus")
def doc_focus(name: str, req: FocusRequest):
    """閱讀位置改變：細翻只處理目前頁和下一頁。"""
    _pdf(name)
    worker.session(name).set_focus([req.page, req.page + 1])
    worker.notify()
    return {"ok": True}


@app.get("/api/docs/{name}/events")
async def doc_events(name: str, request: Request):
    _pdf(name)
    session = await run_in_threadpool(worker.session, name)
    queue = asyncio.Queue()
    session.subscribe(asyncio.get_running_loop(), queue)
    worker.notify()

    async def stream():
        try:
            yield _sse(session.progress())
            if session.status:
                yield _sse({"type": "status", "text": session.status})
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield _sse(msg)
        finally:
            session.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _sse(msg: dict) -> str:
    return f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"


# ---------- 擷取內容與問 Claude ----------

class ExtractRequest(BaseModel):
    pages: list[int]
    text: str


@app.post("/api/docs/{name}/extract")
def extract_content(name: str, req: ExtractRequest):
    """選取範圍的原文與 LaTeX 公式，取代截圖；不呼叫任何 API。"""
    _pdf(name)
    result = worker.session(name).extract(req.pages[:20], req.text[:50000])
    return {**result, "html": render_markdown(result["markdown"])}


class AskRequest(BaseModel):
    text: str
    question: str = ""


@app.post("/api/docs/{name}/ask")
def ask_claude(name: str, req: AskRequest):
    """把擷取的內容交給 Claude 解說；會傳送該段內容到 Anthropic。"""
    session = worker.session(name)
    paper_map = session.paper_map or {}
    context = {"title": paper_map.get("title", name), "domain": paper_map.get("domain", "")}
    try:
        result = claude_assist.ask(req.text[:50000], req.question[:2000], context)
    except claude_assist.ClaudeUnavailable as e:
        raise HTTPException(503, str(e))
    return {"html": render_markdown(result["answer"]), "model": result["model"]}


# ---------- 單字查詢 ----------

class LookupRequest(BaseModel):
    term: str


@app.post("/api/lookup")
def lookup_word(req: LookupRequest):
    """Google 即時譯名；語境解釋隨後由 /api/explain 的本機模型補上。"""
    try:
        return {"translation": rough.translate(req.term.strip()[:200])}
    except rough.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except rough.RoughUnavailable as e:
        raise HTTPException(503, str(e))


class ExplainRequest(BaseModel):
    term: str
    context: str | None = None


EXPLAIN_PROMPT = (
    "You are an expert bilingual scientific paper reading assistant. "
    "Explain the requested term based on the provided context, "
    "and provide 1-2 high-quality academic example sentences using this term. "
    "Use Traditional Chinese as used in Taiwan.\n\n"
    "Respond ONLY with a raw JSON object matching this schema:\n"
    '{"translation": "在此語境下的繁體中文譯名", "explanation": "針對該詞彙在學術上下文中的簡短中文解釋", '
    '"sentences": [{"en": "Example sentence.", "zh": "對應的繁體中文翻譯"}]}'
)


EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "translation": {"type": "string"},
        "explanation": {"type": "string"},
        "sentences": {"type": "array", "maxItems": 2, "items": {
            "type": "object", "properties": {"en": {"type": "string"}, "zh": {"type": "string"}},
            "required": ["en", "zh"]}},
    },
    "required": ["translation", "explanation", "sentences"],
}


@app.post("/api/explain")
def explain_word(payload: ExplainRequest):
    user_content = f"Term: {payload.term}\n" + (f"Context: {payload.context}" if payload.context else "")
    try:
        response = ollama.chat(
            model=config.MODEL_NAME,
            messages=[{"role": "system", "content": EXPLAIN_PROMPT}, {"role": "user", "content": user_content}],
            format=EXPLAIN_SCHEMA,
            think=False,  # 思考型模型關閉 thinking，查詢才會即時回應
            options={"temperature": 0.3, "num_predict": 500},  # 沒有上限時 JSON 模式可能停不下來
        )
        result = json.loads(response["message"]["content"].strip())
        return {"translation": result.get("translation", ""), "explanation": result.get("explanation", ""),
                "sentences": result.get("sentences", [])}
    except Exception as e:
        raise HTTPException(503, f"呼叫本機模型時發生錯誤：{e}")

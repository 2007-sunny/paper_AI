"""PaperAI 本機服務：書庫、閱讀器、即時翻譯、生字本與選字查詢 API。

啟動：python server.py（會自動開啟瀏覽器），或 uvicorn server:app --host 127.0.0.1 --port 8000
"""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import ollama
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from . import config, library
from .glossary import upsert_user_term
from .session import Worker

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
worker = Worker()


@asynccontextmanager
async def lifespan(_app):
    worker.start()
    yield


app = FastAPI(title="PaperAI", lifespan=lifespan)

# 以 file:// 開啟的靜態閱讀器（Origin: null）仍可使用選字查詢
app.add_middleware(CORSMiddleware, allow_origins=["null"], allow_methods=["POST"], allow_headers=["Content-Type"])


@app.middleware("http")
async def reject_cross_site_writes(request: Request, call_next):
    """服務只綁定本機，但任何網頁都能對 127.0.0.1 發送請求；非本站來源的寫入一律拒絕。"""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        own = f"http://{request.headers.get('host', '')}"
        allowed = origin in (None, own) or (origin == "null" and request.url.path == "/api/explain")
        if not allowed:
            return JSONResponse({"detail": "不允許的來源"}, status_code=403)
    return await call_next(request)


def _doc_dir(name: str) -> Path:
    root = config.OUTPUT_ROOT.resolve()
    folder = (root / name).resolve()
    if folder.parent != root or not (folder / f"{name}.md").is_file():
        raise HTTPException(404, f"找不到文件：{name}")
    return folder


# ---------- 書庫 ----------

@app.get("/", response_class=HTMLResponse)
def library_page():
    return (TEMPLATE_DIR / "library.html").read_text(encoding="utf-8")


@app.get("/api/library")
def library_state():
    docs = library.list_documents()
    for doc in docs:  # 已開啟的文件用即時進度
        session = worker.loaded_session(doc["name"])
        if session:
            p = session.progress()
            doc.update(translated=p["done"], total=p["total"])
    return {"docs": docs, "pdfs": library.list_pdfs(), "folders": library.list_folders(),
            "jobs": worker.jobs_snapshot(), "model": config.MODEL_NAME}


class FolderRequest(BaseModel):
    path: str


@app.post("/api/folders")
def add_folder(req: FolderRequest):
    try:
        return {"path": library.add_folder(req.path)}
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))


@app.post("/api/folders/remove")
def remove_folder(req: FolderRequest):
    library.remove_folder(req.path)
    return {"ok": True}


class ConvertRequest(BaseModel):
    path: str
    pages: str = ""


@app.post("/api/convert")
def convert(req: ConvertRequest):
    path = Path(req.path.strip().strip('"'))
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise HTTPException(400, f"找不到 PDF：{req.path}")
    return worker.submit_convert(str(path.resolve()), req.pages.strip())


# ---------- 閱讀器 ----------

@app.get("/read/{name}")
def read_redirect(name: str):
    # 需要結尾斜線，圖片等相對路徑才會解析到 /read/<name>/ 底下
    return RedirectResponse(f"/read/{name}/")


@app.get("/read/{name}/", response_class=HTMLResponse)
def read_page(name: str):
    _doc_dir(name)
    return worker.session(name).page_html()


@app.get("/read/{name}/{file_path:path}")
def doc_file(name: str, file_path: str):
    folder = _doc_dir(name)
    target = (folder / file_path).resolve()
    if folder not in target.parents or not target.is_file() or target.suffix.lower() not in (
            ".jpeg", ".jpg", ".png", ".gif", ".svg", ".webp"):
        raise HTTPException(404)
    return FileResponse(target)


@app.get("/api/docs/{name}/events")
async def doc_events(name: str, request: Request):
    _doc_dir(name)
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


class FocusRequest(BaseModel):
    visible: list[str]


@app.post("/api/docs/{name}/focus")
def doc_focus(name: str, req: FocusRequest):
    session = worker.loaded_session(name)
    if session:
        session.set_focus(req.visible[:50])
        worker.notify()
    return {"ok": True}


# ---------- 術語與生字本 ----------

class GlossaryRequest(BaseModel):
    term: str
    translation: str
    old_translation: str = ""
    category: str = "general"


@app.post("/api/glossary")
def update_glossary(req: GlossaryRequest):
    try:
        entry = upsert_user_term(req.term, req.translation, req.old_translation, req.category)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"entry": entry, "retranslating": worker.glossary_changed(req.term)}


class VocabRequest(BaseModel):
    term: str
    translation: str = ""
    category: str = ""
    doc: str = ""
    context: str = ""


@app.get("/api/vocab")
def get_vocab():
    return library.list_vocab()


@app.post("/api/vocab")
def add_vocab(req: VocabRequest):
    return {"added": library.add_vocab(req.term, req.translation, req.category, req.doc, req.context)}


@app.delete("/api/vocab/{vocab_id}")
def delete_vocab(vocab_id: int):
    library.delete_vocab(vocab_id)
    return {"ok": True}


# ---------- 選字查詢 ----------

class ExplainRequest(BaseModel):
    term: str
    context: str | None = None
    model_name: str | None = None


EXPLAIN_PROMPT = (
    "You are an expert bilingual scientific paper reading assistant. "
    "Translate and explain the requested term based on the provided context, "
    "and provide 1-2 high-quality academic example sentences using this term. "
    "Use Traditional Chinese as used in Taiwan.\n\n"
    "Respond ONLY with a raw JSON object matching this schema:\n"
    '{"translation": "繁體中文翻譯", "explanation": "針對該詞彙在學術上下文中的簡短中文解釋", '
    '"category": "術語類別（例如 tech, math, physics, general）", '
    '"sentences": [{"en": "Example sentence.", "zh": "對應的繁體中文翻譯"}]}'
)


@app.post("/api/explain")
def explain_word(payload: ExplainRequest):
    active_model = payload.model_name or config.MODEL_NAME
    user_content = f"Term: {payload.term}\n" + (f"Context: {payload.context}" if payload.context else "")
    try:
        response = ollama.chat(
            model=active_model,
            messages=[{"role": "system", "content": EXPLAIN_PROMPT}, {"role": "user", "content": user_content}],
            format="json",
            think=False,  # 思考型模型關閉 thinking，查詢才會即時回應
            options={"temperature": 0.3},
        )
        result = json.loads(response["message"]["content"].strip())
        return {
            "term": payload.term,
            "translation": result.get("translation", "翻譯失敗"),
            "explanation": result.get("explanation", "無法提供解釋"),
            "category": result.get("category", "custom"),
            "sentences": result.get("sentences", []),
            "model_used": active_model,
        }
    except Exception as e:
        return {"term": payload.term, "translation": "錯誤", "explanation": f"呼叫本機 AI 時發生錯誤：{e}",
                "category": "error", "sentences": [], "model_used": active_model}

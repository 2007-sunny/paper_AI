# 最後修改：Claude Opus 5.5（PaperAI 維護），20261103
"""PaperAI 本機服務：PDF 閱讀器、Ctrl 即時粗翻、本機細翻、單字查詢與問 Claude。

啟動：python server.py（會自動開啟瀏覽器），或 uvicorn server:app --host 127.0.0.1 --port 8000
"""
import asyncio
import html
import json
import os
import re
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import ollama
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import claude_assist, config, library, picker, rough
from .convert import _page_indices, normalize_pages
from .llm import active_model
from .render import render_markdown
from .session import Worker

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_LOCAL_HOST_RE = re.compile(r"(127\.0\.0\.1|localhost)(:\d+)?", re.IGNORECASE)
worker = Worker()


@asynccontextmanager
async def lifespan(_app):
    worker.start()
    yield
    worker.parser.cancel()


app = FastAPI(title="PaperAI", lifespan=lifespan)


@app.middleware("http")
async def reject_cross_site_writes(request: Request, call_next):
    """服務只綁定本機，但任何網頁都能對 127.0.0.1 發送請求；非本站來源的寫入一律拒絕。"""
    # DNS rebinding 時 Origin 和 Host 都是攻擊者的網域，讀取也可能外洩，所以每個請求都檢查 Host
    if not _LOCAL_HOST_RE.fullmatch(request.headers.get("host", "")):
        return JSONResponse({"detail": "不允許的主機"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin not in (None, f"http://{request.headers.get('host', '')}"):
            return JSONResponse({"detail": "不允許的來源"}, status_code=403)
    return await call_next(request)


def _ocr_dir(name: str) -> str:
    return str(config.Doc(name).output_dir / "ocr")


def _page_blocks(name: str, page: int) -> dict:
    return rough.page_blocks(str(_pdf(name)), page, _ocr_dir(name))


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
            "usage": library.rough_usage(), "settings": library.translation_settings(),
            "defaults": config.ADVANCED_DEFAULTS, "claude_api": claude_assist.api_available()}


class StateRequest(BaseModel):
    last: str | None = None
    page: int | None = None
    zoom: float | None = None


@app.post("/api/state")
def save_state(req: StateRequest):
    library.save_state(req.last, req.page, req.zoom)
    return {"ok": True}


class SettingsRequest(BaseModel):
    fine_mode: str | None = None
    lookahead: int | None = Field(None, ge=0, le=2)
    model: str | None = None
    monthly_chars: int | None = Field(None, ge=0, le=config.ROUGH_MAX_CHARS)
    marker_idle_minutes: int | None = Field(None, ge=1, le=60)
    chunk_timeout_minutes: int | None = Field(None, ge=1, le=30)
    zoom_sensitivity: int | None = Field(None, ge=5, le=200)
    zoom_settle_ms: int | None = Field(None, ge=50, le=1000)
    overlay_font_max: int | None = Field(None, ge=10, le=80)
    overlay_font_min: int | None = Field(None, ge=6, le=24)


def _check_mode(mode: str) -> None:
    if mode not in config.FINE_MODES:
        raise HTTPException(400, f"不支援的細翻模式：{mode}")


def _installed_models() -> list:
    try:
        return [m.model for m in ollama.list().models]
    except Exception as e:
        raise HTTPException(503, f"無法連線到 Ollama：{e}")


@app.get("/api/models")
def list_models():
    return {"models": _installed_models()}


@app.post("/api/settings")
def save_settings(req: SettingsRequest):
    changes = req.model_dump(exclude_none=True)
    if "fine_mode" in changes:
        _check_mode(changes["fine_mode"])
    if changes.get("model", active_model()) != active_model() and changes["model"] not in _installed_models():
        raise HTTPException(400, f"Ollama 沒有這個模型：{changes['model']}")
    settings = library.save_translation_settings(changes)
    worker.settings_changed()
    return settings


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


@app.get("/api/check-pdf")
def check_pdf(path: str):
    """引用跳轉的 ?pdf=：只檢查路徑，不讀 PDF 內容。允許的是開啟過的資料夾內、實際存在的 .pdf。"""
    denied = {"allowed": False, "in_library": False, "name": None}
    raw = Path(path.strip().strip('"'))
    if not path.strip() or ".." in raw.parts:
        return denied
    try:
        target = raw.resolve(strict=True)  # 也展開符號連結，避免繞出資料夾
    except (OSError, RuntimeError):
        return denied
    if not target.is_file() or target.suffix.lower() != ".pdf":
        return denied
    folders = []
    for folder in library.load_state()["folders"]:
        try:
            folders.append(Path(folder).resolve(strict=True))
        except (OSError, RuntimeError):
            continue
    if not any(folder in target.parents for folder in folders):
        return denied
    key = os.path.normcase(str(target))
    name = next((n for n, s in library.list_sources().items()
                 if not s["pages"] and os.path.normcase(s["pdf_path"]) == key), None)
    return {"allowed": True, "in_library": name is not None, "name": name}


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


class DocModeRequest(BaseModel):
    mode: str | None = None  # None 表示跟隨所有文件


@app.post("/api/docs/{name}/mode")
def save_doc_mode(name: str, req: DocModeRequest):
    _known_doc(name)
    if req.mode:
        _check_mode(req.mode)
    settings = library.save_doc_mode(name, req.mode)
    worker.settings_changed()
    return settings


@app.post("/api/docs/{name}/rebuild-ocr")
def rebuild_ocr(name: str):
    """重新 OCR 掃描頁並覆蓋原始 PDF 的文字層（使用者在設置按兩次才會呼叫）。

    這是唯一會改動原始 PDF 的地方：第一次覆蓋前原檔備份到 output/<名稱>/original/。
    """
    _known_doc(name)
    pdf = _pdf(name)
    backup = config.Doc(name).output_dir / "original" / pdf.name
    try:
        return rough.rebuild_text_layer(str(pdf), _ocr_dir(name), backup)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError:
        raise HTTPException(409, "PDF 正在被其他程式使用（例如在別的閱讀器開著），關閉後再試；原檔沒有被改動")


ANNOTATION_COLORS = ("yellow", "green", "blue", "red")


class AnnotationRequest(BaseModel):
    page: int = Field(ge=0)
    rects: list[list[float]] = Field(min_length=1, max_length=200)
    text: str = Field("", max_length=5000)
    color: str = "yellow"
    note: str = Field("", max_length=5000)
    kind: Literal["text", "box"] = "text"


class AnnotationUpdate(BaseModel):
    color: str | None = None
    note: str | None = Field(None, max_length=5000)


def _check_color(color):
    if color is not None and color not in ANNOTATION_COLORS:
        raise HTTPException(400, f"不支援的顏色：{color}")


@app.get("/api/docs/{name}/annotations")
def doc_annotations(name: str):
    _known_doc(name)
    return {"annotations": library.list_annotations(name)}


@app.post("/api/docs/{name}/annotations")
def add_annotation(name: str, req: AnnotationRequest):
    """標記與註解只存在書庫，不寫回 PDF。"""
    _known_doc(name)
    _check_color(req.color)
    if any(len(rect) != 4 or rect[2] <= rect[0] or rect[3] <= rect[1] for rect in req.rects):
        raise HTTPException(400, "標記範圍格式錯誤")
    return library.add_annotation(name, req.page, req.rects, req.text, req.color, req.note, req.kind)


@app.patch("/api/annotations/{annotation_id}")
def update_annotation(annotation_id: int, req: AnnotationUpdate):
    _check_color(req.color)
    result = library.update_annotation(annotation_id, req.color, req.note)
    if result is None:
        raise HTTPException(404, "找不到這個標記")
    return result


@app.delete("/api/annotations/{annotation_id}")
def delete_annotation(annotation_id: int):
    if not library.delete_annotation(annotation_id):
        raise HTTPException(404, "找不到這個標記")
    return {"ok": True}


class BookmarkRequest(BaseModel):
    page: int = Field(ge=0)
    y: float = Field(0, ge=0, le=1)  # 書籤位置在該頁的高度比例
    title: str = Field("", max_length=200)


class BookmarkUpdate(BaseModel):
    title: str = Field(max_length=200)


@app.get("/api/docs/{name}/bookmarks")
def doc_bookmarks(name: str):
    _known_doc(name)
    return {"bookmarks": library.list_bookmarks(name)}


@app.post("/api/docs/{name}/bookmarks")
def add_bookmark(name: str, req: BookmarkRequest):
    """自己加的書籤，和標記一樣只存在書庫。PDF 內建的目錄由前端用 PDF.js 讀，不經過這裡。"""
    _known_doc(name)
    return library.add_bookmark(name, req.page, req.y, req.title.strip() or f"第 {req.page + 1} 頁")


@app.patch("/api/bookmarks/{bookmark_id}")
def rename_bookmark(bookmark_id: int, req: BookmarkUpdate):
    result = library.rename_bookmark(bookmark_id, req.title.strip())
    if result is None:
        raise HTTPException(404, "找不到這個書籤")
    return result


class OutlineItem(BaseModel):
    title: str = Field(max_length=300)
    depth: int = Field(ge=0, le=10)
    page: int | None = Field(None, ge=0)
    y: float = Field(0, ge=0, le=1)


class OutlineRequest(BaseModel):
    items: list[OutlineItem] = Field(max_length=3000)


@app.get("/api/docs/{name}/outline")
def doc_outline(name: str):
    """自訂目錄；None 表示沒編輯過，前端改用 PDF 內建目錄。"""
    _known_doc(name)
    return {"items": library.get_outline(name)}


@app.put("/api/docs/{name}/outline")
def save_outline(name: str, req: OutlineRequest):
    """編輯過的目錄整份覆寫，只存在書庫，不寫回 PDF。"""
    _known_doc(name)
    library.save_outline(name, [item.model_dump() for item in req.items])
    return {"ok": True}


@app.delete("/api/docs/{name}/outline")
def reset_outline(name: str):
    _known_doc(name)
    library.reset_outline(name)
    return {"ok": True}


@app.delete("/api/bookmarks/{bookmark_id}")
def delete_bookmark(bookmark_id: int):
    if not library.delete_bookmark(bookmark_id):
        raise HTTPException(404, "找不到這個書籤")
    return {"ok": True}


@app.get("/api/docs/{name}/info")
def doc_info(name: str):
    import fitz
    pdf = fitz.open(_pdf(name))
    try:
        total = len(pdf)
        pages = _page_indices(normalize_pages(next((d["pages"] for d in library.list_documents() if d["name"] == name), "")), total)
        # 每頁的寬高比：不能全部用第一頁的。封面或某幾頁大小不同時，頁框被拉成錯的比例，
        # 畫面圖片跟著變形、文字層卻是真實尺寸，選字與譯文框越往頁底偏越多（Schroeder 第 135 頁偏半行）
        aspects = {p: round(pdf[p].rect.width / pdf[p].rect.height, 5) for p in pages}
    finally:
        pdf.close()
    # version：PDF 修改時間，前端加在 source.pdf 網址後，重新製作 OCR 後才不會讀到瀏覽器快取的舊檔
    return {"name": name, "pages": pages, "aspects": aspects, "version": int(_pdf(name).stat().st_mtime)}


@app.get("/api/docs/{name}/source.pdf")
def doc_pdf(name: str):
    """只提供書庫記錄的那個檔案（支援 Range，大型書籍可分段載入）。"""
    return FileResponse(_pdf(name), media_type="application/pdf")


@app.get("/api/docs/{name}/pages/{page}")
def doc_page(name: str, page: int):
    try:
        return _page_blocks(name, page)
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
    source: str = "auto"  # auto：有細翻用細翻；rough、fine：Shift+Ctrl 指定其中一種


def _block_text(name: str, page: int, block_id: str) -> str:
    blocks = _page_blocks(name, page)["blocks"]
    block = next((b for b in blocks if b["id"] == block_id), None)
    if block is None:
        raise HTTPException(404, "找不到這個段落")
    return block["text"]


@app.post("/api/docs/{name}/translate")
def translate_paragraph(name: str, req: TranslateRequest):
    """按下 Ctrl 時呼叫：有細翻就用細翻，否則即時粗翻。"""
    text = _block_text(name, req.page, req.block)
    session = worker.session(name)
    fine = session.fine_translation(req.page, text) if req.source != "rough" else None
    if fine:
        return {"html": render_markdown(fine), "source": "fine"}
    if req.source == "fine":
        session.request_fine(req.page, text)
        worker.notify()
        raise HTTPException(404, "這一段還沒有細翻，已排入細翻")
    if req.source == "auto":
        session.request_fine(req.page, text)
        worker.notify()
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
    """閱讀位置改變：細翻只處理目前頁和後面幾頁。"""
    _pdf(name)
    lookahead = library.translation_settings()["lookahead"]
    worker.session(name).set_focus(list(range(req.page, req.page + lookahead + 1)))
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
    context: str = Field("", max_length=50000)  # 滑鼠所在的 PDF 段落；只選幾個字時用來找區塊
    whole: bool = False                          # True：交回整段，不裁到選取範圍


@app.post("/api/docs/{name}/extract")
def extract_content(name: str, req: ExtractRequest):
    """選取範圍的原文與 LaTeX 公式，取代截圖；不呼叫任何 API。"""
    _pdf(name)
    result = worker.session(name).extract(req.pages[:20], req.text[:50000], req.context, req.whole)
    worker.notify()
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
            model=active_model(),
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

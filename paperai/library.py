# 最後修改：Claude Opus 5.5（PaperAI 維護），20261101
"""書庫：PDF 來源紀錄、閱讀狀態與粗翻快取，存在 output/library.db（SQLite）。

PDF 不需要複製到專案內；書庫只記錄路徑。
"""
import hashlib
import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import config

DB_PATH = config.OUTPUT_ROOT / "library.db"
MAX_PDFS = 2000  # 單次掃描上限，避免開到整顆硬碟時卡住
_lock = threading.Lock()


def _connect():
    config.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
            name TEXT PRIMARY KEY, pdf_path TEXT, pages TEXT, added_at TEXT);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS rough_cache (hash TEXT PRIMARY KEY, zh TEXT);
        CREATE TABLE IF NOT EXISTS rough_usage (month TEXT PRIMARY KEY, chars INTEGER);
    """)
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- 閱讀狀態與粗翻 ----------

def load_state() -> dict:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'state'").fetchone()
    state = json.loads(row["value"]) if row else {"last": "", "positions": {}, "zoom": 1.0}
    state.setdefault("files", [state["last"]] if state["last"] else [])  # 側邊欄開著的檔案
    state.setdefault("folders", [])
    return state


def _write_state(state: dict) -> None:
    with _lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('state', ?)", (json.dumps(state, ensure_ascii=False),))


def save_state(last: str = None, page: int = None, zoom: float = None) -> None:
    state = load_state()
    if last is not None:
        state["last"] = last
        if page is not None:
            state["positions"][last] = page
    if zoom is not None:
        state["zoom"] = zoom
    _write_state(state)


def save_workspace(files: list, folders: list) -> None:
    state = load_state()
    state.update(files=files, folders=folders)
    _write_state(state)


def translation_settings() -> dict:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'translation'").fetchone()
    return {"fine_mode": config.FINE_MODE, "doc_modes": {}, **config.ADVANCED_DEFAULTS,
            **(json.loads(row["value"]) if row else {})}


def _write_translation_settings(settings: dict) -> None:
    with _lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('translation', ?)",
                     (json.dumps(settings, ensure_ascii=False),))


def save_translation_settings(changes: dict) -> dict:
    settings = {**translation_settings(), **changes}
    _write_translation_settings(settings)
    return settings


def save_doc_mode(name: str, mode: str = None) -> dict:
    """單份文件的細翻模式；mode 為 None 時改回跟隨所有文件。"""
    settings = translation_settings()
    settings["doc_modes"].pop(name, None)
    if mode:
        settings["doc_modes"][name] = mode
    _write_translation_settings(settings)
    return settings


def fine_mode(name: str) -> str:
    settings = translation_settings()
    return settings["doc_modes"].get(name, settings["fine_mode"])


def folder_pdfs(folder: str) -> list:
    pdfs = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]  # 略過 .obsidian、.git 等隱藏資料夾
        for fn in names:
            if fn.lower().endswith(".pdf"):
                path = Path(root) / fn
                pdfs.append({"path": str(path), "label": str(path.relative_to(folder))})
                if len(pdfs) >= MAX_PDFS:
                    return pdfs
    return sorted(pdfs, key=lambda p: p["label"].lower())


def rough_cached(key: str):
    with _lock, _connect() as conn:
        row = conn.execute("SELECT zh FROM rough_cache WHERE hash = ?", (key,)).fetchone()
    return row["zh"] if row else None


def rough_store(key: str, zh: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO rough_cache VALUES (?, ?)", (key, zh))


def rough_usage() -> int:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT chars FROM rough_usage WHERE month = ?", (_month(),)).fetchone()
    return row["chars"] if row else 0


def reserve_chars(count: int, limit: int) -> bool:
    """送出前先記帳；超過上限時不記帳並回傳 False。"""
    with _lock, _connect() as conn:
        row = conn.execute("SELECT chars FROM rough_usage WHERE month = ?", (_month(),)).fetchone()
        used = row["chars"] if row else 0
        if used + count > limit:
            return False
        conn.execute("INSERT OR REPLACE INTO rough_usage VALUES (?, ?)", (_month(), used + count))
    return True


def _month() -> str:
    return datetime.now().strftime("%Y-%m")


def record_source(name: str, pdf_path: str, pages: str = None) -> None:
    with _lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO sources VALUES (?, ?, ?, ?)",
                     (name, str(Path(pdf_path).resolve()), pages or "", _now()))


def _sources() -> dict:
    with _lock, _connect() as conn:
        return {r["name"]: dict(r) for r in conn.execute("SELECT * FROM sources")}


list_sources = _sources


def doc_name(pdf_path, sources: dict = None) -> str:
    """PDF 對應的文件名稱：不同位置的同名 PDF 各有一份文件；原檔已搬走的紀錄會被新路徑接手。"""
    path = str(Path(pdf_path).resolve())
    sources = _sources() if sources is None else sources
    for name, source in sources.items():
        if source["pdf_path"] == path and not source["pages"]:
            return name
    stem = Path(path).stem
    owner = sources.get(stem)
    if owner is None or not Path(owner["pdf_path"]).is_file():
        return stem
    return f"{stem}_{hashlib.sha1(path.encode('utf-8')).hexdigest()[:6]}"


def source_pdf(name: str):
    """文件的原始 PDF 路徑；沒有紀錄時嘗試 input/<名稱>.pdf（早期放在 input/ 的文件）。"""
    source = _sources().get(name)
    if source and Path(source["pdf_path"]).is_file():
        return Path(source["pdf_path"])
    fallback = config.PROJECT_ROOT / "input" / f"{name}.pdf"
    return fallback if fallback.is_file() else None


# ---------- 文件清單 ----------

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def doc_converted(name: str) -> bool:
    return (config.OUTPUT_ROOT / name / f"{name}.md").is_file()


def list_documents() -> list:
    """加入過的 PDF，以及 output/ 下早期已轉換的文件。"""
    sources = _sources()
    names = set(sources)
    if config.OUTPUT_ROOT.is_dir():
        names |= {f.name for f in config.OUTPUT_ROOT.iterdir() if doc_converted(f.name)}
    docs = []
    for name in names:
        source = sources.get(name) or {}
        paper_map = _read_json(config.OUTPUT_ROOT / name / f"{name}_paper_map.json") or {}
        docs.append({
            "name": name,
            "title": paper_map.get("title") or name,
            "pages": source.get("pages", ""),
            "added_at": source.get("added_at", ""),
            "converted": doc_converted(name),
            "has_pdf": source_pdf(name) is not None,
        })
    return sorted(docs, key=lambda d: d["title"].lower())


# ---------- 刪除翻譯資料（永遠不動原始 PDF） ----------

def _doc_folder(name: str) -> Path:
    root = config.OUTPUT_ROOT.resolve()
    folder = (root / name).resolve()
    if folder.parent != root:
        raise ValueError(f"不合法的文件名稱：{name}")
    return folder


def document_storage(name: str) -> dict:
    files = [f for f in _doc_folder(name).rglob("*") if f.is_file()]
    return {"files": len(files), "mb": round(sum(f.stat().st_size for f in files) / 1024 ** 2, 1)}


def delete_document(name: str) -> None:
    """刪除 output/<名稱>/ 與書庫紀錄。PDF 若放在這個資料夾內就拒絕，避免連帶刪掉原檔。"""
    folder = _doc_folder(name)
    pdf = source_pdf(name)
    if pdf and folder in pdf.resolve().parents:
        raise ValueError("原始 PDF 位於翻譯資料夾內，為避免刪到原檔已停止")
    if folder.is_dir():
        shutil.rmtree(folder)
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM sources WHERE name = ?", (name,))
    state = load_state()
    state["files"] = [f for f in state["files"] if f != name]
    state["positions"].pop(name, None)
    if state["last"] == name:
        state["last"] = ""
    _write_state(state)
    save_doc_mode(name)

"""書庫：監看資料夾、PDF 來源紀錄與生字本，存在 output/library.db（SQLite）。

PDF 不需要複製到專案內；書庫只記錄路徑，並掃描使用者指定的資料夾。
"""
import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import config

DB_PATH = config.OUTPUT_ROOT / "library.db"
MAX_PDFS = 2000  # 單次掃描上限，避免誤加整顆硬碟時卡住

_lock = threading.Lock()


def _connect():
    config.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS folders (path TEXT PRIMARY KEY, added_at TEXT);
        CREATE TABLE IF NOT EXISTS sources (
            name TEXT PRIMARY KEY, pdf_path TEXT, pages TEXT, added_at TEXT);
        CREATE TABLE IF NOT EXISTS vocab (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL, translation TEXT, category TEXT,
            doc TEXT, context TEXT, added_at TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS vocab_term ON vocab (lower(term));
    """)
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- 監看資料夾 ----------

def list_folders() -> list:
    with _lock, _connect() as conn:
        return [r["path"] for r in conn.execute("SELECT path FROM folders ORDER BY added_at")]


def add_folder(path: str) -> str:
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"找不到資料夾：{path}")
    with _lock, _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO folders VALUES (?, ?)", (str(folder), _now()))
    return str(folder)


def remove_folder(path: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM folders WHERE path = ?", (path,))


def record_source(name: str, pdf_path: str, pages: str = None) -> None:
    with _lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO sources VALUES (?, ?, ?, ?)",
                     (name, str(Path(pdf_path).resolve()), pages or "", _now()))


def _sources() -> dict:
    with _lock, _connect() as conn:
        return {r["name"]: dict(r) for r in conn.execute("SELECT * FROM sources")}


# ---------- 文件與 PDF 清單 ----------

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def list_documents() -> list:
    """output/ 下所有已轉成 Markdown 的文件，附翻譯進度。"""
    sources = _sources()
    docs = []
    if not config.OUTPUT_ROOT.is_dir():
        return docs
    for folder in config.OUTPUT_ROOT.iterdir():
        md = folder / f"{folder.name}.md"
        if not md.is_file():
            continue
        paper_map = _read_json(folder / f"{folder.name}_paper_map.json") or {}
        knowledge_path = folder / f"{folder.name}_translation_knowledge.json"
        stats = {}
        if knowledge_path.is_file():
            # knowledge 可能很大，只讀開頭的 metadata 不划算；直接讀取，文件數量通常不多
            stats = ((_read_json(knowledge_path) or {}).get("metadata") or {}).get("stats") or {}
        source = sources.get(folder.name) or {}
        docs.append({
            "name": folder.name,
            "title": paper_map.get("title") or folder.name,
            "domain": paper_map.get("domain", ""),
            "translated": stats.get("translated_blocks", 0),
            "total": stats.get("translatable_blocks") or stats.get("translated_blocks", 0),
            "pdf_path": source.get("pdf_path", ""),
            "pages": source.get("pages", ""),
            "modified": md.stat().st_mtime,
        })
    return sorted(docs, key=lambda d: d["modified"], reverse=True)


def list_pdfs() -> list:
    """監看資料夾中的 PDF，標示是否已轉換。"""
    converted = {s["pdf_path"] for s in _sources().values()}
    converted_names = {f.name for f in config.OUTPUT_ROOT.iterdir()} if config.OUTPUT_ROOT.is_dir() else set()
    pdfs = []
    for folder in list_folders():
        for root, _dirs, files in os.walk(folder):
            for fn in files:
                if not fn.lower().endswith(".pdf"):
                    continue
                path = Path(root) / fn
                pdfs.append({
                    "path": str(path),
                    "name": fn,
                    "folder": folder,
                    "size_mb": round(path.stat().st_size / 1024 ** 2, 1),
                    "converted": str(path) in converted or path.stem in converted_names,
                })
                if len(pdfs) >= MAX_PDFS:
                    return pdfs
    return sorted(pdfs, key=lambda p: p["name"].lower())


# ---------- 生字本 ----------

def list_vocab() -> list:
    with _lock, _connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM vocab ORDER BY id DESC")]


def add_vocab(term: str, translation: str, category: str, doc: str = "", context: str = "") -> bool:
    """新增生字；已存在（不分大小寫）時回傳 False。"""
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO vocab (term, translation, category, doc, context, added_at) VALUES (?, ?, ?, ?, ?, ?)",
            (term.strip(), translation, category, doc, context[:500], _now()))
        return cur.rowcount > 0


def delete_vocab(vocab_id: int) -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM vocab WHERE id = ?", (vocab_id,))

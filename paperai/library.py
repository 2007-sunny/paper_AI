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
    # 複習欄位是後來加的，舊資料庫需要補欄位
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(vocab)")}
    for column, ddl in (("box", "INTEGER DEFAULT 0"), ("due", "TEXT"), ("reviews", "INTEGER DEFAULT 0"),
                        ("lapses", "INTEGER DEFAULT 0")):
        if column not in columns:
            conn.execute(f"ALTER TABLE vocab ADD COLUMN {column} {ddl}")
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- 監看資料夾 ----------

def list_folders() -> list:
    with _lock, _connect() as conn:
        return [r["path"] for r in conn.execute("SELECT path FROM folders ORDER BY added_at")]


def add_folder(path: str) -> str:
    """加入監看資料夾；貼上的是 PDF 檔案路徑時，改為加入它所在的資料夾。"""
    path = path.strip().strip('"').strip()
    folder = Path(path).expanduser().resolve()
    if folder.is_file() and folder.suffix.lower() == ".pdf":
        folder = folder.parent
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


def source_pdf(name: str):
    """文件的原始 PDF 路徑；沒有紀錄時嘗試 input/<名稱>.pdf（早期放在 input/ 的文件）。"""
    source = _sources().get(name)
    if source and Path(source["pdf_path"]).is_file():
        return Path(source["pdf_path"])
    fallback = config.PROJECT_ROOT / "input" / f"{name}.pdf"
    return fallback if fallback.is_file() else None


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


# ---------- 複習（Leitner 盒子：記得就升一盒、間隔加倍；忘記回到第 0 盒） ----------

REVIEW_INTERVAL_DAYS = [0, 1, 2, 4, 8, 16, 32, 64]


def due_vocab(limit: int = 30) -> dict:
    now = _now()
    with _lock, _connect() as conn:
        cards = [dict(r) for r in conn.execute(
            "SELECT * FROM vocab WHERE due IS NULL OR due <= ? ORDER BY due IS NOT NULL, due, id LIMIT ?",
            (now, limit))]
        total = conn.execute("SELECT COUNT(*) FROM vocab").fetchone()[0]
        due_count = conn.execute("SELECT COUNT(*) FROM vocab WHERE due IS NULL OR due <= ?", (now,)).fetchone()[0]
    return {"cards": cards, "due": due_count, "total": total}


def review_vocab(vocab_id: int, remembered: bool) -> dict:
    from datetime import timedelta
    with _lock, _connect() as conn:
        row = conn.execute("SELECT box FROM vocab WHERE id = ?", (vocab_id,)).fetchone()
        if row is None:
            raise KeyError(vocab_id)
        box = min((row["box"] or 0) + 1, len(REVIEW_INTERVAL_DAYS) - 1) if remembered else 0
        # 忘記的字 10 分鐘後再出現；記得的依盒子間隔
        delta = timedelta(days=REVIEW_INTERVAL_DAYS[box]) if remembered else timedelta(minutes=10)
        due = (datetime.now() + delta).isoformat(timespec="seconds")
        conn.execute("UPDATE vocab SET box = ?, due = ?, reviews = reviews + 1, lapses = lapses + ? WHERE id = ?",
                     (box, due, 0 if remembered else 1, vocab_id))
    return {"box": box, "due": due}


def export_anki_tsv() -> str:
    """Anki 可直接匯入的 TSV：正面為單字，背面為譯名、例句與出處。"""
    import html as _html
    lines = ["#separator:tab", "#html:true", "#tags column:3"]
    for v in list_vocab():
        back = _html.escape(v.get("translation") or "")
        if v.get("context"):
            back += f"<br><br><i>{_html.escape(v['context'])}</i>"
        if v.get("doc"):
            back += f"<br><small>{_html.escape(v['doc'])}</small>"
        tag = "PaperAI " + (v.get("category") or "").replace(" ", "_")
        term = _html.escape(v["term"])
        lines.append("\t".join(x.replace("\t", " ").replace("\n", " ") for x in (term, back, tag.strip())))
    return "\n".join(lines) + "\n"

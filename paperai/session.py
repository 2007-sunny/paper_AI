"""本機服務的背景工作：文件工作階段、分段轉檔、細翻排程與事件推送。

單一 Worker 執行緒處理所有 GPU 工作，而且只處理有人正在閱讀的文件：
依序處理目前頁與下一頁，缺少 Marker 分段就先轉檔，再細翻這一頁。離開後不再排隊。
粗翻由 API 執行緒即時處理，不經過這裡。細翻完成後以事件通知閱讀器（SSE），並定期寫回 knowledge JSON。
"""
import copy
import json
import threading
import time
import traceback

from . import config, library
from .blocks import TRANSLATABLE_TYPES, parse_blocks
from .convert import CHUNK_PAGES, OVERLAP_PAGES, ChunkTimeout, MarkerService, chunk_start
from .llm import save_json_atomic
from .pages import _block_text, _words, assign_pages
from .pipeline import build_knowledge, prepare, restore_translations
from .translator import TranslationCache, translate_block

SAVE_INTERVAL = 5.0     # 翻譯中最多每幾秒寫回一次 knowledge JSON
ERROR_BACKOFF = 30.0    # 模型呼叫失敗後暫停幾秒再試（例如 Ollama 沒開）
MATCH_PARAGRAPH = 0.7   # PDF 段落的單字出現在 Marker 區塊的比例
MATCH_BLOCK = 0.4       # Marker 區塊的單字出現在 PDF 段落的比例（跨欄段落只佔一部分）


class DocSession:
    def __init__(self, name: str, worker: "Worker"):
        self.name = name
        self.worker = worker
        self.doc = config.Doc(name)
        self.pdf_path = library.source_pdf(name)
        self.chunk_dir = self.doc.output_dir / "chunks"
        self.whole = library.doc_converted(name)  # 分段轉檔之前的文件有完整的 Markdown
        self.chunks = set()
        self.skipped = set()  # 解析逾時的分段，這次服務不再重試，維持粗翻
        self.blocks, self.by_id, self.order, self.originals, self.pending = [], {}, [], {}, set()
        self.focus = []
        if self.whole:
            self.markdown = self.doc.load_markdown()
            self.blocks = parse_blocks(self.markdown)
            # 必須在複製 originals 之前指定頁碼，翻譯完的區塊才會保留
            assign_pages(self.blocks, self.doc.path("_meta.json"), self.pdf_path)
            self._index(self.blocks)
        else:
            self.markdown = _pdf_text(self.pdf_path)  # 摘要與術語表用，不必等 Marker
            for path in sorted(self.chunk_dir.glob("*.md")):
                self._load_chunk(int(path.stem), path.read_text(encoding="utf-8"))
        self.cache = TranslationCache(self.doc.path("_translation_cache_v2.json"))
        self.lock = threading.RLock()
        self.subscribers = []
        self.last_active = time.time()
        self.paused_until = 0.0
        self.last_save = time.time()
        self.dirty = False
        self.status = ""
        self.closed = False  # 文件已刪除，不再寫檔
        self.paper_map, self.glossary = None, []
        # 摘要與術語表都已存在時不需呼叫模型，直接載入
        if self.doc.path("_paper_map.json").exists() and self.doc.path("_glossary.json").exists():
            self.paper_map, self.glossary = prepare(self.doc, self.markdown)

    # ---------- 狀態 ----------

    def progress(self) -> dict:
        with self.lock:
            done = sum(1 for bid in self.order if "units" in self.by_id[bid])
            return {"type": "progress", "done": done, "total": len(self.order), "pending": len(self.pending)}

    def _index(self, blocks: list):
        ids = [b["id"] for b in blocks if b["type"] in TRANSLATABLE_TYPES]
        self.by_id.update((b["id"], b) for b in blocks)
        self.order.extend(ids)
        # 保留未翻譯的原始區塊，翻譯時使用
        self.originals.update((bid, copy.deepcopy(self.by_id[bid])) for bid in ids)
        restore_translations(blocks, self.doc.path("_translation_knowledge.json"))
        self.pending.update(bid for bid in ids if "items" in self.by_id[bid])

    def _load_chunk(self, start: int, markdown: str):
        pages = list(range(start, start + CHUNK_PAGES + OVERLAP_PAGES))
        blocks = parse_blocks(markdown)
        assign_pages(blocks, None, self.pdf_path, pages)
        kept = [b for b in blocks if b["page"] < start + CHUNK_PAGES]
        for block in kept:
            block["id"] = f"c{start}{block['id']}"
        self.blocks.extend(kept)
        self._index(kept)
        self.chunks.add(start)

    def fine_translation(self, page: int, text: str):
        """PDF 段落對應的細翻譯文；Marker 的區塊和 PDF 段落切法不同，依單字重疊比對。"""
        words = _words(text)
        if len(words) < 3:
            return None
        best, best_score = None, 0
        with self.lock:
            for block in self.blocks:
                if "units" not in block or abs(block.get("page", -9) - page) > 1:
                    continue
                other = _words(_block_text(block))
                shared = len(words & other)
                coverage, precision = shared / len(words), shared / max(len(other), 1)
                if coverage >= MATCH_PARAGRAPH and precision >= MATCH_BLOCK and coverage + precision > best_score:
                    best, best_score = block, coverage + precision
            if best:
                return "\n\n".join(" ".join(s["zh"] for s in unit) for unit in best["units"])
        return None

    def set_status(self, text: str):
        self.status = text
        self.broadcast({"type": "status", "text": text})

    # ---------- 事件推送 ----------

    def subscribe(self, loop, queue):
        with self.lock:
            self.subscribers.append((loop, queue))
            self.last_active = time.time()

    def unsubscribe(self, queue):
        with self.lock:
            self.subscribers = [(lp, q) for lp, q in self.subscribers if q is not queue]
            self.last_active = time.time()  # 背景翻譯時間從離開閱讀頁起算

    def broadcast(self, msg: dict):
        with self.lock:
            targets = list(self.subscribers)
        for loop, queue in targets:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, msg)
            except RuntimeError:  # 連線的事件迴圈已關閉
                self.unsubscribe(queue)

    @property
    def has_viewers(self) -> bool:
        return bool(self.subscribers)

    # ---------- 排程 ----------

    def set_focus(self, pages: list):
        """閱讀器回報目前頁：只處理這些頁，離開後不再排隊。"""
        with self.lock:
            self.focus = pages
            self.last_active = time.time()

    def _next_step(self):
        """依頁序找下一件事：這一頁缺分段就轉檔，有分段就細翻。"""
        for page in self.focus:
            start = chunk_start(page)
            if not self.whole and start not in self.chunks and start not in self.skipped:
                return ("chunk", start)
            bid = next((b for b in self.order if b in self.pending and self.by_id[b].get("page") == page), None)
            if bid:
                return ("block", bid)
        return None

    def next_task(self):
        with self.lock:
            if time.time() < self.paused_until:
                return None
            step = self._next_step()
            if step is None:
                return self.save if self.dirty else None
            kind, value = step
            if kind == "chunk":
                return lambda: self._convert_chunk(value)
            if self.paper_map is None:
                return self._prepare
            return lambda: self._translate(value)

    # ---------- 工作 ----------

    def _prepare(self):
        self.set_status("第一次開啟：正在生成文件摘要與術語表…")
        paper_map, glossary = prepare(self.doc, self.markdown)
        with self.lock:
            self.paper_map, self.glossary = paper_map, glossary
        self.set_status("")

    def _convert_chunk(self, start: int):
        marker = self.worker.marker
        pages = list(range(start, start + CHUNK_PAGES + OVERLAP_PAGES))
        load_note = "" if marker.running else "（第一次要載入 Marker，約 40 秒）"
        self.set_status(f"正在解析第 {start + 1}–{start + CHUNK_PAGES} 頁{load_note}")
        try:
            markdown = marker.convert(self.pdf_path, pages)
        except ChunkTimeout:
            self.skipped.add(start)
            self.set_status(f"第 {start + 1}–{start + CHUNK_PAGES} 頁解析太久，先維持粗翻")
            return
        with self.lock:
            if self.closed:
                return
            self.chunk_dir.mkdir(exist_ok=True)
            (self.chunk_dir / f"{start}.md").write_text(markdown, encoding="utf-8")
            self._load_chunk(start, markdown)
        self.set_status("")

    def _translate(self, bid: str):
        with self.lock:
            block = copy.deepcopy(self.originals[bid])
            paper_map, glossary = self.paper_map, self.glossary
        translate_block(block, paper_map, glossary, self.cache)
        with self.lock:
            target = self.by_id[bid]
            target.clear()
            target.update(block)
            self.pending.discard(bid)
            self.dirty = True
        self.broadcast({"type": "fine", "page": block.get("page")})
        self.broadcast(self.progress())
        if time.time() - self.last_save > SAVE_INTERVAL or not self.pending:
            self.save()

    def save(self):
        with self.lock:
            if self.closed:
                return
            knowledge = build_knowledge(self.doc, self.paper_map or {}, self.glossary, self.blocks)
            data = json.loads(json.dumps(knowledge, ensure_ascii=False))  # 取得快照後即可釋放鎖
            self.dirty = False
            self.last_save = time.time()
        save_json_atomic(self.doc.path("_translation_knowledge.json"), data)

    def on_error(self, exc: Exception):
        self.paused_until = time.time() + ERROR_BACKOFF
        self.set_status(f"細翻暫停：{exc}（{int(ERROR_BACKOFF)} 秒後重試）")


class Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="paperai-worker")
        self.cv = threading.Condition()
        self.sessions = {}
        self.marker = MarkerService()
        self.running = ""  # 正在處理的文件；刪除時用

    # ---------- 對外介面（由 API 執行緒呼叫） ----------

    def session(self, name: str) -> DocSession:
        with self.cv:
            if name not in self.sessions:
                self.sessions[name] = DocSession(name, self)
                self.cv.notify()
            return self.sessions[name]

    def loaded_session(self, name: str):
        with self.cv:
            return self.sessions.get(name)

    def notify(self):
        with self.cv:
            self.cv.notify()

    def close_document(self, name: str):
        """刪除文件前呼叫：關閉工作階段。Marker 正在處理它時無法中斷，回傳 False。"""
        with self.cv:
            if self.running == name:
                return False
            session = self.sessions.pop(name, None)
        if session:
            with session.lock:
                session.closed = True
                session.pending.clear()
                session.focus = []
        return True

    # ---------- 排程 ----------

    def _next(self):
        sessions = sorted(self.sessions.values(), key=lambda s: s.last_active, reverse=True)
        for s in sessions:
            task = s.next_task() if s.has_viewers else (s.save if s.dirty else None)
            if task:
                return task, s
        return None, None

    def run(self):
        while True:
            with self.cv:
                task, session = self._next()
                while task is None:
                    self.running = ""
                    self.marker.stop_if_idle()
                    self.cv.wait(timeout=5)
                    task, session = self._next()
                self.running = session.name
            try:
                task()
            except Exception as exc:  # 單一工作失敗不能讓整個服務停擺
                traceback.print_exc()
                session.on_error(exc)


def _pdf_text(pdf_path) -> str:
    import fitz
    pdf = fitz.open(pdf_path)
    try:
        return "\n".join(page.get_text() for page in pdf)
    finally:
        pdf.close()

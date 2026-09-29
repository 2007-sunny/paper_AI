"""本機服務的背景工作：文件工作階段、翻譯排程與事件推送。

只有一張 GPU，所以轉檔與翻譯都由單一 Worker 執行緒依序處理：
1. 排隊中的 PDF 轉檔（使用者明確要求）
2. 有人正在閱讀的文件（最近捲動的優先），從目前閱讀位置往後翻譯
3. 最近 30 分鐘內開啟過、尚未翻完的文件
翻譯結果以事件推送給閱讀頁（SSE），並定期寫回 knowledge JSON。
"""
import copy
import json
import threading
import time
import traceback
from collections import deque
from datetime import datetime

from . import config, library
from .blocks import TRANSLATABLE_TYPES, parse_blocks
from .convert import convert_pdf
from .glossary import build_glossary, generate_glossary, load_user_glossary, term_pattern
from .llm import save_json_atomic
from .pipeline import build_knowledge, prepare, restore_translations
from .render import render_block, render_glossary_rows, render_reader, render_reader_html
from .translator import TranslationCache, translate_block

SAVE_INTERVAL = 5.0     # 翻譯中最多每幾秒寫回一次 knowledge JSON
ERROR_BACKOFF = 30.0    # 模型呼叫失敗後暫停幾秒再試（例如 Ollama 沒開）
# 關閉閱讀頁後，背景繼續翻譯的時間；避免開過一次的大型書籍長期佔用 GPU
BACKGROUND_WINDOW = 30 * 60


class DocSession:
    def __init__(self, name: str, worker: "Worker"):
        self.name = name
        self.worker = worker
        self.doc = config.Doc(name)
        self.markdown = self.doc.load_markdown()
        self.blocks = parse_blocks(self.markdown)
        self.by_id = {b["id"]: b for b in self.blocks}
        self.order = [b["id"] for b in self.blocks if b["type"] in TRANSLATABLE_TYPES]
        # 保留未翻譯的原始區塊，修改術語後重新翻譯時使用
        self.originals = {bid: copy.deepcopy(self.by_id[bid]) for bid in self.order}
        restore_translations(self.blocks, self.doc.path("_translation_knowledge.json"))
        self.pending = {bid for bid in self.order if "items" in self.by_id[bid]}
        self.priority = []
        self.focus = 0
        self.cache = TranslationCache(self.doc.path("_translation_cache_v2.json"))
        self.lock = threading.RLock()
        self.subscribers = []
        self.last_active = time.time()
        self.paused_until = 0.0
        self.last_save = time.time()
        self.dirty = False
        self.status = ""
        self.paper_map, self.glossary = None, []
        # 摘要與術語表都已存在時不需呼叫模型，直接載入
        if self.doc.path("_paper_map.json").exists() and self.doc.path("_glossary.json").exists():
            self.paper_map, self.glossary = prepare(self.doc, self.markdown)

    # ---------- 狀態 ----------

    def progress(self) -> dict:
        with self.lock:
            done = sum(1 for bid in self.order if "units" in self.by_id[bid])
            return {"type": "progress", "done": done, "total": len(self.order), "pending": len(self.pending)}

    def page_html(self) -> str:
        with self.lock:
            knowledge = build_knowledge(self.doc, self.paper_map or {}, self.glossary, self.blocks)
            return render_reader_html(knowledge, config.MODEL_NAME, self.name, server_mode=True)

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

    def set_focus(self, visible_ids: list):
        """閱讀頁回報目前看得到的區塊：這些優先翻譯，之後從這裡往後。"""
        with self.lock:
            index = {bid: i for i, bid in enumerate(self.order)}
            positions = [index[b] for b in visible_ids if b in index]
            if positions:
                self.focus = min(positions)
            self.priority = [b for b in visible_ids if b in self.pending]
            self.last_active = time.time()

    def _pick(self):
        for bid in self.priority:
            if bid in self.pending:
                return bid
        rotated = self.order[self.focus:] + self.order[:self.focus]
        return next((bid for bid in rotated if bid in self.pending), None)

    def next_task(self):
        with self.lock:
            if time.time() < self.paused_until:
                return None
            if self.paper_map is None:
                return self._prepare
            bid = self._pick()
            if bid is None:
                if self.dirty:
                    return self.save
                return None
            return lambda: self._translate(bid)

    # ---------- 工作 ----------

    def _prepare(self):
        self.set_status("第一次開啟：正在生成文件摘要與術語表…")
        paper_map, glossary = prepare(self.doc, self.markdown)
        with self.lock:
            self.paper_map, self.glossary = paper_map, glossary
        self.broadcast({
            "type": "meta",
            "title": paper_map.get("title", self.name),
            "summary": paper_map.get("summary", ""),
            "domain": paper_map.get("domain", ""),
            "topics": "、".join(paper_map.get("topics", [])),
            "glossary_rows": render_glossary_rows(glossary),
        })
        self.set_status("")

    def _translate(self, bid: str):
        with self.lock:
            block = copy.deepcopy(self.originals[bid])
            paper_map, glossary = self.paper_map, self.glossary
        translate_block(block, paper_map, glossary, self.cache)
        html = render_block(block, {g["term"].lower(): g for g in glossary})
        with self.lock:
            target = self.by_id[bid]
            target.clear()
            target.update(block)
            self.pending.discard(bid)
            self.dirty = True
        self.broadcast({"type": "block", "id": bid, "html": html})
        self.broadcast(self.progress())
        if time.time() - self.last_save > SAVE_INTERVAL or not self.pending:
            self.save()

    def save(self):
        with self.lock:
            knowledge = build_knowledge(self.doc, self.paper_map or {}, self.glossary, self.blocks)
            data = json.loads(json.dumps(knowledge, ensure_ascii=False))  # 取得快照後即可釋放鎖
            self.dirty = False
            self.last_save = time.time()
            finished = not self.pending
        save_json_atomic(self.doc.path("_translation_knowledge.json"), data)
        if finished:
            # 全部翻完時同步更新靜態閱讀器，離線也能開
            render_reader(data, self.doc.path("_reader.html"), config.MODEL_NAME, self.name)

    def on_error(self, exc: Exception):
        self.paused_until = time.time() + ERROR_BACKOFF
        self.set_status(f"翻譯暫停：{exc}（{int(ERROR_BACKOFF)} 秒後重試；請確認 Ollama 已啟動）")

    def on_glossary_changed(self, term: str) -> list:
        """使用者修改術語後：更新術語表，含該術語的區塊重新排入翻譯（優先）。"""
        with self.lock:
            if self.paper_map is None:
                return []
            pattern = term_pattern(term)
            affected = [bid for bid in self.order if pattern.search(" ".join(self.originals[bid]["items"]))]
            if not affected:
                return []
            auto_terms = generate_glossary(self.paper_map, self.markdown, self.doc.path("_glossary.json"))
            self.glossary = build_glossary(auto_terms, load_user_glossary(), self.markdown)
            self.pending.update(affected)
            self.priority = affected + [b for b in self.priority if b not in affected]
        self.broadcast({"type": "stale", "ids": affected})
        self.broadcast({"type": "meta", "glossary_rows": render_glossary_rows(self.glossary)})
        self.broadcast(self.progress())
        return affected


class Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="paperai-worker")
        self.cv = threading.Condition()
        self.sessions = {}
        self.convert_queue = deque()
        self.jobs = deque(maxlen=30)  # 顯示在書庫頁的轉檔紀錄

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

    def submit_convert(self, pdf_path: str, pages: str = "") -> dict:
        job = {"id": f"job{int(time.time() * 1000)}", "pdf": pdf_path, "pages": pages or "",
               "status": "排隊中", "message": "", "doc": "", "time": datetime.now().strftime("%H:%M:%S")}
        with self.cv:
            self.jobs.appendleft(job)
            self.convert_queue.append(job)
            self.cv.notify()
        return job

    def jobs_snapshot(self) -> list:
        with self.cv:
            return [dict(j) for j in self.jobs]

    def glossary_changed(self, term: str) -> int:
        with self.cv:
            sessions = list(self.sessions.values())
        count = sum(len(s.on_glossary_changed(term)) for s in sessions)
        self.notify()
        return count

    # ---------- 排程 ----------

    def _next(self):
        # 轉檔是使用者明確要求的，優先處理；翻譯只是暫停幾分鐘
        if self.convert_queue:
            job = self.convert_queue.popleft()
            return (lambda: self._convert(job)), None
        sessions = sorted(self.sessions.values(), key=lambda s: s.last_active, reverse=True)
        for s in sessions:
            if s.has_viewers:
                task = s.next_task()
                if task:
                    return task, s
        for s in sessions:
            if time.time() - s.last_active < BACKGROUND_WINDOW:
                task = s.next_task()
                if task:
                    return task, s
            elif s.dirty:  # 超過背景時間仍要把已翻譯的結果存檔
                return s.save, s
        return None, None

    def run(self):
        while True:
            with self.cv:
                task, session = self._next()
                while task is None:
                    self.cv.wait(timeout=5)
                    task, session = self._next()
            try:
                task()
            except Exception as exc:  # 單一工作失敗不能讓整個服務停擺
                traceback.print_exc()
                if session:
                    session.on_error(exc)

    def _convert(self, job: dict):
        job["status"] = "轉換中"
        try:
            _unload_ollama_models()  # Marker 與 Ollama 共用 8GB VRAM，先釋放
            name = convert_pdf(job["pdf"], job["pages"], log=lambda m: job.update(message=m))
            library.record_source(name, job["pdf"], job["pages"])
            job.update(status="完成", doc=name, message=f"已轉換為 {name}")
        except Exception as exc:
            job.update(status="失敗", message=str(exc))


def _unload_ollama_models():
    try:
        import ollama
        for m in ollama.ps().models:
            ollama.generate(model=m.model, prompt="", keep_alive=0)
    except Exception:
        pass

# 最後修改：Claude Opus 5.5（PaperAI 維護），20261103
"""本機服務的背景工作：文件工作階段、分段轉檔、細翻排程與事件推送。

單一 Worker 執行緒處理所有 GPU 工作，而且只處理有人正在閱讀的文件：
細翻模式 auto 依序處理目前頁與後面幾頁，缺少分段就先轉檔，再細翻這一頁，離開後不再排隊；
ctrl 只處理按過 Ctrl 的段落；off 完全不用 GPU。
粗翻由 API 執行緒即時處理，不經過這裡。細翻完成後以事件通知閱讀器（SSE），並定期寫回 knowledge JSON。
"""
import copy
import json
import re
import threading
import time
import traceback
import unicodedata

from . import config, library
from .blocks import TRANSLATABLE_TYPES, parse_blocks
from .convert import CHUNK_PAGES, OVERLAP_PAGES, ChunkCancelled, ChunkTimeout, chunk_start, make_parser
from .llm import save_json_atomic, unload_model
from .pages import _block_text, _words, assign_pages, word_list
from .pipeline import build_knowledge, prepare, restore_translations
from .rough import fix_font
from .translator import TranslationCache, translate_block, untranslated

SAVE_INTERVAL = 5.0     # 翻譯中最多每幾秒寫回一次 knowledge JSON
ERROR_BACKOFF = 30.0    # 模型呼叫失敗後暫停幾秒再試（例如 Ollama 沒開）
MATCH_PARAGRAPH = 0.7   # PDF 段落的單字出現在 Marker 區塊的比例
EDGE_WORDS = 8          # 擷取內容時，用選取開頭與結尾各幾個單字定位區塊
_MD_MATH_RE = re.compile(r"\$\$.+?\$\$|\$[^$\n]+\$", re.S)
_MD_CHUNK_RE = re.compile(r"\$\$.+?\$\$|\$[^$\n]+\$|\S+", re.S)  # 以空白分開的片段，公式算一段
_LETTERS_RE = re.compile(r"[^\W\d_]+")


def _md_words(md: str) -> list:
    """Markdown 裡公式以外的單字與位置 [(單字, 開始, 結束)]；單字規則同 word_list。"""
    masked = _MD_MATH_RE.sub(lambda m: " " * len(m.group()), md)
    return [(w, m.start(), m.end()) for m in _LETTERS_RE.finditer(masked) for w in word_list(m.group())]


def _trim_to_selection(md: str, text: str):
    """把擷取到的整段 Markdown 裁到選取範圍；找不到選取的開頭或結尾時回傳 None（交回整段）。

    PDF 文字和 Markdown 只有一般單字對得上（公式在 Markdown 是 LaTeX），所以先用前後幾個單字定位，
    再依選取開頭／結尾在第一個與最後一個單字外面還有幾個片段（|Im|、τm、句點），往外多包幾個 Markdown 片段。
    """
    sel, spans = word_list(text), _md_words(md)
    if not sel or not spans:
        return None
    words = [w for w, _, _ in spans]

    def find(seq, near=None):
        hits = [i for i in range(len(words) - len(seq) + 1) if words[i:i + len(seq)] == seq]
        if not hits:
            return None
        return hits[0] if near is None else min(hits, key=lambda i: abs(i - near))

    start = first = None
    for skip in range(min(3, len(sel))):  # 開頭幾個字可能是換行斷字（volt-age），往後試
        for k in [k for k in ((3, 2) if len(sel) - skip >= 2 else (1,)) if k <= len(sel) - skip]:
            i = find(sel[skip:skip + k])
            if i is not None:
                start, first = i, sel[skip]
                break
        if start is not None:
            break
    end = last = None
    for skip in range(min(3, len(sel))):
        for k in [k for k in ((3, 2) if len(sel) - skip >= 2 else (1,)) if k <= len(sel) - skip]:
            seq = sel[len(sel) - skip - k:len(sel) - skip]
            i = find(seq, near=(start or 0) + len(sel) - skip - k)
            if i is not None and (start is None or i + k - 1 >= start):
                end, last = i + k - 1, seq[-1]
                break
        if end is not None:
            break
    if start is None or end is None or end < start:
        return None
    begin, finish = spans[start][1], spans[end][2]

    flat = unicodedata.normalize("NFKC", text).lower()
    hits_first = [m.start() for m in re.finditer(rf"(?<![a-z]){re.escape(first)}(?![a-z])", flat)]
    hits_last = [m.end() for m in re.finditer(rf"(?<![a-z]){re.escape(last)}(?![a-z])", flat)]
    head = flat[:hits_first[0]] if hits_first else ""
    tail = flat[hits_last[-1]:] if hits_last else ""
    if head.split():  # 選取從公式或短字開始（|Im| is the …）：往前多包同樣多個片段
        before = list(_MD_CHUNK_RE.finditer(md[:begin]))
        if before:
            begin = before[-min(len(head.split()), len(before))].start()
    if tail.strip():  # 選取以公式或標點結束（… and τm、used.）：往後多包
        after = list(_MD_CHUNK_RE.finditer(md, finish))
        n = len(tail.split()) or 1
        if after:
            finish = after[min(n, len(after)) - 1].end()
    return md[begin:finish].strip()
MATCH_BLOCK = 0.4       # Marker 區塊的單字出現在 PDF 段落的比例（跨欄段落只佔一部分）
MATCH_RUN_BLOCK = 0.6   # 一段跨好幾個區塊時，每個區塊至少這麼多單字在段落裡，才算這段的一部分
RUN_TRIM = 0.05         # 頭尾的區塊拿掉後涵蓋率下降不到這麼多，就不算這段的一部分
EDGE_ANCHOR = 3         # 頭尾區塊的前（後）幾個單字和 PDF 段落相同，就是段落的開頭（結尾），不修剪


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
        self.retried = set()  # 細翻沒翻成中文、已重翻過一次的區塊
        self.blocks, self.by_id, self.order, self.originals, self.pending = [], {}, [], {}, set()
        self.focus = []
        self.requests = []  # ctrl 模式等待處理的 (頁碼, 段落文字)；文字為空表示只需要解析這一頁
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

    def _match_blocks(self, page: int, text: str, translated: bool = True) -> list:
        """PDF 段落對應的區塊（依文件順序）；Marker／MinerU 的區塊和 PDF 段落切法不同，依單字重疊比對。

        先找單一區塊；找不到時，PDF 段落可能跨好幾個區塊（中間夾公式、參考文獻在 PDF 合成一塊），
        改找連續的區塊：每個區塊大部分單字都在段落裡，合起來涵蓋段落的大部分。中間的公式等不翻譯的區塊也一起回傳。
        translated=True 時只回傳已全部細翻的結果。
        """
        words = _words(text)
        if len(words) < 3:
            return []
        near = [b for b in self.blocks if abs(b.get("page", -9) - page) <= 1]
        best, best_score = None, 0
        for block in near:
            if block["id"] not in self.originals or (translated and "units" not in block):
                continue
            other = _words(_block_text(block))
            shared = len(words & other)
            coverage, precision = shared / len(words), shared / max(len(other), 1)
            if coverage >= MATCH_PARAGRAPH and precision >= MATCH_BLOCK and coverage + precision > best_score:
                best, best_score = block, coverage + precision
        if best:
            return [best]

        runs, run = [], []
        for block in near:
            if block["id"] not in self.originals:  # 公式、圖等：夾在中間不打斷
                if run:
                    run.append(block)
                continue
            other = _words(_block_text(block))
            shared = len(words & other)
            # 標題不併進段落（Arfken 5.2 節的標題字都出現在下一段裡）
            if block["type"] != "heading" and shared >= 3 and shared / max(len(other), 1) >= MATCH_RUN_BLOCK:
                run.append(block)
            elif run:
                runs.append(run)
                run = []
        if run:
            runs.append(run)

        def coverage(blocks):
            members = [b for b in blocks if b["id"] in self.originals]
            return len(words & set().union(*(_words(_block_text(b)) for b in members))) / len(words) if members else 0

        # PDF 段落開頭與結尾的幾個字：頭尾區塊若正好是段落的開頭／結尾，就算單字都被別的區塊涵蓋也不能去掉
        # （規格書的條列：「0.1Hz ~ 25 MHz Resolution」幾乎全是數字）
        order = word_list(text)
        head, tail = order[:EDGE_ANCHOR], order[-EDGE_ANCHOR:]

        def anchored(part, at_start):
            if at_start:
                return word_list(_block_text(part[0]))[:EDGE_ANCHOR] == head
            return word_list(_block_text(part[-1]))[-EDGE_ANCHOR:] == tail

        best, best_coverage = [], 0
        for run in runs:
            # 頭尾的區塊拿掉後涵蓋率幾乎不變，就是相鄰段落的一句（只因常用字重疊而被併進來），去掉
            while True:
                while run and run[-1]["id"] not in self.originals:  # 頭尾的公式不屬於這段
                    run.pop()
                while run and run[0]["id"] not in self.originals:
                    run.pop(0)
                full = coverage(run)
                trimmed = next((part for part, at_start in ((run[1:], True), (run[:-1], False))
                                if len(part) > 1 and not anchored(run, at_start)
                                and coverage(part) >= full - RUN_TRIM), None)
                if trimmed is None:
                    break
                run = trimmed
            if sum(b["id"] in self.originals for b in run) < 2:
                continue
            full = coverage(run)
            if full >= MATCH_PARAGRAPH and full > best_coverage:
                best, best_coverage = run, full
        if translated and any("units" not in b for b in best if b["id"] in self.originals):
            return []
        return best

    def fine_translation(self, page: int, text: str):
        """有細翻就回傳（跨好幾個區塊時依序接起來，公式保留原文）；
        模型原樣交回英文時當作沒有，改顯示粗翻，並重新細翻一次。"""
        with self.lock:
            blocks = self._match_blocks(page, text)
            parts, redo = [], False
            for block in blocks:
                if block["id"] not in self.originals:
                    if block.get("type") == "math":
                        parts.append(block.get("source", ""))
                    continue
                zh = "\n\n".join(" ".join(s["zh"] for s in unit) for unit in block["units"])
                if untranslated(zh):
                    redo = True
                    self._retry(block, page, text)
                parts.append(zh)
            return "\n\n".join(parts) if parts and not redo else None

    def _retry(self, block: dict, page: int, text: str) -> None:
        bid = block["id"]
        if bid in self.retried:  # 每段只重試一次，避免模型一直交回英文時反覆佔用 GPU
            return
        self.retried.add(bid)
        block.clear()
        block.update(copy.deepcopy(self.originals[bid]))
        self.pending.add(bid)
        if self.mode == "ctrl" and (page, text) not in self.requests:
            self.requests.append((page, text))

    def _source_text(self, block: dict) -> str:
        """區塊的原文 Markdown，公式保留 LaTeX；已翻譯的區塊要從 originals 取。"""
        original = self.originals.get(block["id"], block)
        if original["type"] == "heading":
            return "#" * original.get("level", 1) + " " + original["items"][0]
        if original["type"] == "list":
            return "\n".join(f"- {item}" for item in original["items"])
        if "source" in original:
            return original["source"]
        return "\n".join(original["items"])

    def extract(self, pages: list, text: str, context: str = "", whole: bool = False) -> dict:
        """選取範圍對應的原文（公式是 LaTeX），包含夾在中間的獨立公式。waiting 表示這些頁還在解析。

        先用選取的開頭與結尾各幾個單字找出區塊（只選幾個字時改用滑鼠所在段落 context 找，避免對到別段的同一個字），
        再把區塊裁到選取的範圍（_trim_to_selection）；whole=True 或裁不出來時交回整段，trimmed 表示有沒有裁。
        """
        locate = context if context and len(word_list(text)) < 4 else text
        result = self._extract_blocks(pages, locate)
        if whole or not result["markdown"]:
            return {**result, "trimmed": False}
        trimmed = _trim_to_selection(result["markdown"], text)
        return {**result, "markdown": trimmed or result["markdown"], "trimmed": bool(trimmed)}

    def _extract_blocks(self, pages: list, text: str) -> dict:
        """選取範圍涉及的整個區塊。用開頭與結尾各幾個單字定位，才不會把單字相近的標題或前段一起帶進來。"""
        words = word_list(text)
        head, tail = set(words[:EDGE_WORDS]), set(words[-EDGE_WORDS:])
        with self.lock:
            missing = [p for p in pages if self._missing_chunk(p) is not None]
            waiting = bool(missing) and self.mode != "off"
            if self.mode == "ctrl":
                self.requests.extend((p, "") for p in missing if (p, "") not in self.requests)
            blocks = sorted((b for b in self.blocks if b.get("page") in pages), key=lambda b: b["page"])
            scored = [(i, _words(self._source_text(b))) for i, b in enumerate(blocks) if b["type"] in TRANSLATABLE_TYPES]
            if not scored or not head:
                return {"waiting": waiting, "markdown": ""}
            start_score, start = max((len(head & w), -i) for i, w in scored)
            end_score, end = max((len(tail & w), i) for i, w in scored)
            start = -start
            if start_score * 2 < len(head) or end_score * 2 < len(tail) or end < start:
                return {"waiting": waiting, "markdown": ""}
            chosen = [b for b in blocks[start:end + 1] if b["type"] != "image"]
            return {"waiting": waiting, "markdown": "\n\n".join(fix_font(self._source_text(b)) for b in chosen)}

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

    @property
    def mode(self) -> str:
        return library.fine_mode(self.name)

    def request_fine(self, page: int, text: str):
        """ctrl 模式：使用者按 Ctrl 的段落排入細翻。"""
        with self.lock:
            if self.mode == "ctrl" and (page, text) not in self.requests:
                self.requests.append((page, text))

    def _missing_chunk(self, page: int):
        start = chunk_start(page)
        return None if self.whole or start in self.chunks or start in self.skipped else start

    def _focus_step(self):
        """依頁序找下一件事：這一頁缺分段就轉檔，有分段就細翻。"""
        for page in self.focus:
            start = self._missing_chunk(page)
            if start is not None:
                return ("chunk", start)
            bid = next((b for b in self.order if b in self.pending and self.by_id[b].get("page") == page), None)
            if bid:
                return ("block", bid)
        return None

    def _request_step(self):
        """逐一處理按過 Ctrl 的段落；找不到對應區塊就維持粗翻。"""
        while self.requests:
            page, text = self.requests[0]
            start = self._missing_chunk(page)
            if start is not None:
                return ("chunk", start)
            # 一段可能對到好幾個區塊：逐一翻完才移除這個請求
            blocks = self._match_blocks(page, text, translated=False) if text else []
            bid = next((b["id"] for b in blocks if b["id"] in self.pending), None)
            if bid:
                return ("block", bid)
            self.requests.pop(0)
        return None

    def _next_step(self):
        mode = self.mode
        return self._focus_step() if mode == "auto" else self._request_step() if mode == "ctrl" else None

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
        parser = self.worker.parser
        pages = list(range(start, start + CHUNK_PAGES + OVERLAP_PAGES))
        load_note = "" if parser.running else "（第一次要載入解析模型，約 1 分鐘）"
        self.set_status(f"正在解析第 {start + 1}–{start + CHUNK_PAGES} 頁{load_note}")
        unload_model()  # 兩邊同時佔著 VRAM 時解析會慢好幾倍，重新載入本機模型只要約 15 秒
        try:
            markdown = parser.convert(self.pdf_path, pages)
        except ChunkTimeout:
            self.skipped.add(start)
            self.set_status(f"第 {start + 1}–{start + CHUNK_PAGES} 頁解析太久，先維持粗翻")
            return
        except ChunkCancelled:
            self.set_status("")
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
        if self.status:  # 例如上一段的解析逾時提示
            self.set_status("")
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
        self.parser = make_parser()
        self.running = ""  # 正在處理的文件；刪除時用
        self.release_gpu = False  # 切到關閉後，等手上的工作結束就卸載本機模型

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

    def _gpu_wanted(self) -> bool:
        return any(s.has_viewers and s.mode != "off" for s in self.sessions.values())

    def settings_changed(self):
        """翻譯設定改變：正在閱讀的文件都不需要 GPU 時，立刻停掉 Marker 並卸載模型。"""
        with self.cv:
            wanted = self._gpu_wanted()
            self.release_gpu = not wanted
            self.cv.notify()
        if not wanted:
            self.parser.cancel()

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
        self.parser.reset()
        while True:
            with self.cv:
                task, session = self._next()
                while task is None:
                    self.running = ""
                    self.parser.stop_if_idle()
                    if self.release_gpu:
                        self.release_gpu = False
                        if not self._gpu_wanted():
                            self.parser.cancel()
                            unload_model()
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

(() => {
    const config = window.PAPERAI || {};
    const serverMode = !!config.serverMode;
    const docApi = `/api/docs/${encodeURIComponent(config.baseName || '')}`;
    const $ = (id) => document.getElementById(id);
    const content = document.querySelector('.reader-content');

    document.querySelectorAll('.server-only').forEach((el) => { el.hidden = !serverMode; });
    document.querySelectorAll('.static-only').forEach((el) => { el.hidden = serverMode; });

    // ---------- 共用工具 ----------
    function load(key, fallback) {
        try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch (e) { return fallback; }
    }
    function save(key, value) {
        try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) {}
    }
    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
    }
    async function postJson(url, body, method = 'POST') {
        const res = await fetch(url, {
            method, headers: { 'Content-Type': 'application/json' },
            body: body === undefined ? undefined : JSON.stringify(body)
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        return data;
    }
    function showNotification(msg) {
        const bar = $('notif-bar');
        bar.textContent = msg;
        bar.style.display = 'block';
        clearTimeout(showNotification.timer);
        showNotification.timer = setTimeout(() => { bar.style.display = 'none'; }, 2500);
    }
    function markWideMath(root) {
        // 只標記比所在欄還寬的行內公式；全部加捲動條會讓每個公式都出現灰色 bar
        root.querySelectorAll('.lang mjx-container:not([display="true"])').forEach((m) => {
            m.classList.remove('math-wide');
            const lang = m.closest('.lang');
            if (lang && lang.clientWidth && m.getBoundingClientRect().width > lang.clientWidth) m.classList.add('math-wide');
        });
    }
    function typeset(el) {
        if (window.MathJax && MathJax.typesetPromise) MathJax.typesetPromise([el]).then(() => markWideMath(el)).catch(() => {});
    }
    window.addEventListener('paperai-math-ready', () => markWideMath(document));
    let wideTimer = null;
    const remeasure = () => { clearTimeout(wideTimer); wideTimer = setTimeout(() => markWideMath(document), 300); };
    window.addEventListener('resize', remeasure);

    // ---------- 檢視模式與主題 ----------
    function switchView(mode) {
        document.body.setAttribute('data-view', mode);
        document.querySelectorAll('.tab-btn').forEach((btn) =>
            btn.classList.toggle('active', btn.dataset.viewBtn === mode));
        save('paperai_view', mode);
        remeasure();  // 對照與單欄的欄寬不同
    }
    document.querySelectorAll('.tab-btn').forEach((btn) =>
        btn.addEventListener('click', () => switchView(btn.dataset.viewBtn)));
    switchView(load('paperai_view', 'parallel'));

    $('theme-toggle').addEventListener('click', () => {
        const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        try { localStorage.setItem('paperai_theme', next); } catch (e) {}
    });

    // ---------- 側欄 ----------
    function openDrawer(section) {
        $('side-drawer').classList.add('open');
        $('side-drawer').setAttribute('aria-hidden', 'false');
        $('drawer-backdrop').classList.add('open');
        const target = $({ help: 'help-panel', toc: 'toc-panel' }[section] || 'vocab-panel');
        setTimeout(() => target.scrollIntoView({ block: 'start' }), 80);
    }
    function closeDrawer() {
        $('side-drawer').classList.remove('open');
        $('side-drawer').setAttribute('aria-hidden', 'true');
        $('drawer-backdrop').classList.remove('open');
    }
    document.querySelectorAll('[data-open-drawer]').forEach((btn) =>
        btn.addEventListener('click', () => openDrawer(btn.dataset.openDrawer)));
    $('drawer-close').addEventListener('click', closeDrawer);
    $('drawer-backdrop').addEventListener('click', closeDrawer);

    // ---------- 目錄 ----------
    function plainText(el) {
        // MathJax 渲染後的節點含輔助文字，放進目錄會變成亂碼，先移除
        const clone = el.cloneNode(true);
        clone.querySelectorAll('mjx-container').forEach((m) => m.remove());
        return clone.textContent.replace(/\s+/g, ' ').trim();
    }
    function buildToc() {
        const items = [...document.querySelectorAll('.block-heading')].map((sec) => {
            const heading = sec.querySelector('.en h2, .en h3, .en h4, .en h5, .en h6');
            if (!heading) return '';
            const zh = sec.querySelector('.zh h2, .zh h3, .zh h4, .zh h5, .zh h6');
            const zhText = zh && !sec.classList.contains('pending') ? plainText(zh) : '';
            return `<a href="#${sec.id}" class="toc-${heading.tagName.slice(1)}">${escapeHtml(plainText(heading))}` +
                (zhText ? `<span class="toc-zh">${escapeHtml(zhText)}</span>` : '') + '</a>';
        });
        $('toc').innerHTML = items.join('') || '<p class="empty-state">這份文件沒有標題。</p>';
    }
    $('toc').addEventListener('click', (e) => { if (e.target.closest('a')) closeDrawer(); });
    buildToc();

    // ---------- 句子對照 ----------
    content.addEventListener('mouseover', (e) => {
        const sent = e.target.closest('.sentence');
        if (!sent || sent.classList.contains('hl')) return;
        document.querySelectorAll('.sentence.hl').forEach((s) => s.classList.remove('hl'));
        document.querySelectorAll(`.sentence[data-block="${sent.dataset.block}"][data-sid="${sent.dataset.sid}"]`)
            .forEach((s) => s.classList.add('hl'));
    });
    content.addEventListener('mouseleave', () =>
        document.querySelectorAll('.sentence.hl').forEach((s) => s.classList.remove('hl')));

    // ---------- 生字本（本機服務存 SQLite，靜態檔存瀏覽器） ----------
    let vocabList = [];

    const vocabStore = serverMode ? {
        async list() { return (await fetch('/api/vocab')).json(); },
        async add(item) { return (await postJson('/api/vocab', { ...item, doc: config.baseName })).added; },
        async remove(item) { await postJson(`/api/vocab/${item.id}`, undefined, 'DELETE'); },
    } : {
        async list() { return load('vocab_list', []); },
        async add(item) {
            const list = load('vocab_list', []);
            if (list.some((v) => v.term.toLowerCase() === item.term.toLowerCase())) return false;
            save('vocab_list', [...list, item]);
            return true;
        },
        async remove(item) {
            save('vocab_list', load('vocab_list', []).filter((v) => v.term !== item.term));
        },
    };

    async function refreshVocab() {
        try { vocabList = await vocabStore.list(); } catch (e) { vocabList = []; }
        const box = $('vocab-list');
        if (!vocabList.length) {
            box.innerHTML = '<p class="empty-state">尚未加入任何生字。</p>';
            return;
        }
        box.innerHTML = vocabList.map((item, i) => `
            <div class="vocab-item">
                <div><div class="term-name">${escapeHtml(item.term)}</div>
                <div class="sub">${escapeHtml(item.translation)}${item.category ? ' · ' + escapeHtml(item.category) : ''}</div></div>
                <button class="delete-btn" data-remove-vocab="${i}" aria-label="刪除">&times;</button>
            </div>`).join('');
    }

    async function addWordToVocab(term, translation, category, context = '') {
        try {
            const added = await vocabStore.add({ term, translation, category, context });
            showNotification(added ? `已加入「${term}」。` : `「${term}」已在生字本中。`);
            refreshVocab();
        } catch (e) {
            showNotification(`加入失敗：${e.message}`);
        }
    }

    $('vocab-list').addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-remove-vocab]');
        if (!btn) return;
        await vocabStore.remove(vocabList[Number(btn.dataset.removeVocab)]);
        refreshVocab();
    });

    $('download-vocab').addEventListener('click', () => {
        const blob = new Blob([JSON.stringify({ unknown_words: vocabList }, null, 2)], { type: 'application/json' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `${config.baseName || 'paper'}_vocab.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(a.href);
    });

    // ---------- 術語：加入生字本或修正譯名 ----------
    let activeTerm = null;

    function openTermModal(el) {
        activeTerm = { term: el.dataset.term, translation: el.dataset.translation, category: el.dataset.category,
                       context: el.closest('.sentence')?.innerText || '' };
        $('term-title').textContent = activeTerm.term;
        $('term-current').textContent = activeTerm.translation;
        $('term-input').value = activeTerm.translation;
        $('term-modal').classList.add('open');
        if (serverMode) setTimeout(() => $('term-input').select(), 50);
    }
    function closeTermModal() { $('term-modal').classList.remove('open'); }

    $('term-add-vocab').addEventListener('click', () => {
        addWordToVocab(activeTerm.term, activeTerm.translation, activeTerm.category, activeTerm.context);
        closeTermModal();
    });
    $('term-close').addEventListener('click', closeTermModal);
    $('term-modal').addEventListener('click', (e) => { if (e.target === $('term-modal')) closeTermModal(); });

    async function saveTerm() {
        const translation = $('term-input').value.trim();
        if (!translation || translation === activeTerm.translation) { closeTermModal(); return; }
        try {
            const res = await postJson('/api/glossary', {
                term: activeTerm.term, translation, old_translation: activeTerm.translation, category: activeTerm.category
            });
            showNotification(`已更新「${activeTerm.term}」→「${translation}」，${res.retranslating} 個段落重新翻譯中`);
            closeTermModal();
        } catch (e) {
            showNotification(`儲存失敗：${e.message}`);
        }
    }
    $('term-save').addEventListener('click', saveTerm);
    $('term-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') saveTerm(); });

    document.addEventListener('click', (e) => {
        const term = e.target.closest('.term');
        if (term) {
            e.stopPropagation();
            openTermModal(term);
            return;
        }
        const add = e.target.closest('[data-add-term]');
        if (add) addWordToVocab(add.dataset.addTerm, add.dataset.addTranslation, add.dataset.addCategory);
    });

    // ---------- AI 查詢（server.py） ----------
    const modelSelect = $('ai-model-select');
    modelSelect.innerHTML = `<option value="${escapeHtml(config.defaultModel)}">${escapeHtml(config.defaultModel)}（預設）</option>`;

    let aiHistory = load('ai_translation_history', []);

    async function explainTerm(term, context) {
        try {
            return await postJson(`${config.apiBase || ''}/api/explain`, { term, context, model_name: modelSelect.value });
        } catch (error) {
            return { term, translation: '查詢失敗', category: 'error', sentences: [], model_used: '—',
                     explanation: '無法連線到本機服務，請先執行 python server.py' };
        }
    }

    function fillModal(data) {
        $('modal-title').textContent = data.term;
        $('modal-translation').textContent = data.translation;
        $('modal-category').textContent = data.category || '—';
        $('modal-explanation').textContent = data.explanation;
        $('modal-model').textContent = data.model_used || '—';
        const sentences = data.sentences || [];
        $('modal-sentences').innerHTML = sentences.length
            ? sentences.map((s) => `<li>${escapeHtml(s.en)}<br><span class="zh-line">${escapeHtml(s.zh)}</span></li>`).join('')
            : '<li>無示範例句</li>';
        $('ai-modal').classList.add('open');
    }

    async function lookupAndAdd(term, context) {
        fillModal({ term, translation: '讀取中…', category: '—', explanation: 'AI 正在分析上下文，請稍候…',
                    sentences: [], model_used: modelSelect.value });
        const result = await explainTerm(term, context);
        fillModal(result);
        if (result.category !== 'error') {
            aiHistory = [result, ...aiHistory.filter((h) => h.term.toLowerCase() !== term.toLowerCase())];
            save('ai_translation_history', aiHistory);
            renderHistory();
            addWordToVocab(result.term, result.translation, result.category, context);
        }
    }

    function renderHistory() {
        const box = $('ai-history');
        if (!aiHistory.length) {
            box.innerHTML = '<p class="empty-state">尚無查詢紀錄。</p>';
            return;
        }
        box.innerHTML = aiHistory.map((item, i) => `
            <div class="history-item">
                <div><div class="term-name" data-open-history="${i}">${escapeHtml(item.term)}</div>
                <div class="sub">${escapeHtml(item.translation)} · ${escapeHtml(item.model_used)}</div></div>
                <button class="delete-btn" data-remove-history="${i}" aria-label="刪除">&times;</button>
            </div>`).join('');
    }

    $('ai-history').addEventListener('click', (e) => {
        const open = e.target.closest('[data-open-history]');
        if (open) fillModal(aiHistory[Number(open.dataset.openHistory)]);
        const remove = e.target.closest('[data-remove-history]');
        if (remove) {
            aiHistory.splice(Number(remove.dataset.removeHistory), 1);
            save('ai_translation_history', aiHistory);
            renderHistory();
        }
    });
    $('clear-history').addEventListener('click', () => {
        if (!confirm('確定要清除全部 AI 查詢紀錄？')) return;
        aiHistory = [];
        save('ai_translation_history', aiHistory);
        renderHistory();
    });
    $('modal-close').addEventListener('click', () => $('ai-modal').classList.remove('open'));
    $('ai-modal').addEventListener('click', (e) => {
        if (e.target === $('ai-modal')) $('ai-modal').classList.remove('open');
    });

    // ---------- 選字浮動按鈕 ----------
    const popup = $('selection-popup');
    let pending = { term: '', context: '' };

    function cleanTerm(text) {
        return text.trim().replace(/^[\s.,;:!?()[\]{}"'「」（），。]+|[\s.,;:!?()[\]{}"'「」（），。]+$/g, '').slice(0, 80);
    }
    function contextOf(node) {
        const el = node && (node.nodeType === Node.TEXT_NODE ? node.parentElement : node);
        const sentence = el && el.closest('.sentence');
        return (sentence || el)?.innerText?.slice(0, 1000) || '';
    }

    document.addEventListener('mouseup', (e) => {
        if (e.target === popup) return;
        setTimeout(() => {
            const sel = window.getSelection();
            const term = sel ? cleanTerm(sel.toString()) : '';
            if (!term || !sel.rangeCount || !content.contains(sel.anchorNode)) {
                popup.style.display = 'none';
                return;
            }
            pending = { term, context: contextOf(sel.anchorNode) };
            const rect = sel.getRangeAt(0).getBoundingClientRect();
            popup.style.left = `${Math.min(rect.left + rect.width / 2, window.innerWidth - 170)}px`;
            popup.style.top = `${Math.max(rect.top - 44, 8)}px`;
            popup.style.display = 'block';
        }, 0);
    });
    popup.addEventListener('mousedown', (e) => e.preventDefault());
    popup.addEventListener('click', async () => {
        popup.style.display = 'none';
        window.getSelection()?.removeAllRanges();
        if (pending.term) await lookupAndAdd(pending.term, pending.context);
    });

    // ---------- 本機服務：即時翻譯 ----------
    if (serverMode) {
        const live = $('live-status');
        let statusText = '';
        let progressText = '';
        const updateLive = () => {
            const text = [statusText, progressText].filter(Boolean).join('　');
            live.textContent = text;
            live.hidden = !text;
        };

        let tocTimer = null;
        const handlers = {
            block(msg) {
                const old = $(msg.id);
                if (!old) return;
                const tpl = document.createElement('template');
                tpl.innerHTML = msg.html.trim();
                const el = tpl.content.firstElementChild;
                el.classList.add('flash');
                old.replaceWith(el);
                observer.observe(el);
                typeset(el);
                if (el.classList.contains('block-heading')) {
                    clearTimeout(tocTimer);
                    tocTimer = setTimeout(buildToc, 500);
                }
            },
            stale(msg) { msg.ids.forEach((id) => $(id)?.classList.add('stale')); },
            progress(msg) {
                progressText = msg.pending ? `翻譯中 ${msg.done} / ${msg.total}` : (msg.total ? '已全部翻譯' : '');
                $('meta-status').textContent = `${msg.done} / ${msg.total} 個區塊已翻譯`;
                updateLive();
            },
            status(msg) { statusText = msg.text; updateLive(); },
            meta(msg) {
                const set = (id, v) => { if (v !== undefined && $(id)) $(id).textContent = v; };
                set('meta-title', msg.title);
                set('meta-summary', msg.summary);
                set('meta-domain', msg.domain);
                set('meta-topics', msg.topics);
                if (msg.glossary_rows !== undefined) $('glossary-rows').innerHTML = msg.glossary_rows;
            },
        };

        const events = new EventSource(`${docApi}/events`);
        events.onmessage = (e) => {
            const msg = JSON.parse(e.data);
            handlers[msg.type]?.(msg);
        };
        events.onerror = () => { statusText = '與本機服務的連線中斷，重新連線中…'; updateLive(); };
        events.onopen = () => { statusText = ''; updateLive(); reportFocus(); };

        // 回報目前看得到的區塊，伺服器優先翻譯這些段落
        const visible = new Set();
        let focusTimer = null;
        function reportFocus() {
            clearTimeout(focusTimer);
            focusTimer = setTimeout(() => {
                const ids = [...document.querySelectorAll('.block.pair')]
                    .filter((el) => visible.has(el.id)).map((el) => el.id);
                if (ids.length) postJson(`${docApi}/focus`, { visible: ids }).catch(() => {});
            }, 400);
        }
        const observer = new IntersectionObserver((entries) => {
            entries.forEach((en) => { if (en.isIntersecting) visible.add(en.target.id); else visible.delete(en.target.id); });
            reportFocus();
        }, { rootMargin: '200px 0px 600px 0px' });
        document.querySelectorAll('.block.pair').forEach((el) => observer.observe(el));
    }

    refreshVocab();
    renderHistory();
})();

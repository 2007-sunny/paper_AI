(() => {
    const config = window.PAPERAI || {};
    const $ = (id) => document.getElementById(id);

    // ---------- 儲存（localStorage 可能被停用，全部包 try/catch） ----------
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

    function showNotification(msg) {
        const bar = $('notif-bar');
        bar.textContent = msg;
        bar.style.display = 'block';
        clearTimeout(showNotification.timer);
        showNotification.timer = setTimeout(() => { bar.style.display = 'none'; }, 2500);
    }

    // ---------- 檢視模式與主題 ----------
    function switchView(mode) {
        document.body.setAttribute('data-view', mode);
        document.querySelectorAll('.tab-btn').forEach((btn) =>
            btn.classList.toggle('active', btn.dataset.viewBtn === mode));
        save('paperai_view', mode);
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
        const target = $(section === 'help' ? 'help-panel' : 'vocab-panel');
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

    // ---------- 句子對照 ----------
    const content = document.querySelector('.reader-content');
    content.addEventListener('mouseover', (e) => {
        const sent = e.target.closest('.sentence');
        if (!sent || sent.classList.contains('hl')) return;
        document.querySelectorAll('.sentence.hl').forEach((s) => s.classList.remove('hl'));
        document.querySelectorAll(`.sentence[data-block="${sent.dataset.block}"][data-sid="${sent.dataset.sid}"]`)
            .forEach((s) => s.classList.add('hl'));
    });
    content.addEventListener('mouseleave', () =>
        document.querySelectorAll('.sentence.hl').forEach((s) => s.classList.remove('hl')));

    // ---------- 生字本 ----------
    let vocabList = load('vocab_list', []);

    function renderVocab() {
        const box = $('vocab-list');
        if (!vocabList.length) {
            box.innerHTML = '<p class="empty-state">尚未加入任何生字。</p>';
            return;
        }
        box.innerHTML = vocabList.map((item, i) => `
            <div class="vocab-item">
                <div><div class="term-name">${escapeHtml(item.term)}</div>
                <div class="sub">${escapeHtml(item.translation)} · ${escapeHtml(item.category)}</div></div>
                <button class="delete-btn" data-remove-vocab="${i}" aria-label="刪除">&times;</button>
            </div>`).join('');
    }

    function addWordToVocab(term, translation, category) {
        if (vocabList.some((item) => item.term.toLowerCase() === term.toLowerCase())) {
            showNotification(`「${term}」已在生字本中。`);
            return;
        }
        vocabList.push({ term, translation, category });
        save('vocab_list', vocabList);
        renderVocab();
        showNotification(`已加入「${term}」。`);
    }

    $('vocab-list').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-remove-vocab]');
        if (!btn) return;
        vocabList.splice(Number(btn.dataset.removeVocab), 1);
        save('vocab_list', vocabList);
        renderVocab();
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

    // 點術語或術語表按鈕加入生字本
    document.addEventListener('click', (e) => {
        const term = e.target.closest('.term');
        if (term) {
            e.stopPropagation();
            addWordToVocab(term.dataset.term, term.dataset.translation, term.dataset.category);
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
            const response = await fetch(config.apiUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ term, context, model_name: modelSelect.value })
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (error) {
            return { term, translation: '查詢失敗', category: 'error', sentences: [], model_used: '—',
                     explanation: '無法連線到本機 server.py（port 8000），請先啟動：uvicorn server:app --host 127.0.0.1 --port 8000' };
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
            addWordToVocab(result.term, result.translation, result.category);
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

    function showPopup(x, y) {
        popup.style.left = `${Math.min(x, window.innerWidth - 170)}px`;
        popup.style.top = `${Math.max(y - 44, 8)}px`;
        popup.style.display = 'block';
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
            showPopup(rect.left + rect.width / 2, rect.top);
        }, 0);
    });

    popup.addEventListener('mousedown', (e) => e.preventDefault());
    popup.addEventListener('click', async () => {
        popup.style.display = 'none';
        window.getSelection()?.removeAllRanges();
        if (pending.term) await lookupAndAdd(pending.term, pending.context);
    });

    renderVocab();
    renderHistory();
})();

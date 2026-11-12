(() => {
    const PDFJS = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/';
    const $ = (id) => document.getElementById(id);
    const viewer = $('viewer');
    const pagesBox = $('pages');

    // ---------- 共用工具 ----------
    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
    }
    async function api(url, body) {
        const res = await fetch(url, body === undefined ? {} : {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        return data;
    }
    function notify(message) {
        $('notice').textContent = message;
        $('notice').hidden = false;
        clearTimeout(notify.timer);
        notify.timer = setTimeout(() => { $('notice').hidden = true; }, 3500);
    }
    const docApi = (name) => `/api/docs/${encodeURIComponent(name)}`;
    function loadScript(src) {
        return new Promise((resolve, reject) => {
            const s = document.createElement('script');
            s.src = src; s.onload = resolve; s.onerror = () => reject(new Error('無法載入 PDF.js（需要網路）'));
            document.head.appendChild(s);
        });
    }

    // ---------- 側邊欄 ----------
    let lastReported = null;
    let reportTimer = null;
    let app = { docs: [], state: { last: '', positions: {}, zoom: 1 } };
    let current = null;  // { name, pdf, pages, events }

    let sideView = 'files';
    let folderFiles = {};  // 資料夾路徑 → PDF 清單
    let collapsed = new Set();  // 收合的開啟資料夾（預設展開）
    let expanded = new Set();   // 展開的次級資料夾（預設收合，避免大型資料夾一次攤開）

    function docLabel(d) { return `${d.title}${d.pages ? `（頁 ${d.pages}）` : ''}`; }
    function docRow(d, action) {
        const title = escapeHtml(docLabel(d));
        return `<div class="doc-row${current?.name === d.name ? ' active' : ''}">
            <button class="doc-item" data-doc="${escapeHtml(d.name)}" title="${title}"${d.has_pdf ? '' : ' disabled'}>
                <span class="doc-name">${title}</span>
                ${d.has_pdf ? '' : '<span class="doc-note">找不到 PDF</span>'}
            </button>${action}</div>`;
    }
    function folderTree(pdfs) {
        const root = { dirs: new Map(), files: [] };
        pdfs.forEach((p) => {
            const parts = p.label.split(/[\\/]/);
            let node = root;
            parts.slice(0, -1).forEach((part) => {
                if (!node.dirs.has(part)) node.dirs.set(part, { dirs: new Map(), files: [] });
                node = node.dirs.get(part);
            });
            node.files.push({ ...p, base: parts[parts.length - 1] });
        });
        return root;
    }
    function treeRows(node, key, depth) {
        const indent = `style="padding-left:${depth * 12}px"`;
        const dirs = [...node.dirs.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([name, child]) => {
            const id = `${key}/${name}`;
            const open = expanded.has(id);
            return `<div class="side-group" ${indent}>
                <button class="side-group-name" data-toggle-dir="${escapeHtml(id)}" title="${escapeHtml(name)}">${open ? '▾' : '▸'} ${escapeHtml(name)}</button>
            </div>${open ? treeRows(child, id, depth + 1) : ''}`;
        });
        const files = node.files.map((p) => {
            return `<div class="doc-row${current?.name === p.name ? ' active' : ''}" ${indent}>
                <button class="doc-item" data-path="${escapeHtml(p.path)}" title="${escapeHtml(p.label)}">
                    <span class="doc-name">${escapeHtml(p.base)}</span>
                </button></div>`;
        });
        return dirs.join('') + files.join('');
    }
    function folderBlock(folder) {
        const label = escapeHtml(folder.split(/[\\/]/).filter(Boolean).pop() || folder);
        const open = !collapsed.has(folder);
        const pdfs = folderFiles[folder] || [];
        const rows = !open ? '' : pdfs.length ? treeRows(folderTree(pdfs), folder, 1)
            : '<p class="doc-note">這個資料夾沒有 PDF。</p>';
        return `<div class="side-group">
            <button class="side-group-name" data-toggle-folder="${escapeHtml(folder)}" title="${escapeHtml(folder)}">${open ? '▾' : '▸'} ${label}</button>
            <button class="doc-action" style="opacity:1" data-close-folder="${escapeHtml(folder)}" title="關閉資料夾">×</button>
        </div>${rows}`;
    }
    function renderSidebar() {
        $('file-actions').hidden = sideView !== 'files';
        $('history-head').hidden = sideView === 'files';
        $('side-title').textContent = { annotations: '註解列表', bookmarks: '書籤與目錄' }[sideView] || '歷史管理';
        $('open-bookmarks').classList.toggle('active', sideView === 'bookmarks');
        const { files = [], folders = [] } = app.state;
        if (sideView === 'annotations') {
            $('doc-list').innerHTML = annotationRows();
        } else if (sideView === 'bookmarks') {
            $('doc-list').innerHTML = bookmarkRows();
            $('doc-list').querySelector('.bookmark-input')?.focus();
            if (focusOutline !== null) {
                $('doc-list').querySelector(`[data-outline-input="${focusOutline}"]`)?.select();
                focusOutline = null;
            }
        } else if (sideView === 'files') {
            const open = files.map((n) => app.docs.find((d) => d.name === n)).filter(Boolean)
                .map((d) => docRow(d, `<button class="doc-action" data-close-file="${escapeHtml(d.name)}" title="關閉（保留在歷史）">×</button>`));
            $('doc-list').innerHTML = (open.join('') + folders.map(folderBlock).join(''))
                || '<p class="doc-note">開啟檔案或資料夾開始閱讀；看過的文件在左下角設定的「歷史管理」。</p>';
        } else {
            $('doc-list').innerHTML = app.docs.map((d) =>
                docRow(d, `<button class="doc-action" data-delete="${escapeHtml(d.name)}" title="刪除翻譯">刪除</button>`)).join('')
                || '<p class="doc-note">還沒有文件。</p>';
        }
        const used = Math.round(app.usage / 1000), limit = Math.round(app.settings.monthly_chars / 1000);
        $('usage').textContent = `粗翻 ${used}k / ${limit}k｜細翻：${MODE_LABELS[currentMode()]}`;
        renderSettings();
    }
    async function refresh() {
        try {
            app = await api('/api/state');
            renderSidebar();
            clearTimeout(refresh.timer);
            refresh.timer = setTimeout(refresh, 15000);
        } catch (e) {
            notify(`無法連線到本機服務：${e.message}`);
        }
    }
    function saveWorkspace() {
        const { files, folders } = app.state;
        api('/api/workspace', { files, folders }).catch((e) => notify(e.message));
        renderSidebar();
    }
    function keepOpen(name) {
        if (!app.state.files.includes(name)) app.state.files.push(name);
    }
    async function loadFolders() {
        await Promise.all(app.state.folders.map(async (folder) => {
            try { folderFiles[folder] = (await api(`/api/folder?path=${encodeURIComponent(folder)}`)).pdfs; }
            catch (e) { folderFiles[folder] = []; }
        }));
        renderSidebar();
    }
    $('doc-list').addEventListener('click', async (e) => {
        const target = e.target.closest('button');
        if (!target) return;
        const { doc, path, closeFile, closeFolder, toggleFolder, toggleDir, delete: remove } = target.dataset;
        if (toggleDir) {
            expanded.has(toggleDir) ? expanded.delete(toggleDir) : expanded.add(toggleDir);
            renderSidebar();
        }
        if (doc) { keepOpen(doc); saveWorkspace(); openDoc(doc); }
        if (path) {
            try {
                const { name } = await api('/api/docs', { path });
                await refresh();
                await loadFolders();  // 同名 PDF 的文件名稱在開啟後才確定
                openDoc(name);
            } catch (err) { notify(err.message); }
        }
        if (closeFile) {
            app.state.files = app.state.files.filter((n) => n !== closeFile);
            if (current?.name === closeFile) {
                showEmpty();
                api('/api/state', { last: '' }).catch(() => {});
            }
            saveWorkspace();
        }
        if (closeFolder) {
            app.state.folders = app.state.folders.filter((f) => f !== closeFolder);
            saveWorkspace();
        }
        if (toggleFolder) {
            collapsed.has(toggleFolder) ? collapsed.delete(toggleFolder) : collapsed.add(toggleFolder);
            renderSidebar();
        }
        if (remove) startDelete(remove);
    });
    $('history-back').addEventListener('click', () => {
        sideView = 'files';
        renderSidebar();
    });
    $('toggle-sidebar').addEventListener('click', () => {
        document.body.classList.toggle('side-closed');
        fitPages();
    });
    $('open-file').addEventListener('click', async () => {
        try {
            const { paths } = await api('/api/pick', { kind: 'pdf' });
            let name = null;
            for (const path of paths) {
                name = (await api('/api/docs', { path })).name;
                keepOpen(name);
            }
            if (!name) return;
            await api('/api/workspace', { files: app.state.files, folders: app.state.folders });
            await refresh();
            openDoc(name);
        } catch (e) { notify(e.message); }
    });
    $('open-folder').addEventListener('click', async () => {
        try {
            const { paths } = await api('/api/pick', { kind: 'folder' });
            if (!paths.length || app.state.folders.includes(paths[0])) return;
            app.state.folders.push(paths[0]);
            saveWorkspace();
            loadFolders();
        } catch (e) { notify(e.message); }
    });

    // ---------- 設定選單與設置 ----------
    const MODE_LABELS = { auto: '自動', ctrl: '按 Ctrl 才翻', off: '關閉' };
    const MODE_HINTS = {
        auto: '讀到哪裡，就解析並細翻到哪裡。',
        ctrl: '按 Ctrl 先顯示粗翻，同時細翻這一段，完成後自動換掉。',
        off: '省電：不用 GPU，停止解析、細翻和單字的本機補充；已完成的細翻照常顯示。',
    };
    const SETTING_INPUTS = { 'set-lookahead': 'lookahead', 'set-model': 'model', 'set-monthly': 'monthly_chars',
                             'set-idle': 'marker_idle_minutes', 'set-timeout': 'chunk_timeout_minutes',
                             'set-zoom-speed': 'zoom_sensitivity', 'set-zoom-settle': 'zoom_settle_ms' };
    function currentMode() {
        return app.settings.doc_modes[current?.name] ?? app.settings.fine_mode;
    }
    function renderSettings() {
        const mode = currentMode();
        const scope = current && app.settings.doc_modes[current.name] ? 'doc' : 'all';
        document.querySelectorAll('#fine-modes button').forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
        document.querySelectorAll('#mode-scope button').forEach((b) => b.classList.toggle('active', b.dataset.scope === scope));
        document.querySelector('#mode-scope [data-scope="doc"]').disabled = !current;
        $('mode-hint').textContent = MODE_HINTS[mode];
        $('scope-hint').textContent = current ? $('doc-title').textContent : '開啟文件後才能單獨設定。';
        $('usage-detail').textContent = `本月 ${app.usage.toLocaleString()} / ${app.settings.monthly_chars.toLocaleString()} 字元`;
        Object.entries(SETTING_INPUTS).forEach(([id, key]) => {
            if (document.activeElement !== $(id)) $(id).value = app.settings[key];
        });
    }
    async function saveSettings(changes, url = '/api/settings') {
        try {
            app.settings = await api(url, changes);
            lastReported = null;  // 重新回報目前頁，新的模式與預先頁數才會生效
            reportPosition();
        } catch (e) { notify(e.message); }
        renderSidebar();
    }
    async function loadModels() {
        const select = $('set-model');
        let models = [];
        try { models = (await api('/api/models')).models; }
        catch (e) { notify(e.message); }
        const names = [...new Set([app.settings.model, ...models])];
        select.innerHTML = names.map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join('');
        select.value = app.settings.model;
    }

    let flyoutTimer = null;
    function showFlyout(open) {
        clearTimeout(flyoutTimer);
        $('settings-flyout').hidden = !open;
        $('settings-button').setAttribute('aria-expanded', open);
    }
    $('settings-menu').addEventListener('mouseenter', () => showFlyout(true));
    $('settings-menu').addEventListener('mouseleave', () => {
        flyoutTimer = setTimeout(() => showFlyout(false), 300);
    });
    $('settings-menu').addEventListener('focusout', (e) => {
        if (!$('settings-menu').contains(e.relatedTarget)) showFlyout(false);
    });
    $('settings-button').addEventListener('click', () => showFlyout(true));
    $('open-history').addEventListener('click', () => {
        showFlyout(false);
        sideView = 'history';
        renderSidebar();
    });
    $('open-annotations').addEventListener('click', () => {
        showFlyout(false);
        sideView = 'annotations';
        renderSidebar();
    });
    $('open-translation').addEventListener('click', () => {
        showFlyout(false);
        $('settings-panel').hidden = false;
        renderSettings();
        loadModels();
    });
    $('settings-close').addEventListener('click', () => { $('settings-panel').hidden = true; });
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        showFlyout(false);
        $('settings-panel').hidden = true;
    });
    $('fine-modes').addEventListener('click', (e) => {
        const { mode } = e.target.dataset;
        if (!mode) return;
        if (current && app.settings.doc_modes[current.name]) saveSettings({ mode }, `${docApi(current.name)}/mode`);
        else saveSettings({ fine_mode: mode });
    });
    $('mode-scope').addEventListener('click', (e) => {
        const { scope } = e.target.dataset;
        if (!scope || !current) return;
        saveSettings({ mode: scope === 'doc' ? currentMode() : null }, `${docApi(current.name)}/mode`);
    });
    Object.entries(SETTING_INPUTS).forEach(([id, key]) => $(id).addEventListener('change', () => {
        const value = $(id).value;
        if (!$(id).checkValidity()) { notify(`請輸入 ${$(id).min}–${$(id).max} 之間的整數`); return; }
        saveSettings({ [key]: key === 'model' ? value : Number(value) });
    }));
    $('settings-reset').addEventListener('click', () => saveSettings({ ...app.defaults }));

    // ---------- 刪除翻譯：預覽 → 說明 → 確認 ----------
    let deleting = null;
    async function startDelete(name) {
        const doc = app.docs.find((d) => d.name === name);
        deleting = name;
        $('delete-title').textContent = docLabel(doc);
        $('delete-step-1').hidden = false;
        $('delete-step-2').hidden = true;
        $('delete-next').hidden = false;
        $('delete-confirm').hidden = true;
        $('delete-canvas').hidden = !doc.has_pdf;
        $('delete-no-pdf').hidden = doc.has_pdf;
        $('delete-modal').hidden = false;
        if (!doc.has_pdf) return;
        try {
            await ensurePdfJs();
            const pdf = await pdfjsLib.getDocument(`${docApi(name)}/source.pdf`).promise;
            const info = await api(`${docApi(name)}/info`);
            const page = await pdf.getPage(info.pages[0] + 1);
            const viewport = page.getViewport({ scale: 1 });
            const canvas = $('delete-canvas');
            canvas.width = viewport.width;
            canvas.height = viewport.height;
            await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
            pdf.destroy();
        } catch (e) {
            $('delete-canvas').hidden = true;
            $('delete-no-pdf').hidden = false;
        }
    }
    function endDelete() {
        deleting = null;
        $('delete-modal').hidden = true;
    }
    $('delete-cancel').addEventListener('click', endDelete);
    $('delete-next').addEventListener('click', async () => {
        const doc = app.docs.find((d) => d.name === deleting);
        $('delete-name').textContent = docLabel(doc);
        $('delete-size').textContent = '計算中';
        $('delete-step-1').hidden = true;
        $('delete-step-2').hidden = false;
        $('delete-next').hidden = true;
        $('delete-confirm').hidden = false;
        try {
            const size = await api(`${docApi(deleting)}/storage`);
            $('delete-size').textContent = `${size.files} 個檔案，${size.mb} MB`;
        } catch (e) { $('delete-size').textContent = '大小未知'; }
    });
    $('delete-confirm').addEventListener('click', async () => {
        const name = deleting;
        try {
            const res = await fetch(docApi(name), { method: 'DELETE' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
            if (current?.name === name) showEmpty();
            notify('已刪除翻譯資料，原始 PDF 保留');
            await refresh();
        } catch (e) { notify(e.message); }
        endDelete();
    });

    // ---------- PDF 頁面 ----------
    let zoom = 1;
    let pinch = null;  // 縮放手勢進行中：{ from: 開始時的倍率, x, y: 手勢起點 }
    let pinchTimer = null;
    const FIT_WIDTH = 900;  // 100% 時的頁寬上限，寬螢幕才不會把頁面放得太大

    function pageWidth() {
        return Math.round(Math.min(viewer.clientWidth - 24, FIT_WIDTH) * zoom);
    }
    // 大文件（上千頁）要注意：迴圈裡只寫樣式、不讀尺寸。寫完馬上讀 clientWidth 之類會強迫瀏覽器
    // 每頁重算一次整份版面，Arfken 全本 1199 頁放大一次卡 13 秒；先算好寬度再一次寫完只要幾十毫秒
    function fitPages() {
        const anchor = currentPage(), anchorHolder = holderOf(anchor);
        const line = viewer.scrollTop + viewer.clientHeight / 3;  // currentPage 的判斷線，放大前後讓它停在同一點
        const ratio = anchorHolder ? Math.min(Math.max((line - anchorHolder.offsetTop) / anchorHolder.clientHeight, 0), 1) : 0;
        const width = pageWidth();
        pagesBox.querySelectorAll('.pdf-page').forEach((holder) => {
            const ratio = width / (parseFloat(holder.style.width) || width);
            holder.style.width = `${width}px`;
            holder.style.height = `${width / Number(holder.dataset.aspect)}px`;
            if (!holder.dataset.rendered) return;  // 沒畫過的頁不用動，進入畫面時觀察器自然會觸發
            delete holder.dataset.rendered;
            holder.renderGen = (holder.renderGen || 0) + 1;  // 讓還在進行的舊渲染作廢
            holder.renderTask?.cancel();
            // 舊的 canvas 留著（CSS 會拉伸到新尺寸），新的畫好才換掉，畫面不會閃白；
            // PDF.js 文字層的位置用 --scale-factor 算，跟著改就能先對齊
            holder.querySelectorAll('.textLayer:not(.ocr-text)').forEach((layer) =>
                layer.style.setProperty('--scale-factor', parseFloat(layer.style.getPropertyValue('--scale-factor')) * ratio));
            lazy.unobserve(holder);
            lazy.observe(holder);
        });
        pagesBox.querySelectorAll('.overlay').forEach(fitOverlay);
        $('zoom-label').textContent = `${Math.round(zoom * 100)}%`;
        if (anchorHolder) viewer.scrollTop = anchorHolder.offsetTop + anchorHolder.clientHeight * ratio - viewer.clientHeight / 3;
        else scrollToPage(anchor);
    }

    async function renderPage(holder) {
        if (holder.dataset.rendered || !current) return;
        holder.dataset.rendered = '1';
        const owner = current, gen = holder.renderGen || 0;
        const stale = () => current !== owner || (holder.renderGen || 0) !== gen;
        const page = await owner.pdf.getPage(Number(holder.dataset.page) + 1);
        if (stale()) return;
        const cssScale = holder.clientWidth / page.getViewport({ scale: 1 }).width;
        const viewport = page.getViewport({ scale: cssScale * (window.devicePixelRatio || 1) });
        // 新的 canvas 先在畫面外畫好，再換掉舊的（放大縮小時舊圖一直顯示，不會閃）
        const canvas = document.createElement('canvas');
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        holder.renderTask = page.render({ canvasContext: canvas.getContext('2d'), viewport });
        try { await holder.renderTask.promise; }
        catch (e) { return; }  // 放大縮小時被取消
        finally { if (!stale()) holder.renderTask = null; }
        if (stale()) return;
        holder.querySelectorAll('canvas').forEach((old) => old.remove());
        holder.prepend(canvas);

        const oldLayers = [...holder.querySelectorAll('.textLayer')];
        const layer = document.createElement('div');
        layer.className = 'textLayer';
        layer.style.setProperty('--scale-factor', cssScale);
        layer.style.visibility = 'hidden';
        canvas.after(layer);
        const textContentSource = await page.getTextContent();
        if (stale()) { layer.remove(); return; }
        await pdfjsLib.renderTextLayer({
            textContentSource, container: layer,
            viewport: page.getViewport({ scale: cssScale }), textDivs: [],
        }).promise;
        if (stale()) { layer.remove(); return; }
        oldLayers.forEach((old) => old.remove());  // OCR 層也一起換，ensureOcrLayer 會依新尺寸重建
        layer.style.visibility = '';

        ensureOcrLayer(holder);
    }

    // 掃描頁：捲動停下後，只替目前頁與前後各一頁做 OCR。捲動途中經過的頁不做，
    // 否則上百頁排隊，佔滿伺服器的工作執行緒，按 Ctrl 的翻譯請求要等好幾分鐘
    let settledPage = null;
    async function ensureOcrLayer(holder) {
        const page = Number(holder.dataset.page);
        const canvas = holder.querySelector('canvas');
        // 頁面還在渲染（沒有 .textLayer）時先跳過，renderPage 完成後會再呼叫
        if (!canvas || !holder.querySelector('.textLayer') || holder.querySelector('.ocr-text')
            || settledPage === null || Math.abs(page - settledPage) > 1) return;
        const owner = current;
        const data = await pageData(page).catch(() => null);
        if (data?.ocr_lines?.length && current === owner && holder.contains(canvas) && !holder.querySelector('.ocr-text')) {
            renderOcrLayer(holder, data.ocr_lines);
        }
    }

    // 掃描頁沒有文字層：用 OCR 的文字行建一層透明文字，才能選取、複製、查詢與擷取
    function renderOcrLayer(holder, lines) {
        holder.querySelector('.ocr-text')?.remove();
        const layer = document.createElement('div');
        layer.className = 'textLayer ocr-text';
        holder.querySelector('.textLayer').after(layer);
        const width = holder.clientWidth, height = holder.clientHeight;
        lines.forEach((line) => {
            const span = document.createElement('span');
            span.textContent = line.text;
            Object.assign(span.style, { left: `${line.left}%`, top: `${line.top}%`,
                                        fontSize: `${line.height / 100 * height * 0.85}px` });
            layer.append(span, document.createElement('br'));
            const natural = span.getBoundingClientRect().width;
            if (natural) span.style.transform = `scaleX(${line.width / 100 * width / natural})`;
        });
    }

    const lazy = new IntersectionObserver((entries) => {
        entries.forEach((en) => { if (en.isIntersecting) renderPage(en.target).catch(() => {}); });
    }, { root: viewer, rootMargin: '800px 0px' });

    function holderOf(page) { return pagesBox.querySelector(`.pdf-page[data-page="${page}"]`); }
    function scrollToPage(page) {
        const holder = holderOf(page);
        if (holder) viewer.scrollTop = holder.offsetTop - 12;
    }
    // 視窗上方 1/3 處所在的頁即為目前頁
    function currentPage() {
        const y = viewer.getBoundingClientRect().top + viewer.clientHeight / 3;
        const hit = [...pagesBox.querySelectorAll('.pdf-page')].find((el) => el.getBoundingClientRect().bottom >= y);
        return hit ? Number(hit.dataset.page) : (current?.pages[0] ?? 0);
    }

    async function ensurePdfJs() {
        if (window.pdfjsLib) return;
        await loadScript(PDFJS + 'pdf.min.js');
        pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS + 'pdf.worker.min.js';
    }

    async function openDoc(name, startPage = null) {
        if (current?.name === name) return;
        closeDoc();
        const owner = { name, pdf: null, pages: [], events: null, blocks: {}, views: {}, annotations: [], bookmarks: [], outline: null };
        current = owner;
        outlineOpen.clear();
        renamingBookmark = null;
        outlineEditing = false;
        renderSidebar();
        const doc = app.docs.find((d) => d.name === name);
        $('doc-title').textContent = doc?.title || name;
        $('empty').hidden = true;
        try {
            await ensurePdfJs();
            const info = await api(`${docApi(name)}/info`);
            owner.pdf = await pdfjsLib.getDocument(`${docApi(name)}/source.pdf`).promise;
            if (current !== owner) { owner.pdf.destroy(); return; }
            owner.pages = info.pages;
            const first = (await owner.pdf.getPage(info.pages[0] + 1)).getViewport({ scale: 1 });
            const aspect = first.width / first.height;
            pagesBox.innerHTML = info.pages.map((p) =>
                `<div class="pdf-page" data-page="${p}" data-aspect="${aspect}"><div class="annot-layer"></div><div class="overlay-layer"></div></div>`).join('');
            $('page-total').textContent = owner.pdf.numPages;
            pagesBox.querySelectorAll('.pdf-page').forEach((holder) => lazy.observe(holder));
            fitPages();
            scrollToPage(info.pages.includes(startPage) ? startPage : (app.state.positions?.[name] ?? info.pages[0]));
            reportPosition();
            connectEvents();
            loadAnnotations(owner);
            loadBookmarks(owner);
        } catch (e) {
            notify(`無法開啟：${e.message}`);
        }
    }
    function closeDoc() {
        if (!current) return;
        current.events?.close();
        current.pdf?.destroy();
        settledPage = null;
        lazy.disconnect();  // 舊文件的頁面不再觀察，否則上千個節點會一直留在觀察器裡
        pagesBox.innerHTML = '';
        setStatus('');
        current = null;
    }
    function showEmpty() {
        closeDoc();
        $('doc-title').textContent = '';
        $('page-input').value = '';
        $('page-total').textContent = '—';
        $('empty').hidden = false;
        renderSidebar();
    }

    // ---------- 閱讀位置：記住頁碼，並讓細翻跟著目前頁 ----------
    function reportPosition() {
        clearTimeout(reportTimer);
        reportTimer = setTimeout(() => {
            if (!current?.pages.length) return;
            const page = currentPage();
            $('page-input').value = page + 1;
            settledPage = page;
            [page - 1, page, page + 1].forEach((p) => { const holder = holderOf(p); if (holder) ensureOcrLayer(holder); });
            if (holdPosition === current.name) {  // 引用跳轉：不改記住的位置，只讓細翻跟著目前頁
                api(`${docApi(current.name)}/focus`, { page }).catch(() => {});
                return;
            }
            if (lastReported === `${current.name}:${page}`) return;
            lastReported = `${current.name}:${page}`;
            app.state.positions = { ...app.state.positions, [current.name]: page };
            api('/api/state', { last: current.name, page }).catch(() => {});
            api(`${docApi(current.name)}/focus`, { page }).catch(() => {});
        }, 300);
    }
    viewer.addEventListener('scroll', reportPosition, { passive: true });
    $('page-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') scrollToPage(Number($('page-input').value) - 1);
    });

    const clampZoom = (z) => Math.max(0.5, Math.min(3, Math.round(z * 100) / 100));
    function setZoom(next) {
        if (pinch) commitPinch();
        zoom = clampZoom(next);
        api('/api/state', { zoom }).catch(() => {});
        if (current) fitPages();
    }
    $('zoom-in').addEventListener('click', () => setZoom(Math.floor(zoom * 10 + 1.001) / 10));
    $('zoom-out').addEventListener('click', () => setZoom(Math.ceil(zoom * 10 - 1.001) / 10));
    $('zoom-label').addEventListener('click', () => setZoom(1));
    let resizeTimer = null;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => { if (current) fitPages(); }, 200);
    });

    // ---------- 細翻狀態 ----------
    function setStatus(text) { $('status').textContent = text; }
    let statusText = '';
    const handlers = {
        progress(msg) {
            if (!statusText) setStatus(msg.pending || currentMode() !== 'auto' ? '' : '已全部細翻');
        },
        status(msg) { statusText = msg.text; setStatus(msg.text); },
        fine(msg) {
            // 粗翻顯示中的段落換成細翻；區塊可能被歸到相鄰頁
            [msg.page - 1, msg.page, msg.page + 1].forEach((page) => {
                holderOf(page)?.querySelectorAll('.overlay:not(.fine):not([data-forced])').forEach((el) => fillOverlay(el, page));
            });
        },
    };
    function connectEvents() {
        const owner = current;
        if (!owner?.pages.length || owner.events) return;
        owner.events = new EventSource(`${docApi(owner.name)}/events`);
        owner.events.onmessage = (e) => {
            const msg = JSON.parse(e.data);
            if (current === owner) handlers[msg.type]?.(msg);
        };
        owner.events.onopen = () => {
            lastReported = null;
            reportPosition();
        };
    }

    // ---------- Ctrl：切換滑鼠所在段落的譯文 ----------
    const mouse = { x: 0, y: 0 };
    document.addEventListener('mousemove', (e) => {
        mouse.x = e.clientX;
        mouse.y = e.clientY;
        updateFigureButton(e.target);
    }, { passive: true });

    async function pageData(page) {
        const owner = current;
        if (!owner.blocks[page]) {
            // 失敗不記住，下次再試；否則這一頁之後按 Ctrl 都沒有反應
            owner.blocks[page] = api(`${docApi(owner.name)}/pages/${page}`)
                .catch((e) => { delete owner.blocks[page]; throw e; });
        }
        return owner.blocks[page];
    }
    async function pageBlocks(page) { return (await pageData(page)).blocks; }
    function holderAtMouse() {
        return [...pagesBox.querySelectorAll('.pdf-page')].find((el) => {
            const r = el.getBoundingClientRect();
            return mouse.x >= r.left && mouse.x <= r.right && mouse.y >= r.top && mouse.y <= r.bottom;
        });
    }
    async function blockAtPoint(x, y) {
        const holder = holderAtMouse();
        if (!holder) return null;
        const r = holder.getBoundingClientRect();
        const px = (x - r.left) / r.width * 100, py = (y - r.top) / r.height * 100;
        const blocks = await pageBlocks(Number(holder.dataset.page));
        const block = blocks.find((b) => px >= b.left - 0.5 && px <= b.left + b.width + 0.5
                                      && py >= b.top - 0.5 && py <= b.top + b.height + 0.5);
        return block ? { block, holder } : null;
    }

    const MIN_FONT = 10;
    // 譯文字級：放得進框的最大值，讓譯文填滿框（中文通常比英文短）。不用區塊的行數估字級：
    // 舊掃描 PDF 的 PyMuPDF 會把一行拆成好幾段，摘要 6 行報成 21 行，估出來的字太小。
    // 上下限都跟著頁高走，放大縮小時譯文和框等比例變化
    function fitOverlay(el) {
        const pageHeight = el.closest('.pdf-page').clientHeight;
        const fits = (size) => { el.style.fontSize = `${size}px`; return el.scrollHeight <= el.clientHeight; };
        let hi = Math.min(el.clientHeight, pageHeight * 0.035);  // 大框裡只有幾個字時，也不超過頁高 3.5%
        let lo = Math.min(MIN_FONT, pageHeight * 0.008);  // 縮得很小時也要能縮進框裡，不出捲軸
        if (fits(hi)) return;
        if (!fits(lo)) return;  // 最小字級也放不下：維持最小字級，讓框可以捲動
        // 二分搜尋（精度為字級的 2%）；每量一次都要重算版面，所以不逐步縮小
        const step = Math.max(lo * 0.02, 0.25);
        while (hi - lo > step) {
            const mid = (lo + hi) / 2;
            if (fits(mid)) lo = mid; else hi = mid;
        }
        el.style.fontSize = `${lo}px`;
    }
    async function fillOverlay(el, page, source = 'auto') {
        try {
            const res = await api(`${docApi(current.name)}/translate`, { page, block: el.dataset.block, source });
            if (source === 'auto') delete el.dataset.forced;
            else el.dataset.forced = source;  // 手動選的不會被細翻完成事件換掉
            el.innerHTML = res.html;
            el.classList.remove('waiting');
            el.classList.toggle('fine', res.source === 'fine');
            fitOverlay(el);
            if (res.source === 'fine' && window.MathJax?.typesetPromise) {
                await MathJax.typesetPromise([el]).catch(() => {});
                fitOverlay(el);
            }
            return true;
        } catch (e) {
            if (el.classList.contains('waiting')) el.remove();
            notify(e.message);
            return false;
        }
    }
    async function toggleParagraph() {
        if (!current?.pages.length) return;
        // 掃描頁第一次要 OCR，可能等好幾秒，先讓使用者知道有在處理
        const slow = setTimeout(() => notify('正在辨識這一頁的文字（掃描頁需要 OCR，約幾秒）…'), 600);
        let hit;
        try {
            hit = await blockAtPoint(mouse.x, mouse.y);
        } catch (e) {
            notify(`無法取得這一頁的文字：${e.message}`);
            return;
        } finally {
            clearTimeout(slow);
        }
        if (!hit) {
            const page = holderAtMouse()?.dataset.page;
            if (page !== undefined && !(await pageBlocks(Number(page))).length) notify('這一頁找不到可翻譯的段落，細翻完成後才看得到譯文');
            return;
        }
        const { block, holder } = hit;
        const existing = holder.querySelector(`.overlay[data-block="${block.id}"]`);
        if (existing) { existing.remove(); return; }
        const el = document.createElement('div');
        el.className = 'overlay waiting';
        Object.assign(el.dataset, { block: block.id, height: block.height, lines: block.lines });
        Object.assign(el.style, { left: `${block.left}%`, top: `${block.top}%`,
                                  width: `${block.width}%`, height: `${block.height}%` });
        el.textContent = '翻譯中…';
        holder.querySelector('.overlay-layer').append(el);
        fitOverlay(el);
        fillOverlay(el, Number(holder.dataset.page));
    }
    // Shift+Ctrl：滑鼠所在的譯文在粗翻與細翻之間切換
    async function switchSource() {
        if (!current?.pages.length) return;
        const hit = await blockAtPoint(mouse.x, mouse.y).catch(() => null);
        const el = hit && hit.holder.querySelector(`.overlay[data-block="${hit.block.id}"]`);
        if (!el) { notify('先按 Ctrl 顯示譯文，再按 Shift+Ctrl 切換粗翻與細翻'); return; }
        const target = el.classList.contains('fine') ? 'rough' : 'fine';
        if (await fillOverlay(el, Number(hit.holder.dataset.page), target)) notify(target === 'fine' ? '細翻' : '粗翻');
    }

    // ---------- 游標移到圖上時顯示「擷取圖片」 ----------
    const figureBox = document.createElement('div');
    figureBox.className = 'figure-box';
    figureBox.innerHTML = '<button class="tool primary" type="button">擷取圖片</button>';
    const figureButton = figureBox.firstChild;
    let hoverFigure = null;

    function hideFigure() {
        hoverFigure = null;
        figureBox.remove();
    }
    async function updateFigureButton(target) {
        const holder = target.closest?.('.pdf-page');
        if (!holder || !current?.pages.length) { hideFigure(); return; }
        const page = Number(holder.dataset.page);
        const owner = current;
        const { figures } = await pageData(page).catch(() => ({ figures: [] }));
        if (current !== owner) return;
        const r = holder.getBoundingClientRect();
        const x = (mouse.x - r.left) / r.width * 100, y = (mouse.y - r.top) / r.height * 100;
        const index = figures.findIndex((f) => x >= f.left && x <= f.left + f.width && y >= f.top && y <= f.top + f.height);
        if (index < 0) { hideFigure(); return; }
        if (hoverFigure?.page === page && hoverFigure.index === index) return;
        hoverFigure = { page, index };
        const f = figures[index];
        Object.assign(figureBox.style, { left: `${f.left}%`, top: `${f.top}%`, width: `${f.width}%`, height: `${f.height}%` });
        holder.querySelector('.overlay-layer').append(figureBox);
    }
    figureButton.addEventListener('mousedown', (e) => e.stopPropagation());
    figureButton.addEventListener('click', async () => {
        const { page, index } = hoverFigure;
        const url = `${docApi(current.name)}/pages/${page}/figures/${index}.png`;
        try {
            // 直接傳 Promise，瀏覽器才會把等待下載的時間算在這次點擊內
            await navigator.clipboard.write([new ClipboardItem({ 'image/png': fetch(url).then((r) => r.blob()) })]);
            notify('已複製圖片，可以貼到 AI');
        } catch (e) {
            window.open(url, '_blank');
            notify('無法直接複製，已在新分頁開啟圖片');
        }
    });

    // 只有單獨按下再放開 Ctrl 才切換；Ctrl+C、Ctrl+滾輪、Ctrl+點擊都不算。
    // 只按 Shift+Ctrl（沒有其他鍵）則切換粗翻與細翻
    let ctrlAlone = false, shiftCtrl = false;
    const typing = () => ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName);
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Control' || e.key === 'Shift') {
            if (e.repeat) return;
            if (e.ctrlKey && e.shiftKey) { shiftCtrl = true; ctrlAlone = false; }
            else if (e.key === 'Control') ctrlAlone = true;
        } else {
            ctrlAlone = false;
            shiftCtrl = false;
        }
    });
    document.addEventListener('keyup', (e) => {
        if ((e.key === 'Control' || e.key === 'Shift') && shiftCtrl) {
            shiftCtrl = false;
            ctrlAlone = false;
            if (!typing()) switchSource();
        } else if (e.key === 'Control' && ctrlAlone) {
            ctrlAlone = false;
            if (!typing()) toggleParagraph();
        }
    });
    document.addEventListener('mousedown', () => { ctrlAlone = false; shiftCtrl = false; });
    window.addEventListener('blur', () => { ctrlAlone = false; shiftCtrl = false; });
    // 觸控板雙指縮放（瀏覽器送成 ctrlKey 的 wheel）與 Ctrl+滾輪：手勢進行中只用 CSS transform 放大整疊頁面，
    // 以手勢起點為中心、跟著 deltaY 連續變化，像看圖片一樣順；停下 200 毫秒才真的重排、重畫成清晰的頁面
    viewer.addEventListener('wheel', (e) => {
        if (!e.ctrlKey) return;
        e.preventDefault();
        ctrlAlone = false;
        if (!current) return;
        if (!pinch) {
            const box = pagesBox.getBoundingClientRect();
            pinch = { from: zoom, x: e.clientX, y: e.clientY };
            pagesBox.style.transformOrigin = `${e.clientX - box.left}px ${e.clientY - box.top}px`;
        }
        // 觸控板每次只送幾個單位，滑鼠滾輪一格約 100；限制單次幅度，預設靈敏度下滑鼠一格約放大 14%
        // 靈敏度與等待時間在「設置」的縮放區塊調整（library.db，預設值見 config.ADVANCED_DEFAULTS）
        const { zoom_sensitivity: speed = 22, zoom_settle_ms: settle = 200 } = app.settings || {};
        const delta = Math.max(-60, Math.min(60, e.deltaMode === 1 ? e.deltaY * 20 : e.deltaY));
        zoom = clampZoom(zoom * Math.exp(-delta * speed / 10000));
        pagesBox.style.transform = `scale(${zoom / pinch.from})`;
        $('zoom-label').textContent = `${Math.round(zoom * 100)}%`;
        clearTimeout(pinchTimer);
        pinchTimer = setTimeout(commitPinch, settle);
    }, { passive: false });
    // 手勢結束：記下手勢起點下方是哪一頁的哪個位置，拿掉 transform 重排後把同一點捲回原處
    function commitPinch() {
        clearTimeout(pinchTimer);
        const p = pinch;
        pinch = null;
        if (!p) return;
        const holder = document.elementFromPoint(p.x, p.y)?.closest('.pdf-page') || holderOf(currentPage());
        const before = holder?.getBoundingClientRect();
        pagesBox.style.transform = '';
        api('/api/state', { zoom }).catch(() => {});
        if (!current) return;
        fitPages();
        if (!before) return;
        const fx = (p.x - before.left) / before.width, fy = (p.y - before.top) / before.height;
        const after = holder.getBoundingClientRect();
        viewer.scrollTop += after.top + fy * after.height - p.y;
        viewer.scrollLeft += after.left + fx * after.width - p.x;
    }

    // ---------- 選字：查詢與問 Claude ----------
    const menu = $('selection-menu');
    let picked = { text: '', context: '', pages: [] };

    function selectedPages(sel) {
        const pageOf = (node) => Number((node.nodeType === Node.TEXT_NODE ? node.parentElement : node)
            ?.closest('.pdf-page')?.dataset.page);
        const ends = [pageOf(sel.anchorNode), pageOf(sel.focusNode)].filter((p) => !Number.isNaN(p));
        if (!ends.length) return [];
        const pages = [];
        for (let p = Math.min(...ends); p <= Math.max(...ends); p++) pages.push(p);
        return pages;
    }
    function cleanTerm(text) {
        return text.replace(/\s+/g, ' ').replace(/^[\s.,;:!?()[\]{}"'「」（），。]+|[\s.,;:!?()[\]{}"'「」（），。]+$/g, '');
    }
    document.addEventListener('mouseup', (e) => {
        if (menu.contains(e.target) || $('annot-menu').contains(e.target) || drawing) return;
        setTimeout(async () => {
            const sel = window.getSelection();
            const text = sel ? cleanTerm(sel.toString()) : '';
            if (!text || !sel.rangeCount || !viewer.contains(sel.anchorNode)) {
                menu.hidden = true;
                showAnnotationMenu(e);  // 沒有選字時，點在標記上就開標記選單
                return;
            }
            $('annot-menu').hidden = true;
            const rect = sel.getRangeAt(0).getBoundingClientRect();
            const hit = await blockAtPoint(mouse.x, mouse.y).catch(() => null);
            picked = { text, context: hit?.block.text?.slice(0, 1000) || '', pages: selectedPages(sel) };
            menu.style.left = `${Math.min(rect.left, window.innerWidth - 150)}px`;
            menu.style.top = `${Math.max(rect.top - 38, 8)}px`;
            menu.hidden = false;
        }, 0);
    });
    menu.addEventListener('mousedown', (e) => e.preventDefault());

    // ---------- 標記與註解：只存在書庫，不寫回 PDF ----------
    // rects 單位是 point，相對 PyMuPDF 的 page.rect（與引用跳轉相同）；畫面上換成頁面比例
    const ANNOT_LABELS = { yellow: '黃', green: '綠', blue: '藍', red: '紅' };
    let activeAnnotation = null;

    async function pageView(owner, page) {
        if (!owner.views[page]) {
            const view = (await owner.pdf.getPage(page + 1)).getViewport({ scale: 1 });
            owner.views[page] = { width: view.width, height: view.height };
        }
        return owner.views[page];
    }
    async function loadAnnotations(owner = current) {
        try {
            owner.annotations = (await api(`${docApi(owner.name)}/annotations`)).annotations;
        } catch (e) { notify(`無法讀取註解：${e.message}`); return; }
        if (current === owner) { await renderAnnotations(owner); renderSidebar(); }
    }
    async function renderAnnotations(owner) {
        pagesBox.querySelectorAll('.annot-mark, .annot-badge').forEach((el) => el.remove());
        for (const a of owner.annotations) {
            const holder = holderOf(a.page);
            if (!holder || current !== owner) continue;
            const view = await pageView(owner, a.page);
            a.rects.forEach(([x0, y0, x1, y1]) => {
                const mark = document.createElement('div');
                mark.className = a.kind === 'box' ? 'annot-mark box' : 'annot-mark';
                mark.dataset.id = a.id;
                mark.dataset.color = a.color;
                Object.assign(mark.style, { left: `${x0 / view.width * 100}%`, top: `${y0 / view.height * 100}%`,
                                            width: `${(x1 - x0) / view.width * 100}%`, height: `${(y1 - y0) / view.height * 100}%` });
                holder.querySelector('.annot-layer').append(mark);
            });
            if (a.note) {
                const [, y0, x1, y1] = a.rects[0];
                const badge = document.createElement('button');
                badge.className = 'annot-badge';
                badge.type = 'button';
                badge.textContent = '註';
                badge.title = a.note;  // 滑鼠移上去顯示註解；用屬性設定，不經過 HTML
                Object.assign(badge.style, { left: `${x1 / view.width * 100}%`, top: `${(y0 + y1) / 2 / view.height * 100}%` });
                badge.addEventListener('mousedown', (e) => e.stopPropagation());
                badge.addEventListener('click', () => editNote(a));
                holder.querySelector('.overlay-layer').append(badge);
            }
        }
    }
    // 選取範圍換成每頁的標記框：同一行的片段併成一個框，單位換成 point
    async function selectionRects(sel, pages) {
        const clientRects = [...sel.getRangeAt(0).getClientRects()].filter((r) => r.width >= 1 && r.height >= 1);
        const byPage = new Map();
        for (const page of pages) {
            const holder = holderOf(page);
            if (!holder) continue;
            const box = holder.getBoundingClientRect();
            const inside = clientRects
                .filter((r) => r.top + r.height / 2 >= box.top && r.top + r.height / 2 <= box.bottom && r.height < box.height * 0.08)
                .sort((a, b) => a.top - b.top || a.left - b.left);
            const lines = [];
            for (const r of inside) {
                const last = lines[lines.length - 1];
                if (last && Math.abs(r.top - last.top) < r.height * 0.5 && r.left <= last.right + 8) {
                    last.right = Math.max(last.right, r.right);
                    last.top = Math.min(last.top, r.top);
                    last.bottom = Math.max(last.bottom, r.bottom);
                } else {
                    lines.push({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });
                }
            }
            if (!lines.length) continue;
            const view = await pageView(current, page);
            const sx = view.width / box.width, sy = view.height / box.height;
            byPage.set(page, lines.map((l) => [(l.left - box.left) * sx, (l.top - box.top) * sy,
                                               (l.right - box.left) * sx, (l.bottom - box.top) * sy].map((v) => Math.round(v * 10) / 10)));
        }
        return byPage;
    }
    function askNote(quote, initial = '') {
        $('note-quote').textContent = quote;
        $('note-text').value = initial;
        $('note-modal').hidden = false;
        $('note-text').focus();
        return new Promise((resolve) => {
            $('note-save').onclick = () => { $('note-modal').hidden = true; resolve($('note-text').value.trim()); };
            $('note-cancel').onclick = () => { $('note-modal').hidden = true; resolve(null); };
        });
    }
    async function createAnnotations(color, withNote) {
        const sel = window.getSelection();
        if (!current || !sel?.rangeCount) return;
        const text = sel.toString().replace(/\s+/g, ' ').trim().slice(0, 5000);
        const groups = await selectionRects(sel, picked.pages);  // 開註解視窗前先算，焦點移走後選取會消失
        menu.hidden = true;
        if (!groups.size) { notify('這段文字無法標記'); return; }
        let note = '';
        if (withNote) {
            note = await askNote(text);
            if (note === null) return;
        }
        sel.removeAllRanges();
        try {
            for (const [page, rects] of groups) {
                await api(`${docApi(current.name)}/annotations`, { page, rects, text, color, note });
            }
        } catch (e) { notify(e.message); }
        loadAnnotations();
    }
    $('menu-colors').addEventListener('click', (e) => {
        const { color } = e.target.dataset;
        if (color) createAnnotations(color, false);
    });
    $('menu-note').addEventListener('click', () => createAnnotations('yellow', true));

    // 框選註解：不靠文字層，直接在頁面上拖曳畫方框（掃描頁、圖、公式都能標）
    let drawing = false;
    let draft = null;
    function setDrawing(on) {
        drawing = on;
        viewer.classList.toggle('drawing', on);
        $('draw-box').classList.toggle('active', on);
        if (!on) { draft?.el.remove(); draft = null; }
    }
    $('draw-box').addEventListener('click', () => setDrawing(!drawing));
    // 方框內的文字：取文字層（含 OCR 層）中心落在框內的片段，沒有就留空
    function textInBox(holder, box) {
        return [...holder.querySelectorAll('.textLayer span')].filter((s) => {
            const r = s.getBoundingClientRect();
            const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
            return r.width && cx >= box.left && cx <= box.right && cy >= box.top && cy <= box.bottom;
        }).map((s) => s.textContent).join(' ').replace(/\s+/g, ' ').trim().slice(0, 5000);
    }
    viewer.addEventListener('mousedown', (e) => {
        const holder = drawing && e.button === 0 && !e.target.closest('.overlay, .tool, .annot-badge') && e.target.closest('.pdf-page');
        if (!holder) return;
        e.preventDefault();  // 不要開始選字
        const el = document.createElement('div');
        el.className = 'annot-draft';
        holder.append(el);
        draft = { holder, el, x: e.clientX, y: e.clientY };
    });
    function draftBox(e) {
        const page = draft.holder.getBoundingClientRect();
        const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
        const x = clamp(e.clientX, page.left, page.right), y = clamp(e.clientY, page.top, page.bottom);
        return { left: Math.min(x, draft.x), top: Math.min(y, draft.y), right: Math.max(x, draft.x), bottom: Math.max(y, draft.y), page };
    }
    document.addEventListener('mousemove', (e) => {
        if (!draft) return;
        const b = draftBox(e);
        Object.assign(draft.el.style, { left: `${b.left - b.page.left}px`, top: `${b.top - b.page.top}px`,
                                        width: `${b.right - b.left}px`, height: `${b.bottom - b.top}px` });
    });
    document.addEventListener('mouseup', async (e) => {
        if (!draft) return;
        const { holder, el } = draft;
        const b = draftBox(e);
        draft = null;
        if (b.right - b.left < 6 || b.bottom - b.top < 6) { el.remove(); showAnnotationMenu(e); return; }  // 只是點一下：點在標記上就開選單
        const owner = current, page = Number(holder.dataset.page);
        const view = await pageView(owner, page);
        const sx = view.width / b.page.width, sy = view.height / b.page.height;
        const rect = [(b.left - b.page.left) * sx, (b.top - b.page.top) * sy, (b.right - b.page.left) * sx, (b.bottom - b.page.top) * sy]
            .map((v) => Math.round(v * 10) / 10);
        const text = textInBox(holder, b);
        const note = await askNote(text || `第 ${page + 1} 頁的框選範圍`);
        el.remove();
        if (note === null) return;
        try {
            await api(`${docApi(owner.name)}/annotations`, { page, rects: [rect], text, color: 'yellow', note, kind: 'box' });
        } catch (err) { notify(err.message); }
        loadAnnotations(owner);
    });

    function annotationAt(x, y) {
        const hit = [...pagesBox.querySelectorAll('.annot-mark')].find((m) => {
            const r = m.getBoundingClientRect();
            return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
        });
        return hit && current?.annotations.find((a) => String(a.id) === hit.dataset.id);
    }
    function showAnnotationMenu(e) {
        const a = e.target.closest?.('#viewer') && !e.target.closest('.overlay, .tool, .annot-badge') && annotationAt(e.clientX, e.clientY);
        if (!a) { $('annot-menu').hidden = true; return; }
        activeAnnotation = a;
        Object.assign($('annot-menu').style, { left: `${Math.min(e.clientX, window.innerWidth - 220)}px`, top: `${Math.max(e.clientY - 44, 8)}px` });
        $('annot-menu').hidden = false;
    }
    async function updateAnnotation(a, changes) {
        try { await fetch(`/api/annotations/${a.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes) }).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); }); }
        catch (e) { notify(`無法更新標記：${e.message}`); }
        loadAnnotations();
    }
    async function editNote(a) {
        $('annot-menu').hidden = true;
        const note = await askNote(a.text, a.note || '');
        if (note !== null) updateAnnotation(a, { note });
    }
    $('annot-menu').addEventListener('mousedown', (e) => e.preventDefault());
    $('annot-colors').addEventListener('click', (e) => {
        const { color } = e.target.dataset;
        if (color && activeAnnotation) { $('annot-menu').hidden = true; updateAnnotation(activeAnnotation, { color }); }
    });
    $('annot-edit').addEventListener('click', () => { if (activeAnnotation) editNote(activeAnnotation); });
    $('annot-delete').addEventListener('click', async () => {
        $('annot-menu').hidden = true;
        if (!activeAnnotation) return;
        try {
            const res = await fetch(`/api/annotations/${activeAnnotation.id}`, { method: 'DELETE' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
        } catch (e) { notify(`無法刪除標記：${e.message}`); }
        loadAnnotations();
    });

    function annotationRows() {
        if (!current) return '<p class="doc-note">先開啟一份文件。</p>';
        if (!current.annotations.length) return '<p class="doc-note">這份文件還沒有標記。選取文字後，在浮動選單選顏色或按「註解」；沒有文字的地方可以按工具列的「框選」畫方框。</p>';
        return current.annotations.map((a) => `<button class="annot-row" data-annot="${a.id}">
                <span class="doc-note"><span class="annot-dot annot-mark-dot" data-color="${escapeHtml(a.color)}"></span>第 ${a.page + 1} 頁・${escapeHtml(ANNOT_LABELS[a.color] || '')}</span>
                <span class="annot-text">${escapeHtml(a.text || (a.kind === 'box' ? '（框選範圍）' : '（沒有文字）'))}</span>
                ${a.note ? `<span class="annot-note">${escapeHtml(a.note)}</span>` : ''}
            </button>`).join('');
    }
    $('doc-list').addEventListener('click', (e) => {
        const row = e.target.closest('[data-annot]');
        if (!row) return;
        const a = current?.annotations.find((x) => String(x.id) === row.dataset.annot);
        if (!a) return;
        const holder = holderOf(a.page);
        if (!holder) return;
        viewer.scrollTop = holder.offsetTop + holder.clientHeight * (a.rects[0][1] / (current.views[a.page]?.height || 1)) - viewer.clientHeight / 3;
        pagesBox.querySelectorAll(`.annot-mark[data-id="${a.id}"]`).forEach((m) => {
            m.classList.add('flash');
            setTimeout(() => m.classList.remove('flash'), 1500);
        });
    });

    // ---------- 書籤與目錄 ----------
    // 自己加的書籤存在書庫（page 0 起算、y 是頁高比例）；目錄是 PDF 內建的大綱，用 PDF.js 讀，不存
    let renamingBookmark = null;
    const outlineOpen = new Set();
    async function loadBookmarks(owner = current) {
        try { owner.bookmarks = (await api(`${docApi(owner.name)}/bookmarks`)).bookmarks; }
        catch (e) { notify(`無法讀取書籤：${e.message}`); return; }
        if (current === owner) renderSidebar();
    }
    // 目錄是一列 { title, depth, page, y }；parent 與 children 由深度推回，編輯時只動這四個欄位
    function linkOutline(items) {
        const stack = [];
        items.forEach((item, i) => {
            item.depth = Math.min(item.depth, stack.length);  // 不能比上一項深超過一層
            stack.length = item.depth;
            item.parent = item.depth ? stack[item.depth - 1] : -1;
            item.children = false;
            if (item.parent >= 0) items[item.parent].children = true;
            stack.push(i);
        });
        return items;
    }
    // 有自訂目錄就用它，否則讀 PDF 內建大綱；頁碼先解析好，才能顯示，也才知道在不在開啟範圍內
    async function loadOutline(owner) {
        owner.outline = 'loading';
        const custom = await api(`${docApi(owner.name)}/outline`).then((r) => r.items).catch(() => null);
        if (custom) {
            owner.outline = linkOutline(custom);
            owner.outlineCustom = true;
            if (current === owner && sideView === 'bookmarks') renderSidebar();
            return;
        }
        const items = [];
        const walk = async (nodes, depth, parent) => {
            for (const node of nodes || []) {
                const item = { title: node.title || '（無標題）', depth, page: null, y: 0 };
                items.push(item);
                try {
                    const dest = typeof node.dest === 'string' ? await owner.pdf.getDestination(node.dest) : node.dest;
                    if (dest) {
                        item.page = typeof dest[0] === 'number' ? dest[0] : await owner.pdf.getPageIndex(dest[0]);
                        if (dest[1]?.name === 'XYZ' && typeof dest[3] === 'number') {
                            const view = await pageView(owner, item.page);
                            item.y = Math.min(Math.max(1 - dest[3] / view.height, 0), 1);
                        }
                    }
                } catch (e) { /* 壞掉的目的地：照樣列出，但不能跳 */ }
                if (node.items?.length) await walk(node.items, depth + 1, items.length - 1);
            }
        };
        try { await walk(await owner.pdf.getOutline(), 0, -1); } catch (e) { /* 沒有大綱 */ }
        owner.outline = linkOutline(items);
        owner.outlineCustom = false;
        if (current === owner && sideView === 'bookmarks') renderSidebar();
    }

    // 編輯目錄：每次變動都整份存回書庫（只存 title、depth、page、y），第一次存就成了這份文件的自訂目錄
    let outlineEditing = false;
    let resetArmed = false;
    let focusOutline = null;  // 重繪後要聚焦的項目（新增時）
    let outlineSaving = Promise.resolve();
    function saveOutline() {
        const owner = current;
        linkOutline(owner.outline);
        const items = owner.outline.map(({ title, depth, page, y }) => ({ title, depth, page, y }));
        outlineSaving = outlineSaving.then(() => putOutline(owner, items));  // 依序送出，後一次一定蓋過前一次
    }
    async function putOutline(owner, items) {
        try {
            const res = await fetch(`${docApi(owner.name)}/outline`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items }) });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            owner.outlineCustom = true;
        } catch (e) { notify(`無法儲存目錄：${e.message}`); }
    }
    function editOutline(i, action) {
        const items = current.outline, item = items[i];
        if (action === 'left') item.depth = Math.max(item.depth - 1, 0);
        if (action === 'right') item.depth += 1;  // linkOutline 會限制在上一項深度 + 1
        if (action === 'up' && i > 0) [items[i - 1], items[i]] = [items[i], items[i - 1]];
        if (action === 'down' && i < items.length - 1) [items[i + 1], items[i]] = [items[i], items[i + 1]];
        if (action === 'delete') items.splice(i, 1);
        saveOutline();
        renderSidebar();
    }
    function insertOutline() {
        const page = currentPage(), box = holderOf(page)?.getBoundingClientRect();
        if (!box) return;
        const y = Math.round(Math.min(Math.max((viewer.getBoundingClientRect().top + 12 - box.top) / box.height, 0), 1) * 1000) / 1000;
        const items = current.outline;
        let at = items.length;  // 插在第一個位置比它後面的項目之前
        const later = items.findIndex((o) => o.page !== null && (o.page > page || (o.page === page && o.y > y)));
        if (later >= 0) at = later;
        items.splice(at, 0, { title: `第 ${page + 1} 頁`, depth: at > 0 ? items[at - 1].depth : 0, page, y });
        focusOutline = at;
        saveOutline();
        renderSidebar();
    }
    function outlineHead() {
        if (!Array.isArray(current.outline)) return '<div class="side-section"><strong>目錄</strong></div>';
        if (!outlineEditing) {
            return `<div class="side-section"><strong>目錄${current.outlineCustom ? '<span class="doc-note">（已編輯）</span>' : ''}</strong>
                <button class="tool" data-outline-mode="edit">編輯</button></div>`;
        }
        return `<div class="side-section"><strong>編輯目錄</strong><span class="outline-tools">
            <button class="tool" data-outline-mode="insert" title="在目前捲到的位置插入一節">＋ 目前位置</button>
            ${current.outlineCustom ? `<button class="tool" data-outline-mode="reset">${resetArmed ? '確定還原？' : '還原'}</button>` : ''}
            <button class="tool primary" data-outline-mode="done">完成</button></span></div>`;
    }
    function outlineRows() {
        if (!Array.isArray(current.outline)) {
            if (current.pdf && current.outline === null) loadOutline(current);
            return '<p class="doc-note">讀取中…</p>';
        }
        if (!current.outline.length) {
            return outlineEditing ? '<p class="doc-note">捲到章節開頭，按「＋ 目前位置」加入。</p>'
                : '<p class="doc-note">這份 PDF 沒有內建目錄（掃描書通常沒有）。按「編輯」可以自己建立。</p>';
        }
        if (outlineEditing) {
            return current.outline.map((item, i) => `<div class="doc-row outline-edit" style="padding-left:${item.depth * 12}px">
                <input class="outline-input" data-outline-input="${i}" value="${escapeHtml(item.title)}" maxlength="300">
                <button class="doc-action" data-outline-jump="${i}" title="跳到這一節">${item.page !== null ? item.page + 1 : '—'}</button>
                <button class="doc-action" data-outline-edit="left" data-i="${i}" title="升一層">←</button>
                <button class="doc-action" data-outline-edit="right" data-i="${i}" title="降一層">→</button>
                <button class="doc-action" data-outline-edit="up" data-i="${i}" title="上移">↑</button>
                <button class="doc-action" data-outline-edit="down" data-i="${i}" title="下移">↓</button>
                <button class="doc-action" data-outline-edit="delete" data-i="${i}" title="刪除這一項（子項目保留）">×</button>
            </div>`).join('');
        }
        const visible = (item) => item.parent < 0 || (outlineOpen.has(item.parent) && visible(current.outline[item.parent]));
        return current.outline.map((item, i) => {
            if (!visible(item)) return '';
            const usable = item.page !== null && current.pages.includes(item.page);
            const toggle = item.children
                ? `<button class="outline-toggle" data-outline-toggle="${i}">${outlineOpen.has(i) ? '▾' : '▸'}</button>`
                : '<span class="outline-toggle"></span>';
            return `<div class="doc-row" style="padding-left:${item.depth * 12}px">${toggle}
                <button class="doc-item" data-outline="${i}" ${usable ? '' : 'disabled'} title="${escapeHtml(item.title)}">
                    ${item.page !== null ? `<span class="doc-note">${item.page + 1}</span>` : ''}<span class="doc-name">${escapeHtml(item.title)}</span></button>
            </div>`;
        }).join('');
    }
    function bookmarkRows() {
        if (!current) return '<p class="doc-note">先開啟一份文件。</p>';
        const marks = current.bookmarks.map((b) => renamingBookmark === b.id
            ? `<div class="doc-row"><input class="bookmark-input" data-bookmark-input="${b.id}" value="${escapeHtml(b.title)}" maxlength="200"></div>`
            : `<div class="doc-row">
                <button class="doc-item" data-bookmark="${b.id}" title="${escapeHtml(b.title)}">
                    <span class="doc-note">${b.page + 1}</span><span class="doc-name">${escapeHtml(b.title)}</span></button>
                <button class="doc-action" data-rename-bookmark="${b.id}" title="重新命名">改名</button>
                <button class="doc-action" data-delete-bookmark="${b.id}" title="刪除書籤">×</button>
            </div>`).join('') || '<p class="doc-note">還沒有書籤。捲到想記住的位置，按「＋ 加入目前位置」。</p>';
        return `<div class="side-section"><strong>我的書籤</strong><button class="tool" data-add-bookmark="1">＋ 加入目前位置</button></div>
            ${marks}
            ${outlineHead()}
            ${outlineRows()}`;
    }
    function scrollToSpot(page, y) {
        const holder = holderOf(page);
        if (!holder) { notify('這一頁不在目前開啟的範圍內'); return; }
        viewer.scrollTop = holder.offsetTop + holder.clientHeight * y - 12;
    }
    async function addBookmark() {
        if (!current?.pages.length) return;
        const page = currentPage(), box = holderOf(page).getBoundingClientRect();
        const top = viewer.getBoundingClientRect().top + 12;
        const y = Math.round(Math.min(Math.max((top - box.top) / box.height, 0), 1) * 1000) / 1000;
        // 預設名稱用這個位置所在的目錄章節；沒有目錄就由伺服器給「第 N 頁」
        const outline = Array.isArray(current.outline) ? current.outline : [];
        const section = outline.filter((o) => o.page !== null && (o.page < page || (o.page === page && o.y <= y))).pop();
        try {
            const b = await api(`${docApi(current.name)}/bookmarks`, { page, y, title: section?.title || '' });
            current.bookmarks.push(b);
            current.bookmarks.sort((a, c) => a.page - c.page || a.y - c.y || a.id - c.id);
            renamingBookmark = b.id;  // 加完直接可以改名，Enter 保留
        } catch (e) { notify(e.message); }
        renderSidebar();
    }
    async function finishRename(input, save) {
        if (renamingBookmark === null) return;
        const id = Number(input.dataset.bookmarkInput), title = input.value.trim();
        renamingBookmark = null;
        const b = current?.bookmarks.find((x) => x.id === id);
        if (save && title && b && title !== b.title) {
            try {
                const res = await fetch(`/api/bookmarks/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                b.title = title;
            } catch (e) { notify(`無法重新命名：${e.message}`); }
        }
        renderSidebar();
    }
    $('doc-list').addEventListener('keydown', (e) => {
        if (!e.target.matches('.bookmark-input')) return;
        if (e.key === 'Enter') finishRename(e.target, true);
        if (e.key === 'Escape') { e.stopPropagation(); finishRename(e.target, false); }
    });
    $('doc-list').addEventListener('focusout', (e) => {
        if (e.target.matches('.bookmark-input')) finishRename(e.target, true);
    });
    $('doc-list').addEventListener('click', async (e) => {
        const target = e.target.closest('button');
        if (!target || !current || sideView !== 'bookmarks') return;
        const { addBookmark: add, bookmark, renameBookmark, deleteBookmark, outline, outlineToggle } = target.dataset;
        if (add) addBookmark();
        if (bookmark) {
            const b = current.bookmarks.find((x) => String(x.id) === bookmark);
            if (b) scrollToSpot(b.page, b.y);
        }
        if (renameBookmark) { renamingBookmark = Number(renameBookmark); renderSidebar(); }
        if (deleteBookmark) {
            try {
                const res = await fetch(`/api/bookmarks/${deleteBookmark}`, { method: 'DELETE' });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                current.bookmarks = current.bookmarks.filter((b) => String(b.id) !== deleteBookmark);
            } catch (err) { notify(`無法刪除書籤：${err.message}`); }
            renderSidebar();
        }
        if (outline) {
            const item = current.outline[Number(outline)];
            scrollToSpot(item.page, item.y);
        }
        if (outlineToggle) {
            const i = Number(outlineToggle);
            outlineOpen.has(i) ? outlineOpen.delete(i) : outlineOpen.add(i);
            renderSidebar();
        }
        const { outlineMode, outlineEdit, outlineJump } = target.dataset;
        if (outlineEdit) editOutline(Number(target.dataset.i), outlineEdit);
        if (outlineJump) {
            const item = current.outline[Number(outlineJump)];
            if (item.page !== null) scrollToSpot(item.page, item.y);
        }
        if (outlineMode === 'edit' || outlineMode === 'done') {
            outlineEditing = outlineMode === 'edit';
            resetArmed = false;
            renderSidebar();
        }
        if (outlineMode === 'insert') insertOutline();
        if (outlineMode === 'reset') {
            if (!resetArmed) {  // 按兩次才還原，避免誤觸丟掉整份編輯
                resetArmed = true;
                renderSidebar();
                setTimeout(() => { if (resetArmed) { resetArmed = false; renderSidebar(); } }, 3000);
                return;
            }
            resetArmed = false;
            try {
                const res = await fetch(`${docApi(current.name)}/outline`, { method: 'DELETE' });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
            } catch (err) { notify(`無法還原目錄：${err.message}`); return; }
            current.outline = null;
            outlineOpen.clear();
            renderSidebar();
        }
    });
    // 改名在離開輸入框時存；Enter 等同離開
    $('doc-list').addEventListener('change', (e) => {
        if (!e.target.matches('.outline-input') || !current) return;
        const item = current.outline[Number(e.target.dataset.outlineInput)];
        const title = e.target.value.trim();
        if (item && title && title !== item.title) { item.title = title; saveOutline(); }
    });
    $('doc-list').addEventListener('keydown', (e) => {
        if (e.target.matches('.outline-input') && e.key === 'Enter') e.target.blur();
    });
    $('open-bookmarks').addEventListener('click', () => {
        sideView = sideView === 'bookmarks' ? 'files' : 'bookmarks';
        if (document.body.classList.contains('side-closed')) {
            document.body.classList.remove('side-closed');
            fitPages();
        }
        renderSidebar();
    });

    let lookupId = 0;
    $('menu-lookup').addEventListener('click', async () => {
        menu.hidden = true;
        const id = ++lookupId;
        const { text, context } = picked;
        $('lookup').hidden = false;
        $('lookup-term').textContent = text;
        $('lookup-zh').textContent = '查詢中…';
        const useLocal = currentMode() !== 'off';  // 關閉細翻時不用 GPU，只顯示 Google 譯名
        $('lookup-more').textContent = useLocal ? '本機模型正在依上下文補充…' : '';
        $('lookup-sentences').innerHTML = '';
        api('/api/lookup', { term: text })
            .then((r) => { if (id === lookupId) $('lookup-zh').textContent = r.translation; })
            .catch((e) => { if (id === lookupId) $('lookup-zh').textContent = e.message; });
        if (!useLocal) return;
        try {
            const more = await api('/api/explain', { term: text, context });
            if (id !== lookupId) return;
            $('lookup-more').textContent = [more.translation && `此處：${more.translation}`, more.explanation]
                .filter(Boolean).join('　');
            $('lookup-sentences').innerHTML = (more.sentences || []).map((s) =>
                `<li>${escapeHtml(s.en)}<br>${escapeHtml(s.zh)}</li>`).join('');
        } catch (e) {
            if (id === lookupId) $('lookup-more').textContent = e.message;
        }
    });
    $('lookup-close').addEventListener('click', () => { lookupId++; $('lookup').hidden = true; });

    // 只選幾個字時擷取整段；公式在 Marker 解析完才有 LaTeX，先給 PDF 文字，解析好再換掉
    let askTimer = null;
    async function loadExtract(owner) {
        const text = picked.text.length < 40 ? (picked.context || picked.text) : picked.text;
        try {
            const res = await api(`${docApi(owner)}/extract`, { pages: picked.pages, text });
            if ($('ask-modal').hidden) return;
            if (res.markdown) {
                $('ask-text').value = res.markdown;
                $('ask-preview').innerHTML = res.html;
                window.MathJax?.typesetPromise?.([$('ask-preview')]);
                $('ask-note').textContent = '公式已是 LaTeX，貼到 AI 就能讀。可以直接修改下面的內容。';
                return;
            }
            $('ask-note').textContent = res.waiting ? '這幾頁正在解析，完成後公式會自動換成 LaTeX；目前是 PDF 文字。'
                : '這段找不到解析結果，目前是 PDF 文字，公式可能失真。';
            if (res.waiting) askTimer = setTimeout(() => loadExtract(owner), 3000);
        } catch (e) {
            $('ask-note').textContent = e.message;
        }
    }
    $('menu-ask').addEventListener('click', () => {
        menu.hidden = true;
        $('ask-text').value = picked.text.length < 40 ? (picked.context || picked.text) : picked.text;
        $('ask-preview').innerHTML = '';
        $('ask-answer').innerHTML = '';
        $('ask-input').value = '';
        $('ask-note').textContent = '';
        $('ask-direct').hidden = !app.claude_api;
        $('ask-modal').hidden = false;
        loadExtract(current.name);
    });
    $('ask-close').addEventListener('click', () => {
        clearTimeout(askTimer);
        $('ask-modal').hidden = true;
    });
    $('ask-copy').addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText($('ask-text').value);
            notify('已複製');
        } catch (e) {
            $('ask-text').select();
            notify('無法自動複製，請按 Ctrl+C');
        }
    });
    $('ask-api').addEventListener('click', async () => {
        $('ask-answer').textContent = 'Claude 思考中…';
        try {
            const res = await api(`${docApi(current.name)}/ask`, { text: $('ask-text').value, question: $('ask-input').value });
            $('ask-answer').innerHTML = res.html;
            window.MathJax?.typesetPromise?.([$('ask-answer')]);
        } catch (e) {
            $('ask-answer').textContent = e.message;
        }
    });

    // ---------- 引用跳轉：/?doc=<文件名稱>&page=<1 起算>&bbox=x0,y0,x1,y1 ----------
    // bbox 單位是 point，相對 PyMuPDF 的 page.rect，原點在左上角；換成頁面比例畫在 overlay 上。
    // 跳轉後的位置不寫回記住的閱讀位置，直到使用者自己捲動、點擊或按鍵。
    let holdPosition = null;  // 暫停記錄位置的文件名稱
    ['wheel', 'mousedown', 'touchstart'].forEach((type) =>
        viewer.addEventListener(type, () => { holdPosition = null; }, { passive: true }));
    document.addEventListener('keydown', (e) => {
        if (!['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) holdPosition = null;
        if (e.key === 'Escape') { citationBox.remove(); $('annot-menu').hidden = true; setDrawing(false); }
    });

    const citationBox = document.createElement('div');
    citationBox.className = 'citation-box';
    citationBox.innerHTML = '<button class="tool" type="button" title="清除標示（Esc）">×</button>';
    citationBox.firstChild.addEventListener('mousedown', (e) => e.stopPropagation());
    citationBox.firstChild.addEventListener('click', () => citationBox.remove());

    function readJump() {
        const params = new URLSearchParams(location.search);
        if (!params.has('doc') && !params.has('pdf')) return null;
        history.replaceState(null, '', location.pathname);  // 重新整理時不再跳一次
        return { name: params.get('doc'), pdf: params.get('pdf'), page: params.get('page'), bbox: params.get('bbox') };
    }
    // ?pdf=<完整路徑>：已在書庫就直接跳；在開啟過的資料夾內但還沒加入時，先問使用者
    async function jumpToPdf(jump) {
        let check;
        try {
            check = await api(`/api/check-pdf?path=${encodeURIComponent(jump.pdf)}`);
        } catch (e) { notify(`無法檢查檔案：${e.message}`); return; }
        if (!check.allowed) { notify('不能加入這個檔案（必須是已開啟資料夾內的 PDF）'); return; }
        if (check.in_library) { await jumpTo({ ...jump, name: check.name }); return; }
        const parts = jump.pdf.split(/[\\/]/);
        $('add-file').textContent = parts[parts.length - 1];
        $('add-path').textContent = jump.pdf;
        $('add-modal').hidden = false;
        const confirmed = await new Promise((resolve) => {
            $('add-confirm').onclick = () => resolve(true);
            $('add-cancel').onclick = () => resolve(false);
        });
        $('add-modal').hidden = true;
        if (!confirmed) return;
        try {
            const { name } = await api('/api/docs', { path: jump.pdf });
            await refresh();
            await jumpTo({ ...jump, name });
        } catch (e) { notify(e.message); }
    }
    function parseBbox(text) {
        if (!text) return null;
        const values = text.split(',').map(Number);
        const valid = values.length === 4 && values.every(Number.isFinite) && values[2] > values[0] && values[3] > values[1];
        return valid ? values : undefined;
    }
    async function jumpTo({ name, page, bbox }) {
        const doc = app.docs.find((d) => d.name === name);
        if (!doc?.has_pdf) { notify(doc ? '找不到這份文件的 PDF' : '找不到文件'); return; }
        const index = Number(page) - 1;
        holdPosition = name;
        if (current?.name !== name) {
            keepOpen(name);
            saveWorkspace();
            await openDoc(name, index);
        }
        if (current?.name !== name || !current.pages.length) return;
        if (!Number.isInteger(index) || !current.pages.includes(index)) { notify('頁碼超出範圍'); return; }
        scrollToPage(index);
        const box = parseBbox(bbox);
        if (box === undefined) { notify('框的座標格式錯誤，只捲到該頁'); return; }
        if (box) await markCitation(index, box);
    }
    async function markCitation(index, [x0, y0, x1, y1]) {
        const view = (await current.pdf.getPage(index + 1)).getViewport({ scale: 1 });
        if (x0 < -1 || y0 < -1 || x1 > view.width + 1 || y1 > view.height + 1) { notify('框超出頁面範圍，只捲到該頁'); return; }
        Object.assign(citationBox.style, {
            left: `${x0 / view.width * 100}%`, top: `${y0 / view.height * 100}%`,
            width: `${(x1 - x0) / view.width * 100}%`, height: `${(y1 - y0) / view.height * 100}%`,
        });
        const holder = holderOf(index);
        holder.querySelector('.overlay-layer').append(citationBox);
        // 框放在視窗上方 1/3 處，這也是判斷目前頁的位置
        viewer.scrollTop = holder.offsetTop + holder.clientHeight * (y0 / view.height) - viewer.clientHeight / 3;
    }

    // ---------- 啟動：網址指定位置時跳過去，否則回到上次的檔案 ----------
    (async () => {
        await refresh();
        await loadFolders();
        zoom = app.state.zoom || 1;
        $('zoom-label').textContent = `${Math.round(zoom * 100)}%`;
        const jump = readJump();
        if (jump) { await (jump.name ? jumpTo(jump) : jumpToPdf(jump)); return; }  // 兩者都有時以 doc 為準
        if (app.docs.some((d) => d.name === app.state.last && d.has_pdf)) {
            keepOpen(app.state.last);  // 上次從資料夾開的文件也要出現在清單
            saveWorkspace();
            openDoc(app.state.last);
        }
    })();
})();

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
        document.querySelectorAll('.side-tab').forEach((b) => b.classList.toggle('active', b.dataset.side === sideView));
        const { files = [], folders = [] } = app.state;
        if (sideView === 'files') {
            const open = files.map((n) => app.docs.find((d) => d.name === n)).filter(Boolean)
                .map((d) => docRow(d, `<button class="doc-action" data-close-file="${escapeHtml(d.name)}" title="關閉（保留在歷史）">×</button>`));
            $('doc-list').innerHTML = (open.join('') + folders.map(folderBlock).join(''))
                || '<p class="doc-note">開啟檔案或資料夾開始閱讀；看過的文件在「歷史」。</p>';
        } else {
            $('doc-list').innerHTML = app.docs.map((d) =>
                docRow(d, `<button class="doc-action" data-delete="${escapeHtml(d.name)}" title="刪除翻譯">刪除</button>`)).join('')
                || '<p class="doc-note">還沒有文件。</p>';
        }
        const used = Math.round(app.usage / 1000), limit = Math.round(app.limit / 1000);
        $('usage').textContent = `本月粗翻 ${used}k / ${limit}k 字元`;
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
    document.querySelectorAll('.side-tab').forEach((btn) => btn.addEventListener('click', () => {
        sideView = btn.dataset.side;
        renderSidebar();
    }));
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
    const FIT_WIDTH = 900;  // 100% 時的頁寬上限，寬螢幕才不會把頁面放得太大

    function pageWidth() {
        return Math.round(Math.min(viewer.clientWidth - 24, FIT_WIDTH) * zoom);
    }
    function fitPages() {
        const anchor = currentPage();
        pagesBox.querySelectorAll('.pdf-page').forEach((holder) => {
            holder.style.width = `${pageWidth()}px`;
            holder.style.height = `${pageWidth() / Number(holder.dataset.aspect)}px`;
            delete holder.dataset.rendered;
            holder.querySelector('canvas')?.remove();
            holder.querySelector('.textLayer')?.remove();
            lazy.unobserve(holder);
            lazy.observe(holder);
            holder.querySelectorAll('.overlay').forEach(fitOverlay);
        });
        $('zoom-label').textContent = `${Math.round(zoom * 100)}%`;
        scrollToPage(anchor);
    }

    async function renderPage(holder) {
        if (holder.dataset.rendered || !current) return;
        holder.dataset.rendered = '1';
        const owner = current;
        const page = await owner.pdf.getPage(Number(holder.dataset.page) + 1);
        const cssScale = holder.clientWidth / page.getViewport({ scale: 1 }).width;
        const viewport = page.getViewport({ scale: cssScale * (window.devicePixelRatio || 1) });
        const canvas = document.createElement('canvas');
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        holder.prepend(canvas);
        await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;

        const layer = document.createElement('div');
        layer.className = 'textLayer';
        layer.style.setProperty('--scale-factor', cssScale);
        canvas.after(layer);
        await pdfjsLib.renderTextLayer({
            textContentSource: await page.getTextContent(), container: layer,
            viewport: page.getViewport({ scale: cssScale }), textDivs: [],
        }).promise;
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

    async function openDoc(name) {
        if (current?.name === name) return;
        closeDoc();
        const owner = { name, pdf: null, pages: [], events: null, blocks: {} };
        current = owner;
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
                `<div class="pdf-page" data-page="${p}" data-aspect="${aspect}"><div class="overlay-layer"></div></div>`).join('');
            $('page-total').textContent = owner.pdf.numPages;
            fitPages();
            scrollToPage(app.state.positions?.[name] ?? info.pages[0]);
            reportPosition();
            connectEvents();
        } catch (e) {
            notify(`無法開啟：${e.message}`);
        }
    }
    function closeDoc() {
        if (!current) return;
        current.events?.close();
        current.pdf?.destroy();
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
    let lastReported = null;
    let reportTimer = null;
    function reportPosition() {
        clearTimeout(reportTimer);
        reportTimer = setTimeout(() => {
            if (!current?.pages.length) return;
            const page = currentPage();
            $('page-input').value = page + 1;
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

    function setZoom(next) {
        zoom = Math.max(0.5, Math.min(3, Math.round(next * 10) / 10));
        api('/api/state', { zoom }).catch(() => {});
        if (current) fitPages();
    }
    $('zoom-in').addEventListener('click', () => setZoom(zoom + 0.1));
    $('zoom-out').addEventListener('click', () => setZoom(zoom - 0.1));
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
            if (!statusText) setStatus(msg.pending ? '' : '已全部細翻');
        },
        status(msg) { statusText = msg.text; setStatus(msg.text); },
        fine(msg) {
            // 粗翻顯示中的段落換成細翻
            holderOf(msg.page)?.querySelectorAll('.overlay:not(.fine)').forEach((el) => fillOverlay(el, msg.page));
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
    document.addEventListener('mousemove', (e) => { mouse.x = e.clientX; mouse.y = e.clientY; }, { passive: true });

    async function pageBlocks(page) {
        const owner = current;
        if (!owner.blocks[page]) owner.blocks[page] = (await api(`${docApi(owner.name)}/pages/${page}`)).blocks;
        return owner.blocks[page];
    }
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
    function fitOverlay(el) {
        const holder = el.closest('.pdf-page');
        const lineHeight = Number(el.dataset.height) / 100 * holder.clientHeight / Number(el.dataset.lines);
        let size = Math.max(MIN_FONT, Math.min(lineHeight * 0.8, 22));
        el.style.fontSize = `${size}px`;
        while (el.scrollHeight > el.clientHeight && size > MIN_FONT) {
            size -= 0.5;
            el.style.fontSize = `${size}px`;
        }
    }
    async function fillOverlay(el, page) {
        try {
            const res = await api(`${docApi(current.name)}/translate`, { page, block: el.dataset.block });
            el.innerHTML = res.html;
            el.classList.remove('waiting');
            el.classList.toggle('fine', res.source === 'fine');
            fitOverlay(el);
            if (res.source === 'fine' && window.MathJax?.typesetPromise) {
                await MathJax.typesetPromise([el]).catch(() => {});
                fitOverlay(el);
            }
        } catch (e) {
            if (el.classList.contains('waiting')) el.remove();
            notify(e.message);
        }
    }
    async function toggleParagraph() {
        if (!current?.pages.length) return;
        const hit = await blockAtPoint(mouse.x, mouse.y);
        if (!hit) {
            const page = holderAtMouse()?.dataset.page;
            if (page !== undefined && !(await pageBlocks(Number(page))).length) notify('這一頁沒有可翻譯的文字層，細翻完成後才看得到譯文');
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

    // 只有單獨按下再放開 Ctrl 才切換；Ctrl+C、Ctrl+滾輪、Ctrl+點擊都不算
    let ctrlAlone = false;
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Control') { if (!e.repeat) ctrlAlone = true; }
        else ctrlAlone = false;
    });
    document.addEventListener('keyup', (e) => {
        if (e.key === 'Control' && ctrlAlone) {
            ctrlAlone = false;
            if (!['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) toggleParagraph();
        }
    });
    document.addEventListener('mousedown', () => { ctrlAlone = false; });
    window.addEventListener('blur', () => { ctrlAlone = false; });
    viewer.addEventListener('wheel', (e) => {
        if (!e.ctrlKey) return;
        e.preventDefault();
        ctrlAlone = false;
        setZoom(zoom + (e.deltaY < 0 ? 0.1 : -0.1));
    }, { passive: false });

    // ---------- 選字：查詢與問 Claude ----------
    const menu = $('selection-menu');
    let picked = { text: '', context: '', translation: '' };

    function cleanTerm(text) {
        return text.replace(/\s+/g, ' ').replace(/^[\s.,;:!?()[\]{}"'「」（），。]+|[\s.,;:!?()[\]{}"'「」（），。]+$/g, '');
    }
    document.addEventListener('mouseup', (e) => {
        if (menu.contains(e.target)) return;
        setTimeout(async () => {
            const sel = window.getSelection();
            const text = sel ? cleanTerm(sel.toString()) : '';
            if (!text || !sel.rangeCount || !viewer.contains(sel.anchorNode)) { menu.hidden = true; return; }
            const rect = sel.getRangeAt(0).getBoundingClientRect();
            const hit = await blockAtPoint(mouse.x, mouse.y).catch(() => null);
            const overlay = sel.anchorNode.parentElement?.closest('.overlay');
            picked = { text, context: hit?.block.text?.slice(0, 1000) || '', translation: overlay?.innerText || '' };
            menu.style.left = `${Math.min(rect.left, window.innerWidth - 150)}px`;
            menu.style.top = `${Math.max(rect.top - 38, 8)}px`;
            menu.hidden = false;
        }, 0);
    });
    menu.addEventListener('mousedown', (e) => e.preventDefault());

    let lookupId = 0;
    $('menu-lookup').addEventListener('click', async () => {
        menu.hidden = true;
        const id = ++lookupId;
        const { text, context } = picked;
        $('lookup').hidden = false;
        $('lookup-term').textContent = text;
        $('lookup-zh').textContent = '查詢中…';
        $('lookup-more').textContent = '本機模型正在依上下文補充…';
        $('lookup-sentences').innerHTML = '';
        api('/api/lookup', { term: text })
            .then((r) => { if (id === lookupId) $('lookup-zh').textContent = r.translation; })
            .catch((e) => { if (id === lookupId) $('lookup-zh').textContent = e.message; });
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

    let askMode = 'explain';
    const askBody = () => ({ text: picked.context || picked.text, translation: picked.translation,
                             mode: askMode, question: $('ask-input').value });
    $('menu-ask').addEventListener('click', () => {
        menu.hidden = true;
        $('ask-source').textContent = picked.context || picked.text;
        $('ask-answer').innerHTML = '';
        $('ask-input').value = picked.context && picked.text !== picked.context ? `「${picked.text}」是什麼意思？` : '';
        $('ask-api').hidden = !app.claude_api;
        $('ask-modal').hidden = false;
    });
    document.querySelectorAll('.mode').forEach((btn) => btn.addEventListener('click', () => {
        askMode = btn.dataset.mode;
        document.querySelectorAll('.mode').forEach((b) => b.classList.toggle('active', b === btn));
    }));
    $('ask-close').addEventListener('click', () => { $('ask-modal').hidden = true; });
    $('ask-copy').addEventListener('click', async () => {
        try {
            const { text } = await api(`${docApi(current.name)}/prompt`, askBody());
            await navigator.clipboard.writeText(text);
            notify('已複製提示詞');
        } catch (e) { notify(e.message); }
    });
    $('ask-api').addEventListener('click', async () => {
        $('ask-answer').textContent = 'Claude 思考中…';
        try {
            const res = await api(`${docApi(current.name)}/ask`, askBody());
            $('ask-answer').innerHTML = res.html;
            window.MathJax?.typesetPromise?.([$('ask-answer')]);
        } catch (e) {
            $('ask-answer').textContent = e.message;
        }
    });

    // ---------- 啟動：回到上次的檔案 ----------
    (async () => {
        await refresh();
        await loadFolders();
        zoom = app.state.zoom || 1;
        $('zoom-label').textContent = `${Math.round(zoom * 100)}%`;
        if (app.docs.some((d) => d.name === app.state.last && d.has_pdf)) {
            keepOpen(app.state.last);  // 上次從資料夾開的文件也要出現在清單
            saveWorkspace();
            openDoc(app.state.last);
        }
    })();
})();

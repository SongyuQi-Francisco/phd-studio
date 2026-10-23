import { render } from "preact";
import { useEffect, useMemo, useRef, useState, useCallback } from "preact/hooks";
import { html, Icon, api, prefs } from "/lib.js";
import { PdfView } from "/pdfview.js";
import { ChatPanel, NotePanel, PROMPTS } from "/chat.js";

// ---------------- library ----------------

function Library({ papers, current, open, onPick, onClose }) {
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState(prefs.get("libFilter", "queue"));
  const inputRef = useRef();
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 60); }, [open]);
  useEffect(() => prefs.set("libFilter", filter), [filter]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let list = papers.filter((p) => !needle || (p.stem + " " + p.title + " " + p.tags.join(" ")).toLowerCase().includes(needle));
    if (!needle && filter === "queue") list = list.filter((p) => p.queue_rank != null).sort((a, b) => a.queue_rank - b.queue_rank);
    else if (filter === "pdf") list = list.filter((p) => p.pdf === "local");
    else list = list.slice().sort((a, b) => b.mtime - a.mtime);
    return list;
  }, [papers, q, filter]);

  const onKey = (e) => {
    if (e.key === "Escape") onClose();
    if (e.key === "Enter" && shown[0] && shown[0].pdf !== "none") onPick(shown[0].stem);
  };

  return html`<div class=${"drawer" + (open ? " open" : "")}>
    <div class="drawer-head">
      <h2>论文<small>${papers.length}</small></h2>
      <label class="search"><${Icon} name="search" sm /><input ref=${inputRef} placeholder="搜索标题、标签" value=${q} onInput=${(e) => setQ(e.target.value)} onKeyDown=${onKey} /></label>
      <div class="seg">
        ${[["queue", "待读"], ["all", "最近"], ["pdf", "本地 PDF"]].map(([k, l]) => html`<button class=${filter === k && !q ? "on" : ""} onClick=${() => { setFilter(k); setQ(""); }}>${l}</button>`)}
      </div>
    </div>
    <div class="list">
      ${shown.map((p) => html`<button class=${"row" + (p.stem === current ? " on" : "") + (p.pdf === "none" ? " disabled" : "")}
          title=${p.pdf === "none" ? "笔记里没有 PDF 链接" : p.title} onClick=${() => p.pdf !== "none" && onPick(p.stem)}>
        <span class=${"dot" + (p.pdf === "local" ? " ok" : "")} />
        <span style="min-width:0"><b>${p.stem}</b><span class="t">${p.title}</span></span>
      </button>`)}
    </div>
  </div>`;
}

// ---------------- reader ----------------

function Reader({ stem, paper, onSelectionAction, onCropAction, pageRef, viewRef, toast }) {
  const containerRef = useRef();
  const wrapRef = useRef();
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(0);
  const [scale, setScale] = useState(1);
  const [loading, setLoading] = useState("");
  const [variant, setVariant] = useState("original");
  const [bi, setBi] = useState({ state: "none" });
  const [showOutline, setShowOutline] = useState(false);
  const [outline, setOutline] = useState([]);
  const [findOpen, setFindOpen] = useState(false);
  const [findQ, setFindQ] = useState("");
  const [confirmBi, setConfirmBi] = useState(false);
  const [cropMode, setCropMode] = useState(false);
  const [drag, setDrag] = useState(null);
  const [pop, setPop] = useState(null); // {x,y,kind:'sel'|'crop', text, page, crop, trans}

  useEffect(() => {
    const v = new PdfView(containerRef.current, {
      onPage: (n) => { setPage(n); pageRef.current = n; },
      onPages: setPages,
    });
    v.onScale = setScale;
    viewRef.current = v;
    return () => v.destroy();
  }, []);

  const load = useCallback(async (which, keepPage) => {
    const v = viewRef.current;
    const startPage = keepPage ?? prefs.get("page:" + stem, 1);
    setLoading(which === "original" ? "正在打开…" : "正在打开双语版…");
    setPop(null);
    try {
      await v.open(`/api/pdf/${encodeURIComponent(stem)}${which === "original" ? "" : "?variant=" + which}`, { page: startPage });
      setLoading("");
      v.outline().then(setOutline);
    } catch (e) {
      setLoading(which === "original" ? "打不开这篇的 PDF：" + (e.message || "下载失败") : "双语版打开失败");
    }
  }, [stem]);

  useEffect(() => {
    setVariant("original"); setShowOutline(false); setFindOpen(false);
    load("original");
  }, [stem]);

  useEffect(() => { if (pages) prefs.set("page:" + stem, page); }, [page]);
  useEffect(() => { if (paper?.bilingual) setBi(paper.bilingual); }, [paper]);

  // poll bilingual job
  useEffect(() => {
    if (bi.state !== "running") return;
    const t = setInterval(async () => {
      const s = await api(`/api/bilingual/${encodeURIComponent(stem)}`);
      setBi(s);
      if (s.state === "done") toast("双语版已生成");
      if (s.state === "error") toast("双语生成失败");
    }, 3000);
    return () => clearInterval(t);
  }, [bi.state, stem]);

  const switchVariant = (v) => {
    if (v === variant) return;
    if (v !== "original" && bi.state !== "done") { setConfirmBi(true); return; }
    setVariant(v);
    load(v, page);
  };
  const startBi = async () => {
    setConfirmBi(false);
    try { setBi(await api(`/api/bilingual/${encodeURIComponent(stem)}`, { method: "POST" })); }
    catch (e) { toast(e.message); }
  };

  // ---- text selection popover ----
  useEffect(() => {
    const el = containerRef.current;
    const up = (e) => {
      if (cropMode || e.altKey) return;
      setTimeout(() => {
        const sel = window.getSelection();
        const text = sel?.toString().trim();
        if (!text || text.length < 2 || !el.contains(sel.anchorNode)) return;
        const r = sel.getRangeAt(0).getBoundingClientRect();
        const box = wrapRef.current.getBoundingClientRect();
        const pageEl = sel.anchorNode.parentElement?.closest(".page");
        setPop({ kind: "sel", text: text.replace(/\s+/g, " "), page: Number(pageEl?.dataset.pageNumber) || page,
          x: r.left + r.width / 2 - box.left, y: r.top - box.top });
      }, 0);
    };
    const down = (e) => { if (!e.target.closest(".pop")) setPop(null); };
    el.addEventListener("mouseup", up);
    wrapRef.current.addEventListener("mousedown", down, true);
    const hide = () => setPop((p) => (p && !p.trans ? null : p));
    el.addEventListener("scroll", hide);
    return () => { el.removeEventListener("mouseup", up); el.removeEventListener("scroll", hide); };
  }, [cropMode, page]);

  // ---- rectangle capture: toolbar toggle or ⌥-drag ----
  const startDrag = (e) => {
    if (!(cropMode || e.altKey) || e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    window.getSelection()?.removeAllRanges();
    const box = wrapRef.current.getBoundingClientRect();
    const d = { x0: e.clientX, y0: e.clientY, x1: e.clientX, y1: e.clientY, box };
    setDrag(d);
    const move = (ev) => setDrag((p) => p && { ...p, x1: ev.clientX, y1: ev.clientY });
    const done = async (ev) => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", done);
      setDrag(null);
      const rect = { left: Math.min(d.x0, ev.clientX), right: Math.max(d.x0, ev.clientX), top: Math.min(d.y0, ev.clientY), bottom: Math.max(d.y0, ev.clientY) };
      if (rect.right - rect.left < 12 || rect.bottom - rect.top < 12) return;
      const shot = viewRef.current.capture(rect);
      setCropMode(false);
      if (!shot) return;
      const { name } = await api("/api/crops", { json: { png_base64: shot.dataUrl } });
      setPop({ kind: "crop", crop: { name, page: shot.page }, x: (rect.left + rect.right) / 2 - box.left, y: rect.top - box.top });
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", done);
  };

  const doTranslate = async () => {
    setPop((p) => ({ ...p, trans: { loading: true } }));
    try {
      const r = await api("/api/translate", { json: { text: pop.text } });
      setPop((p) => p && { ...p, trans: r });
    } catch (e) { setPop((p) => p && { ...p, trans: { text: e.message, engine: "错误" } }); }
  };

  // keyboard
  useEffect(() => {
    const k = (e) => {
      if (e.target.matches("input, textarea")) return;
      if ((e.metaKey || e.ctrlKey) && e.key === "f") { e.preventDefault(); setFindOpen(true); }
      if (e.key === "Escape") { setPop(null); setCropMode(false); setShowOutline(false); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);

  const v = viewRef.current;
  const popStyle = pop && { left: Math.max(8, pop.x - (pop.trans ? 210 : 120)) + "px", top: Math.max(8, pop.y - 48) + "px" };
  const transStyle = pop?.trans && { left: Math.max(8, pop.x - 210) + "px", top: Math.max(8, pop.y - 48) + "px", transform: "none" };

  return html`<div class="reader" ref=${wrapRef} onMouseDownCapture=${startDrag}>
    <div class="toolbar">
      <span class="tb-title" title=${paper?.title}>${stem}</span>
      <span class="grow" />
      <span class="pageinput"><input value=${page} onKeyDown=${(e) => e.key === "Enter" && v.goTo(Number(e.target.value))} />/ ${pages || "–"}</span>
      <div class="tb-sep" />
      <button class="tb-btn" title="缩小" onClick=${() => v.zoom(1 / 1.15)}><${Icon} name="minus" sm /></button>
      <span class="zoomval">${Math.round(scale * 100)}%</span>
      <button class="tb-btn" title="放大" onClick=${() => v.zoom(1.15)}><${Icon} name="plus" sm /></button>
      <button class="tb-btn" title="适合宽度" onClick=${() => v.fitWidth()}><${Icon} name="fit" sm /></button>
      <div class="tb-sep" />
      <div class="seg">
        <button class=${variant === "original" ? "on" : ""} onClick=${() => switchVariant("original")}>原文</button>
        <button class=${variant === "dual" ? "on" : ""} onClick=${() => switchVariant("dual")}>${bi.state === "running" ? `生成中 ${elapsed(bi.started)}` : "双语"}</button>
        <button class=${variant === "mono" ? "on" : ""} onClick=${() => switchVariant("mono")}>中文</button>
      </div>
      <div class="tb-sep" />
      <button class=${"tb-btn" + (cropMode ? " on" : "")} title="框选图表 / 公式（或按住 ⌥ 拖动）" onClick=${() => setCropMode(!cropMode)}><${Icon} name="crop" sm /></button>
      <button class=${"tb-btn" + (showOutline ? " on" : "")} title="目录" onClick=${() => setShowOutline(!showOutline)}><${Icon} name="outline" sm /></button>
      ${findOpen
        ? html`<span class="findbox"><input autofocus placeholder="查找" value=${findQ} onInput=${(e) => { setFindQ(e.target.value); v.find(e.target.value); }}
            onKeyDown=${(e) => { if (e.key === "Enter") v.find(findQ, e.shiftKey); if (e.key === "Escape") { setFindOpen(false); v.find(""); } }} />
            <button class="tb-btn" onClick=${() => { setFindOpen(false); v.find(""); }}><${Icon} name="x" sm /></button></span>`
        : html`<button class="tb-btn" title="查找 (⌘F)" onClick=${() => setFindOpen(true)}><${Icon} name="search" sm /></button>`}
      <span class="grow" />
    </div>

    <div id="viewerContainer" ref=${containerRef}><div class="pdfViewer" /></div>
    ${loading && html`<div class="loading">${loading}</div>`}
    ${cropMode && html`<div class="crop-layer" /><div class="croptip">拖动框选图表或公式 · Esc 取消</div>`}
    ${drag && html`<div class="crop-rect" style=${{
      left: Math.min(drag.x0, drag.x1) - drag.box.left + "px", top: Math.min(drag.y0, drag.y1) - drag.box.top + "px",
      width: Math.abs(drag.x1 - drag.x0) + "px", height: Math.abs(drag.y1 - drag.y0) + "px" }} />`}

    ${showOutline && html`<div class="pop outline">
      ${outline.length ? flatten(outline).map(([o, depth]) => html`<button style=${{ paddingLeft: 10 + depth * 14 + "px" }} onClick=${() => { v.goToDest(o.dest); setShowOutline(false); }}>${o.title}</button>`)
        : html`<div style="padding:10px;color:var(--text-3);font-size:13px">这份 PDF 没有目录</div>`}
    </div>`}

    ${confirmBi && html`<div class="pop confirm">
      <h3>生成整篇双语 PDF</h3>
      <p>用 pdf2zh 翻译全文，保留公式、图表和版式，左右对照。一般 3–8 分钟，后台进行，期间可以继续阅读。</p>
      <div class="row-end"><button class="btn ghost" onClick=${() => setConfirmBi(false)}>取消</button><button class="btn primary" onClick=${startBi}>开始生成</button></div>
    </div>`}

    ${pop && pop.kind === "sel" && html`<div class="pop" style=${pop.trans ? transStyle : popStyle}>
      <div class="pop-actions">
        <button class="primary" onClick=${doTranslate}><${Icon} name="translate" sm />翻译</button>
        <button onClick=${() => { onSelectionAction("explain", pop); setPop(null); }}><${Icon} name="sparkle" sm />讲解</button>
        <button onClick=${() => { onSelectionAction("ask", pop); setPop(null); }}><${Icon} name="chat" sm />问 Claude</button>
      </div>
      ${pop.trans && html`<div class="trans">
        ${pop.trans.loading ? html`<div class="typing"><i /><i /><i /></div>` : html`<div>${pop.trans.text}</div>
          <div class="meta"><span>${pop.trans.engine}</span><button onClick=${() => { navigator.clipboard?.writeText(pop.trans.text); toast("已复制"); }}>复制</button></div>`}
      </div>`}
    </div>`}

    ${pop && pop.kind === "crop" && html`<div class="pop" style=${popStyle}>
      <div class="pop-actions">
        <button class="primary" onClick=${() => { onCropAction("explain", pop.crop); setPop(null); }}><${Icon} name="sparkle" sm />讲解</button>
        <button onClick=${() => { onCropAction("ask", pop.crop); setPop(null); }}><${Icon} name="chat" sm />问 Claude</button>
        <button onClick=${() => setPop(null)}><${Icon} name="x" sm /></button>
      </div>
    </div>`}
  </div>`;
}

function elapsed(start) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - (start || Date.now() / 1000)));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function flatten(items, depth = 0, out = []) {
  for (const o of items) { out.push([o, depth]); if (o.items?.length && depth < 2) flatten(o.items, depth + 1, out); }
  return out;
}

// ---------------- settings ----------------

function Settings({ status, onClose, onSaved }) {
  const [s, setS] = useState(status.settings);
  const save = async (patch) => { const n = await api("/api/settings", { json: patch }); setS(n); onSaved(); };
  const c = status.claude;
  return html`<div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <div class="modal">
      <h2>设置</h2>
      <div class="field"><div><label>Claude</label>
        <small>${c.ok ? "已连接 · " + c.method : "未登录：" + c.hint}</small></div><span class=${"dot " + (c.ok ? "ok" : "bad")} /></div>
      <div class="field"><div><label>伴读模型</label><small>默认跟随你的 Claude 账号设置</small></div>
        <select value=${s.claude_model} onChange=${(e) => save({ claude_model: e.target.value })}>
          <option value="">默认</option><option value="opus">Opus</option><option value="sonnet">Sonnet</option><option value="haiku">Haiku（快）</option>
        </select></div>
      <div class="field"><div><label>翻译引擎</label><small>划词翻译和整篇双语 PDF</small></div>
        <select value=${s.translate_engine} onChange=${(e) => save({ translate_engine: e.target.value })}>
          <option value="deepseek">DeepSeek</option><option value="google">Google 翻译（免费）</option>
        </select></div>
      <div class="row-end" style="margin-top:12px"><button class="btn" onClick=${onClose}>完成</button></div>
    </div>
  </div>`;
}

// ---------------- app ----------------

function Home({ papers, onPick }) {
  const last = prefs.get("lastStem", null);
  const lastPaper = papers.find((p) => p.stem === last);
  const queue = papers.filter((p) => p.queue_rank != null && p.pdf !== "none" && p.stem !== last).sort((a, b) => a.queue_rank - b.queue_rank).slice(0, 5);
  const row = (p) => html`<button class="row" onClick=${() => onPick(p.stem)}><span class=${"dot" + (p.pdf === "local" ? " ok" : "")} />
    <span style="min-width:0"><b>${p.stem}</b><span class="t">${p.title}</span></span></button>`;
  return html`<div class="empty"><div class="empty-inner">
    <h1>读点什么</h1>
    <p>按 <span class="kbd">⌘K</span> 搜索全部 ${papers.length} 篇</p>
    ${lastPaper && html`<div class="label">继续阅读</div><div class="card-list">${row(lastPaper)}</div>`}
    ${queue.length > 0 && html`<div class="label">待读</div><div class="card-list">${queue.map(row)}</div>`}
  </div></div>`;
}

function App() {
  const [papers, setPapers] = useState([]);
  const [stem, setStem] = useState(prefs.get("lastStem", null));
  const [paper, setPaper] = useState(null);
  const [drawer, setDrawer] = useState(false);
  const [tab, setTab] = useState("claude");
  const [status, setStatus] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [attachments, setAttachments] = useState({ selection: null, images: [] });
  const [noteVersion, setNoteVersion] = useState(0);
  const [toastMsg, setToastMsg] = useState("");
  const [sideW, setSideW] = useState(prefs.get("sideW", 420));
  const pageRef = useRef(1);
  const viewRef = useRef(null);
  const chatRef = useRef(null);

  const toast = useCallback((m) => { setToastMsg(m); setTimeout(() => setToastMsg(""), 2200); }, []);
  const refreshStatus = () => api("/api/status").then(setStatus).catch(() => {});

  useEffect(() => {
    api("/api/papers").then((r) => setPapers(r.papers));
    refreshStatus();
    const k = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); setDrawer((d) => !d); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);

  useEffect(() => {
    if (!stem) return;
    prefs.set("lastStem", stem);
    setPaper(null);
    setAttachments({ selection: null, images: [] });
    api(`/api/papers/${encodeURIComponent(stem)}`).then(setPaper).catch(() => { setStem(null); prefs.set("lastStem", null); });
  }, [stem]);

  useEffect(() => { document.documentElement.style.setProperty("--side-w", sideW + "px"); prefs.set("sideW", sideW); }, [sideW]);

  const pick = (s) => { setStem(s); setDrawer(false); };

  const toChat = () => { setTab("claude"); setTimeout(() => chatRef.current?.focus(), 50); };
  const onSelectionAction = (kind, pop) => {
    const sel = { text: pop.text, page: pop.page };
    if (kind === "explain") { setTab("claude"); chatRef.current?.send(PROMPTS.explainSel, { selection: sel, images: [], label: "讲解" }); }
    else { setAttachments((a) => ({ ...a, selection: sel })); toChat(); }
  };
  const onCropAction = (kind, crop) => {
    if (kind === "explain") { setTab("claude"); chatRef.current?.send(PROMPTS.explainImg, { images: [crop], selection: null, label: "讲解" }); }
    else { setAttachments((a) => ({ ...a, images: [...a.images, crop] })); toChat(); }
  };
  const goPage = (n) => viewRef.current?.goTo(n);

  const startResize = (e) => {
    e.preventDefault();
    const move = (ev) => setSideW(Math.min(760, Math.max(320, window.innerWidth - ev.clientX)));
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const claudeOk = status?.claude?.ok;

  return html`<div class=${"shell" + (stem ? "" : " no-side")}>
    <nav class="rail">
      <div class="logo" title="PhD Studio" />
      <button class=${"rail-btn" + (drawer ? " on" : "")} title="论文库 (⌘K)" onClick=${() => setDrawer(!drawer)}><${Icon} name="library" /></button>
      ${stem && html`
        <button class=${"rail-btn" + (tab === "claude" ? " on" : "")} title="Claude 伴读" onClick=${() => setTab("claude")}><${Icon} name="chat" /></button>
        <button class=${"rail-btn" + (tab === "note" ? " on" : "")} title="笔记" onClick=${() => setTab("note")}><${Icon} name="note" /></button>`}
      <div class="spacer" />
      <button class="rail-btn" title="设置" onClick=${() => { refreshStatus(); setSettingsOpen(true); }}>
        <${Icon} name="settings" />${status && html`<span class=${"dot " + (claudeOk ? "ok" : "bad")} />`}
      </button>
    </nav>

    <div class="center">
      <${Library} papers=${papers} current=${stem} open=${drawer} onPick=${pick} onClose=${() => setDrawer(false)} />
      ${stem
        ? html`<${Reader} key=${stem} stem=${stem} paper=${paper} pageRef=${pageRef} viewRef=${viewRef} toast=${toast}
            onSelectionAction=${onSelectionAction} onCropAction=${onCropAction} />`
        : html`<${Home} papers=${papers} onPick=${pick} />`}
    </div>

    ${stem && html`<div class="resizer" onMouseDown=${startResize} />
    <aside class="side">
      <div style=${{ display: tab === "claude" ? "contents" : "none" }}>
        <${ChatPanel} stem=${stem} paper=${paper} getPage=${() => pageRef.current} attachments=${attachments} setAttachments=${setAttachments}
          registerSend=${(api_) => (chatRef.current = api_)} onPage=${goPage} claudeOk=${claudeOk}
          onNoteChanged=${() => setNoteVersion((n) => n + 1)} />
      </div>
      ${tab === "note" && html`<${NotePanel} paper=${paper} version=${noteVersion}
        onAskUpdate=${() => { setTab("claude"); chatRef.current?.send(PROMPTS.note(paper.note_rel), { selection: null, images: [], label: "更新笔记" }); }} />`}
    </aside>`}

    ${settingsOpen && status && html`<${Settings} status=${status} onClose=${() => setSettingsOpen(false)} onSaved=${refreshStatus} />`}
    ${toastMsg && html`<div class="toast">${toastMsg}</div>`}
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));

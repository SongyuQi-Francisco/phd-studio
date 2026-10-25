import { render } from "preact";
import { useEffect, useMemo, useRef, useState, useCallback } from "preact/hooks";
import { html, Icon, api, prefs, copyText, composing } from "/lib.js";
import { t, LANGS, langSetting, systemLang, initI18n, onLangChange, setLangSetting } from "/i18n.js";
import { PdfView } from "/pdfview.js";
import { BilingualSettings } from "/bilingual.js";
import { ChatPanel, NotePanel, PROMPTS } from "/chat.js";
import { ProviderSettings, refreshProviders } from "/models.js";
import { ReadingPage, DoneDialog } from "/reading.js";
import { OverviewPage, TimelinePage, VenueSheet, IdeaCapture } from "/plan.js";
import { IdeasPage } from "/ideas.js";
import { TrackPage } from "/track.js";
import { DirectionPage, LinePanel, keepLines } from "/direction.js";
import { NoteSheet } from "/note.js";
import { ZoteroButton, ZoteroSettings } from "/zotero.js";
import { ReminderRow } from "/reminders.js";

// ---------------- library ----------------

function Library({ papers, current, open, onPick, onClose }) {
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState(prefs.get("libFilter", "queue"));
  const inputRef = useRef();
  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);
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

  // A closed drawer must not keep keyboard focus (or every shortcut would land in its hidden search box).
  return html`<div class=${"drawer" + (open ? " open" : "")} inert=${!open}>
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

// Exactly the line the server appends to the note (see app/excerpt.py line_for).
function excerptLine(text, page, comment) {
  const q = text.replace(/\s+/g, " ").trim().replace(/"/g, "'").replace(/([\\<>&$])/g, "\\$1");
  const c = (comment || "").replace(/\s+/g, " ").trim();
  return `- "${q}"` + (page ? ` [p.${page}]` : "") + (c ? ` — ${c}` : "");
}

function Reader({ stem, paper, onSelectionAction, onCropAction, pageRef, viewRef, jumpRef, toast, onDone, onAddToList, onExcerpted, onNoteChanged }) {
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
    // A page / section link from a note may have asked to open this paper at a given place.
    const jump = jumpRef.current?.stem === stem ? jumpRef.current : null;
    jumpRef.current = null;
    load("original", jump?.page).then(async () => {
      if (jump?.sec && (await viewRef.current.goToSection(jump.sec)) === false) toast(`PDF 里找不到 §${jump.sec}`);
    });
  }, [stem]);

  useEffect(() => { if (pages) prefs.set("page:" + stem, page); }, [page]);
  useEffect(() => { if (paper?.bilingual) setBi(paper.bilingual); }, [paper]);

  // poll bilingual job (also while it waits in the background queue)
  useEffect(() => {
    if (bi.state !== "running" && bi.state !== "queued") return;
    const t = setInterval(async () => {
      const s = await api(`/api/bilingual/${encodeURIComponent(stem)}`);
      setBi(s);
      if (s.state === "done") toast("双语版已生成");
      if (s.state === "error") toast("双语生成失败");
    }, bi.state === "queued" ? 5000 : 3000);
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
    const hide = () => setPop((p) => (p && !p.trans && !p.excerpt ? null : p));
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

  // ---- excerpt: append the selection with its page to a section of the note ----
  const openExcerpt = async () => {
    try {
      const r = await api(`/api/excerpt/${encodeURIComponent(stem)}`);
      const isNew = !r.sections.includes(r.default);
      const sections = isNew ? [...r.sections, r.default] : r.sections;
      const last = prefs.get("excerptSection", r.default);
      setPop((p) => p && { ...p, trans: null, excerpt: { sections, isNew, section: sections.includes(last) ? last : r.default, comment: "" } });
    } catch (e) { toast(e.message); }
  };
  const setEx = (patch) => setPop((p) => p && { ...p, excerpt: { ...p.excerpt, ...patch } });
  const saveExcerpt = async () => {
    const { text, page: pg, excerpt: x } = pop;
    if (x.saving) return;
    setEx({ saving: true });
    try {
      const r = await api(`/api/excerpt/${encodeURIComponent(stem)}`, { json: { text, page: pg, section: x.section, comment: x.comment.trim() || null } });
      prefs.set("excerptSection", x.section);
      setPop(null);
      window.getSelection()?.removeAllRanges();
      if (r.duplicate) toast("这段已经摘录过");
      else onExcerpted({ ...r, page: pg, stem });
    } catch (e) { setEx({ saving: false }); toast(e.message); }
  };

  // keyboard
  useEffect(() => {
    const k = (e) => {
      if (e.target?.closest?.("input, textarea, select, [contenteditable]")) return;
      if ((e.metaKey || e.ctrlKey) && e.key === "f") { e.preventDefault(); setFindOpen(true); }
      if (e.key === "Escape") { setPop(null); setCropMode(false); setShowOutline(false); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);

  const v = viewRef.current;
  const popStyle = pop && { left: Math.max(8, pop.x - (pop.trans ? 210 : 120)) + "px", top: Math.max(8, pop.y - 48) + "px" };
  const transStyle = (pop?.trans || pop?.excerpt) && { left: Math.max(8, pop.x - 210) + "px", top: Math.max(8, pop.y - 48) + "px", transform: "none" };

  return html`<div class="reader" ref=${wrapRef} onMouseDownCapture=${startDrag}>
    <div class="toolbar">
      <span class="tb-title" title=${paper?.title}>${paper?.external ? paper.title : stem}</span>
      ${paper && (paper.reading
        ? html`<span class="rchip">
            <span class=${"tier " + (paper.reading.status === "queued" ? paper.reading.tier : paper.reading.status === "reading" ? "R" : "D")}>
              ${{ queued: paper.reading.tier, reading: "在读", done: "读完", inbox: "收件箱", snoozed: "稍后", dismissed: "不读" }[paper.reading.status]}</span>
            ${paper.reading.status !== "done" && html`<button class="tb-btn" title="记为读完" onClick=${onDone}><${Icon} name="check" sm />读完</button>`}
          </span>`
        : html`<button class="tb-btn" title="加入阅读清单 (T2)" onClick=${onAddToList}><${Icon} name="plus" sm />清单</button>`)}
      ${paper && html`<${ZoteroButton} stem=${stem} toast=${toast} onNoteChanged=${onNoteChanged} />`}
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
        <button class=${variant === "dual" ? "on" : ""} onClick=${() => switchVariant("dual")}>${bi.state === "running" ? `生成中 ${elapsed(bi.started)}` : bi.state === "queued" ? (bi.paused ? "双语 · 暂停" : `排队第 ${bi.position}`) : "双语"}</button>
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
      <h3>${bi.state === "queued" ? "已在预生成队列" : "生成整篇双语 PDF"}</h3>
      <p>${bi.state === "queued" ? `排在第 ${bi.position} 位${bi.paused ? "（队列已暂停）" : ""}，轮到时在后台生成。也可以现在就开始。`
        : "用 pdf2zh 翻译全文，保留公式、图表和版式，左右对照。一般 3–8 分钟，后台进行，期间可以继续阅读。"}</p>
      <div class="row-end"><button class="btn ghost" onClick=${() => setConfirmBi(false)}>取消</button><button class="btn primary" onClick=${startBi}>${bi.state === "queued" ? "现在就生成" : "开始生成"}</button></div>
    </div>`}

    ${pop && pop.kind === "sel" && html`<div class="pop" style=${transStyle || popStyle}>
      <div class="pop-actions">
        <button class="primary" onClick=${doTranslate}><${Icon} name="translate" sm />翻译</button>
        <button onClick=${() => { onSelectionAction("explain", pop); setPop(null); }}><${Icon} name="sparkle" sm />讲解</button>
        <button onClick=${() => { onSelectionAction("ask", pop); setPop(null); }}><${Icon} name="chat" sm />问 Claude</button>
        <button class=${pop.excerpt ? "on" : ""} disabled=${!paper?.note_rel} title=${paper?.note_rel ? "摘录到笔记（带页码）" : "先建笔记"}
          onClick=${() => (pop.excerpt ? setPop({ ...pop, excerpt: null }) : openExcerpt())}><${Icon} name="quote" sm />摘录</button>
      </div>
      ${pop.excerpt && html`<div class="excerpt">
        <label class="ex-row"><span>小节</span>
          <select value=${pop.excerpt.section} onChange=${(e) => setEx({ section: e.target.value })}>
            ${pop.excerpt.sections.map((sec) => html`<option value=${sec}>${sec}${pop.excerpt.isNew && sec === "Excerpts" ? "（新建）" : ""}</option>`)}
          </select></label>
        <input class="ex-comment" placeholder="批注（可选）· Enter 摘录" value=${pop.excerpt.comment}
          ref=${(el) => { if (el && !el.dataset.focused) { el.dataset.focused = "1"; el.focus(); } }}
          onInput=${(e) => setEx({ comment: e.target.value })}
          onKeyDown=${(e) => { if (e.key === "Enter" && !composing(e)) { e.preventDefault(); saveExcerpt(); } if (e.key === "Escape") setPop(null); }} />
        <div class="ex-preview" title="将追加到笔记的这一行">${excerptLine(pop.text, pop.page, pop.excerpt.comment)}</div>
        <div class="row-end"><button class="btn ghost" onClick=${() => setPop(null)}>取消</button>
          <button class="btn primary" disabled=${pop.excerpt.saving} onClick=${saveExcerpt}>${pop.excerpt.saving ? "写入中…" : "摘录"}</button></div>
      </div>`}
      ${pop.trans && html`<div class="trans">
        ${pop.trans.loading ? html`<div class="typing"><i /><i /><i /></div>` : html`<div>${pop.trans.text}</div>
          <div class="meta"><span>${pop.trans.engine}</span><button onClick=${async () => toast((await copyText(pop.trans.text)) ? "已复制" : "复制失败，请手动选中")}>复制</button></div>`}
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
  // Saved first, so the server's messages and Markdown views switch together with the page.
  const setUiLanguage = async (v) => { await save({ ui_language: v }); setLangSetting(v); };
  const c = status.claude;
  const langName = (l) => t("common.lang." + l);
  return html`<div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <div class="modal settings">
      <h2>${t("settings.title")}</h2>
      <div class="field"><div><label>${t("settings.ui_language")}</label><small>${t("settings.ui_language_hint")}</small></div>
        <select value=${langSetting()} onChange=${(e) => setUiLanguage(e.target.value)}>
          <option value="system">${t("settings.lang_system", { lang: langName(systemLang()) })}</option>
          ${LANGS.map((l) => html`<option value=${l}>${langName(l)}</option>`)}
        </select></div>
      <div class="field"><div><label>${t("settings.answer_language")}</label><small>${t("settings.answer_language_hint")}</small></div>
        <select value=${s.answer_language} onChange=${(e) => save({ answer_language: e.target.value })}>
          <option value="">${t("settings.answer_profile", { lang: status.answer_language })}</option>
          <option value="ui">${t("settings.answer_ui")}</option>
          ${LANGS.map((l) => html`<option value=${l}>${langName(l)}</option>`)}
        </select></div>
      <div class="field"><div><label>Claude</label>
        <small>${c.ok ? t("settings.claude_connected", { method: c.method }) : t("settings.claude_signed_out", { hint: c.hint })}</small></div><span class=${"dot " + (c.ok ? "ok" : "bad")} /></div>
      <div class="field"><div><label>${t("settings.model")}</label><small>${t("settings.model_hint")}</small></div>
        <select value=${s.claude_model} onChange=${(e) => save({ claude_model: e.target.value })}>
          <option value="">${t("common.default")}</option><option value="opus">Opus</option><option value="sonnet">Sonnet</option><option value="haiku">${t("settings.model_haiku")}</option>
        </select></div>
      <${ProviderSettings} />
      <${ZoteroSettings} settings=${s} save=${save} />
      <div class="field"><div><label>${t("settings.engine")}</label><small>${t("settings.engine_hint")}</small></div>
        <select value=${s.translate_engine} onChange=${(e) => save({ translate_engine: e.target.value })}>
          <option value="deepseek">DeepSeek</option><option value="glm">${t("models.glm")}</option><option value="google">${t("settings.engine_google")}</option>
        </select></div>
      <${BilingualSettings} settings=${s} save=${save} />
      <${ReminderRow} />
      <div class="row-end" style="margin-top:12px"><button class="btn" onClick=${onClose}>${t("common.done")}</button></div>
    </div>
  </div>`;
}

// ---------------- app ----------------

const STUDIO_CHIPS = [
  { label: "今天该做什么", prompt: "看看我的时间线、阅读清单和想法，告诉我今天最该做的 3 件事（按截止紧迫度排）。" },
  { label: "最近截稿", prompt: "未来 60 天我关注的会议有哪些截稿？按时间列出，标出和我投稿目标相关的。" },
  { label: "加里程碑", prompt: "帮我加一个里程碑：", fill: true, write: true },
  { label: "记想法", prompt: "记一个想法：", fill: true, write: true },
];

function App() {
  const [papers, setPapers] = useState([]);
  const [stem, setStem] = useState(prefs.get("lastStem", null));
  const [view, setView] = useState("overview");
  const [tlTab, setTlTab] = useState("watched");
  const [doneOpen, setDoneOpen] = useState(false);
  const [dataVersion, setDataVersion] = useState(0);
  const [quitState, setQuitState] = useState(""); // "" | "confirm" | "done"
  const [paper, setPaper] = useState(null);
  const [drawer, setDrawer] = useState(false);
  const [tab, setTab] = useState("claude");
  const [assistant, setAssistant] = useState(prefs.get("assistant", true));
  const [venueKey, setVenueKey] = useState(null);
  const [notePath, setNotePath] = useState(null);
  const [ideaOpen, setIdeaOpen] = useState(false);
  const [status, setStatus] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [attachments, setAttachments] = useState({ selection: null, images: [] });
  const [studioAtt, setStudioAtt] = useState({ selection: null, images: [] });
  const [noteVersion, setNoteVersion] = useState(0);
  const [toastMsg, setToastMsg] = useState(null); // {text, action?: {label, run}}
  const [sideW, setSideW] = useState(prefs.get("sideW", 420));
  const pageRef = useRef(1);
  const viewRef = useRef(null);
  const jumpRef = useRef(null); // {stem, page?|sec?}: where to land when that paper opens
  const toastTimer = useRef(null);
  const chatRef = useRef(null);
  const studioRef = useRef(null);
  // Research-line chats: the ones mounted this session (kept so a running answer survives switching) and the one shown.
  const [lineChats, setLineChats] = useState([]);
  const [sideLine, setSideLine] = useState(null); // chat key; null = 工作台助手
  const lineRefs = useRef({});
  const lineUsed = useRef({}); // chat key -> when it was last opened
  const kicked = useRef(new Set());

  const toast = useCallback((text, { action } = {}) => {
    clearTimeout(toastTimer.current);
    setToastMsg({ text, action });
    toastTimer.current = setTimeout(() => setToastMsg(null), action ? 6000 : 2400);
  }, []);
  const refreshStatus = () => api("/api/status").then(setStatus).catch(() => {});
  const bump = () => setDataVersion((n) => n + 1);
  // A language switch re-renders everything from here down; data the server worded in the old language is fetched again.
  const [, setLang] = useState(null);
  useEffect(() => onLangChange((l) => { setLang(l); refreshStatus(); refreshProviders(); bump(); }), []);

  useEffect(() => {
    api("/api/papers").then((r) => setPapers(r.papers));
    refreshStatus();
    const k = (e) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      if (e.key === "k") { e.preventDefault(); setDrawer((d) => !d); }
      if (e.key === "j") { e.preventDefault(); setAssistant((a) => !a); }
      if (e.key === "i") { e.preventDefault(); setIdeaOpen(true); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);
  useEffect(() => prefs.set("assistant", assistant), [assistant]);
  // A clicked reminder notification (app/desktop.py → app/notify.py) opens its event.
  useEffect(() => {
    window.studioOpen = (d) => { setNotePath(null); setSettingsOpen(false); go(d.view === "plan" ? "plan" : "timeline"); if (d.venue) setVenueKey(d.venue); };
    if (window.studioOpenPending) { window.studioOpen(window.studioOpenPending); window.studioOpenPending = null; }
    return () => { delete window.studioOpen; };
  }, []);

  const stemRef = useRef(stem);
  stemRef.current = stem;
  // Only the paper still open gets its data applied (a reply can arrive after you switched papers).
  const loadPaper = (s) => api(`/api/papers/${encodeURIComponent(s)}`)
    .then((p) => { if (stemRef.current === s) setPaper(p); })
    .catch(() => { if (stemRef.current !== s) return; setStem(null); prefs.set("lastStem", null); setView("list"); });

  useEffect(() => {
    if (!stem || view !== "reader") return;
    prefs.set("lastStem", stem);
    setPaper(null);
    setAttachments({ selection: null, images: [] });
    // Opening a paper moves its reading-list entry to "reading".
    api("/api/reading/opened", { json: { key: stem } }).finally(() => loadPaper(stem));
  }, [stem, view]);

  useEffect(() => { document.documentElement.style.setProperty("--side-w", sideW + "px"); prefs.set("sideW", sideW); }, [sideW]);

  const pick = (s) => { document.activeElement?.blur?.(); setStem(s); setView("reader"); setDrawer(false); };
  const go = (v) => {
    setDrawer(false);
    if (v === "plan") { setTlTab("plan"); setView("timeline"); }
    else { if (v === "timeline") setTlTab("watched"); setView(v); }
    bump();
  };
  const addToList = async () => {
    try {
      await api("/api/reading", { json: paper.external ? (paper.arxiv ? { arxiv: paper.arxiv, tier: "T2" } : { ext: stem, tier: "T2" }) : { paper: stem, tier: "T2" } });
      toast("已加入阅读清单 · T2");
      loadPaper(stem);
    } catch (e) { toast(e.message); }
  };

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
  const goPage = (n) => {
    const v = viewRef.current;
    if (v?.doc && (n < 1 || n > v.doc.numPages)) toast(`这篇只有 ${v.doc.numPages} 页`);
    else v?.goTo(n);
  };
  const goSection = async (sec) => {
    const ok = await viewRef.current?.goToSection(sec);
    if (ok === false) toast(`PDF 里找不到 §${sec}`);
    else if (!ok) toast("PDF 还没打开，没法跳转");
  };
  // r.stem is the paper the excerpt went into; you may have switched papers before pressing 撤销.
  const onExcerpted = (r) => {
    const refresh = () => { if (stemRef.current === r.stem) { loadPaper(r.stem); setNoteVersion((n) => n + 1); } };
    refresh();
    const undo = async () => {
      try {
        const u = await api(`/api/excerpt/${encodeURIComponent(r.stem)}/undo`,
          { json: { line: r.line, inserted: r.inserted, section: r.section, created: r.created, mtime_ns: r.mtime_ns } });
        refresh();
        toast(u.ok ? "已撤销摘录" : "撤销失败：笔记里已找不到这一行");
      } catch (e) { toast(e.message); }
    };
    toast(`已摘录到 ${r.section}${r.page ? " · p." + r.page : ""}`, { action: { label: "撤销", run: undo } });
  };
  // Hand a task to the workbench assistant (opens its panel if needed).
  const ask = (prompt, label) => {
    setAssistant(true);
    setSideLine(null);
    const trySend = (n) => studioRef.current ? studioRef.current.send(prompt, { selection: null, images: [], label })
      : n > 0 && setTimeout(() => trySend(n - 1), 120);
    setTimeout(() => trySend(20), 60);
  };
  // Show a research line's own chat in the side panel; "discuss" starts it if it is new, "packet" drafts an advisor packet.
  const openLine = (l, action) => {
    const line = { key: l.chat_key, name: l.name };
    lineUsed.current[line.key] = Date.now();
    setLineChats((ls) => {
      if (ls.some((x) => x.key === line.key)) return ls;
      const kept = keepLines([...ls, line], line.key, lineUsed.current, (k) => !!lineRefs.current[k]?.busy);
      ls.filter((x) => !kept.includes(x)).forEach((x) => { delete lineRefs.current[x.key]; delete lineUsed.current[x.key]; });
      return kept;
    });
    setSideLine(line.key);
    setAssistant(true);
    const run = (n) => {
      const r = lineRefs.current[line.key];
      if (!r) return n > 0 && setTimeout(() => run(n - 1), 120);
      if (action === "packet") r.packet();
      else if (action === "discuss" && !l.chat?.turns && !kicked.current.has(line.key)) { kicked.current.add(line.key); r.kickoff(); }
      else r.focus();
    };
    setTimeout(() => run(20), 60);
  };
  // target: {page} or {sec} from a page / section link in a note
  const openPaper = (s, target) => {
    setNotePath(null);
    if (target && view === "reader" && stem === s) {
      if (target.page) goPage(target.page); else goSection(target.sec);
      return;
    }
    jumpRef.current = target ? { stem: s, ...target } : null;
    pick(s);
  };

  const startResize = (e) => {
    e.preventDefault();
    const move = (ev) => setSideW(Math.min(760, Math.max(320, window.innerWidth - ev.clientX)));
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const claudeOk = status?.claude?.ok;
  const reading = view === "reader" && stem;
  const studioSide = !reading && assistant;
  const nav = (v, icon, label) => html`<button class=${"rail-btn" + (view === v && !drawer ? " on" : "")} title=${label}
    onClick=${() => go(v)}><${Icon} name=${icon} /></button>`;

  let page;
  if (reading) page = html`<${Reader} key=${stem} stem=${stem} paper=${paper} pageRef=${pageRef} viewRef=${viewRef} jumpRef=${jumpRef} toast=${toast}
      onSelectionAction=${onSelectionAction} onCropAction=${onCropAction} onDone=${() => setDoneOpen(true)} onAddToList=${addToList}
      onExcerpted=${onExcerpted} onNoteChanged=${() => { loadPaper(stem); setNoteVersion((n) => n + 1); }} />`;
  else if (view === "list") page = html`<${ReadingPage} version=${dataVersion} toast=${toast} onOpen=${(i) => pick(i.key)} />`;
  else if (view === "timeline") page = html`<${TimelinePage} version=${dataVersion} tab=${tlTab} onOpenVenue=${setVenueKey} toast=${toast} onChanged=${bump} />`;
  else if (view === "ideas") page = html`<${IdeasPage} version=${dataVersion} lines=${status?.lines || []} radarPath=${status?.idea_radar}
      onAsk=${ask} onOpenNote=${setNotePath} onOpenPaper=${openPaper} toast=${toast} />`;
  else if (view === "track") page = html`<${TrackPage} version=${dataVersion} onAsk=${ask} toast=${toast} onOpenPaper=${openPaper}
      onOpenNote=${setNotePath} onChanged=${bump} />`;
  else if (view === "direction") page = html`<${DirectionPage} version=${dataVersion} onOpenNote=${setNotePath} onOpenVenue=${setVenueKey}
      onLine=${openLine} activeLine=${studioSide ? sideLine : null} />`;
  else page = html`<${OverviewPage} version=${dataVersion} onOpenVenue=${setVenueKey} go=${go} />`;

  return html`<div class=${"shell" + (reading || studioSide ? "" : " no-side")}>
    <nav class="rail">
      <img class="logo-img" src="/icon.svg" alt="PhD Studio" title="PhD Studio" />
      ${nav("overview", "home", "总览")}
      ${nav("list", "inbox", "阅读清单")}
      ${nav("timeline", "calendar", "时间线 · 会议与规划")}
      ${nav("ideas", "bulb", "想法 (⌘I 随手记)")}
      ${nav("direction", "compass", "方向 · 研究线与路线图")}
      ${nav("track", "radar", "追踪 · 同行动态与捕捉")}
      <button class=${"rail-btn" + (drawer ? " on" : "")} title="论文库 (⌘K)" onClick=${() => setDrawer(!drawer)}><${Icon} name="library" /></button>
      ${stem && !reading && html`<button class="rail-btn" title="回到正在读的论文" onClick=${() => setView("reader")}><${Icon} name="book" /></button>`}
      ${reading && html`<div class="rail-sep" />
        <button class=${"rail-btn" + (tab === "claude" ? " on" : "")} title="Claude 伴读" onClick=${() => setTab("claude")}><${Icon} name="chat" /></button>
        <button class=${"rail-btn" + (tab === "note" ? " on" : "")} title="笔记" onClick=${() => setTab("note")}><${Icon} name="note" /></button>`}
      <div class="spacer" />
      ${!reading && html`<button class=${"rail-btn" + (assistant ? " on" : "")} title="工作台助手 (⌘J)" onClick=${() => setAssistant(!assistant)}><${Icon} name="sparkle" /></button>`}
      <button class="rail-btn" title="关闭工作台" onClick=${() => setQuitState("confirm")}><${Icon} name="power" /></button>
      <button class="rail-btn" title="设置" onClick=${() => { refreshStatus(); setSettingsOpen(true); }}>
        <${Icon} name="settings" />${status && html`<span class=${"dot " + (claudeOk ? "ok" : "bad")} />`}
      </button>
    </nav>

    <div class="center">
      <${Library} papers=${papers} current=${stem} open=${drawer} onPick=${pick} onClose=${() => setDrawer(false)} />
      ${page}
    </div>

    ${reading && html`<div class="resizer" onMouseDown=${startResize} />
    <aside class="side">
      <div style=${{ display: tab === "claude" ? "contents" : "none" }}>
        <${ChatPanel} stem=${stem} paper=${paper} getPage=${() => pageRef.current} attachments=${attachments} setAttachments=${setAttachments}
          registerSend=${(api_) => (chatRef.current = api_)} onPage=${goPage} onSection=${goSection} claudeOk=${claudeOk}
          onNoteChanged=${() => setNoteVersion((n) => n + 1)} />
      </div>
      ${tab === "note" && html`<${NotePanel} paper=${paper} version=${noteVersion} onPage=${goPage} onSection=${goSection}
        onAskUpdate=${() => { setTab("claude"); chatRef.current?.send(PROMPTS.note(paper.note_rel), { selection: null, images: [], label: "更新笔记", write: true }); }}
        onAskIntake=${() => { setTab("claude"); chatRef.current?.send(PROMPTS.intake, { selection: null, images: [], label: "建笔记", write: true }); }} />`}
    </aside>`}
    ${studioSide && html`<div class="resizer" onMouseDown=${startResize} />
    <aside class="side">
      <div style=${{ display: sideLine ? "none" : "contents" }}>
        <${ChatPanel} stem="studio" paper=${null} attachments=${studioAtt} setAttachments=${setStudioAtt}
          registerSend=${(api_) => (studioRef.current = api_)} claudeOk=${claudeOk} title="工作台助手"
          chips=${STUDIO_CHIPS} emptyHint="跟我说要做的事：加里程碑、设投稿目标、记想法、把论文推进清单、查会议截稿……"
          onStudioChanged=${(area) => { bump(); if (area === "venues") toast("助手提交了会议核对结果，等你确认", { action: { label: "去看看", run: () => go("timeline") } }); }} />
      </div>
      ${lineChats.map((l) => html`<div key=${l.key} style=${{ display: sideLine === l.key ? "contents" : "none" }}>
        <${LinePanel} line=${l} claudeOk=${claudeOk} registerSend=${(api_) => (lineRefs.current[l.key] = api_)} toast=${toast}
          onBack=${() => setSideLine(null)} onStudioChanged=${bump} onOpenNote=${setNotePath} />
      </div>`)}
    </aside>`}

    ${notePath && html`<${NoteSheet} path=${notePath} onClose=${() => setNotePath(null)} onOpenPaper=${openPaper}
      actions=${[{ label: "和助手讨论", run: (n) => { setNotePath(null); ask(`我们来讨论这篇笔记：${n.path}。先读它，然后告诉我要点、和我当前课题的关系、以及值得跟进的下一步。`, `讨论 ${n.name}`); } }]} />`}
    ${venueKey && html`<${VenueSheet} vkey=${venueKey} onClose=${() => setVenueKey(null)} toast=${toast} onChanged=${bump} onAsk=${ask} />`}
    ${ideaOpen && html`<${IdeaCapture} lines=${status?.lines || []} context=${reading ? { paper: stem, page: pageRef.current } : null}
      onClose=${() => setIdeaOpen(false)} onSaved=${() => { setIdeaOpen(false); toast("想法已记下"); bump(); }} />`}
    ${doneOpen && paper?.reading && html`<${DoneDialog} item=${{ ...paper.reading, key: stem, title: paper.title }}
      onClose=${() => setDoneOpen(false)} onSaved=${() => { setDoneOpen(false); toast("已记为读完"); loadPaper(stem); }} />`}
    ${settingsOpen && status && html`<${Settings} status=${status} onClose=${() => setSettingsOpen(false)} onSaved=${refreshStatus} />`}
    ${toastMsg && html`<div class="toast"><span>${toastMsg.text}</span>
      ${toastMsg.action && html`<button onClick=${() => { const a = toastMsg.action; setToastMsg(null); a.run(); }}>${toastMsg.action.label}</button>`}</div>`}
    ${quitState === "confirm" && html`<div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && setQuitState("")}>
      <div class="modal">
        <h2>关闭工作台？</h2>
        <p class="muted-p">正在进行的 Claude 回答和双语生成会停止。对话、阅读清单都已保存，下次从桌面图标打开即可。</p>
        <div class="row-end"><button class="btn ghost" onClick=${() => setQuitState("")}>取消</button>
          <button class="btn danger" onClick=${async () => { let r = {}; try { r = await api("/api/shutdown", { method: "POST" }); } catch {} if (!r.window) { setQuitState("done"); setTimeout(() => window.close(), 600); } }}>关闭</button></div>
      </div></div>`}
    ${quitState === "done" && html`<div class="quit-screen"><div><img class="logo-img big" src="/icon.svg" alt="" /><h2>工作台已关闭</h2><p>可以关掉这个窗口了。下次双击桌面上的 PhD Studio 图标启动。</p></div></div>`}
  </div>`;
}

initI18n().then(() => render(html`<${App} />`, document.getElementById("app")));

// Ideas: capture inbox -> keep / drop -> promote into a proper idea note (drafted by the assistant).
// Free tags (inline, filterable) and merging several ideas into one, with the originals kept and restorable.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, api, composing } from "/lib.js";
import { C, Funnel, Sparkline, perWeek } from "/charts.js";
import { Thumb, Glyph, loadThumbs, Skeleton, Empty, transition } from "/ui.js";

const STATUS = { new: "新", kept: "保留", promoted: "已整理", dropped: "丢弃", merged: "已合并" };
const ST_COLOR = { new: "var(--accent)", kept: "var(--series-3)", promoted: "var(--good)", dropped: "var(--text-3)", merged: "var(--text-3)" };
const SOURCE = { self: "自己记的", assistant: "助手", claude: "其他会话" };
const key = (t) => t.toLowerCase();
const hasTag = (i, t) => (i.tags || []).some((x) => key(x) === t);
const clip = (t, n = 36) => (t.length > n ? t.slice(0, n) + "…" : t);
const mergeable = (i) => i.status !== "merged" && i.status !== "promoted";
// The line most of the ideas are on (what the backend picks when none is given).
const commonLine = (items) => { const c = {}; items.forEach((i) => i.line && (c[i.line] = (c[i.line] || 0) + 1)); return Object.keys(c).sort((a, b) => c[b] - c[a])[0] || ""; };

export const promotePrompt = (i) => `把想法 ${i.id} 整理成正式 idea 笔记。原文：「${i.text}」${i.line ? `（研究线：${i.line}）` : ""}${i.paper ? `（来自论文 ${i.paper}${i.page ? " p." + i.page : ""}）` : ""}。
要求：用 Idea Note Template 的结构，按 AGENTS.md 的 Idea Maintenance 写清 问题 / 为什么是现在 / 技术核心 / 第一个实验 / Kill criteria / 下一步要找的证据；先查 vault 里已有的相关 idea 和论文笔记，避免重复，并链接相关论文。
先用三四句话告诉我笔记文件名和要点，再写入 idea 笔记文件夹；写完用 idea_update 把这条想法标为 promoted，note 填笔记路径。`;

function Tags({ i, active, onToggle, onChange, readOnly }) {
  const [adding, setAdding] = useState(false);
  const [val, setVal] = useState("");
  const ref = useRef();
  useEffect(() => { if (adding) ref.current?.focus(); }, [adding]);
  const commit = () => { const v = val.trim(); setVal(""); if (v) onChange(i, { add: [v] }); };
  return html`<div class="idea-tags">
    ${(i.tags || []).map((t) => html`<span class=${"tg" + (active.includes(key(t)) ? " on" : "")}>
      <button class="tg-t" title="按这个标签筛选" onClick=${() => onToggle(key(t))}>${t}</button>
      ${!readOnly && html`<button class="tg-x" title="移除标签" aria-label=${"移除标签 " + t} onClick=${() => onChange(i, { remove: [t] })}>×</button>`}</span>`)}
    ${!readOnly && (adding
      ? html`<input class="tg-in" list="idea-tags" placeholder="标签，回车添加" value=${val} ref=${ref}
          onInput=${(e) => setVal(e.target.value)}
          onKeyDown=${(e) => { if (composing(e)) return; if (e.key === "Enter") commit(); if (e.key === "Escape") { setVal(""); setAdding(false); } }}
          onBlur=${() => { commit(); setAdding(false); }} />`
      : html`<button class="tg add" title="添加标签" onClick=${() => setAdding(true)}>${(i.tags || []).length ? "+" : "+ 标签"}</button>`)}
  </div>`;
}

function Provenance({ i, byId, onRestore, onUnmerge, onOpenPaper }) {
  const [open, setOpen] = useState(false);
  const [ask, setAsk] = useState(false);
  const edited = !i.merge_untouched; // changed since it was merged
  const srcs = i.merged_sources || [];
  if (!srcs.length) return null;
  const papers = new Set(srcs.map((s) => s.paper).filter(Boolean)).size;
  return html`<div class="idea-from">
    <button class="from-head" onClick=${() => setOpen(!open)} aria-expanded=${open}>
      <${Icon} name=${open ? "down" : "right"} sm />合并自 <b>${srcs.length}</b> 条${papers ? html`<span class="muted small"> · ${papers} 篇论文</span>` : ""}</button>
    ${open && html`<div class="from-list">
      ${srcs.map((s) => html`<div class="from-row">
        <div class="from-text">${s.text}</div>
        <div class="from-meta">
          <span class="muted small">${(s.created_at || "").replace("T", " ").slice(5, 16)} · ${SOURCE[s.source] || s.source || "自己记的"}${s.line ? " · " + s.line : ""}</span>
          ${s.paper && html`<button class="chip" onClick=${() => onOpenPaper(s.paper)}>${s.paper}${s.page ? " p." + s.page : ""}</button>`}
          <span class="grow" />
          ${byId[s.id]?.status === "merged" && html`<button class="abtn txt" title="把这一条放回去" onClick=${() => onRestore(s.id)}>恢复</button>`}
        </div></div>`)}
      <div class="from-foot">${ask
        ? html`<span class="small">原想法全部恢复；这条合并结果${edited ? "改过，会放进「丢弃」" : "会被删除"}。</span><span class="grow" />
          <button class="abtn txt" onClick=${() => setAsk(false)}>取消</button>
          <button class="abtn txt warn" onClick=${() => { setAsk(false); onUnmerge(i); }}>确认拆开</button>`
        : html`<span class="muted small">原想法没有删除，在「已合并」里也看得到</span><span class="grow" />
          <button class="abtn txt" onClick=${() => setAsk(true)}>全部拆开</button>`}</div>
    </div>`}
  </div>`;
}

function IdeaCard({ i, lines, update, onTag, tagSel, onToggleTag, onPromote, onOpenNote, onOpenPaper, ready, n, byId,
  selecting, picked, onPick, onRestore, onUnmerge, flash }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(i.text);
  const save = () => { setEditing(false); if (text.trim() && text !== i.text) update(i, { text: text.trim() }); };
  const merged = i.status === "merged";
  const into = merged ? byId[i.merged_into] : null;
  const pickable = selecting && mergeable(i);
  const own = !merged && !selecting;
  return html`<div data-id=${i.id} class=${"idea" + (i.status === "dropped" || merged ? " dropped" : "") + (pickable ? " pickable" : "") + (picked ? " picked" : "") + (flash ? " flash" : "")}
    style=${{ "--st": ST_COLOR[i.status], animationDelay: Math.min(n, 10) * 30 + "ms" }}
    onClick=${pickable ? (e) => { if (!e.target.closest("button, a, input, select, textarea")) onPick(i.id); } : undefined}>
    <div class="idea-top">
      ${selecting && html`<button class=${"pickbox" + (picked ? " on" : "")} disabled=${!pickable} aria-label=${picked ? "取消选择" : "选择"}
        title=${pickable ? "选中后合并" : i.status === "promoted" ? "已整理成笔记，不能合并" : "已合并"}
        onClick=${(e) => { e.stopPropagation(); onPick(i.id); }}>${picked && html`<${Icon} name="check" sm />`}</button>`}
      <span class=${"tag st-" + i.status}>${STATUS[i.status] || i.status}</span>
      <span class="muted small">${(i.created_at || "").replace("T", " ").slice(5, 16)}${i.source === "assistant" ? " · 助手" : i.source === "claude" ? " · 其他会话" : ""}</span>
      <span class="grow" />
      ${!merged && html`<select value=${i.line || ""} disabled=${selecting} onChange=${(e) => update(i, { line: e.target.value || null })}>
        <option value="">不分组</option>${lines.map((l) => html`<option value=${l}>${l}</option>`)}</select>`}
    </div>
    ${editing && !selecting
      ? html`<textarea class="ta" rows="3" value=${text} onInput=${(e) => setText(e.target.value)} onBlur=${save}
          onKeyDown=${(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save(); if (e.key === "Escape") { setText(i.text); setEditing(false); } }} autofocus />`
      : html`<div class="idea-text" title=${selecting || merged ? "" : "点击编辑"} onClick=${() => !selecting && !merged && setEditing(true)}>${i.text}</div>`}
    ${merged && html`<div class="idea-into">${into ? html`合并入：<b>${clip(into.text)}</b>` : "合并入的那条想法已不在了"}</div>`}
    ${i.paper && html`<button class="idea-paper" onClick=${(e) => { e.stopPropagation(); onOpenPaper(i.paper); }}>
      <${Thumb} k=${i.paper} title=${i.paper} size="sm" ready=${ready} /><span><b>${i.paper}</b>${i.page ? html`<span class="muted small">第 ${i.page} 页</span>` : ""}</span></button>`}
    <${Tags} i=${i} active=${tagSel} onToggle=${onToggleTag} onChange=${onTag} readOnly=${merged || selecting} />
    <${Provenance} i=${i} byId=${byId} onRestore=${onRestore} onUnmerge=${onUnmerge} onOpenPaper=${onOpenPaper} />
    <div class="idea-meta">
      ${i.note && html`<button class="chip" onClick=${() => onOpenNote(i.note)}><${Icon} name="note" sm />${i.note.split("/").pop().replace(/\.md$/, "")}</button>`}
      <span class="grow" />
      ${merged && html`<button class="abtn txt" onClick=${() => onRestore(i.id)}>恢复</button>`}
      ${own && i.status !== "promoted" && html`<button class="chip" onClick=${() => onPromote(i)}><${Icon} name="sparkle" sm />整理成笔记</button>`}
      ${own && i.status === "new" && html`<button class="abtn txt" onClick=${() => update(i, { status: "kept" })}>保留</button>`}
      ${own && i.status !== "dropped" && i.status !== "promoted" && html`<button class="abtn" title="丢弃（保留记录）" onClick=${() => update(i, { status: "dropped" })}><${Icon} name="x" sm /></button>`}
      ${own && i.status === "dropped" && html`<button class="abtn txt" onClick=${() => update(i, { status: "kept" })}>恢复</button>`}
    </div>
  </div>`;
}

// Pick which text survives (or write a new one), then merge. The originals stay, hidden, and can be restored.
function MergeDialog({ items, lines, onClose, onMerge }) {
  const [text, setText] = useState(items[0].text);
  const [line, setLine] = useState(commonLine(items));
  const [busy, setBusy] = useState(false);
  const ref = useRef();
  useEffect(() => ref.current?.focus(), []);
  const tags = [...new Map(items.flatMap((i) => i.tags || []).map((t) => [key(t), t])).values()];
  const go = async () => { if (!text.trim() || busy) return; setBusy(true); try { await onMerge(text.trim(), line); } finally { setBusy(false); } }; // line "" = 不分组, not "pick one for me"
  return html`<div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <div class="modal wide">
      <h2>合并 ${items.length} 条想法</h2>
      <div class="label" style="margin-top:0">留下哪一条的文字（点一下填进下面，也可以自己改）</div>
      <div class="mg-opts">
        ${items.map((i) => html`<button class=${"mg-opt" + (text === i.text ? " on" : "")} onClick=${() => setText(i.text)}>${i.text}</button>`)}
        <button class=${"mg-opt cat" + (text === items.map((i) => i.text).join("\n") ? " on" : "")} onClick=${() => setText(items.map((i) => i.text).join("\n"))}>全部接在一起</button>
      </div>
      <textarea ref=${ref} class="ta" rows="4" value=${text} onInput=${(e) => setText(e.target.value)}
        onKeyDown=${(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) go(); if (e.key === "Escape") onClose(); }} />
      <div class="mg-sum">
        ${lines.length > 0 && html`<label>研究线 <select value=${line} onChange=${(e) => setLine(e.target.value)}>
          <option value="">不分组</option>${lines.map((l) => html`<option value=${l}>${l}</option>`)}</select></label>`}
        ${tags.length > 0 && html`<span class="muted small">标签合并：${tags.map((t) => "#" + t).join(" ")}</span>`}
      </div>
      <p class="muted small" style="margin:10px 0 0">每条原想法的原文、论文和页码会保存在合并后的想法里；原想法放进「已合并」，随时可以恢复。</p>
      <div class="row-end" style="margin-top:14px"><button class="btn ghost" onClick=${onClose}>取消</button>
        <button class="btn primary" disabled=${!text.trim() || busy} onClick=${go}>合并 ⌘↵</button></div>
    </div></div>`;
}

const tagCounts = (items) => {
  const m = new Map();
  items.forEach((i) => (i.tags || []).forEach((t) => { const e = m.get(key(t)) || { k: key(t), t, n: 0 }; e.n++; m.set(key(t), e); }));
  return [...m.values()].sort((a, b) => b.n - a.n || a.t.localeCompare(b.t));
};
const TAG_ROW = 14;

export function IdeasPage({ version, lines, radarPath, onAsk, onOpenNote, onOpenPaper, toast }) {
  const [ideas, setIdeas] = useState(null);
  const [filter, setFilter] = useState("active");
  const [line, setLine] = useState("");
  const [tagSel, setTagSel] = useState([]); // lower-cased; an idea must carry all of them
  const [moreTags, setMoreTags] = useState(false);
  const [draft, setDraft] = useState("");
  const [ready, setReady] = useState(0);
  const [selecting, setSelecting] = useState(false);
  const [picked, setPicked] = useState([]); // ids in the order they were picked
  const [merging, setMerging] = useState(false);
  const [flash, setFlash] = useState(null);
  const load = () => api("/api/ideas?merged=1").then((r) => {
    setIdeas(r.ideas);
    loadThumbs(r.ideas.map((i) => i.paper).filter(Boolean)).then(() => setReady((n) => n + 1));
  });
  useEffect(() => { load(); }, [version]);
  useEffect(() => { if (flash) document.querySelector(`.idea[data-id="${flash}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" }); }, [flash]);
  const call = async (fn, done) => { try { await fn(); if (done) toast(done); } catch (e) { toast(e.message); } load(); };
  const post = (path, json = {}) => api(path, { json });
  const update = (i, patch) => call(() => api(`/api/ideas/${i.id}`, { method: "PATCH", json: patch }));
  const onTag = (i, ops) => call(() => post(`/api/ideas/${i.id}/tags`, ops));
  const restore = (id) => call(() => post(`/api/ideas/${id}/restore`), "已恢复");
  const unmerge = async (i) => {
    try {
      const r = await post(`/api/ideas/${i.id}/unmerge`);
      toast(r.survivor === "dropped" ? "已拆开，原想法都恢复了；改过的合并结果放进了「丢弃」" : "已拆开，原想法都恢复了");
    } catch (e) { toast(e.message); }
    load();
  };
  const toggleTag = (t) => setTagSel((sel) => (sel.includes(t) ? sel.filter((x) => x !== t) : [...sel, t]));
  const pick = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const stopSelecting = () => { setSelecting(false); setPicked([]); setMerging(false); };
  const add = async () => {
    if (!draft.trim()) return;
    await api("/api/ideas", { json: { text: draft, line: line || null } });
    setDraft(""); toast("想法已记下"); load();
  };
  const merge = async (text, mline) => {
    try {
      const m = await post("/api/ideas/merge", { ids: picked, text, line: mline });
      stopSelecting();
      setFilter((f) => (f === "active" || f === "all" ? f : "active"));
      await load();
      setFlash(m.id); setTimeout(() => setFlash(null), 2400);
      toast(`已合并 ${m.merged_from.length} 条想法`, { action: { label: "撤销", run: () => unmerge(m) } });
    } catch (e) { toast(e.message); }
  };
  const all = ideas || [];
  const byId = useMemo(() => Object.fromEntries(all.map((i) => [i.id, i])), [ideas]);
  const counts = useMemo(() => Object.fromEntries(Object.keys(STATUS).map((k) => [k, all.filter((i) => i.status === k).length])), [ideas]);
  if (!ideas) return html`<${Skeleton} tiles=${0} rows=${4} chart=${false} />`;
  const live = all.filter((i) => i.status !== "merged"); // merged-away ideas are only a view away, never counted twice
  const scoped = all.filter((i) => (filter === "active" ? ["new", "kept"].includes(i.status) : filter === "all" ? i.status !== "merged" : i.status === filter)
    && (!line || i.line === line));
  const shown = tagSel.length ? scoped.filter((i) => tagSel.every((t) => hasTag(i, t))) : scoped;
  const facet = tagCounts(scoped); // counts follow the status and line filters, so a chip's number is what you get
  const chips = [...facet, ...tagSel.filter((k) => !facet.some((f) => f.k === k)).map((k) => ({ k, t: k, n: 0 }))];
  const chipsShown = moreTags ? chips : chips.filter((c, n) => n < TAG_ROW || tagSel.includes(c.k));
  const pickedItems = picked.map((id) => byId[id]).filter((i) => i && mergeable(i));
  const weekly = perWeek(live.map((i) => i.created_at));
  const byLine = lines.map((l) => [l, live.filter((i) => i.line === l && i.status !== "dropped").length]);
  const capture = html`<div class="capture-row">
    <input placeholder="一句话记下想法，回车保存（任何页面也可以按 ⌘I）" value=${draft} onInput=${(e) => setDraft(e.target.value)}
      onKeyDown=${(e) => e.key === "Enter" && !composing(e) && add()} />
    <button class="btn primary" disabled=${!draft.trim()} onClick=${add}>记下</button>
  </div>`;
  return html`<div class="page"><div class="page-inner">
    <div class="page-head"><div><h1>想法</h1>
      <div class="sub">${live.length ? `共 ${live.length} 条 · 近 12 周记下 ${weekly.counts.reduce((a, b) => a + b, 0)} 条` : "先记下来，之后再整理"}</div></div>
      ${radarPath && html`<button class="chip" onClick=${() => onOpenNote(radarPath)}><${Icon} name="note" sm />Idea Radar</button>`}</div>
    ${capture}
    ${live.length === 0 && counts.merged === 0 ? html`<div class="card"><${Empty} icon="bulb" title="还没有想法"
        hint="读论文时按 ⌘I 随手记一句，会自动附上论文和页码；也可以对右侧助手说「记一个想法：…」。攒起来的想法会在这里经过 保留 → 整理成笔记 的漏斗。" />
        <div class="es-steps">${[["⌘I", "随手记"], ["保留", "值得再想"], ["整理", "助手按 Idea 模板起草笔记"]].map(([k, l], n) => html`<div><b>${k}</b><span>${l}</span>${n < 2 && html`<${Icon} name="right" sm />`}</div>`)}</div>
      </div>` : html`
      <section class="card idea-insight">
        <div><div class="ri-head"><b>漏斗</b><span class="muted small">丢弃 ${counts.dropped} 条（保留记录）${counts.merged ? ` · 合并掉 ${counts.merged} 条` : ""}</span></div>
          <${Funnel} steps=${[
            { key: "all", label: "记下", value: live.length, color: "var(--ord-1)" },
            { key: "active", label: "留下", value: counts.kept + counts.promoted, color: "var(--ord-2)" },
            { key: "promoted", label: "成笔记", value: counts.promoted, color: "var(--ord-3)" }]} /></div>
        <div class="ii-side">
          <div class="ri-head"><b>每周记下</b><span class="muted small">近 12 周</span></div>
          <${Sparkline} values=${weekly.counts} labels=${weekly.labels.map((l) => l + " 那周")} unit=" 条" color=${C.blue} height=${44} />
          ${byLine.length > 0 && html`<div class="ii-lines">${byLine.map(([l, n]) => html`<button class=${"chip" + (line === l ? " on" : "")} onClick=${() => setLine(line === l ? "" : l)}>${l}<b>${n}</b></button>`)}</div>`}
        </div>
      </section>
      <div class="filters">
        <div class="seg">
          ${[["active", `待处理 ${counts.new + counts.kept}`], ["promoted", `已整理 ${counts.promoted}`], ["dropped", `丢弃 ${counts.dropped}`],
            ...(counts.merged > 0 || filter === "merged" ? [["merged", `已合并 ${counts.merged}`]] : []), ["all", "全部"]]
            .map(([k, l]) => html`<button class=${filter === k ? "on" : ""} onClick=${() => transition(() => { setFilter(k); if (k === "merged") stopSelecting(); })}>${l}</button>`)}
        </div>
        ${lines.length > 0 && html`<div class="seg"><button class=${!line ? "on" : ""} onClick=${() => setLine("")}>全部研究线</button>
          ${lines.map((l) => html`<button class=${line === l ? "on" : ""} onClick=${() => setLine(l)}>${l}</button>`)}</div>`}
        <span class="grow" />
        ${filter !== "merged" && live.length >= 2 && html`<button class=${"chip" + (selecting ? " on" : "")} title="选几条想法合并成一条"
          onClick=${() => (selecting ? stopSelecting() : setSelecting(true))}><${Glyph} name="layers" sm />${selecting ? "退出合并" : "合并…"}</button>`}
      </div>
      ${chips.length > 0 && html`<div class="tagbar">
        <span class="tb-label">标签</span>
        ${chipsShown.map((c) => html`<button class=${"chip" + (tagSel.includes(c.k) ? " on" : "")} onClick=${() => toggleTag(c.k)}>#${c.t}<b>${c.n}</b></button>`)}
        ${chips.length > TAG_ROW && html`<button class="chip more" onClick=${() => setMoreTags(!moreTags)}>${moreTags ? "收起" : `更多 ${chips.length - chipsShown.length}`}</button>`}
        ${tagSel.length > 0 && html`<button class="abtn txt" onClick=${() => setTagSel([])}>清除</button>`}
      </div>`}
      <datalist id="idea-tags">${tagCounts(live).map((c) => html`<option value=${c.t} />`)}</datalist>
      ${shown.length === 0 && html`<div class="card"><${Empty} compact icon=${filter === "active" && !tagSel.length ? "check" : "bulb"}
        title=${tagSel.length ? "没有同时带这些标签的想法" : filter === "active" ? "没有待处理的想法" : "这里还是空的"}
        hint=${tagSel.length ? "去掉一个标签，或者换个状态看看" : filter === "active" ? "都处理完了。读论文时按 ⌘I 记下一个新的。" : "换个筛选看看"}>
        ${tagSel.length > 0 && html`<button class="btn" style="margin-top:10px" onClick=${() => setTagSel([])}>清除标签筛选</button>`}</${Empty}></div>`}
      <div class="idea-grid">${shown.map((i, n) => html`<${IdeaCard} key=${i.id} i=${i} n=${n} lines=${lines} update=${update} ready=${ready} byId=${byId}
        onTag=${onTag} tagSel=${tagSel} onToggleTag=${toggleTag} selecting=${selecting} picked=${picked.includes(i.id)} onPick=${pick}
        onRestore=${restore} onUnmerge=${unmerge} flash=${flash === i.id}
        onPromote=${(x) => onAsk(promotePrompt(x), "整理想法")} onOpenNote=${onOpenNote} onOpenPaper=${onOpenPaper} />`)}</div>`}
    ${selecting && html`<div class="merge-bar">
      <span>已选 <b>${pickedItems.length}</b> 条${pickedItems.length < 2 ? html`<span class="muted small"> · 至少选两条</span>` : ""}</span>
      <button class="btn ghost" onClick=${stopSelecting}>取消</button>
      <button class="btn primary" disabled=${pickedItems.length < 2} onClick=${() => setMerging(true)}>合并…</button></div>`}
    ${merging && pickedItems.length >= 2 && html`<${MergeDialog} items=${pickedItems} lines=${lines} onClose=${() => setMerging(false)} onMerge=${merge} />`}
  </div></div>`;
}

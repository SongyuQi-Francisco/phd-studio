// Ideas: capture inbox -> keep / drop -> promote into a proper idea note (drafted by the assistant).
// Free tags (inline, filterable) and merging several ideas into one, with the originals kept and restorable.
// i18n: done
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, api, composing } from "/lib.js";
import { t, tx } from "/i18n.js";
import { C, Funnel, Sparkline, perWeek, weekLabel } from "/charts.js";
import { Thumb, Glyph, loadThumbs, Skeleton, Empty, transition } from "/ui.js";

const STATUSES = ["new", "kept", "promoted", "dropped", "merged"]; // i18n-keys: ideas.status.{new,kept,promoted,dropped,merged}
const ST_COLOR = { new: "var(--accent)", kept: "var(--series-3)", promoted: "var(--good)", dropped: "var(--text-3)", merged: "var(--text-3)" };
const SOURCES = ["self", "assistant", "claude"]; // i18n-keys: ideas.source.{self,assistant,claude}
const statusName = (s) => (STATUSES.includes(s) ? t("ideas.status." + s) : s);
const sourceName = (s) => (SOURCES.includes(s) ? t("ideas.source." + s) : s);
const key = (tag) => tag.toLowerCase();
const hasTag = (i, tag) => (i.tags || []).some((x) => key(x) === tag);
const clip = (text, n = 36) => (text.length > n ? text.slice(0, n) + "…" : text);
const mergeable = (i) => i.status !== "merged" && i.status !== "promoted";
// The line most of the ideas are on (what the backend picks when none is given).
const commonLine = (items) => { const c = {}; items.forEach((i) => i.line && (c[i.line] = (c[i.line] || 0) + 1)); return Object.keys(c).sort((a, b) => c[b] - c[a])[0] || ""; };

// What the "promote" button sends to the assistant, in the interface language.
export const promotePrompt = (i) => t("ideas.promote.prompt", {
  id: i.id, text: i.text,
  line: i.line ? t("ideas.promote.line", { line: i.line }) : "",
  paper: i.paper ? t("ideas.promote.paper", { paper: i.paper + (i.page ? " p." + i.page : "") }) : "",
});

function Tags({ i, active, onToggle, onChange, readOnly }) {
  const [adding, setAdding] = useState(false);
  const [val, setVal] = useState("");
  const ref = useRef();
  useEffect(() => { if (adding) ref.current?.focus(); }, [adding]);
  const commit = () => { const v = val.trim(); setVal(""); if (v) onChange(i, { add: [v] }); };
  return html`<div class="idea-tags">
    ${(i.tags || []).map((tag) => html`<span class=${"tg" + (active.includes(key(tag)) ? " on" : "")}>
      <button class="tg-t" title=${t("ideas.tag.filter")} onClick=${() => onToggle(key(tag))}>${tag}</button>
      ${!readOnly && html`<button class="tg-x" title=${t("ideas.tag.remove")} aria-label=${t("ideas.tag.remove_named", { tag })} onClick=${() => onChange(i, { remove: [tag] })}>×</button>`}</span>`)}
    ${!readOnly && (adding
      ? html`<input class="tg-in" list="idea-tags" placeholder=${t("ideas.tag.placeholder")} value=${val} ref=${ref}
          onInput=${(e) => setVal(e.target.value)}
          onKeyDown=${(e) => { if (composing(e)) return; if (e.key === "Enter") commit(); if (e.key === "Escape") { setVal(""); setAdding(false); } }}
          onBlur=${() => { commit(); setAdding(false); }} />`
      : html`<button class="tg add" title=${t("ideas.tag.add")} onClick=${() => setAdding(true)}>${(i.tags || []).length ? "+" : "+ " + t("ideas.tags")}</button>`)}
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
      <${Icon} name=${open ? "down" : "right"} sm />${tx("ideas.from.head", { n: srcs.length, count: html`<b>${srcs.length}</b>` })}${papers ? html`<span class="muted small"> · ${t("common.unit.papers", { n: papers })}</span>` : ""}</button>
    ${open && html`<div class="from-list">
      ${srcs.map((s) => html`<div class="from-row">
        <div class="from-text">${s.text}</div>
        <div class="from-meta">
          <span class="muted small">${(s.created_at || "").replace("T", " ").slice(5, 16)} · ${sourceName(s.source || "self")}${s.line ? " · " + s.line : ""}</span>
          ${s.paper && html`<button class="chip" onClick=${() => onOpenPaper(s.paper)}>${s.paper}${s.page ? " p." + s.page : ""}</button>`}
          <span class="grow" />
          ${byId[s.id]?.status === "merged" && html`<button class="abtn txt" title=${t("ideas.from.restore_title")} onClick=${() => onRestore(s.id)}>${t("ideas.restore")}</button>`}
        </div></div>`)}
      <div class="from-foot">${ask
        ? html`<span class="small">${t(edited ? "ideas.from.unmerge_edited" : "ideas.from.unmerge_plain")}</span><span class="grow" />
          <button class="abtn txt" onClick=${() => setAsk(false)}>${t("common.cancel")}</button>
          <button class="abtn txt warn" onClick=${() => { setAsk(false); onUnmerge(i); }}>${t("ideas.from.unmerge_confirm")}</button>`
        : html`<span class="muted small">${t("ideas.from.kept_note")}</span><span class="grow" />
          <button class="abtn txt" onClick=${() => setAsk(true)}>${t("ideas.from.unmerge_all")}</button>`}</div>
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
      ${selecting && html`<button class=${"pickbox" + (picked ? " on" : "")} disabled=${!pickable} aria-label=${picked ? t("ideas.pick.deselect") : t("ideas.pick.select")}
        title=${pickable ? t("ideas.pick.to_merge") : i.status === "promoted" ? t("ideas.pick.promoted") : t("ideas.pick.merged")}
        onClick=${(e) => { e.stopPropagation(); onPick(i.id); }}>${picked && html`<${Icon} name="check" sm />`}</button>`}
      <span class=${"tag st-" + i.status}>${statusName(i.status)}</span>
      <span class="muted small">${(i.created_at || "").replace("T", " ").slice(5, 16)}${i.source === "assistant" || i.source === "claude" ? " · " + sourceName(i.source) : ""}</span>
      <span class="grow" />
      ${!merged && html`<select value=${i.line || ""} disabled=${selecting} onChange=${(e) => update(i, { line: e.target.value || null })}>
        <option value="">${t("ideas.ungrouped")}</option>${lines.map((l) => html`<option value=${l}>${l}</option>`)}</select>`}
    </div>
    ${editing && !selecting
      ? html`<textarea class="ta" rows="3" value=${text} onInput=${(e) => setText(e.target.value)} onBlur=${save}
          onKeyDown=${(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save(); if (e.key === "Escape") { setText(i.text); setEditing(false); } }} autofocus />`
      : html`<div class="idea-text" title=${selecting || merged ? "" : t("ideas.click_edit")} onClick=${() => !selecting && !merged && setEditing(true)}>${i.text}</div>`}
    ${merged && html`<div class="idea-into">${into ? tx("ideas.into", { text: html`<b>${clip(into.text)}</b>` }) : t("ideas.into_gone")}</div>`}
    ${i.paper && html`<button class="idea-paper" onClick=${(e) => { e.stopPropagation(); onOpenPaper(i.paper); }}>
      <${Thumb} k=${i.paper} title=${i.paper} size="sm" ready=${ready} /><span><b>${i.paper}</b>${i.page ? html`<span class="muted small">${t("ideas.page", { n: i.page })}</span>` : ""}</span></button>`}
    <${Tags} i=${i} active=${tagSel} onToggle=${onToggleTag} onChange=${onTag} readOnly=${merged || selecting} />
    <${Provenance} i=${i} byId=${byId} onRestore=${onRestore} onUnmerge=${onUnmerge} onOpenPaper=${onOpenPaper} />
    <div class="idea-meta">
      ${i.note && html`<button class="chip" onClick=${() => onOpenNote(i.note)}><${Icon} name="note" sm />${i.note.split("/").pop().replace(/\.md$/, "")}</button>`}
      <span class="grow" />
      ${merged && html`<button class="abtn txt" onClick=${() => onRestore(i.id)}>${t("ideas.restore")}</button>`}
      ${own && i.status !== "promoted" && html`<button class="chip" onClick=${() => onPromote(i)}><${Icon} name="sparkle" sm />${t("ideas.promote.button")}</button>`}
      ${own && i.status === "new" && html`<button class="abtn txt" onClick=${() => update(i, { status: "kept" })}>${t("ideas.keep")}</button>`}
      ${own && i.status !== "dropped" && i.status !== "promoted" && html`<button class="abtn" title=${t("ideas.drop_title")} onClick=${() => update(i, { status: "dropped" })}><${Icon} name="x" sm /></button>`}
      ${own && i.status === "dropped" && html`<button class="abtn txt" onClick=${() => update(i, { status: "kept" })}>${t("ideas.restore")}</button>`}
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
  const tags = [...new Map(items.flatMap((i) => i.tags || []).map((tag) => [key(tag), tag])).values()];
  const go = async () => { if (!text.trim() || busy) return; setBusy(true); try { await onMerge(text.trim(), line); } finally { setBusy(false); } }; // line "" = ungrouped, not "pick one for me"
  return html`<div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <div class="modal wide">
      <h2>${t("ideas.merge.title", { n: items.length })}</h2>
      <div class="label" style="margin-top:0">${t("ideas.merge.which")}</div>
      <div class="mg-opts">
        ${items.map((i) => html`<button class=${"mg-opt" + (text === i.text ? " on" : "")} onClick=${() => setText(i.text)}>${i.text}</button>`)}
        <button class=${"mg-opt cat" + (text === items.map((i) => i.text).join("\n") ? " on" : "")} onClick=${() => setText(items.map((i) => i.text).join("\n"))}>${t("ideas.merge.join_all")}</button>
      </div>
      <textarea ref=${ref} class="ta" rows="4" value=${text} onInput=${(e) => setText(e.target.value)}
        onKeyDown=${(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) go(); if (e.key === "Escape") onClose(); }} />
      <div class="mg-sum">
        ${lines.length > 0 && html`<label>${t("common.term.research_line")} <select value=${line} onChange=${(e) => setLine(e.target.value)}>
          <option value="">${t("ideas.ungrouped")}</option>${lines.map((l) => html`<option value=${l}>${l}</option>`)}</select></label>`}
        ${tags.length > 0 && html`<span class="muted small">${t("ideas.merge.tags")}${tags.map((tag) => "#" + tag).join(" ")}</span>`}
      </div>
      <p class="muted small" style="margin:10px 0 0">${t("ideas.merge.note")}</p>
      <div class="row-end" style="margin-top:14px"><button class="btn ghost" onClick=${onClose}>${t("common.cancel")}</button>
        <button class="btn primary" disabled=${!text.trim() || busy} onClick=${go}>${t("ideas.merge.button")} ⌘↵</button></div>
    </div></div>`;
}

const tagCounts = (items) => {
  const m = new Map();
  items.forEach((i) => (i.tags || []).forEach((tag) => { const e = m.get(key(tag)) || { k: key(tag), name: tag, n: 0 }; e.n++; m.set(key(tag), e); }));
  return [...m.values()].sort((a, b) => b.n - a.n || a.name.localeCompare(b.name));
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
  const restore = (id) => call(() => post(`/api/ideas/${id}/restore`), t("ideas.restored"));
  const unmerge = async (i) => {
    try {
      const r = await post(`/api/ideas/${i.id}/unmerge`);
      toast(t(r.survivor === "dropped" ? "ideas.unmerged_dropped" : "ideas.unmerged"));
    } catch (e) { toast(e.message); }
    load();
  };
  const toggleTag = (tag) => setTagSel((sel) => (sel.includes(tag) ? sel.filter((x) => x !== tag) : [...sel, tag]));
  const pick = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const stopSelecting = () => { setSelecting(false); setPicked([]); setMerging(false); };
  const add = async () => {
    if (!draft.trim()) return;
    await api("/api/ideas", { json: { text: draft, line: line || null } });
    setDraft(""); toast(t("ideas.added")); load();
  };
  const merge = async (text, mline) => {
    try {
      const m = await post("/api/ideas/merge", { ids: picked, text, line: mline });
      stopSelecting();
      setFilter((f) => (f === "active" || f === "all" ? f : "active"));
      await load();
      setFlash(m.id); setTimeout(() => setFlash(null), 2400);
      toast(t("ideas.merged_done", { n: m.merged_from.length }), { action: { label: t("common.undo"), run: () => unmerge(m) } });
    } catch (e) { toast(e.message); }
  };
  const all = ideas || [];
  const byId = useMemo(() => Object.fromEntries(all.map((i) => [i.id, i])), [ideas]);
  const counts = useMemo(() => Object.fromEntries(STATUSES.map((k) => [k, all.filter((i) => i.status === k).length])), [ideas]);
  if (!ideas) return html`<${Skeleton} tiles=${0} rows=${4} chart=${false} />`;
  const live = all.filter((i) => i.status !== "merged"); // merged-away ideas are only a view away, never counted twice
  const scoped = all.filter((i) => (filter === "active" ? ["new", "kept"].includes(i.status) : filter === "all" ? i.status !== "merged" : i.status === filter)
    && (!line || i.line === line));
  const shown = tagSel.length ? scoped.filter((i) => tagSel.every((tag) => hasTag(i, tag))) : scoped;
  const facet = tagCounts(scoped); // counts follow the status and line filters, so a chip's number is what you get
  const chips = [...facet, ...tagSel.filter((k) => !facet.some((f) => f.k === k)).map((k) => ({ k, name: k, n: 0 }))];
  const chipsShown = moreTags ? chips : chips.filter((c, n) => n < TAG_ROW || tagSel.includes(c.k));
  const pickedItems = picked.map((id) => byId[id]).filter((i) => i && mergeable(i));
  const weekly = perWeek(live.map((i) => i.created_at));
  const byLine = lines.map((l) => [l, live.filter((i) => i.line === l && i.status !== "dropped").length]);
  const capture = html`<div class="capture-row">
    <input placeholder=${t("ideas.capture_hint")} value=${draft} onInput=${(e) => setDraft(e.target.value)}
      onKeyDown=${(e) => e.key === "Enter" && !composing(e) && add()} />
    <button class="btn primary" disabled=${!draft.trim()} onClick=${add}>${t("ideas.capture_button")}</button>
  </div>`;
  return html`<div class="page"><div class="page-inner">
    <div class="page-head"><div><h1>${t("common.nav.ideas")}</h1>
      <div class="sub">${live.length ? t("ideas.sub", { total: live.length, n: weekly.counts.reduce((a, b) => a + b, 0) }) : t("ideas.sub_empty")}</div></div>
      ${radarPath && html`<button class="chip" onClick=${() => onOpenNote(radarPath)}><${Icon} name="note" sm />Idea Radar</button>`}</div>
    ${capture}
    ${live.length === 0 && counts.merged === 0 ? html`<div class="card"><${Empty} icon="bulb" title=${t("ideas.empty.title")} hint=${t("ideas.empty.hint")} />
        <div class="es-steps">${[["⌘I", t("ideas.empty.step_capture")], [t("ideas.keep"), t("ideas.empty.step_keep")], [t("ideas.empty.promote"), t("ideas.empty.step_promote")]].map(([k, l], n) => html`<div><b>${k}</b><span>${l}</span>${n < 2 && html`<${Icon} name="right" sm />`}</div>`)}</div>
      </div>` : html`
      <section class="card idea-insight">
        <div><div class="ri-head"><b>${t("ideas.funnel.title")}</b><span class="muted small">${t("ideas.funnel.dropped", { n: counts.dropped })}${counts.merged ? " · " + t("ideas.funnel.merged", { n: counts.merged }) : ""}</span></div>
          <${Funnel} steps=${[
            { key: "all", label: t("ideas.funnel.captured"), value: live.length, color: "var(--ord-1)" },
            { key: "active", label: t("ideas.funnel.kept"), value: counts.kept + counts.promoted, color: "var(--ord-2)" },
            { key: "promoted", label: t("ideas.funnel.promoted"), value: counts.promoted, color: "var(--ord-3)" }]} /></div>
        <div class="ii-side">
          <div class="ri-head"><b>${t("ideas.weekly")}</b><span class="muted small">${t("ideas.weeks")}</span></div>
          <${Sparkline} values=${weekly.counts} labels=${weekly.labels.map(weekLabel)} unit=${(n) => t("ideas.unit", { n })} color=${C.blue} height=${44} />
          ${byLine.length > 0 && html`<div class="ii-lines">${byLine.map(([l, n]) => html`<button class=${"chip" + (line === l ? " on" : "")} onClick=${() => setLine(line === l ? "" : l)}>${l}<b>${n}</b></button>`)}</div>`}
        </div>
      </section>
      <div class="filters">
        <div class="seg">
          ${[["active", t("ideas.filter.active", { n: counts.new + counts.kept })], ["promoted", t("ideas.filter.promoted", { n: counts.promoted })], ["dropped", t("ideas.filter.dropped", { n: counts.dropped })],
            ...(counts.merged > 0 || filter === "merged" ? [["merged", t("ideas.filter.merged", { n: counts.merged })]] : []), ["all", t("common.all")]]
            .map(([k, l]) => html`<button class=${filter === k ? "on" : ""} onClick=${() => transition(() => { setFilter(k); if (k === "merged") stopSelecting(); })}>${l}</button>`)}
        </div>
        ${lines.length > 0 && html`<div class="seg"><button class=${!line ? "on" : ""} onClick=${() => setLine("")}>${t("ideas.all_lines")}</button>
          ${lines.map((l) => html`<button class=${line === l ? "on" : ""} onClick=${() => setLine(l)}>${l}</button>`)}</div>`}
        <span class="grow" />
        ${filter !== "merged" && live.length >= 2 && html`<button class=${"chip" + (selecting ? " on" : "")} title=${t("ideas.merge.start_title")}
          onClick=${() => (selecting ? stopSelecting() : setSelecting(true))}><${Glyph} name="layers" sm />${selecting ? t("ideas.merge.exit") : t("ideas.merge.start")}</button>`}
      </div>
      ${chips.length > 0 && html`<div class="tagbar">
        <span class="tb-label">${t("ideas.tags")}</span>
        ${chipsShown.map((c) => html`<button class=${"chip" + (tagSel.includes(c.k) ? " on" : "")} onClick=${() => toggleTag(c.k)}>#${c.name}<b>${c.n}</b></button>`)}
        ${chips.length > TAG_ROW && html`<button class="chip more" onClick=${() => setMoreTags(!moreTags)}>${moreTags ? t("ideas.tag.fewer") : t("ideas.tag.more", { n: chips.length - chipsShown.length })}</button>`}
        ${tagSel.length > 0 && html`<button class="abtn txt" onClick=${() => setTagSel([])}>${t("ideas.tag.clear")}</button>`}
      </div>`}
      <datalist id="idea-tags">${tagCounts(live).map((c) => html`<option value=${c.name} />`)}</datalist>
      ${shown.length === 0 && html`<div class="card"><${Empty} compact icon=${filter === "active" && !tagSel.length ? "check" : "bulb"}
        title=${tagSel.length ? t("ideas.none.tags_title") : filter === "active" ? t("ideas.none.active_title") : t("ideas.none.title")}
        hint=${tagSel.length ? t("ideas.none.tags_hint") : filter === "active" ? t("ideas.none.active_hint") : t("ideas.none.hint")}>
        ${tagSel.length > 0 && html`<button class="btn" style="margin-top:10px" onClick=${() => setTagSel([])}>${t("ideas.none.clear_tags")}</button>`}</${Empty}></div>`}
      <div class="idea-grid">${shown.map((i, n) => html`<${IdeaCard} key=${i.id} i=${i} n=${n} lines=${lines} update=${update} ready=${ready} byId=${byId}
        onTag=${onTag} tagSel=${tagSel} onToggleTag=${toggleTag} selecting=${selecting} picked=${picked.includes(i.id)} onPick=${pick}
        onRestore=${restore} onUnmerge=${unmerge} flash=${flash === i.id}
        onPromote=${(x) => onAsk(promotePrompt(x), t("ideas.promote.label"))} onOpenNote=${onOpenNote} onOpenPaper=${onOpenPaper} />`)}</div>`}
    ${selecting && html`<div class="merge-bar">
      <span>${tx("ideas.bar.picked", { n: pickedItems.length, count: html`<b>${pickedItems.length}</b>` })}${pickedItems.length < 2 ? html`<span class="muted small"> · ${t("ideas.bar.at_least")}</span>` : ""}</span>
      <button class="btn ghost" onClick=${stopSelecting}>${t("common.cancel")}</button>
      <button class="btn primary" disabled=${pickedItems.length < 2} onClick=${() => setMerging(true)}>${t("ideas.merge.start")}</button></div>`}
    ${merging && pickedItems.length >= 2 && html`<${MergeDialog} items=${pickedItems} lines=${lines} onClose=${() => setMerging(false)} onMerge=${merge} />`}
  </div></div>`;
}

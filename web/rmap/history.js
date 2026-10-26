// Time travel on one research map (#46): the time scrubber (weeks W1…Wn rebuilt from the change log,
// with a play button) and the change view (two dates: added nodes glow aqua, dropped / refuted / deleted
// ones red, status changes highlighted, the list of changes below). Both draw the core map view read-only
// with the map of then; the "history" layer (registered while they are open) marks the changes.
// Data: GET /api/rmap/projects/{slug}/history (frames), .../history/diff, .../history/packet.
// i18n: done
// i18n-keys: rmap.status.{todo,active,verified,blocked,refuted,dropped}
// i18n-keys: rmap.type.{question,hypothesis,subline,experiment,result,decision,kill,open}
// i18n-keys: rmap_history.mark.{added,removed,dropped,status,retitled}
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, renderMarkdown } from "/lib.js";
import { t } from "/i18n.js";
import { rmap } from "/rmap/registry.js";
import { label } from "/rmap/strings.js";
import { MapView } from "/rmap/map.js";
import { describe, titles, when } from "/rmap/describe.js";
import { compare, mapAt } from "/rmap/timeline.js";

const PLAY_MS = 900;
const STATUS_LEGEND = ["active", "verified", "blocked", "todo", "dropped"];
const LIST_MAX = 60;
const noop = () => {};
const iso = (d) => d.toLocaleDateString("sv"); // i18n-ignore (YYYY-MM-DD, a machine format)
const daysAgo = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return iso(d); };
const short = (day) => (day || "").slice(5); // "10-05": mono tick labels

const PLAY = html`<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.5v9l7-4.5z" /></svg>`;
const PAUSE = html`<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 3.5h2.5v9H4.5zM9 3.5h2.5v9H9z" /></svg>`;

// ---- the "history" layer: marks on the nodes of a replayed or compared map ----

const markOf = (n, ctx) => ctx.history?.marks?.[n.id];

function MarkPill({ m, box }) {
  if (m.mark === "status") {
    const w = 58;
    return html`<g class="mp-hx-pill status" transform=${`translate(${box.x + box.w - w - 6} ${box.y - 7})`}>
      <title>${t("rmap.status." + m.from)} → ${t("rmap.status." + m.to)}</title>
      <rect width=${w} height="14" rx="7" />
      <circle class=${"st-" + m.from} cx="10" cy="7" r="3.2" /><path d="M17 7h12M25.5 4l3.5 3-3.5 3" />
      <circle class=${"st-" + m.to} cx="37" cy="7" r="3.2" /><text x="44" y="10.2">${m.to === "verified" ? "✓" : ""}</text>
    </g>`;
  }
  const text = m.mark === "dropped" ? t("rmap.status." + m.to) : t("rmap_history.mark." + m.mark);
  const w = Math.max(30, 12 + text.length * (/[　-鿿]/.test(text) ? 10 : 6)); // i18n-ignore (CJK width)
  return html`<g class=${"mp-hx-pill " + m.mark} transform=${`translate(${box.x + box.w - w - 6} ${box.y - 7})`}>
    ${m.mark === "retitled" && html`<title>${m.title}</title>`}
    <rect width=${w} height="14" rx="7" /><text x=${w / 2} y="10.2" text-anchor="middle">${text}</text>
  </g>`;
}

function useHistoryLayer() {
  useEffect(() => rmap.layer({
    id: "history", label: "rmap_history.layer", order: 95, default: true, accent: "var(--mp-verified)",
    nodeClass: (n, ctx) => { const m = markOf(n, ctx); return m ? "mp-hx-" + m.mark + (n.gone ? " mp-hx-gone" : "") : n.gone ? "mp-hx-gone" : ""; },
    node: (n, box, ctx) => { const m = markOf(n, ctx); return m ? html`<${MarkPill} key=${n.id} m=${m} box=${box} />` : null; },
    svg: (ctx) => html`${(ctx.layout?.edges || []).filter((e) => e.gone).map((e) => html`<path key=${e.id} class="mp-hx-ghost" d=${e.d} />`)}`,
    legend: () => html`<span class="mp-hx-lg"><i class="added" />${t("rmap_history.mark.added")}<i class="status" />${t("rmap_history.mark.status")}<i class="removed" />${t("rmap_history.legend.red")}</span>`,
  }), []);
}

// ---- the read-only map ----

// The core map view with another map: selection works, writes are refused (a toast says why), the context
// menu, renaming and the add-child keys are off.
function FrozenMap({ ctx, map, marks, selected, select }) {
  const view = useMemo(() => ({ focus: null, setFocus: noop, collapsed: new Set(), setCollapsed: noop, fold: noop, pending: null, setPending: noop, setSide: noop }), []);
  const fctx = useMemo(() => ({
    ...ctx, map, selected, select, view, history: { marks },
    api: { ...ctx.api, apply: () => Promise.reject(new Error(t("rmap_history.readonly"))) },
  }), [ctx, map, marks, selected]);
  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  const keys = (e) => { if (!e.key.startsWith("Arrow") && !["Escape", "Tab", " "].includes(e.key)) e.stopPropagation(); };
  const sel = selected && map.nodes.find((n) => n.id === selected);
  return html`<div class="mp-body-grid side-right mp-hx-body">
    <div class="mp-canvas mp-hx-ro" onContextMenuCapture=${stop} onDblClickCapture=${stop} onKeyDownCapture=${keys}>
      <${MapView} ctx=${fctx} />
      <div class="mp-legend">${STATUS_LEGEND.map((s) => html`<span class=${"st-" + s} key=${s}><i />${t("rmap.status." + s)}</span>`)}
        ${(ctx.layers.history ?? true) && rmap.get("layer", "history")?.legend?.(fctx)}</div>
    </div>
    <${Card} node=${sel} ctx=${fctx} mark=${sel && marks[sel.id]} onClose=${() => select(null)}
      onOpen=${ctx.closeView && sel && ctx.map.nodes.some((n) => n.id === sel.id) ? () => { ctx.closeView(); ctx.select(sel.id); } : null} />
  </div>`;
}

// The selected node as it was; onOpen (when it is still on the map): close the history and select it there.
function Card({ node, ctx, mark, onClose, onOpen }) {
  if (!node) return html`<div class="mp-detail empty"><span class="muted">${t("rmap_history.card.empty")}</span></div>`;
  const st = node.status || "todo";
  const ev = (ctx.map.attachments?.evidence || []).filter((a) => a.node === node.id);
  return html`<div class="mp-detail mp-hx-card">
    <div class="mp-dh"><span class="mp-type-pick">${t("rmap.type." + node.type)}</span>
      <span class=${"mp-pill st-" + st}><i />${t("rmap.status." + st)}</span>
      <button class="mp-close" title=${t("rmap_history.close")} onClick=${onClose}>×</button></div>
    <h3>${node.title}</h3>
    ${mark && html`<p class=${"mp-hx-why " + mark.mark}>${mark.mark === "status" || mark.mark === "dropped"
      ? t("rmap_history.card.status", { from: t("rmap.status." + mark.from), to: t("rmap.status." + mark.to) })
      : mark.mark === "retitled" ? t("rmap_history.card.retitled", { title: mark.title }) : t("rmap_history.mark." + mark.mark)}</p>`}
    ${node.body && html`<div class="mp-desc md" dangerouslySetInnerHTML=${{ __html: renderMarkdown(node.body) }} />`}
    ${ev.length > 0 && html`<div class="mp-sec"><h4>${t("rmap_history.card.evidence")} <small>${ev.length}</small></h4>
      <div class="mp-chips">${ev.map((a) => html`<span class="mp-chip" key=${a.id}><span class="mp-chip-main">${a.title || a.target}</span></span>`)}</div></div>`}
    <p class="mp-quiet mp-hx-ro-note">${t("rmap_history.card.readonly")}</p>
    ${onOpen && html`<button class="btn ghost sm" onClick=${onOpen}>${t("rmap_history.card.open")}</button>`}
  </div>`;
}

// ---- the list of changes ----

function Chip({ n, kind, onPick }) {
  return html`<button class=${"mp-hx-chip " + kind} onClick=${onPick}><b class="mp-num">${n}</b>${t("rmap_history.sum." + kind)}</button>`; // i18n-keys: rmap_history.sum.{added,status,dropped,removed,retitled,attached}
}

export function ChangeList({ changes, events, total, names, onPick, busy }) {
  const [all, setAll] = useState(false);
  const c = changes;
  const att = Object.entries(c.attachments || {});
  const attN = att.reduce((s, [, x]) => s + x.added.length + x.removed.length, 0);
  const empty = !c.added.length && !c.status.length && !c.dropped.length && !c.removed.length && !c.retitled.length && !attN && !(events || []).length;
  const node = (r, extra) => html`<li key=${r.id + (extra || "")}><button class="mp-hx-n" onClick=${() => onPick(r.id)}>
    <i class=${"mp-dot st-" + r.status} /><span class="mp-hx-ty">${t("rmap.type." + r.type)}</span><span class="mp-hx-ti">${r.title}</span>
    ${r.from !== undefined && r.to && html`<span class="mp-hx-ft"><i class=${"mp-dot st-" + r.from} />${t("rmap.status." + r.from)} → <i class=${"mp-dot st-" + r.to} />${t("rmap.status." + r.to)}</span>`}
    ${r.from !== undefined && !r.to && html`<span class="mp-hx-ft">${t("rmap_history.was", { title: r.from })}</span>`}
  </button></li>`;
  const group = (key, rows) => rows.length > 0 && html`<div class=${"mp-hx-grp " + key}><h4>${t("rmap_history.list." + key)} <small>${rows.length}</small></h4><ul>${rows.map((r) => node(r))}</ul></div>`; // i18n-keys: rmap_history.list.{added,status,dropped,removed,retitled}
  const evs = events || [];
  return html`<div class="mp-hx-list">
    <div class="mp-hx-sum">
      ${c.added.length > 0 && html`<${Chip} n=${c.added.length} kind="added" onPick=${() => onPick(c.added[0].id)} />`}
      ${c.status.length > 0 && html`<${Chip} n=${c.status.length} kind="status" onPick=${() => onPick(c.status[0].id)} />`}
      ${c.dropped.length > 0 && html`<${Chip} n=${c.dropped.length} kind="dropped" onPick=${() => onPick(c.dropped[0].id)} />`}
      ${c.removed.length > 0 && html`<${Chip} n=${c.removed.length} kind="removed" onPick=${() => onPick(c.removed[0].id)} />`}
      ${c.retitled.length > 0 && html`<${Chip} n=${c.retitled.length} kind="retitled" onPick=${() => onPick(c.retitled[0].id)} />`}
      ${attN > 0 && html`<${Chip} n=${attN} kind="attached" onPick=${noop} />`}
      ${empty && html`<span class="muted">${busy ? t("rmap_history.loading") : t("rmap_history.none")}</span>`}
    </div>
    ${!empty && html`<div class="mp-hx-cols">
      <div class="mp-hx-groups">
        ${group("added", c.added)}${group("status", c.status)}${group("dropped", c.dropped)}${group("removed", c.removed)}
        ${group("retitled", c.retitled)}
        ${att.map(([kind, x]) => html`<div class="mp-hx-grp attached" key=${kind}>
          <h4>${kind === "evidence" ? t("rmap.kind.evidence") : label("rmap.kind." + kind)} <small>+${x.added.length}${x.removed.length ? " −" + x.removed.length : ""}</small></h4>
          <ul>${x.added.map((a) => html`<li key=${a.id}><button class="mp-hx-n" onClick=${() => onPick(a.node)}><span class="mp-sign s+">+</span><span class="mp-hx-ti">${a.what}</span><span class="mp-hx-ft">${a.title}</span></button></li>`)}
            ${x.removed.map((a) => html`<li key=${a.id}><button class="mp-hx-n" onClick=${() => onPick(a.node)}><span class="mp-sign s−">−</span><span class="mp-hx-ti">${a.what}</span><span class="mp-hx-ft">${a.title}</span></button></li>`)}</ul></div>`)}
      </div>
      ${evs.length > 0 && html`<div class="mp-hx-log"><h4>${t("rmap_history.list.log")} <small>${total ?? evs.length}</small></h4>
        <ul class="mp-hist">${(all ? evs : evs.slice(0, LIST_MAX)).map((ev) => { const d = describe(ev, names); return html`<li key=${ev.id}>
          <span class=${"mp-sign s" + d.sign}>${d.sign}</span><span class="mp-ht">${d.text}</span>
          <span class="mp-hw" title=${ev.at}>${when(ev.at)}</span></li>`; })}</ul>
        ${!all && evs.length > LIST_MAX && html`<button class="mp-more" onClick=${() => setAll(true)}>${t("rmap_history.more", { n: evs.length - LIST_MAX })}</button>`}
        ${total > evs.length && html`<p class="mp-quiet">${t("rmap_history.cut", { n: total - evs.length })}</p>`}
      </div>`}
    </div>`}
  </div>`;
}

// ---- the time scrubber ----

function Scrubber({ weeks, wi, setWi, playing, setPlaying }) {
  const last = weeks.length - 1;
  const peak = Math.max(1, ...weeks.map((w) => w.events));
  const every = Math.max(1, Math.ceil(weeks.length / 16)); // label at most ~16 ticks
  const w = weeks[wi];
  const pct = (i) => (last ? (i / last) * 100 : 100);
  return html`<div class="mp-tl">
    <button class=${"mp-tl-play" + (playing ? " on" : "")} title=${playing ? t("rmap_history.pause") : t("rmap_history.play")}
      aria-label=${playing ? t("rmap_history.pause") : t("rmap_history.play")} onClick=${() => setPlaying(!playing)}>${playing ? PAUSE : PLAY}</button>
    <div class="mp-tl-track">
      <div class="mp-tl-bars">${weeks.map((x) => html`<i key=${x.i} class=${x.i <= wi ? "on" : ""} style=${{ left: pct(x.i) + "%", height: (x.events ? 3 + (x.events / peak) * 15 : 0) + "px" }} />`)}</div>
      <div class="mp-tl-line"><i style=${{ width: pct(wi) + "%" }} /></div>
      <input type="range" min="0" max=${last} step="1" value=${wi} aria-label=${t("rmap_history.scrubber")}
        aria-valuetext=${w ? `${w.label} ${w.start} – ${w.end}` : ""}
        onInput=${(e) => { setPlaying(false); setWi(+e.target.value); }} />
      <div class="mp-tl-ticks">${weeks.map((x) => (x.i % every === 0 || x.i === last) && html`<button key=${x.i} class=${x.i === wi ? "on" : ""}
          style=${{ left: pct(x.i) + "%" }} title=${t("rmap_history.week.title", { label: x.label, start: x.start, end: x.end, n: x.events })}
          onClick=${() => { setPlaying(false); setWi(x.i); }}>${x.label}</button>`)}</div>
    </div>
    <div class="mp-tl-at mp-num">${w && html`<b>${w.label}</b><span>${short(w.start)} – ${short(w.end)}</span>${wi === last && html`<em>${t("rmap_history.now")}</em>`}`}</div>
  </div>`;
}

function Replay({ ctx, frames }) {
  const weeks = frames.weeks;
  const last = weeks.length - 1;
  const [wi, setWi] = useState(last);
  const [playing, setPlaying] = useState(false);
  const [selected, select] = useState(null);
  const [week, setWeek] = useState(null); // the diff of the shown week: its events
  const lastSeen = useRef(last);
  useEffect(() => { // new weeks (or a fresh map): stay on "now" if that is where we were
    setWi((w) => (w >= lastSeen.current || w > last ? last : w));
    lastSeen.current = last;
  }, [frames]);
  useEffect(() => {
    if (!playing) return;
    if (wi >= last) setWi(0);
    const id = setInterval(() => setWi((w) => { if (w >= last) { setPlaying(false); return w; } return w + 1; }), PLAY_MS);
    return () => clearInterval(id);
  }, [playing, last]);
  const shown = useMemo(() => mapAt(frames, wi), [frames, wi]);
  const cmp = useMemo(() => compare(wi > 0 ? mapAt(frames, wi - 1) : null, shown), [frames, wi, shown]);
  const w = weeks[wi];
  useEffect(() => {
    setWeek(null);
    if (playing || !w) return;
    const id = setTimeout(() => ctx.api.call(`${ctx.api.base}/history/diff?since=${w.start}&until=${w.end}`).then(setWeek).catch(() => setWeek({ events: [] })), 200);
    return () => clearTimeout(id);
  }, [ctx.slug, frames, wi, playing]);
  const names = useMemo(() => titles(cmp.view, week?.events || []), [cmp, week]);
  const verified = shown.nodes.filter((n) => n.status === "verified").length;
  const changes = { ...cmp.changes, attachments: week?.changes?.attachments || {} };
  return html`<div>
    <${Scrubber} weeks=${weeks} wi=${wi} setWi=${setWi} playing=${playing} setPlaying=${setPlaying} />
    <div class="mp-hx-stats">
      <span class="mp-num">${t("rmap_history.stats", { nodes: shown.nodes.length, verified, edges: shown.edges.length })}</span>
      ${w?.notes?.length > 0 && html`<span class="mp-hx-notes">${w.notes.map((s) => html`<span key=${s}>${s}</span>`)}</span>`}
      ${wi < last && html`<button class="btn ghost sm" onClick=${() => { setPlaying(false); setWi(last); }}>${t("rmap_history.back")}</button>`}
    </div>
    <${FrozenMap} ctx=${ctx} map=${cmp.view} marks=${cmp.marks} selected=${selected} select=${select} />
    <${ChangeList} changes=${changes} events=${week?.events} total=${week?.total} names=${names} onPick=${select} busy=${!week && !playing} />
  </div>`;
}

// ---- the change view ----

function Compare({ ctx, frames }) {
  const first = frames.weeks[0]?.start || daysAgo(28);
  const [since, setSince] = useState(() => { const d = daysAgo(27); return d < first ? first : d; });
  const [until, setUntil] = useState(() => iso(new Date()));
  const [d, setD] = useState(null);
  const [error, setError] = useState("");
  const [packet, setPacket] = useState(null);
  const [selected, select] = useState(null);
  useEffect(() => { ctx.api.call(`${ctx.api.base}/history/packet`).then((r) => setPacket(r.packet)).catch(() => setPacket(null)); }, [ctx.slug]);
  useEffect(() => {
    if (!since || !until) return;
    let live = true;
    ctx.api.call(`${ctx.api.base}/history/diff?since=${since}&until=${until}`)
      .then((r) => { if (live) { setD(r); setError(""); } }).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [ctx.slug, frames, since, until]);
  const today = iso(new Date());
  const presets = [
    { key: "week", since: daysAgo(6), until: today, text: t("rmap_history.preset.week") },
    { key: "month", since: daysAgo(27), until: today, text: t("rmap_history.preset.month") },
    ...(packet ? [{ key: "packet", since: packet.date, until: today, text: t("rmap_history.preset.packet", { date: short(packet.date) }), title: packet.name }] : []),
    { key: "all", since: first, until: today, text: t("rmap_history.preset.all") },
  ];
  const names = useMemo(() => d && titles(d.map, d.events), [d]);
  return html`<div>
    <div class="mp-cv-bar">
      <label class="mp-cv-date"><span>${t("rmap_history.from")}</span><input type="date" value=${since} min=${first} max=${until} onChange=${(e) => setSince(e.target.value)} /></label>
      <span class="mp-cv-arrow">→</span>
      <label class="mp-cv-date"><span>${t("rmap_history.to")}</span><input type="date" value=${until} min=${since} max=${today} onChange=${(e) => setUntil(e.target.value)} /></label>
      <div class="mp-cv-presets">${presets.map((p) => html`<button key=${p.key} title=${p.title || null} class=${"mp-layer" + (p.since === since && p.until === until ? " on" : "")}
        onClick=${() => { setSince(p.since); setUntil(p.until); }}>${p.text}</button>`)}</div>
    </div>
    ${error ? html`<p class="muted">${error}</p>` : !d ? html`<p class="muted">${t("rmap_history.loading")}</p>` : html`
      <${FrozenMap} ctx=${ctx} map=${d.map} marks=${d.marks} selected=${selected} select=${select} />
      <${ChangeList} changes=${d.changes} events=${d.events} total=${d.total} names=${names} onPick=${select} />`}
  </div>`;
}

// ---- the panel: replay | compare ----

export function HistoryPanel({ ctx, initial = "replay" }) {
  useHistoryLayer();
  const [mode, setMode] = useState(initial);
  const [frames, setFrames] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    ctx.api.call(`${ctx.api.base}/history`).then((f) => { if (live) { setFrames((old) => (old?.stamp === f.stamp ? old : f)); setError(""); } })
      .catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [ctx.slug, ctx.map?.stamp]);
  if (error) return html`<p class="muted">${error}</p>`;
  if (!frames || frames.slug !== ctx.slug) return html`<p class="muted">${t("rmap_history.loading")}</p>`;
  return html`<div class="mp-hx">
    <div class="mp-hx-head">
      <div class="seg mp-hx-mode">
        <button class=${mode === "replay" ? "on" : ""} onClick=${() => setMode("replay")}>${t("rmap_history.mode.replay")}</button>
        <button class=${mode === "compare" ? "on" : ""} onClick=${() => setMode("compare")}>${t("rmap_history.mode.compare")}</button>
      </div>
      <span class="mp-quiet">${mode === "replay" ? t("rmap_history.hint.replay") : t("rmap_history.hint.compare")}</span>
    </div>
    ${mode === "replay" ? html`<${Replay} key=${ctx.slug} ctx=${ctx} frames=${frames} />` : html`<${Compare} key=${ctx.slug} ctx=${ctx} frames=${frames} />`}
  </div>`;
}

// Peer layer (同行层, #47): papers, scholars and labs hung on nodes (the "peer" attachment kind, app/rmap/peers.py).
// - layer: an orange pill with the count, the aqua dashed 空白 outline on open branches with no peer, a 撞车风险
//   tag where peers are dense and the node is in progress (rules in /rmap/peerlib.js);
// - the 空白分支 KPI, redefined as "no peers";
// - a detail panel section: the node's peers, add / change / remove, and the suggestions for it;
// - a toolbar button with the suggestions for this map (new papers from the tracking page, follows and the
//   reading list, matched by keywords; nothing is attached until the user confirms);
// - the 同行分析 view: the 分支 × 同行 heat table of every map, the gaps and the collision risks.
// i18n: done
// i18n-keys: rmap_peers.rel.{same,competing,builds_on} rmap_peers.type.{paper,scholar,lab}
// i18n-keys: rmap_peers.src.{manual,tracker,follow,scholar,reading,reader,assistant}
// i18n-keys: rmap.status.{todo,active,verified,blocked,refuted,dropped}
// i18n-keys: rmap.type.{question,hypothesis,subline,experiment,result,decision,kill,open}
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { t, lang, onLangChange } from "/i18n.js";
import { rmap } from "/rmap/registry.js";
import { safeUrl } from "/rmap/util.js";
import { useAutoFocus } from "/rmap/dom.js";
import { useStamp } from "/rmap/page.js";
import { RELATIONS, TYPES, HEAT, ruleOf, peersByNode, statsOf, gaps, idsOf, entityFor, initials } from "/rmap/peerlib.js";

// The change log names attachment kinds with label("rmap.kind.<kind>"): give it ours, in the current language.
const nameKind = () => rmap.strings({ [lang()]: { "rmap.kind.peer": t("rmap_peers.kind") } });
nameKind();
onLangChange(nameKind);

const base = (slug) => `/api/rmap/projects/${encodeURIComponent(slug)}`;
const short = (s, n) => (s && s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s || "");

// ---------------------------------------------------------------- the layer

let measurer = null;
function textW(text, font = '600 9.5px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif') {
  measurer = measurer || document.createElement("canvas").getContext("2d");
  measurer.font = font;
  return Math.ceil(measurer.measureText(text).width);
}

function Tag({ x, y, text, cls, title }) {
  const w = textW(text) + 12;
  return html`<g class=${"mpp-tag " + cls} transform=${`translate(${x - w} ${y})`}>${title && html`<title>${title}</title>`}
    <rect width=${w} height="15" rx="7.5" /><text x=${w / 2} y="10.6" text-anchor="middle">${text}</text></g>`;
}

function peerDecor(n, box, ctx) {
  const s = statsOf(n, ctx.map, ctx.meta);
  if (!s.n && !s.gap) return null;
  let right = box.x + box.w + 6 - (box.pinned ? 10 : 0);
  const y = box.y - 8;
  const out = [];
  if (s.n) {
    const txt = String(s.n), w = Math.max(18, textW(txt, '600 10px ui-monospace, "SF Mono", Menlo, monospace') + 11);
    out.push(html`<g class=${"mpp-pill" + (s.risk ? " risk" : "")} transform=${`translate(${right - w} ${y})`}>
      <title>${t("rmap_peers.pill.title", { n: s.n, same: s.same, competing: s.competing, builds_on: s.builds_on })}</title>
      <rect width=${w} height="15" rx="7.5" /><text x=${w / 2} y="11" text-anchor="middle">${txt}</text></g>`);
    right -= w + 4;
  }
  if (s.risk) out.push(html`<${Tag} x=${right} y=${y} cls="risk" text=${t("rmap_peers.tag.risk")} title=${t("rmap_peers.flag.risk", { n: s.same + s.competing })} />`);
  if (s.gap) out.push(html`<${Tag} x=${right} y=${y} cls="gap" text=${t("rmap_peers.tag.gap")} title=${t("rmap_peers.flag.gap")} />`);
  return html`<g key=${n.id} class="mpp-decor">${out}</g>`;
}

rmap.layer({
  id: "peers", label: "rmap_peers.layer", order: 10, default: true, accent: "var(--mp-peer)",
  nodeClass: (n, ctx) => { const s = statsOf(n, ctx.map, ctx.meta); return s.gap ? "mpp-gap" : s.risk ? "mpp-risk" : ""; },
  node: peerDecor,
  legend: () => html`<span class="mpp-lg"><i class="pill">n</i>${t("rmap_peers.lg.peers")}</span>
    <span class="mpp-lg"><i class="gap" />${t("rmap_peers.lg.gap")}</span>
    <span class="mpp-lg"><i class="risk" />${t("rmap_peers.tag.risk")}</span>`,
});

// ---------------------------------------------------------------- KPI: 空白分支 = open branches with no peer

function BlankTile({ ctx }) {
  const rule = ruleOf(ctx.meta);
  const open = ctx.map.nodes.filter((n) => rule.gap_types.includes(n.type) && !ctx.meta.closed.includes(n.status || "todo"));
  const blank = gaps(ctx.map, ctx.meta);
  const names = blank.slice(0, 2).map((n) => n.title).join(t("rmap_peers.sep"));
  return html`<div class="kpi-label">${t("rmap_peers.kpi.blank")}</div>
    <div class="kpi-value mp-num mpp-aqua">${blank.length}<small>/${open.length}</small></div>
    <div class="kpi-sub" title=${blank.map((n) => n.title).join("\n")}>${blank.length ? t("rmap_peers.kpi.blank.sub", { names }) : t("rmap_peers.kpi.blank.none")}</div>
    <div class="kpi-viz"><div class="mpp-gapbar">${open.map((n) => html`<i class=${blank.includes(n) ? "gap" : ""} title=${n.title} />`)}</div></div>`;
}

rmap.kpi({ id: "blank", order: 40, render: (ctx) => html`<${BlankTile} ctx=${ctx} />` });

// ---------------------------------------------------------------- suggestions (shared by the toolbar, the panel and the view)

const sugCache = new Map(); // "<slug>|<stamp>" -> promise
function loadSuggestions(slug, stamp, fresh = false) {
  const key = `${slug || "*"}|${stamp || ""}`;
  if (fresh || !sugCache.has(key)) {
    if (sugCache.size > 8) sugCache.clear();
    sugCache.set(key, api(`/api/rmap/peers/suggestions${slug ? "?slug=" + encodeURIComponent(slug) : ""}`).then((r) => r.suggestions).catch(() => []));
  }
  return sugCache.get(key);
}

// delay: wait that long after the map changed (an edit, a sync) before asking, and only while the page is
// visible, so a burst of edits asks once; the server answers from its cache until a map or a source changes.
function useSuggestions(slug, stamp, delay = 0) {
  const [list, setList] = useState(null);
  const [bump, setBump] = useState(0);
  useEffect(() => {
    let live = true, timer = null;
    const go = () => {
      if (document.hidden) { timer = setTimeout(go, 2000); return; }
      loadSuggestions(slug, stamp, bump > 0).then((l) => live && setList(l));
    };
    if (delay > 0) timer = setTimeout(go, delay); else go();
    return () => { live = false; clearTimeout(timer); };
  }, [slug, stamp, bump, delay > 0]);
  return [list, () => setBump((b) => b + 1)];
}

const VENUE_MAX = 80;
function peerSet(sug, relation) {
  return { type: "paper", title: short(sug.title, 300), relation, url: safeUrl(sug.url) && /^https?:/i.test(sug.url) ? sug.url : null,
    paper: sug.paper || null, authors: (sug.authors || []).slice(0, 4).join(", ").slice(0, 290) || null,
    venue: sug.venue ? sug.venue.slice(0, VENUE_MAX) : null, date: sug.date || null, source: sug.sources?.[0] || "manual", entity: sug.key };
}
function evidenceSet(sug) {
  const title = short(sug.title, 200); // evidence titles hold at most 200 characters
  return sug.paper ? { type: "paper", target: sug.paper, title, stance: "context", entity: sug.key }
    : { type: "url", target: sug.url, title, stance: "context", entity: sug.key };
}

// What the attach route did: added, already there (any of the paper's ids), or a peer's relation changed.
const attachedText = (r, node, relation) => (r.state === "exists" ? t("rmap_peers.attach.exists", { node })
  : r.state === "updated" ? t("rmap_peers.attach.updated", { node, relation: t("rmap_peers.rel." + relation) })
  : t("rmap_peers.sug.attached", { node }));

// Hang a suggestion on a node, as a peer with a relation or as evidence, through /api/rmap/peers/attach (which
// adds nothing when the paper already hangs there); then this map is re-read.
async function confirm(ctx, sug, m, how) {
  const kind = how === "evidence" ? "evidence" : "peer";
  const r = await api("/api/rmap/peers/attach", { json: { slug: m.slug, node: m.node, kind, set: kind === "evidence" ? evidenceSet(sug) : peerSet(sug, how) } });
  if (m.slug === ctx.slug) await ctx.api.refresh();
  ctx.toast(attachedText(r, short(m.title, 40), how));
}

function SuggestionCard({ sug, ctx, slug, node, onDone, onOpen }) {
  const [busy, setBusy] = useState(false);
  const matches = sug.matches.filter((m) => (!slug || m.slug === slug) && (!node || m.node === node));
  const run = async (fn) => { setBusy(true); try { await fn(); onDone?.(); } catch (e) { ctx.toast(e.message); } finally { setBusy(false); } };
  const dismiss = () => run(async () => {
    await api("/api/rmap/peers/dismiss", { json: { key: sug.key, aliases: sug.aliases || [], slugs: [...new Set(matches.map((m) => m.slug))] } });
    ctx.toast(t("rmap_peers.sug.dismissed"));
  });
  const link = safeUrl(sug.url);
  return html`<div class="mpp-sug">
    <div class="mpp-sug-h">
      ${sug.paper ? html`<button class="mpp-sug-t" onClick=${() => ctx.onOpenPaper?.(sug.paper)}>${sug.title}</button>`
        : link ? html`<a class="mpp-sug-t" href=${link} target="_blank" rel="noopener">${sug.title}</a>` : html`<span class="mpp-sug-t">${sug.title}</span>`}
      <button class="mp-chip-x mpp-show" title=${t("rmap_peers.sug.dismiss")} disabled=${busy} onClick=${dismiss}><${Icon} name="x" sm /></button>
    </div>
    <div class="mpp-meta">${[sug.sources.map((s) => t("rmap_peers.src." + s)).join(" · "), sug.via?.[0], sug.date, sug.authors?.slice(0, 2).join(", ")].filter(Boolean).join(" · ")}</div>
    ${matches.map((m) => html`<div class="mpp-sug-m" key=${m.slug + m.node}>
      ${node ? html`<div class="mpp-meta">${t("rmap_peers.sug.why", { terms: m.terms.join(", ") })}</div>`
        : html`<div class="mpp-sug-to"><span class="mp-quiet">${t("rmap_peers.sug.to")}</span>
          ${!slug && html`<span class="mpp-proj">${m.project}</span>`}
          <button class="mpp-node-link" title=${t("rmap_peers.sug.why", { terms: m.terms.join(", ") })} onClick=${() => onOpen?.(m)}>${m.title}</button></div>`}
      <div class="mpp-acts">
        ${RELATIONS.map((r) => html`<button class=${"mpp-act r-" + r} disabled=${busy} onClick=${() => run(() => confirm(ctx, sug, m, r))}>${t("rmap_peers.rel." + r)}</button>`)}
        <button class="mpp-act as-ev" disabled=${busy || !(sug.paper || link)} onClick=${() => run(() => confirm(ctx, sug, m, "evidence"))}>${t("rmap_peers.sug.as_evidence")}</button>
      </div>
    </div>`)}
  </div>`;
}

function SuggestTool({ ctx }) {
  const [open, setOpen] = useState(false);
  const [list, reload] = useSuggestions(ctx.slug, ctx.map?.stamp, open ? 0 : 1500);
  const ref = useRef();
  useEffect(() => {
    if (!open) return;
    const off = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const key = (e) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", off); window.addEventListener("keydown", key);
    return () => { window.removeEventListener("mousedown", off); window.removeEventListener("keydown", key); };
  }, [open]);
  if (!ctx.slug || !ctx.map) return null;
  const n = list?.length || 0;
  return html`<span class="mpp-tool" ref=${ref}>
    <button class=${"btn ghost sm" + (open ? " on" : "")} title=${t("rmap_peers.sug.hint")} onClick=${() => setOpen(!open)}>
      <${Icon} name="radar" sm />${t("rmap_peers.suggest")}${n > 0 && html`<i class="mpp-count">${n}</i>`}</button>
    ${open && html`<div class="mpp-pop">
      <div class="mpp-pop-h"><b>${t("rmap_peers.suggest")}</b><span class="mp-quiet">${t("rmap_peers.sug.hint")}</span></div>
      ${list === null ? html`<p class="mp-quiet">${t("rmap_peers.loading")}</p>`
        : !n ? html`<p class="mp-quiet">${t("rmap_peers.sug.none")}</p>`
        : list.map((s) => html`<${SuggestionCard} key=${s.key} sug=${s} ctx=${ctx} slug=${ctx.slug} onDone=${reload}
            onOpen=${(m) => { ctx.select(m.node); setOpen(false); }} />`)}
      ${ctx.onAsk && n > 0 && html`<button class="btn ghost sm mpp-ask" onClick=${() => { setOpen(false); ctx.onAsk(t("rmap_peers.sug.ask_prompt", { project: ctx.map.project.title }), t("rmap_peers.sug.ask")); }}>
        <${Icon} name="sparkle" sm />${t("rmap_peers.sug.ask")}</button>`}
    </div>`}
  </span>`;
}

rmap.tool({ id: "peer-suggest", label: "rmap_peers.suggest", order: 30, render: (ctx) => html`<${SuggestTool} ctx=${ctx} />` });

// ---------------------------------------------------------------- detail panel: the node's peers

const ovCache = new Map(); // "<slug>|<stamp>" -> promise of the overview's columns (peers merged across projects)
function sharedColumns(ctx) {
  const key = ctx.slug + "|" + ctx.map.stamp;
  if (!ovCache.has(key)) {
    if (ovCache.size > 8) ovCache.clear();
    ovCache.set(key, ctx.api.call("/api/rmap/peers/overview").then((r) => r.columns.filter((c) => c.projects.length > 1)).catch(() => []));
  }
  return ovCache.get(key);
}

function PeerAdd({ node, ctx, onDone }) {
  const [f, setF] = useState({ type: "paper", relation: "same", title: "", link: "", date: "", venue: "" });
  const ref = useAutoFocus(node.id);
  const save = async (e) => {
    e.preventDefault();
    const title = f.title.trim();
    const ids = f.type === "paper" ? idsOf(f.link) : {};
    const url = /^https?:\/\//i.test(f.link.trim()) ? f.link.trim() : ids.arxiv ? `https://arxiv.org/abs/${ids.arxiv}` : null;
    const set = { type: f.type, title, relation: f.relation, url, date: f.date.trim() || null, venue: f.venue.trim() || null, source: "manual",
      entity: entityFor(f.type, { title, ...ids, lab: f.type === "lab" ? title : undefined }) };
    try {
      const r = await ctx.api.call("/api/rmap/peers/attach", { json: { slug: ctx.slug, node: node.id, kind: "peer", set } });
      await ctx.api.refresh();
      if (r.state !== "added") ctx.toast(attachedText(r, short(node.title, 40), f.relation));
      onDone();
    } catch (err) { ctx.toast(err.message); }
  };
  const guard = (e) => { if (e.key === "Escape") onDone(); };
  return html`<form class="mp-evadd" onSubmit=${save} onKeyDown=${guard}>
    <div class="mp-evrow">
      <select value=${f.type} onChange=${(e) => setF({ ...f, type: e.target.value })}>${TYPES.map((x) => html`<option value=${x}>${t("rmap_peers.type." + x)}</option>`)}</select>
      <select value=${f.relation} onChange=${(e) => setF({ ...f, relation: e.target.value })}>${RELATIONS.map((x) => html`<option value=${x}>${t("rmap_peers.rel." + x)}</option>`)}</select>
    </div>
    <input ref=${ref} placeholder=${t("rmap_peers.add.title_ph")} value=${f.title} onInput=${(e) => setF({ ...f, title: e.target.value })} />
    <input placeholder=${t("rmap_peers.add.link_ph")} value=${f.link} onInput=${(e) => setF({ ...f, link: e.target.value })} />
    <div class="mp-evrow">
      <input placeholder=${t("rmap_peers.add.date_ph")} value=${f.date} onInput=${(e) => setF({ ...f, date: e.target.value })} />
      <input placeholder=${t("rmap_peers.add.venue_ph")} value=${f.venue} onInput=${(e) => setF({ ...f, venue: e.target.value })} />
    </div>
    <div class="row-end"><button type="button" class="btn ghost sm" onClick=${onDone}>${t("rmap_peers.cancel")}</button>
      <button class="btn primary sm" disabled=${!f.title.trim()}>${t("rmap_peers.add.save")}</button></div>
  </form>`;
}

function PeerSection({ node, ctx }) {
  const [adding, setAdding] = useState(false);
  const [shared, setShared] = useState({});
  const items = peersByNode(ctx.map).get(node.id) || [];
  const s = statsOf(node, ctx.map, ctx.meta);
  const [sugs, reload] = useSuggestions(ctx.slug, ctx.map.stamp, 600);
  const mine = (sugs || []).filter((x) => x.matches.some((m) => m.slug === ctx.slug && m.node === node.id)).slice(0, 3);
  useEffect(() => setAdding(false), [node.id]);
  // where else each peer hangs: the overview merges a peer across projects by any of its ids
  useEffect(() => {
    if (!items.length) { setShared({}); return; }
    let live = true;
    const title = (slug) => ctx.projects.find((p) => p.slug === slug)?.title || slug;
    sharedColumns(ctx).then((cols) => {
      if (!live) return;
      const out = {};
      for (const p of items) {
        const col = cols.find((c) => c.aliases.some((a) => p.aliases.includes(a)));
        const other = (col?.projects || []).filter((s) => s !== ctx.slug).map(title);
        if (other.length) out[p.id] = other;
      }
      setShared(out);
    });
    return () => { live = false; };
  }, [node.id, ctx.map.stamp]);
  const apply = (ops) => ctx.api.apply(ops).catch((e) => ctx.toast(e.message));
  const open = (p) => {
    if (p.paper) ctx.onOpenPaper?.(p.paper);
    else { const u = safeUrl(p.url); if (u) window.open(u, "_blank", "noopener"); }
  };
  const order = (p) => RELATIONS.indexOf(p.relation);
  return html`<h4>${t("rmap_peers.section")}<small>${items.length || ""}</small>
      <button class="mp-h-btn" title=${t("rmap_peers.add")} onClick=${() => setAdding(!adding)}><${Icon} name="plus" sm /></button></h4>
    ${s.risk && html`<div class="mpp-flag risk">${t("rmap_peers.flag.risk", { n: s.same + s.competing })}</div>`}
    ${s.gap && html`<div class="mpp-flag gap">${t("rmap_peers.flag.gap")}</div>`}
    ${items.length > 0 && html`<div class="mpp-list">${[...items].sort((a, b) => order(a) - order(b)).map((p) => html`<div class="mpp-row" key=${p.id}>
      <span class=${"mpp-ring t-" + p.type} title=${t("rmap_peers.type." + p.type)}>${initials(p.title)}</span>
      <div class="mpp-main">
        ${p.paper || safeUrl(p.url) ? html`<button class="mpp-t" title=${p.title} onClick=${() => open(p)}>${p.title}</button>` : html`<span class="mpp-t" title=${p.title}>${p.title}</span>`}
        <div class="mpp-meta">${[t("rmap_peers.type." + p.type), p.venue, p.date, p.authors && short(p.authors, 40)].filter(Boolean).join(" · ")}
          ${shared[p.id] && html` · <span class="mpp-also">${t("rmap_peers.also", { projects: shared[p.id].join(t("rmap_peers.sep")) })}</span>`}</div>
        ${p.note && html`<div class="mpp-note">${p.note}</div>`}
      </div>
      <select class=${"mpp-rel r-" + p.relation} value=${p.relation} title=${t("rmap_peers.rel.title")}
        onChange=${(e) => apply(p.ids.map((id) => ({ op: "attach.update", kind: "peer", id, set: { relation: e.target.value } })))}>
        ${RELATIONS.map((r) => html`<option value=${r}>${t("rmap_peers.rel." + r)}</option>`)}</select>
      <button class="mp-chip-x" title=${t("rmap_peers.remove")} onClick=${() => apply(p.ids.map((id) => ({ op: "attach.delete", kind: "peer", id })))}><${Icon} name="x" sm /></button>
    </div>`)}</div>`}
    ${!items.length && !adding && html`<p class="mp-quiet">${t("rmap_peers.none")}</p>`}
    ${adding && html`<${PeerAdd} node=${node} ctx=${ctx} onDone=${() => setAdding(false)} />`}
    ${mine.length > 0 && html`<div class="mpp-node-sugs"><div class="mpp-sub-h"><${Icon} name="radar" sm />${t("rmap_peers.sug.h")}</div>
      ${mine.map((x) => html`<${SuggestionCard} key=${x.key} sug=${x} ctx=${ctx} slug=${ctx.slug} node=${node.id} onDone=${reload} />`)}</div>`}`;
}

rmap.section({ id: "peers", order: 20, render: (n, ctx) => html`<${PeerSection} node=${n} ctx=${ctx} />` });

// ---------------------------------------------------------------- the 同行分析 view

const COLS = 14;

function Heat({ data, onOpen }) {
  const cols = data.columns.slice(0, COLS);
  const rest = data.columns.length - cols.length;
  const shown = new Set(cols.map((c) => c.key));
  const rel = (r, c) => r.cells?.[c.key];
  return html`<div class="mpp-heat-scroll"><table class="mpp-heat">
    <thead><tr><th class="mpp-rowh" />
      ${cols.map((c) => html`<th class="mpp-colh" title=${[c.title, t("rmap_peers.type." + c.type), c.projects.length > 1 ? t("rmap_peers.an.in_projects", { n: c.projects.length }) : ""].filter(Boolean).join(" · ")}>
        <span class=${c.projects.length > 1 ? "shared" : ""}>${short(c.title, 26)}</span></th>`)}
      ${rest > 0 && html`<th class="mpp-colh more" title=${t("rmap_peers.an.more_title", { n: rest })}><span>+${rest}</span></th>`}
      <th class="mpp-colh sum"><span>${t("rmap_peers.an.sum")}</span></th></tr></thead>
    <tbody>${data.maps.filter((mp) => mp.rows.length).map((mp) => html`
      <tr class="mpp-proj" key=${mp.slug}><th colspan=${cols.length + 2 + (rest > 0 ? 1 : 0)}>
        ${onOpen ? html`<button onClick=${() => onOpen(mp.slug)}>${mp.title}</button>` : mp.title}
        <small class="mp-num">${t("rmap_peers.an.proj_sub", { peers: mp.peers, gaps: mp.gaps, risks: mp.risks })}</small></th></tr>
      ${mp.rows.map((r) => html`<tr key=${mp.slug + r.id} class=${r.gap ? "gap" : r.risk ? "risk" : ""}>
        <th class="mpp-rowh" style=${{ paddingLeft: 8 + Math.min(r.depth, 4) * 12 + "px" }}>
          <span class="mpp-rowt"><i class=${"mp-dot st-" + r.status} title=${t("rmap.status." + r.status)} />
            ${onOpen ? html`<button title=${t("rmap.type." + r.type) + " · " + r.title} onClick=${() => onOpen(mp.slug, r.id)}>${r.title}</button>` : html`<span>${r.title}</span>`}
            ${r.gap && html`<em class="mpp-chip gap">${t("rmap_peers.tag.gap")}</em>`}${r.risk && html`<em class="mpp-chip risk">${t("rmap_peers.tag.risk")}</em>`}</span></th>
        ${cols.map((c) => { const v = rel(r, c);
          return html`<td class="mpp-cell" key=${c.key}>${v && html`<i class=${"r-" + v} style=${{ opacity: HEAT[v] }} title=${`${c.title} · ${t("rmap_peers.rel." + v)}`} />`}</td>`; })}
        ${rest > 0 && html`<td class="mpp-cell more mp-num">${Object.keys(r.cells || {}).filter((k) => !shown.has(k)).length || ""}</td>`}
        <td class="mpp-cell sum mp-num">${r.n || html`<span class="mpp-zero">0</span>`}</td>
      </tr>`)}`)}
    </tbody></table></div>`;
}

function Analysis({ ctx }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [sugs, reloadSugs] = useSuggestions(null, data?.stamp);
  const load = () => api("/api/rmap/peers/overview").then((d) => { setData(d); setError(""); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [ctx.version]);
  useStamp(data && "/api/rmap/stamp", data?.stamp, load);
  if (error) return html`<p class="muted">${error}</p>`;
  if (!data) return html`<p class="muted">${t("rmap_peers.loading")}</p>`;
  if (!data.maps.length) return html`<p class="muted">${t("rmap_peers.an.empty")}</p>`;
  const onOpen = ctx.open ? (slug, id) => ctx.open(slug, id) : null;
  const total = data.maps.reduce((s, m) => s + m.peers, 0);
  const sharedCols = data.columns.filter((c) => c.projects.length > 1);
  const done = () => { load(); reloadSugs(); };
  const tile = (label, value, sub, cls = "") => html`<div class="kpi"><div class="kpi-label">${label}</div>
    <div class=${"kpi-value mp-num " + cls}>${value}</div><div class="kpi-sub" title=${sub}>${sub}</div></div>`;
  return html`<div class="mpp-an">
    <div class="kpis mp-kpis">
      ${tile(t("rmap_peers.an.peers"), total, t("rmap_peers.an.peers_sub", { n: data.columns.length, maps: data.maps.length }), "mpp-orange")}
      ${tile(t("rmap_peers.kpi.blank"), data.gaps.length, data.gaps.length ? data.gaps.slice(0, 2).map((g) => g.title).join(t("rmap_peers.sep")) : t("rmap_peers.kpi.blank.none"), "mpp-aqua")}
      ${tile(t("rmap_peers.tag.risk"), data.risks.length, data.risks.length ? data.risks.slice(0, 2).map((g) => g.title).join(t("rmap_peers.sep")) : t("rmap_peers.an.none_risks"), data.risks.length ? "mpp-orange" : "")}
      ${tile(t("rmap_peers.an.shared"), sharedCols.length, sharedCols.length ? sharedCols.slice(0, 2).map((c) => short(c.title, 30)).join(t("rmap_peers.sep")) : t("rmap_peers.an.shared_sub"))}
    </div>
    <div class="mpp-card">
      <div class="mpp-card-h"><h3>${t("rmap_peers.an.heat")}</h3><span class="mp-quiet">${t("rmap_peers.an.heat_hint")}</span>
        <span class="mpp-swatches">${RELATIONS.map((r) => html`<span><i class=${"r-" + r} style=${{ opacity: HEAT[r] }} />${t("rmap_peers.rel." + r)}</span>`)}</span></div>
      ${data.columns.length ? html`<${Heat} data=${data} onOpen=${onOpen} />` : html`<p class="mp-quiet mpp-pad">${t("rmap_peers.an.no_peers")}</p>`}
    </div>
    <div class="mpp-lists">
      <div class="mpp-card">
        <div class="mpp-card-h"><h3 class="mpp-aqua">${t("rmap_peers.kpi.blank")}<small class="mp-num">${data.gaps.length}</small></h3><span class="mp-quiet">${t("rmap_peers.an.gaps_hint")}</span></div>
        ${data.gaps.length ? html`<ul class="mpp-ul">${data.gaps.map((g) => html`<li key=${g.slug + g.id}><i class=${"mp-dot st-" + g.status} />
          ${onOpen ? html`<button class="mpp-node-link" onClick=${() => onOpen(g.slug, g.id)}>${g.title}</button>` : html`<span>${g.title}</span>`}
          <span class="mpp-meta">${g.project} · ${t("rmap.type." + g.type)} · ${t("rmap.status." + g.status)}</span></li>`)}</ul>`
          : html`<p class="mp-quiet mpp-pad">${t("rmap_peers.an.none_gaps")}</p>`}
      </div>
      <div class="mpp-card">
        <div class="mpp-card-h"><h3 class="mpp-orange">${t("rmap_peers.tag.risk")}<small class="mp-num">${data.risks.length}</small></h3>
          <span class="mp-quiet">${t("rmap_peers.an.risks_hint", { dense: data.rule.dense, same: data.rule.same_dense })}</span></div>
        ${data.risks.length ? html`<ul class="mpp-ul">${data.risks.map((g) => html`<li key=${g.slug + g.id}><i class=${"mp-dot st-" + g.status} />
          ${onOpen ? html`<button class="mpp-node-link" onClick=${() => onOpen(g.slug, g.id)}>${g.title}</button>` : html`<span>${g.title}</span>`}
          <span class="mpp-meta">${g.project} · ${t("rmap_peers.rel.same")} ${g.same} · ${t("rmap_peers.rel.competing")} ${g.competing}</span>
          ${g.peers.length > 0 && html`<span class="mpp-peers">${g.peers.map((p) => html`<span class="mpp-ring sm" title=${p}>${initials(p)}</span>`)}</span>`}</li>`)}</ul>`
          : html`<p class="mp-quiet mpp-pad">${t("rmap_peers.an.none_risks")}</p>`}
      </div>
    </div>
    <div class="mpp-card">
      <div class="mpp-card-h"><h3>${t("rmap_peers.suggest")}<small class="mp-num">${sugs?.length || ""}</small></h3><span class="mp-quiet">${t("rmap_peers.sug.hint")}</span></div>
      ${sugs === null ? html`<p class="mp-quiet mpp-pad">${t("rmap_peers.loading")}</p>` : !sugs.length ? html`<p class="mp-quiet mpp-pad">${t("rmap_peers.sug.none")}</p>`
        : html`<div class="mpp-sug-grid">${sugs.map((s) => html`<${SuggestionCard} key=${s.key} sug=${s} ctx=${ctx} onDone=${done}
            onOpen=${onOpen ? (m) => onOpen(m.slug, m.node) : null} />`)}</div>`}
    </div>
  </div>`;
}

rmap.view({ id: "peers", label: "rmap_peers.view", order: 50, render: (ctx) => html`<${Analysis} ctx=${ctx} />` });

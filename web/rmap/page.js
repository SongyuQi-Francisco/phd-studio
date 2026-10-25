// Research map page (研究地图): project switcher + registered views, toolbar and layer toggles, the
// assistant's proposals, the KPI row, the map, the node detail panel, live refresh, and the extension
// loader. Extensions plug in through /rmap/registry.js; every hook gets the same `ctx`
// (see docs/research-map.md).
import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, api, prefs } from "/lib.js";
import { Empty } from "/ui.js";
import { rmap, loadExtensions } from "/rmap/registry.js";
import { t } from "/rmap/strings.js";
import { MapView } from "/rmap/map.js";
import { Detail } from "/rmap/detail.js";
import { Proposals } from "/rmap/proposals.js";
import { useAutoFocus } from "/rmap/util.js";
import "/rmap/kpis.js";

const POLL_MS = 2000;
const base = (slug) => `/api/rmap/projects/${encodeURIComponent(slug)}`;
const STATUS_LEGEND = ["active", "verified", "blocked", "todo", "dropped"];

function ensureCss(href) {
  if (document.querySelector(`link[href="${href}"]`)) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = href;
  document.head.appendChild(l);
}

// Re-render when an extension registers something.
function useRegistry() {
  const [, setV] = useState(rmap.version);
  useEffect(() => rmap.watch(setV), []);
}

// Poll a change stamp (cheap: file sizes and times) while the page is visible; onChange gets the new stamp.
export function useStamp(url, stamp, onChange) {
  const cb = useRef(onChange);
  cb.current = onChange;
  useEffect(() => {
    if (!url || !stamp) return;
    let busy = false;
    const tick = async () => {
      if (busy || document.hidden) return;
      busy = true;
      try { const r = await api(url); if (r.stamp !== stamp) await cb.current(r.stamp); } catch {} finally { busy = false; }
    };
    const id = setInterval(tick, POLL_MS);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, [url, stamp]);
}

function NewProject({ lines, onCreated, onCancel, toast }) {
  const [title, setTitle] = useState("");
  const [line, setLine] = useState("");
  const ref = useAutoFocus(1);
  const create = async (e) => {
    e.preventDefault();
    try { onCreated(await api("/api/rmap/projects", { json: { title, line: line || null } })); } catch (err) { toast(err.message); }
  };
  return html`<form class="mp-new" onSubmit=${create}>
    <input ref=${ref} placeholder=${t("rmap.project.title")} value=${title} onInput=${(e) => setTitle(e.target.value)} />
    <select value=${line} onChange=${(e) => { setLine(e.target.value); if (!title) setTitle(e.target.value); }}>
      <option value="">${t("rmap.project.line")}</option>
      ${lines.map((l) => html`<option value=${l}>${l}</option>`)}
    </select>
    <button class="btn primary" disabled=${!title.trim()}>${t("rmap.project.create")}</button>
    ${onCancel && html`<button type="button" class="btn ghost" onClick=${onCancel}>${t("rmap.project.cancel")}</button>`}
  </form>`;
}

// Research lines without a map yet: let the assistant draft one, or draft it straight from the note.
export const seedPrompt = (l) => `用研究地图工具为研究线「${l.line || l.name}」起草第一版地图：先 map_seed_draft("${l.line || l.name}") 拿到骨架，`
  + `再读研究线笔记（${l.path}）和相关的 Evidence 笔记，把标题改短、按最新状态改节点状态、补上已做的实验和结果、挂上证据，`
  + `然后用 map_propose(new_project=…) 提交给我确认。`;

function SeedLines({ toast, onAsk, onSeeded, compact }) {
  const [lines, setLines] = useState(null);
  const [busy, setBusy] = useState(null);
  useEffect(() => { api("/api/rmap/lines").then((r) => setLines(r.lines.filter((l) => !l.map))).catch(() => setLines([])); }, []);
  if (!lines?.length) return null;
  const direct = async (l) => {
    setBusy(l.path);
    try { await api("/api/rmap/seed", { json: { line: l.path } }); onSeeded(); toast(t("rmap.seed.ready")); }
    catch (e) { toast(e.message); } finally { setBusy(null); }
  };
  return html`<div class=${"mp-seed" + (compact ? " compact" : "")}>
    <div class="mp-seed-h">${t("rmap.seed.title")}</div>
    ${lines.map((l) => html`<div class="mp-seed-row" key=${l.path}>
      <span class="mp-seed-n">${l.line || l.name}</span>
      ${onAsk && html`<button class="btn sm" onClick=${() => onAsk(seedPrompt(l), t("rmap.seed.ask.label"))}><${Icon} name="sparkle" sm />${t("rmap.seed.ask")}</button>`}
      <button class="btn ghost sm" disabled=${busy === l.path} onClick=${() => direct(l)}>${t("rmap.seed.direct")}</button>
    </div>`)}
  </div>`;
}

function Legend({ ctx }) {
  const layers = rmap.list("layer").filter((l) => l.legend && (ctx.layers[l.id] ?? l.default));
  return html`<div class="mp-legend">
    ${STATUS_LEGEND.map((s) => html`<span class=${"st-" + s}><i />${t("rmap.status." + s)}</span>`)}
    <span class="mp-lg-e k-depends"><svg width="22" height="8"><path d="M1 4h20" /></svg>${t("rmap.edge.depends")}</span>
    <span class="mp-lg-e k-alternative"><svg width="22" height="8"><path d="M1 4h20" /></svg>${t("rmap.edge.alternative")}</span>
    <span class="mp-lg-e k-conflicts"><svg width="22" height="8"><path d="M1 4h20" /></svg>${t("rmap.edge.conflicts")}</span>
    ${layers.map((l) => html`<span key=${l.id}>${l.legend(ctx)}</span>`)}
    <span class="mp-lg-keys">${t("rmap.keys")}</span>
  </div>`;
}

export function RmapPage({ version, toast, lines = [], open, onAsk, onOpenNote, onOpenPaper }) {
  useRegistry();
  const [projects, setProjects] = useState(null);
  const [meta, setMeta] = useState(null);
  const [active, setActive] = useState(open || prefs.get("rmap:active", null)); // project slug, or "view:<id>"
  const [map, setMap] = useState(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  const [adding, setAdding] = useState(false);
  const [layers, setLayers] = useState(prefs.get("rmap:layers", {}));
  const [focus, setFocus] = useState(null);
  const [collapsed, setCollapsed] = useState(new Set());
  const [pending, setPending] = useState(null); // a node just added, to rename in place once it is drawn
  const [propV, setPropV] = useState(0); // bumped when a proposal is made from this page
  const [side, setSide] = useState("right"); // where the floating detail panel goes in a narrow page: away from the node
  const [ready, setReady] = useState(false); // extensions loaded: they hear the first "load" too
  const lastSeq = useRef({}); // slug -> this Mac's ingest sequence already handed to "change" subscribers
  const [waiting, setWaiting] = useState({}); // slug -> proposals waiting for a confirm, for the project switcher

  useEffect(() => {
    ensureCss("/rmap/rmap.css");
    Promise.all([loadExtensions(api), api("/api/rmap/meta")])
      .then(([, m]) => { setMeta(m); setReady(true); }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { if (open) setActive(open); }, [open]);
  const loadProjects = useCallback(() => api("/api/rmap/projects").then((r) => { setProjects(r.projects); return r.projects; }), []);
  useEffect(() => { loadProjects().catch((e) => setError(e.message)); }, [version]);

  const views = rmap.list("view");
  const view = active?.startsWith("view:") ? rmap.get("view", active.slice(5)) : null;
  const slug = !view && projects ? (projects.some((p) => p.slug === active) ? active : projects[0]?.slug || null) : null;
  useEffect(() => { if (active) prefs.set("rmap:active", active); }, [active]);
  useEffect(() => prefs.set("rmap:layers", layers), [layers]);
  // collapse is remembered per map on this Mac; focus is for the moment
  useEffect(() => { setCollapsed(new Set(slug ? prefs.get("rmap:collapsed:" + slug, []) : [])); setFocus(null); }, [slug]);
  useEffect(() => { if (slug) prefs.set("rmap:collapsed:" + slug, [...collapsed]); }, [collapsed]);

  const loadMap = useCallback(async (s) => {
    try {
      const m = await api(base(s));
      setMap((old) => (old?.slug === s && old.stamp === m.stamp ? old : m)); setError("");
      if (lastSeq.current[s] == null) lastSeq.current[s] = m.seq;
      rmap.emit("load", { slug: s, map: m });
      return m;
    } catch (e) { setError(e.message); return null; }
  }, []);
  useEffect(() => { setMap(null); setSelected(null); if (slug && ready) loadMap(slug); }, [slug, ready]);
  useEffect(() => { if (focus && map && !map.nodes.some((n) => n.id === focus)) setFocus(null); }, [map]);
  useEffect(() => {
    api("/api/rmap/proposals").then((r) => setWaiting(r.proposals.reduce((c, p) => (p.slug ? { ...c, [p.slug]: (c[p.slug] || 0) + 1 } : c), {})))
      .catch(() => {});
  }, [version, propV, map?.stamp]);

  // Live refresh: the files changed (an edit here, the CLI, or the other Mac through Syncthing). "change" gets
  // every event this Mac has seen since, by its ingest sequence: late-synced ones from an offline Mac too.
  const refresh = useCallback(async () => {
    if (!slug) return;
    const m = await loadMap(slug);
    if (!m) return;
    const after = lastSeq.current[slug] ?? m.seq;
    let events = [], page = { next: null };
    do {
      page = await api(`${base(slug)}/events?after=${after}&limit=1000${page.next ? "&before=" + encodeURIComponent(page.next) : ""}`)
        .catch(() => ({ events: [], next: null, seq: after }));
      events = events.concat(page.events);
      lastSeq.current[slug] = Math.max(lastSeq.current[slug] ?? 0, page.seq ?? after);
    } while (page.next);
    if (events.length) rmap.emit("change", { slug, events });
  }, [slug]);
  useStamp(slug && `${base(slug)}/stamp`, map?.stamp, refresh);

  // One batch of changes; then the map is re-read and "change" subscribers get the new events.
  const apply = useCallback(async (ops, summary) => {
    const r = await api(`${base(slug)}/batch`, { json: { ops, summary } });
    await refresh();
    return r;
  }, [slug, refresh]);
  const select = useCallback((id) => { setSelected(id); rmap.emit("select", { slug, node: id }); }, [slug]);
  const pick = (key) => { setActive(key); if (key.startsWith("view:")) rmap.emit("view", { id: key.slice(5) }); };
  const fold = useCallback((id) => setCollapsed((c) => { const x = new Set(c); if (x.has(id)) x.delete(id); else x.add(id); return x; }), []);

  const ctx = useMemo(() => meta && {
    slug, map, meta, projects: projects || [], selected, select, layers, t, toast, onAsk, onOpenNote, onOpenPaper, version,
    view: { focus, setFocus, collapsed, setCollapsed, fold, pending, setPending, setSide },
    api: { apply, refresh, base: slug ? base(slug) : null, call: api },
  }, [slug, map, meta, projects, selected, layers, apply, refresh, focus, collapsed, pending, version]);

  const created = async (p) => { setAdding(false); await loadProjects(); setActive(p.slug); };
  const applied = async (s) => { const ps = await loadProjects(); if (s && ps.some((p) => p.slug === s)) setActive(s); if (s === slug) refresh(); };

  if (!projects || !meta || !ready) return html`<div class="page rmap"><div class="page-inner wide"><p class="muted">${error || t("rmap.loading")}</p></div></div>`;

  if (!projects.length && !views.length) return html`<div class="page rmap"><div class="page-inner wide">
    <div class="page-head"><h1>${t("rmap.title")}</h1></div>
    <${Proposals} slug=${null} map=${null} version=${version + ":" + propV} toast=${toast} onApplied=${applied} />
    <${Empty} icon="topology" title=${t("rmap.empty.title")} hint=${t("rmap.empty.hint")}>
      <${NewProject} lines=${lines} onCreated=${created} toast=${toast} /></${Empty}>
    <${SeedLines} toast=${toast} onAsk=${onAsk} onSeeded=${() => setPropV((n) => n + 1)} />
  </div></div>`;

  const layerList = rmap.list("layer");
  const tools = rmap.list("tool");
  const kpis = rmap.list("kpi");
  return html`<div class="page rmap"><div class="page-inner wide">
    <div class="page-head">
      <div><h1>${t("rmap.title")}</h1>
        ${map && html`<div class="sub"><span>${map.project.title}</span>${map.project.line && html`<span class="tag">${map.project.line}</span>`}
          <span class="mp-num">${t("rmap.nodes", { n: map.nodes.length })}</span>
          ${map.project.note && html`<button class="mp-link" onClick=${() => onOpenNote?.(map.project.note)}><${Icon} name="note" sm />${t("rmap.project.note")}</button>`}</div>`}</div>
      <div class="mp-tools">
        <div class="seg">
          ${views.map((v) => html`<button class=${active === "view:" + v.id ? "on" : ""} onClick=${() => pick("view:" + v.id)}>${t(v.label)}</button>`)}
          ${projects.map((p) => html`<button class=${!view && slug === p.slug ? "on" : ""} onClick=${() => pick(p.slug)}
            title=${waiting[p.slug] ? t("rmap.prop.waiting", { n: waiting[p.slug] }) : p.warnings?.some((w) => w.kind === "same_title") ? t("rmap.same.title") : null}>${p.title}${waiting[p.slug] > 0 && html`<i class="mp-wait">${waiting[p.slug]}</i>`}</button>`)}
        </div>
        <button class="btn ghost sm" title=${t("rmap.project.new")} onClick=${() => setAdding(!adding)}><${Icon} name="plus" sm /></button>
        ${ctx && layerList.map((l) => html`<button class=${"mp-layer" + (layers[l.id] ?? l.default ? " on" : "")} style=${l.accent ? { "--acc": l.accent } : null}
            onClick=${() => setLayers({ ...layers, [l.id]: !(layers[l.id] ?? l.default) })}>${t(l.label)}</button>`)}
        ${ctx && tools.map((tl) => tl.render ? html`<span key=${tl.id}>${tl.render(ctx)}</span>`
          : html`<button class="btn ghost sm" key=${tl.id} title=${t(tl.label)} onClick=${() => tl.run(ctx)}>${tl.icon && html`<${Icon} name=${tl.icon} sm />`}${!tl.icon && t(tl.label)}</button>`)}
      </div>
    </div>
    ${adding && html`<${NewProject} lines=${lines} onCreated=${created} onCancel=${() => setAdding(false)} toast=${toast} />`}
    ${adding && html`<${SeedLines} compact toast=${toast} onAsk=${onAsk} onSeeded=${() => { setAdding(false); setPropV((n) => n + 1); }} />`}
    <${Proposals} slug=${slug} map=${map} version=${version + ":" + propV} toast=${toast} onApplied=${applied} />
    ${view ? view.render(ctx) : error ? html`<p class="muted">${t("rmap.error", { msg: error })}</p>` : !map ? html`<p class="muted">${t("rmap.loading")}</p>` : html`
      ${map.warnings.filter((w) => w.kind === "cycle").map((w) => html`<div class="mp-warn" key="cycle">${t("rmap.cycle.warn", { n: w.edges.length })}</div>`)}
      ${kpis.length > 0 && html`<div class="kpis mp-kpis">${kpis.map((k) => html`<div class="kpi" key=${k.id}>${k.render(ctx)}</div>`)}</div>`}
      <div class=${"mp-body-grid side-" + side}>
        <div class="mp-canvas"><${MapView} ctx=${ctx} /><${Legend} ctx=${ctx} /></div>
        <${Detail} ctx=${ctx} />
      </div>`}
  </div></div>`;
}

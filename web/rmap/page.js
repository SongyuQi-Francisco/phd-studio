// Research map page shell (研究地图): project switcher + registered views, toolbar, KPI row, the map,
// the node detail panel, live refresh, and the extension loader. Extensions plug in through
// /rmap/registry.js; every hook gets the same `ctx` (see docs/research-map.md).
import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, api, prefs, renderMarkdown } from "/lib.js";
import { Empty } from "/ui.js";
import { rmap, loadExtensions } from "/rmap/registry.js";
import { safeUrl } from "/rmap/util.js";
import { t } from "/rmap/strings.js";

const POLL_MS = 2000;
const base = (slug) => `/api/rmap/projects/${encodeURIComponent(slug)}`;

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

// Poll the map's change stamp (cheap: file sizes and times) while the page is visible; onChange gets the new stamp.
export function useStamp(url, stamp, onChange) {
  const cb = useRef(onChange);
  cb.current = onChange;
  useEffect(() => {
    if (!url || !stamp) return;
    let busy = false;
    const tick = async () => {
      if (busy || document.hidden) return;
      busy = true;
      try { const r = await api(url); if (r.stamp !== stamp) cb.current(r.stamp); } catch {} finally { busy = false; }
    };
    const id = setInterval(tick, POLL_MS);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, [url, stamp]);
}

export const statusLabel = (s) => t("rmap.status." + (s || "todo"));
export const typeLabel = (k) => t("rmap.type." + k);

function NewProject({ lines, onCreated, onCancel, toast }) {
  const [title, setTitle] = useState("");
  const [line, setLine] = useState("");
  const create = async (e) => {
    e.preventDefault();
    try { onCreated(await api("/api/rmap/projects", { json: { title, line: line || null } })); } catch (err) { toast(err.message); }
  };
  return html`<form class="mp-new" onSubmit=${create}>
    <input autoFocus placeholder=${t("rmap.project.title")} value=${title} onInput=${(e) => setTitle(e.target.value)} />
    <select value=${line} onChange=${(e) => setLine(e.target.value)}>
      <option value="">${t("rmap.project.line")}</option>
      ${lines.map((l) => html`<option value=${l}>${l}</option>`)}
    </select>
    <button class="btn" disabled=${!title.trim()}>${t("rmap.project.create")}</button>
    ${onCancel && html`<button type="button" class="btn ghost" onClick=${onCancel}>${t("rmap.project.cancel")}</button>`}
  </form>`;
}

// Placeholder map: one column per node type. The SVG map view replaces it (it lays nodes out, draws edges and layers).
function TypeColumns({ ctx }) {
  const cols = ctx.meta.node_types.filter((nt) => ctx.map.nodes.some((n) => n.type === nt.key));
  return html`<div class="mp-cols">
    ${cols.map((nt) => html`<div class="mp-col"><div class="mp-cap">${typeLabel(nt.key)}</div>
      ${ctx.map.nodes.filter((n) => n.type === nt.key).map((n) => html`<button class=${"mp-card st-" + (n.status || "todo") + (ctx.selected === n.id ? " sel" : "")}
          onClick=${() => ctx.select(n.id)}>
        <b>${n.title}</b><span class="mp-st"><i />${statusLabel(n.status)}</span></button>`)}
    </div>`)}
  </div>`;
}

export function Evidence({ node, ctx }) {
  const items = (ctx.map.attachments.evidence || []).filter((a) => a.node === node.id);
  if (!items.length) return null;
  const open = (a) => {
    if (a.type === "url") { const u = safeUrl(a.target); if (u) window.open(u, "_blank", "noopener"); }
    else if (a.type === "paper") ctx.onOpenPaper?.(a.target);
    else ctx.onOpenNote?.(a.target);
  };
  return html`<div class="mp-sec"><h4>${t("rmap.evidence")}<small>${items.length}</small></h4>
    <div class="mp-chips">${items.map((a) => html`<button class=${"mp-chip " + (a.stance || "supports")} title=${a.note || a.target} onClick=${() => open(a)}>
      <${Icon} name=${a.type === "url" ? "external" : a.type === "paper" ? "book" : "note"} sm />${a.title || a.target}</button>`)}</div>
  </div>`;
}

function Detail({ ctx }) {
  const node = ctx.map.nodes.find((n) => n.id === ctx.selected);
  if (!node) return html`<aside class="mp-detail empty"><span class="muted">${t("rmap.select.hint")}</span></aside>`;
  return html`<aside class="mp-detail">
    <div class="mp-dh"><span class="mp-type">${typeLabel(node.type)}</span>
      <span class=${"mp-pill st-" + (node.status || "todo")}><i />${statusLabel(node.status)}</span></div>
    <h3>${node.title}</h3>
    ${node.body && html`<div class="md mp-body" dangerouslySetInnerHTML=${{ __html: renderMarkdown(node.body) }} />`}
    <${Evidence} node=${node} ctx=${ctx} />
    ${rmap.list("section").filter((s) => !s.when || s.when(node, ctx)).map((s) => html`<div class="mp-sec" key=${s.id}>
      ${s.label && html`<h4>${t(s.label)}</h4>`}${s.render(node, ctx)}</div>`)}
  </aside>`;
}

// The map area. Exported so the map view can wrap the same shell; `Canvas` defaults to the placeholder.
export function RmapPage({ version, toast, lines = [], onAsk, onOpenNote, onOpenPaper, Canvas = TypeColumns }) {
  useRegistry();
  const [projects, setProjects] = useState(null);
  const [meta, setMeta] = useState(null);
  const [active, setActive] = useState(prefs.get("rmap:active", null)); // project slug, or "view:<id>"
  const [map, setMap] = useState(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  const [adding, setAdding] = useState(false);
  const [layers, setLayers] = useState(prefs.get("rmap:layers", {}));
  const [ready, setReady] = useState(false); // extensions loaded: they hear the first "load" too
  const lastSeq = useRef({}); // slug -> this Mac's ingest sequence already handed to "change" subscribers

  useEffect(() => {
    ensureCss("/rmap/rmap.css");
    Promise.all([loadExtensions(api), api("/api/rmap/meta")])
      .then(([, m]) => { setMeta(m); setReady(true); }).catch((e) => setError(e.message));
  }, []);
  const loadProjects = useCallback(() => api("/api/rmap/projects").then((r) => { setProjects(r.projects); return r.projects; }), []);
  useEffect(() => { loadProjects().catch((e) => setError(e.message)); }, [version]);

  const views = rmap.list("view");
  const view = active?.startsWith("view:") ? rmap.get("view", active.slice(5)) : null;
  const slug = !view && projects ? (projects.some((p) => p.slug === active) ? active : projects[0]?.slug || null) : null;
  useEffect(() => { if (active) prefs.set("rmap:active", active); }, [active]);
  useEffect(() => prefs.set("rmap:layers", layers), [layers]);

  const loadMap = useCallback(async (s) => {
    try {
      const m = await api(base(s));
      setMap(m); setError("");
      if (lastSeq.current[s] == null) lastSeq.current[s] = m.seq;
      rmap.emit("load", { slug: s, map: m });
      return m;
    } catch (e) { setError(e.message); return null; }
  }, []);
  useEffect(() => { setMap(null); setSelected(null); if (slug && ready) loadMap(slug); }, [slug, ready]);

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

  // One batch of changes; the result's events go to "change" subscribers right away.
  const apply = useCallback(async (ops, summary) => {
    const r = await api(`${base(slug)}/batch`, { json: { ops, summary } });
    await refresh();
    return r;
  }, [slug, refresh]);
  const select = useCallback((id) => { setSelected(id); rmap.emit("select", { slug, node: id }); }, [slug]);
  const pick = (key) => { setActive(key); if (key.startsWith("view:")) rmap.emit("view", { id: key.slice(5) }); };

  const ctx = useMemo(() => meta && {
    slug, map, meta, projects: projects || [], selected, select, layers, t, toast, onAsk, onOpenNote, onOpenPaper,
    api: { apply, refresh, base: slug ? base(slug) : null, call: api },
  }, [slug, map, meta, projects, selected, layers, apply, refresh]);

  if (!projects || !meta || !ready) return html`<div class="page rmap"><div class="page-inner wide"><p class="muted">${error || t("rmap.loading")}</p></div></div>`;

  const created = async (p) => { setAdding(false); await loadProjects(); setActive(p.slug); };
  if (!projects.length && !views.length) return html`<div class="page rmap"><div class="page-inner wide">
    <div class="page-head"><h1>${t("rmap.title")}</h1></div>
    <${Empty} icon="compass" title=${t("rmap.empty.title")} hint=${t("rmap.empty.hint")}>
      <${NewProject} lines=${lines} onCreated=${created} toast=${toast} /></${Empty}>
  </div></div>`;

  const layerList = rmap.list("layer");
  const tools = rmap.list("tool");
  const kpis = rmap.list("kpi");
  return html`<div class="page rmap"><div class="page-inner wide">
    <div class="page-head">
      <div><h1>${t("rmap.title")}</h1>
        ${map && html`<div class="sub"><span>${map.project.title}</span>${map.project.line && html`<span class="tag">${map.project.line}</span>`}
          <span>${t("rmap.nodes", { n: map.nodes.length })}</span></div>`}</div>
      <div class="mp-tools">
        <div class="seg">
          ${views.map((v) => html`<button class=${active === "view:" + v.id ? "on" : ""} onClick=${() => pick("view:" + v.id)}>${t(v.label)}</button>`)}
          ${projects.map((p) => html`<button class=${!view && slug === p.slug ? "on" : ""} onClick=${() => pick(p.slug)}>${p.title}</button>`)}
        </div>
        <button class="btn ghost sm" title=${t("rmap.project.new")} onClick=${() => setAdding(!adding)}><${Icon} name="plus" sm /></button>
        ${ctx && layerList.map((l) => html`<button class=${"mp-layer" + (layers[l.id] ?? l.default ? " on" : "")} style=${l.accent ? { "--acc": l.accent } : null}
            onClick=${() => setLayers({ ...layers, [l.id]: !(layers[l.id] ?? l.default) })}>${t(l.label)}</button>`)}
        ${ctx && tools.map((tl) => tl.render ? html`<span key=${tl.id}>${tl.render(ctx)}</span>`
          : html`<button class="btn ghost sm" key=${tl.id} title=${t(tl.label)} onClick=${() => tl.run(ctx)}>${tl.icon && html`<${Icon} name=${tl.icon} sm />`}${!tl.icon && t(tl.label)}</button>`)}
      </div>
    </div>
    ${adding && html`<${NewProject} lines=${lines} onCreated=${created} onCancel=${() => setAdding(false)} toast=${toast} />`}
    ${view ? view.render(ctx) : error ? html`<p class="muted">${t("rmap.error", { msg: error })}</p>` : !map ? html`<p class="muted">${t("rmap.loading")}</p>` : html`
      ${kpis.length > 0 && html`<div class="kpis mp-kpis">${kpis.map((k) => html`<div class="kpi" key=${k.id}>${k.render(ctx)}</div>`)}</div>`}
      <div class="mp-body-grid">
        <div class="mp-canvas"><${Canvas} ctx=${ctx} /></div>
        <${Detail} ctx=${ctx} />
      </div>`}
  </div></div>`;
}

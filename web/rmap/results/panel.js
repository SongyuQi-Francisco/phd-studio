// The results panel: a sheet over the research map with the comparison (configs against the baselines), the
// trend per config, the datasets side by side and every record, for one experiment node or all of them.
// Adding a record by hand, importing a table and the watched files live here too. Opened from the detail
// panel's results section and from the toolbar.
// i18n: done
import { render } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, prefs } from "/lib.js";
import { t } from "/i18n.js";
import { Compare, Grouped, Trend } from "/rmap/results/charts.js";
import { compare, datasetsOf, dayOf, dirOf, fmt, metricsOf, newestFirst, pick, trend } from "/rmap/results/data.js";
import { ImportDialog } from "/rmap/results/import.js";

export const TYPES = ["experiment", "result"];
const today = () => new Date().toLocaleDateString("sv"); // i18n-ignore (YYYY-MM-DD, a machine format)
const SHOWN = 25;

// Renders its children into <body>: the page root is a layout container, which would trap a fixed sheet.
export function Portal({ children }) {
  const host = useRef(null);
  if (!host.current) host.current = document.createElement("div");
  useEffect(() => { document.body.appendChild(host.current); return () => { render(null, host.current); host.current.remove(); }; }, []);
  useEffect(() => { render(children, host.current); });
  return null;
}

function AddForm({ ctx, node, records, onDone }) {
  const [f, setF] = useState({ config: "", metric: "", dataset: "", value: "", date: today(), baseline: false });
  const [busy, setBusy] = useState(false);
  const configs = [...new Set(records.map((r) => r.config))];
  const names = [...new Set(records.map((r) => r.metric))];
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const ok = f.config.trim() && f.metric.trim() && f.value.trim() !== "";
  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const fields = { config: f.config.trim(), metric: f.metric.trim(), value: f.value.trim(), date: f.date || null,
        dataset: f.dataset.trim() || null, baseline: f.baseline || null, source: "page" };
      await ctx.api.apply([{ op: "attach.add", kind: "results", node: node.id, set: fields }]);
      setF({ ...f, value: "" });
    } catch (err) { ctx.toast(err.message); } finally { setBusy(false); }
  };
  return html`<form class="rs-add" onSubmit=${save} onKeyDown=${(e) => e.key === "Escape" && onDone()}>
    <input list="rs-configs" placeholder=${t("rmap_results.form.config")} value=${f.config} onInput=${set("config")} />
    <input class="rs-mono" list="rs-metrics" placeholder=${t("rmap_results.form.metric")} value=${f.metric} onInput=${set("metric")} />
    <input class="rs-mono" inputmode="decimal" placeholder=${t("rmap_results.form.value")} value=${f.value} onInput=${set("value")} />
    <input placeholder=${t("rmap_results.form.dataset")} value=${f.dataset} onInput=${set("dataset")} />
    <input type="date" value=${f.date} onInput=${set("date")} />
    <label class="rs-check"><input type="checkbox" checked=${f.baseline} onChange=${set("baseline")} />${t("rmap_results.form.baseline")}</label>
    <datalist id="rs-configs">${configs.map((c) => html`<option value=${c} />`)}</datalist>
    <datalist id="rs-metrics">${names.map((c) => html`<option value=${c} />`)}</datalist>
    <div class="row-end"><button type="button" class="btn ghost sm" onClick=${onDone}>${t("rmap.cancel")}</button>
      <button class="btn primary sm" disabled=${!ok || busy}>${t("rmap.add")}</button></div>
  </form>`;
}

// The files a node reads its results from: path, the Mac that watches it, re-read now.
function Sources({ ctx, node, onImport }) {
  const [status, setStatus] = useState({ running: false, errors: {} });
  const [busy, setBusy] = useState(null);
  const list = (ctx.map.attachments.result_sources || []).filter((s) => s.node === node.id);
  const load = () => ctx.api.call(`${ctx.api.base}/results/status`).then(setStatus).catch(() => {});
  useEffect(() => { load(); }, [ctx.map.stamp]);
  if (!list.length) return null;
  const watch = (s, on) => ctx.api.apply([{ op: "attach.update", kind: "result_sources", id: s.id, set: { watch: on || null, device: on ? status.device : null } }])
    .then(load).catch((e) => ctx.toast(e.message));
  const now = async (s) => {
    setBusy(s.id);
    try { await ctx.api.call(`${ctx.api.base}/results/import`, { json: { node: node.id, path: s.path, mapping: s.mapping, remember: false } }); await ctx.api.refresh(); }
    catch (e) { ctx.toast(e.message); } finally { setBusy(null); }
  };
  return html`<div class="rs-sources"><h5>${t("rmap_results.sources")}</h5>
    ${list.map((s) => html`<div class="rs-src" key=${s.id}>
      <span class="rs-path rs-mono" title=${s.path}>${s.path}</span>
      ${status.errors?.[s.id]?.error && html`<span class="rs-err" title=${status.errors[s.id].error}>${t("rmap_results.src.error")}</span>`}
      <button class="btn ghost sm" disabled=${busy === s.id} onClick=${() => now(s)} title=${t("rmap_results.src.read")}><${Icon} name="refresh" sm /></button>
      <label class="rs-check" title=${s.watch && s.device ? t("rmap_results.src.watched_by", { device: s.device }) : ""}>
        <input type="checkbox" checked=${!!s.watch} onChange=${(e) => watch(s, e.target.checked)} />${t("rmap_results.src.watch")}</label>
    </div>`)}</div>`;
}

export function ResultsSheet({ ctx, nodeId, mode, onClose }) {
  const all = ctx.map.attachments.results || [];
  const nodes = ctx.map.nodes.filter((n) => TYPES.includes(n.type));
  const [scope, setScope] = useState(nodeId || "");
  const [form, setForm] = useState(mode === "add" ? "add" : mode === "import" ? "import" : null);
  const [metric, setMetric] = useState(() => (nodeId ? prefs.get("rmap:rs:" + nodeId, null) : null));
  const [dataset, setDataset] = useState(null);
  const [more, setMore] = useState(false);
  const records = useMemo(() => (scope ? all.filter((r) => r.node === scope) : all), [all, scope]);
  const node = scope ? ctx.map.nodes.find((n) => n.id === scope) : null;
  const metrics = metricsOf(records);
  const m = metrics.find((x) => x.metric.toLowerCase() === (metric || "").toLowerCase())?.metric || metrics[0]?.metric || null;
  const sets = m ? datasetsOf(records, m) : [];
  const d = sets.includes(dataset) ? dataset : sets[0] ?? null;
  const cmp = m ? compare(records, m, d) : null;
  const tr = m ? trend(records, m, d) : null;
  const rows = m ? newestFirst(pick(records, m)) : [];
  const titles = Object.fromEntries(ctx.map.nodes.map((n) => [n.id, n.title]));
  useEffect(() => {
    const esc = (e) => { if (e.key === "Escape" && !form) onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [form]);
  useEffect(() => { if (nodeId) prefs.set("rmap:rs:" + nodeId, m); }, [m]);

  const counts = {};
  for (const r of all) counts[r.node] = (counts[r.node] || 0) + 1;
  const fail = (e) => ctx.toast(e.message);
  const remove = (r) => ctx.api.apply([{ op: "attach.delete", kind: "results", id: r.id }]).catch(fail);
  // a baseline is a property of the config: flip it on every record of that config on this metric's node
  const toggleBase = (r) => {
    const on = !r.baseline;
    const same = all.filter((x) => x.node === r.node && x.config === r.config && !!x.baseline !== on);
    ctx.api.apply(same.map((x) => ({ op: "attach.update", kind: "results", id: x.id, set: { baseline: on || null } }))).catch(fail);
  };
  const flipDir = () => {
    const to = cmp.dir === "lower" ? "higher" : "lower";
    const same = pick(records, m).filter((x) => dirOf(x) !== to);
    ctx.api.apply(same.map((x) => ({ op: "attach.update", kind: "results", id: x.id, set: { better: to } }))).catch(fail);
  };
  const cfgs = cmp ? cmp.rows : [];
  const value = (config, ds) => compare(records, m, ds).rows.find((r) => r.config === config)?.value;

  return html`<${Portal}><div class="rmap rs-bg" onMouseDown=${(e) => e.target === e.currentTarget && !form && onClose()}>
    <div class="rs-sheet" role="dialog" aria-label=${t("rmap_results.sheet")}>
      <div class="rs-top">
        <h2>${t("rmap_results.sheet")}</h2>
        <select class="rs-scope" value=${scope} onChange=${(e) => { setScope(e.target.value); setMetric(null); setDataset(null); }} aria-label=${t("rmap_results.scope")}>
          <option value="">${t("rmap_results.all_nodes")}</option>
          ${nodes.map((n) => html`<option value=${n.id}>${n.title}${counts[n.id] ? ` · ${counts[n.id]}` : ""}</option>`)}
        </select>
        <span class="grow" />
        ${node && html`<button class="btn sm" onClick=${() => setForm(form === "add" ? null : "add")}><${Icon} name="plus" sm />${t("rmap_results.add")}</button>
          <button class="btn sm" onClick=${() => setForm(form === "import" ? null : "import")}><${Icon} name="file" sm />${t("rmap_results.import")}</button>`}
        <button class="mp-close" title=${t("rmap.close")} onClick=${onClose}><${Icon} name="x" sm /></button>
      </div>

      ${form === "add" && node && html`<${AddForm} ctx=${ctx} node=${node} records=${all} onDone=${() => setForm(null)} />`}
      ${form === "import" && node && html`<${ImportDialog} ctx=${ctx} node=${node} onClose=${() => setForm(null)} />`}

      ${!records.length ? html`<div class="rs-empty"><p>${t(node ? "rmap_results.empty" : "rmap_results.empty_all")}</p>
          ${node && html`<code class="rs-cli">studio map result add "${ctx.map.project.title}" ${node.id} recall@10=0.412 --config "Ours"</code>`}</div>` : html`
        <div class="rs-controls">
          <div class="rs-chips" role="tablist">${metrics.map((x) => html`<button role="tab" class=${"rs-chip rs-mono" + (x.metric === m ? " on" : "")} key=${x.metric}
            aria-selected=${x.metric === m} onClick=${() => { setMetric(x.metric); setDataset(null); }}>${x.metric.toUpperCase()}<small>${x.n}</small></button>`)}</div>
          ${sets.length > 1 && html`<select class="rs-ds" value=${d} onChange=${(e) => setDataset(e.target.value)} aria-label=${t("rmap_results.col.dataset")}>
            ${sets.map((s) => html`<option value=${s}>${s || "—"}</option>`)}</select>`}
          <button class=${"rs-dirbtn" + (cmp?.dir === "lower" ? " on" : "")} aria-pressed=${cmp?.dir === "lower"} onClick=${flipDir} title=${t("rmap_results.dir.title")}>
            ${t("rmap_results.dir.lower_toggle")}</button>
        </div>
        <div class="rs-grid">
          <section class="rs-card"><h4>${t("rmap_results.compare")}<small class="rs-mono">${m.toUpperCase()}${d ? " · " + d : ""}</small></h4>
            <${Compare} data=${cmp} />
            ${sets.length > 1 && cfgs.length > 1 && html`<h4 class="rs-sub">${t("rmap_results.by_dataset")}</h4>
              <${Grouped} datasets=${sets} configs=${cfgs} value=${value} />`}</section>
          <section class="rs-card"><h4>${t("rmap_results.trend")}<small class="rs-mono">${m.toUpperCase()}${d ? " · " + d : ""}</small></h4>
            ${tr.dates.length > 1 ? html`<${Trend} data=${tr} />` : html`<p class="mp-quiet">${t("rmap_results.trend.one")}</p>`}</section>
        </div>
        <section class="rs-card rs-table-card"><h4>${t("rmap_results.records")}<small>${rows.length}</small></h4>
          <div class="rs-tablewrap"><table class="rs-table"><thead><tr>
            <th>${t("rmap_results.col.date")}</th><th>${t("rmap_results.col.config")}</th><th>${t("rmap_results.col.dataset")}</th>
            <th class="num">${t("rmap_results.col.value")}</th><th>${t("rmap_results.col.run")}</th>
            <th>${t("rmap_results.col.source")}</th>${!scope && html`<th>${t("rmap_results.col.node")}</th>`}<th /></tr></thead>
            <tbody>${(more ? rows : rows.slice(0, SHOWN)).map((r) => html`<tr key=${r.id}>
              <td class="rs-mono">${dayOf(r)}</td>
              <td><button class=${"rs-star" + (r.baseline ? " on" : "")} title=${t("rmap_results.baseline.toggle")} aria-pressed=${!!r.baseline} onClick=${() => toggleBase(r)}><${Icon} name="star" sm /></button>${r.config}</td>
              <td>${r.dataset || "—"}</td><td class="num rs-mono">${fmt(r.value)}</td><td class="rs-mono">${r.run || ""}</td>
              <td class="rs-src-cell" title=${r.source || ""}>${r.source ? r.source.split("/").pop() : ""}</td>
              ${!scope && html`<td class="rs-node" title=${titles[r.node]}>${titles[r.node] || ""}</td>`}
              <td><button class="mp-chip-x" title=${t("rmap_results.remove")} onClick=${() => remove(r)}><${Icon} name="x" sm /></button></td></tr>`)}</tbody></table></div>
          ${rows.length > SHOWN && html`<button class="mp-more" onClick=${() => setMore(!more)}>${more ? t("rmap.less") : t("rmap.more", { n: rows.length - SHOWN })}</button>`}
        </section>`}
      ${node && html`<${Sources} ctx=${ctx} node=${node} />`}
    </div>
  </div></${Portal}>`;
}

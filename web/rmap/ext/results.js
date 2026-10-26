// Experiment results (#48) on the research map: a results section in the detail panel of experiment and
// result nodes, a "latest value" chip on those nodes (a map layer), and a toolbar button for the results
// panel of the whole map. The data is the `results` attachment kind (app/rmap/plugins/results.py); the
// rest of the code is in web/rmap/results/.
// i18n: done
import { useState } from "preact/hooks";
import { html } from "/lib.js";
import { t } from "/i18n.js";
import { rmap } from "/rmap/registry.js";
import { fmt, headline } from "/rmap/results/data.js";
import { ResultsSheet, TYPES } from "/rmap/results/panel.js";
import { ChartIcon, Section } from "/rmap/results/section.js";

// the history lists name attachment kinds by "rmap.kind.<kind>", which only the core's own kinds have a key for
rmap.strings({
  zh: { "rmap.kind.results": "结果记录", "rmap.kind.result_sources": "结果文件" }, // i18n-ignore
  en: { "rmap.kind.results": "Results", "rmap.kind.result_sources": "Result files" },
});

rmap.section({
  id: "results", label: "rmap_results.title", order: 40,
  when: (n) => TYPES.includes(n.type),
  render: (n, ctx) => html`<${Section} node=${n} ctx=${ctx} />`,
});

function Tool({ ctx }) {
  const [open, setOpen] = useState(false);
  const total = (ctx.map?.attachments.results || []).length;
  const node = ctx.map?.nodes.find((n) => n.id === ctx.selected);
  return html`<button class="btn ghost sm" title=${t("rmap_results.tool.title")} onClick=${() => setOpen(true)}>
      <${ChartIcon} />${t("rmap_results.tool")}${total > 0 && html`<span class="rs-count mp-num">${total}</span>`}</button>
    ${open && html`<${ResultsSheet} ctx=${ctx} nodeId=${node && TYPES.includes(node.type) ? node.id : ""} onClose=${() => setOpen(false)} />`}`;
}
rmap.tool({ id: "results", label: "rmap_results.tool", order: 60, render: (ctx) => (ctx.map ? html`<${Tool} ctx=${ctx} />` : null) });

// per node: the latest value of its most recent metric, in the corner of the card
const byNode = new WeakMap();
function index(records) {
  if (!byNode.has(records)) {
    const out = {};
    for (const r of records) (out[r.node] ||= []).push(r);
    byNode.set(records, Object.fromEntries(Object.entries(out).map(([id, list]) => [id, headline(list)])));
  }
  return byNode.get(records);
}
rmap.layer({
  id: "results", label: "rmap_results.layer", order: 20, default: true, accent: "var(--mp-verified)",
  node: (n, box, ctx) => {
    const h = index(ctx.map.attachments.results || [])[n.id];
    if (!h) return null;
    return html`<g class="rs-nodechip" key=${n.id} transform=${`translate(${box.x + box.w - 8} ${box.y + box.h - 9})`}>
      <title>${h.metric.toUpperCase()} ${fmt(h.value)} · ${h.config}</title><text text-anchor="end">${fmt(h.value)}</text></g>`;
  },
  legend: () => html`<span class="rs-lg"><b class="mp-num">0.41</b>${t("rmap_results.layer.legend")}</span>`,
});

// the page asks once per map, which also starts the watcher for files this Mac was told to watch
const asked = new Set();
rmap.on("load", ({ slug }) => {
  if (asked.has(slug)) return;
  asked.add(slug);
  fetch(`/api/rmap/projects/${encodeURIComponent(slug)}/results/status`).catch(() => {});
});

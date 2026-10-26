// What the node's detail panel shows of its results: the metrics measured, the top configs against the
// baseline for one metric, a small trend line, and the way into the results panel.
// i18n: done
import { useEffect, useState } from "preact/hooks";
import { html, Icon, prefs } from "/lib.js";
import { t } from "/i18n.js";
import { C } from "/charts.js";
import { Compare, Spark } from "/rmap/results/charts.js";
import { compare, datasetsOf, metricsOf, spark } from "/rmap/results/data.js";
import { ResultsSheet } from "/rmap/results/panel.js";

export const ChartIcon = () => html`<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20V11M10 20V4M16 20v-6M22 20H2" /></svg>`;

export function Section({ node, ctx }) {
  const [open, setOpen] = useState(null); // null | "sheet" | "add" | "import"
  const [pick, setPick] = useState(() => prefs.get("rmap:rs:" + node.id, null));
  useEffect(() => { setOpen(null); setPick(prefs.get("rmap:rs:" + node.id, null)); }, [node.id]);
  const mine = (ctx.map.attachments.results || []).filter((r) => r.node === node.id);
  const metrics = metricsOf(mine);
  const m = metrics.find((x) => x.metric.toLowerCase() === (pick || "").toLowerCase())?.metric || metrics[0]?.metric || null;
  const d = m ? datasetsOf(mine, m)[0] : null;
  const cmp = m ? compare(mine, m, d) : null;
  const sp = m ? spark(mine, m, d) : null;
  const choose = (x) => { setPick(x); prefs.set("rmap:rs:" + node.id, x); };
  const sheet = open && html`<${ResultsSheet} ctx=${ctx} nodeId=${node.id} mode=${open} onClose=${() => setOpen(null)} />`;

  return html`<div class="rs-sec">
    <div class="rs-sec-h">
      ${metrics.length > 0 && html`<div class="rs-chips">${metrics.slice(0, 6).map((x) => html`<button class=${"rs-chip rs-mono" + (x.metric === m ? " on" : "")} key=${x.metric}
        onClick=${() => choose(x.metric)}>${x.metric.toUpperCase()}</button>`)}${metrics.length > 6 && html`<small class="mp-quiet">+${metrics.length - 6}</small>`}</div>`}
      <span class="grow" />
      <button class="mp-h-btn" title=${t("rmap_results.add")} onClick=${() => setOpen("add")}><${Icon} name="plus" sm /></button>
      <button class="mp-h-btn" title=${t("rmap_results.import")} onClick=${() => setOpen("import")}><${Icon} name="file" sm /></button>
    </div>
    ${!mine.length && html`<p class="mp-quiet">${t("rmap_results.empty_short")}</p>`}
    ${cmp && html`<${Compare} data=${cmp} limit=${4} />`}
    ${sp && sp.values.length > 1 && html`<div class="rs-spark"><span class="rs-mono">${m.toUpperCase()}</span><${Spark} values=${sp.values} labels=${sp.labels} color=${C.aqua} /></div>`}
    ${mine.length > 0 && html`<button class="mp-more" onClick=${() => setOpen("sheet")}><${ChartIcon} /> ${t("rmap_results.open", { n: mine.length })}</button>`}
    ${sheet}
  </div>`;
}

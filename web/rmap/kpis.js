// The core KPI row of a map: 深度, 推进中分支, 已验证 x/y, 空白分支. Registered like any extension's tile, so a
// later phase can replace one by registering the same id (the peer layer redefines "blank" with peers).
import { useEffect, useState } from "preact/hooks";
import { html } from "/lib.js";
import { C, Ring, Sparkline } from "/charts.js";
import { rmap } from "/rmap/registry.js";
import { t } from "/rmap/strings.js";
import { layoutMap } from "/rmap/layout.js";

const COLS = ["question", "hypothesis", "subline", "experiment", "result"];
export const BRANCH = ["hypothesis", "subline"];
const VERIFIABLE = ["hypothesis", "subline", "experiment", "result"];
const WEEKS = 8;

const Tile = ({ label, value, unit, sub, children }) => html`
  <div class="kpi-label">${label}</div>
  <div class="kpi-value mp-num">${value}${unit && html`<small>${unit}</small>`}</div>
  ${sub && html`<div class="kpi-sub">${sub}</div>`}
  ${children && html`<div class="kpi-viz">${children}</div>`}`;

// How far the map reaches: one segment per column, lit when a live node is there, brighter when one is verified.
function Depth({ ctx }) {
  const live = ctx.map.nodes.filter((n) => !ctx.meta.faded.includes(n.status));
  const cols = layoutMap({ ...ctx.map, nodes: live }, ctx.meta).boxes;
  const reached = Math.max(-1, ...Object.values(cols).map((b) => b.col));
  const at = (c) => live.filter((n) => cols[n.id]?.col === c);
  const segs = Array.from({ length: Math.max(5, reached + 1) }, (_, c) => (at(c).some((n) => n.status === "verified") ? "on" : at(c).length ? "half" : ""));
  const deepest = reached >= 0 ? (COLS[reached] ? t("rmap.col." + COLS[reached]) : `L${reached + 1}`) : "—";
  return html`<${Tile} label=${t("rmap.kpi.depth")} value=${reached + 1} unit=${"/" + segs.length} sub=${t("rmap.kpi.depth.sub", { col: deepest })}>
    <div class="mp-segs">${segs.map((s) => html`<i class=${s} />`)}</div></${Tile}>`;
}

// Status of each node at the end of each of the last weeks, rebuilt from the change log.
export function weeklyActive(map, events, types, weeks = WEEKS) {
  const ends = Array.from({ length: weeks }, (_, i) => { const d = new Date(); d.setHours(23, 59, 59, 0); d.setDate(d.getDate() - 7 * (weeks - 1 - i)); return d; });
  const hist = new Map(); // node id -> [[time, status]]
  for (const ev of [...events].reverse()) {
    if (ev.kind !== "node") continue;
    const at = new Date(ev.at);
    if (ev.action === "add") hist.set(ev.target, [[at, ev.record?.status || "todo", ev.record?.type]]);
    else if (ev.action === "update" && ev.changes?.status) (hist.get(ev.target) || hist.set(ev.target, []).get(ev.target)).push([at, ev.changes.status[1]]);
    else if (ev.action === "delete") (hist.get(ev.target) || hist.set(ev.target, []).get(ev.target)).push([at, null]);
  }
  for (const n of map.nodes) if (!hist.has(n.id)) hist.set(n.id, [[new Date(n.created_at || 0), n.status || "todo", n.type]]);
  const typeOf = new Map(map.nodes.map((n) => [n.id, n.type]));
  return ends.map((end) => {
    let count = 0;
    for (const [id, h] of hist) {
      const type = typeOf.get(id) || h[0][2];
      if (!types.includes(type)) continue;
      const last = h.filter(([at]) => at <= end).pop();
      if (last && last[1] === "active") count += 1;
    }
    return count;
  });
}

function Active({ ctx }) {
  const [events, setEvents] = useState(null);
  useEffect(() => {
    ctx.api.call(`${ctx.api.base}/events?limit=1000&kind=node`).then((r) => setEvents(r.events)).catch(() => setEvents([]));
  }, [ctx.slug, ctx.map.stamp]);
  const now = ctx.map.nodes.filter((n) => BRANCH.includes(n.type) && n.status === "active").length;
  const series = events ? weeklyActive(ctx.map, events, BRANCH) : [];
  return html`<${Tile} label=${t("rmap.kpi.active")} value=${now} sub=${t("rmap.kpi.active.sub", { n: ctx.map.nodes.filter((n) => BRANCH.includes(n.type)).length })}>
    ${series.length > 0 && html`<${Sparkline} values=${series} height=${28} color=${C.blue} labels=${series.map((_, i) => `W${i + 1}`)} />`}</${Tile}>`;
}

function Verified({ ctx }) {
  const pool = ctx.map.nodes.filter((n) => VERIFIABLE.includes(n.type) && n.status !== "dropped");
  const done = pool.filter((n) => n.status === "verified").length;
  return html`<div class="mp-kpi-ring">
    <div><${Tile} label=${t("rmap.kpi.verified")} value=${done} unit=${"/" + pool.length}
      sub=${t("rmap.kpi.verified.sub", { n: pool.filter((n) => n.status === "blocked").length })} /></div>
    <${Ring} value=${done} max=${pool.length || 1} size=${52} stroke=${5} color=${C.aqua}>
      <b class="mp-num">${pool.length ? Math.round((done / pool.length) * 100) : 0}</b><small>%</small></${Ring}>
  </div>`;
}

// Open branches with nothing hung on them yet (no evidence, no attachment of any kind).
export function blankBranches(map, meta) {
  const hung = new Set(Object.values(map.attachments || {}).flat().map((a) => a.node));
  return map.nodes.filter((n) => BRANCH.includes(n.type) && !meta.closed.includes(n.status) && !hung.has(n.id));
}

function Blank({ ctx }) {
  const blank = blankBranches(ctx.map, ctx.meta);
  return html`<${Tile} label=${t("rmap.kpi.blank")} value=${blank.length} sub=${blank.length ? blank.slice(0, 2).map((n) => n.title).join("、") : t("rmap.kpi.blank.none")} />`;
}

rmap.kpi({ id: "depth", order: 10, render: (ctx) => html`<${Depth} ctx=${ctx} />` });
rmap.kpi({ id: "active", order: 20, render: (ctx) => html`<${Active} ctx=${ctx} />` });
rmap.kpi({ id: "verified", order: 30, render: (ctx) => html`<${Verified} ctx=${ctx} />` });
rmap.kpi({ id: "blank", order: 40, render: (ctx) => html`<${Blank} ctx=${ctx} />` });

// Experiment results of a node (or of a whole map): which metrics there are, the latest value of each
// config, the gap to the baselines, and the trend over dates. Pure functions with no imports, so they run
// under node / jsc too (tests/js/rmap_results.test.mjs). Records are the `results` attachments:
// {id, node, config, dataset?, metric, value, date?, run?, baseline?, better?, source?}.
// i18n: done

// Metrics where a smaller number is the better one; keep in step with app/rmap/results/model.py.
const LOWER = /(loss|error|err\b|latency|time|perplexity|\bppl\b|\bmae\b|\bmse\b|\brmse\b|\bwer\b|\bcer\b|\bfid\b|cost|memory|\bmem\b|\bms\b|flops|params|size|regret|hallucinat|fail|drift|gap)/i;

export const lowerBetter = (metric) => LOWER.test(metric || "");
export const dirOf = (rec) => rec.better || (lowerBetter(rec.metric) ? "lower" : "higher");
export const dayOf = (rec) => rec.date || (rec.created_at || "").slice(0, 10);
const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
const same = (a, b) => (a || "").toLowerCase() === (b || "").toLowerCase();

// 4 significant digits: 0.4123 -> 0.4123, 12.5 -> 12.5, 1234.56 -> 1235
export function fmt(v) {
  const n = Number(Number(v).toPrecision(4));
  return Math.abs(n) >= 1e6 ? n.toExponential(2) : String(n);
}
export const signed = (v) => (v > 0 ? "+" : v < 0 ? "−" : "±") + fmt(Math.abs(v));

// Distinct (metric, dataset) pairs, the most recently measured first.
export function groups(records) {
  const out = new Map();
  for (const r of records) {
    const k = r.metric.toLowerCase() + "\u0000" + (r.dataset || "").toLowerCase();
    const g = out.get(k) || { metric: r.metric, dataset: r.dataset || "", n: 0, last: "", configs: new Set() };
    g.n += 1;
    g.last = g.last > dayOf(r) ? g.last : dayOf(r);
    g.configs.add(r.config);
    out.set(k, g);
  }
  return [...out.values()].sort((a, b) => (b.last > a.last ? 1 : b.last < a.last ? -1 : b.n - a.n));
}

// Distinct metric names (first spelling seen), most recently measured first.
export function metricsOf(records) {
  const seen = new Map();
  for (const g of groups(records)) if (!seen.has(g.metric.toLowerCase())) seen.set(g.metric.toLowerCase(), { metric: g.metric, n: 0, last: g.last });
  for (const r of records) seen.get(r.metric.toLowerCase()).n += 1;
  return [...seen.values()];
}

export function datasetsOf(records, metric) {
  const out = [];
  for (const g of groups(records)) if (same(g.metric, metric) && !out.includes(g.dataset)) out.push(g.dataset);
  return out;
}

export const pick = (records, metric, dataset) =>
  records.filter((r) => same(r.metric, metric) && (dataset == null || (r.dataset || "") === dataset));

// The latest value of every config for one metric (and dataset): runs on the latest date are averaged.
// Rows come best first; `ref` is the best baseline and every other row gets its gap to it.
export function compare(records, metric, dataset) {
  const rs = pick(records, metric, dataset);
  const dir = rs.length ? dirOf(rs.find((r) => r.better) || rs[0]) : "higher";
  const byConfig = new Map();
  for (const r of rs) {
    if (!byConfig.has(r.config)) byConfig.set(r.config, []);
    byConfig.get(r.config).push(r);
  }
  const rows = [...byConfig].map(([config, list]) => {
    const last = list.map(dayOf).sort().pop();
    const at = list.filter((r) => dayOf(r) === last);
    const vals = at.map((r) => r.value);
    return { config, baseline: list.some((r) => r.baseline), value: mean(vals), n: vals.length, lo: Math.min(...vals), hi: Math.max(...vals),
      date: last, ids: at.map((r) => r.id) };
  });
  rows.sort((a, b) => (dir === "lower" ? a.value - b.value : b.value - a.value) || a.config.localeCompare(b.config));
  const ref = rows.find((r) => r.baseline) || null;
  for (const r of rows) {
    if (ref && !r.baseline) {
      r.delta = r.value - ref.value;
      r.good = dir === "lower" ? r.delta < 0 : r.delta > 0;
      r.rel = ref.value ? r.delta / Math.abs(ref.value) : null;
    }
  }
  return { rows, dir, ref, best: rows[0] || null, metric, dataset };
}

// One line per config over the dates it was measured (runs of a day averaged); baselines after the others.
export function trend(records, metric, dataset) {
  const rs = pick(records, metric, dataset);
  const dir = rs.length ? dirOf(rs.find((r) => r.better) || rs[0]) : "higher";
  const byConfig = new Map();
  for (const r of rs) {
    if (!byConfig.has(r.config)) byConfig.set(r.config, { config: r.config, baseline: false, days: new Map() });
    const s = byConfig.get(r.config);
    s.baseline = s.baseline || !!r.baseline;
    const d = dayOf(r);
    if (!s.days.has(d)) s.days.set(d, []);
    s.days.get(d).push(r.value);
  }
  const series = [...byConfig.values()].map((s) => ({
    config: s.config, baseline: s.baseline,
    points: [...s.days].map(([date, vals]) => ({ date, value: mean(vals), n: vals.length })).sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0)),
  }));
  const last = (s) => s.points[s.points.length - 1].value;
  series.sort((a, b) => (a.baseline - b.baseline) || (dir === "lower" ? last(a) - last(b) : last(b) - last(a)));
  const dates = [...new Set(series.flatMap((s) => s.points.map((p) => p.date)))].sort();
  return { series, dates, dir };
}

// What a node shows at a glance: its most recently measured metric and the best latest value of it.
export function headline(records) {
  if (!records.length) return null;
  const g = groups(records)[0];
  const c = compare(records, g.metric, g.dataset);
  const row = c.rows.find((r) => !r.baseline) || c.best;
  return row ? { metric: g.metric, dataset: g.dataset, value: row.value, config: row.config, count: records.length } : null;
}

// Values of the best config over time, for a sparkline.
export function spark(records, metric, dataset) {
  const c = compare(records, metric, dataset);
  const tr = trend(records, metric, dataset);
  const history = (r) => (tr.series.find((x) => x.config === r.config)?.points.length || 0) > 1;
  const pick1 = c.rows.find((r) => !r.baseline && history(r)) || c.rows.find((r) => !r.baseline) || c.best;
  if (!pick1) return { values: [], labels: [] };
  const s = tr.series.find((x) => x.config === pick1.config);
  return { values: s.points.map((p) => p.value), labels: s.points.map((p) => p.date), config: pick1.config };
}

// Records grouped for the table: newest first.
export const newestFirst = (records) => [...records].sort((a, b) => (dayOf(b) > dayOf(a) ? 1 : dayOf(b) < dayOf(a) ? -1 : (b.created_at || "") > (a.created_at || "") ? 1 : -1));

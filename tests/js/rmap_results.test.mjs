// web/rmap/results/data.js without a browser: run by tests/test_rmap_results.py with node, or with JavaScriptCore's
// jsc (the engine of the desktop window). Prints "ok" or throws. The test copies data.js next to this file as data.mjs.
import { compare, trend, headline, groups, metricsOf, datasetsOf, fmt, signed, lowerBetter, spark } from "./data.mjs";

const out = typeof print === "function" ? print : console.log.bind(console);
const ok = (cond, what) => { if (!cond) throw new Error(what); };
const eq = (actual, expected, what) => {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${what}: expected ${e}, got ${a}`);
};

let n = 0;
const rec = (config, metric, value, extra = {}) => ({ id: `r${n++}`, node: "x", config, metric, value, date: "2026-10-20", ...extra });

const rs = [
  rec("BM25", "Recall@10", 0.38, { baseline: true, dataset: "MS MARCO" }),
  rec("DPR", "Recall@10", 0.41, { baseline: true, dataset: "MS MARCO" }),
  rec("Ours", "Recall@10", 0.44, { dataset: "MS MARCO", date: "2026-10-18" }),
  rec("Ours", "Recall@10", 0.46, { dataset: "MS MARCO", date: "2026-10-22", run: "1" }),
  rec("Ours", "Recall@10", 0.48, { dataset: "MS MARCO", date: "2026-10-22", run: "2" }),
  rec("Ours", "Recall@10", 0.30, { dataset: "NQ", date: "2026-10-22" }),
  rec("BM25", "latency_ms", 12, { baseline: true }),
  rec("Ours", "latency_ms", 48),
];

// latest per config, runs of the latest day averaged, best first, gap to the best baseline
const c = compare(rs, "recall@10", "MS MARCO");
eq(c.rows.map((r) => r.config), ["Ours", "DPR", "BM25"], "order, best first");
ok(Math.abs(c.rows[0].value - 0.47) < 1e-9 && c.rows[0].n === 2 && c.rows[0].date === "2026-10-22", "mean of the runs on the latest day");
eq(c.ref.config, "DPR", "the best baseline is the reference");
ok(Math.abs(c.rows[0].delta - 0.06) < 1e-9 && c.rows[0].good === true, "gap to the reference");
ok(c.rows[1].delta === undefined, "a baseline has no gap");

// smaller is better for latency: the order flips and a smaller number is `good`
const l = compare(rs, "latency_ms");
eq(l.dir, "lower", "latency is lower-is-better");
eq(l.rows.map((r) => r.config), ["BM25", "Ours"], "lower first");
ok(l.rows[1].good === false, "slower than the baseline is not good");
ok(lowerBetter("val_loss") && lowerBetter("WER") && !lowerBetter("Recall@10") && !lowerBetter("accuracy"), "direction guessed from the name");
eq(compare([rec("A", "acc", 1, { better: "lower" })], "acc").dir, "lower", "an explicit `better` wins over the name");

// trend: a line per config, points by date, baselines last
const t = trend(rs, "Recall@10", "MS MARCO");
eq(t.series.map((s) => s.config), ["Ours", "DPR", "BM25"], "series order");
eq(t.series[0].points.map((p) => p.date), ["2026-10-18", "2026-10-22"], "points by date");
eq(t.dates, ["2026-10-18", "2026-10-20", "2026-10-22"], "all dates");

// grouping
eq(metricsOf(rs).map((m) => m.metric).sort(), ["Recall@10", "latency_ms"], "metrics");
eq(datasetsOf(rs, "recall@10").sort(), ["MS MARCO", "NQ"], "datasets");
ok(groups(rs).length === 3, "metric x dataset pairs");
eq(headline(rs).metric, "Recall@10", "headline: the most recently measured metric");
eq(spark(rs, "Recall@10", "MS MARCO").values.length, 2, "sparkline of the best config");
eq(headline([]), null, "no results, no headline");

// numbers
eq([fmt(0.41234), fmt(12.5), fmt(1234.56), fmt(48)], ["0.4123", "12.5", "1235", "48"], "4 significant digits");
eq([signed(0.06), signed(-2), signed(0)], ["+0.06", "−2", "±0"], "signed gaps");

out("ok");

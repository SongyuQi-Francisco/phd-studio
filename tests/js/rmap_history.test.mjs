// web/rmap/timeline.js without a browser: run by tests/test_rmap_history.py with node, or with JavaScriptCore's
// jsc (WebKit's engine, the one the desktop window uses). Prints "ok" or throws.
import { mapAt, compare, metroLayout, dayMs, WEEK_MS } from "./timeline.mjs";

const out = typeof print === "function" ? print : console.log.bind(console);

function ok(cond, what) {
  if (!cond) throw new Error(what);
}
function eq(actual, expected, what) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${what}: expected ${e}, got ${a}`);
}

// frames: q from W0, h added in W1 and validated in W2, x added in W1 and gone in W3
const q = { id: "q", type: "question", title: "Q", status: "active" };
const h = { id: "h", type: "hypothesis", title: "H", status: "active" };
const x = { id: "x", type: "experiment", title: "X", status: "todo" };
const frames = {
  slug: "p", stamp: "s",
  project: [[0, { title: "P" }]],
  nodes: { q: [[0, q]], h: [[1, h], [2, { ...h, status: "verified", title: "H2" }]], x: [[1, x], [3, null]] },
  edges: { e1: [[1, { id: "e1", from: "q", to: "h", kind: "refines" }]], e2: [[1, { id: "e2", from: "h", to: "x", kind: "refines" }], [3, null]] },
  attachments: { evidence: { v1: [[2, { id: "v1", node: "h", type: "paper", target: "A" }]] } },
};
eq(mapAt(frames, 0).nodes.map((n) => n.id), ["q"], "week 0");
eq(mapAt(frames, 1).nodes.map((n) => n.id), ["q", "h", "x"], "week 1");
eq(mapAt(frames, 2).nodes.find((n) => n.id === "h").status, "verified", "status at week 2");
eq(mapAt(frames, 2).attachments.evidence.length, 1, "evidence at week 2");
eq(mapAt(frames, 3).nodes.map((n) => n.id), ["q", "h"], "x gone at week 3");
eq(mapAt(frames, 3).edges.map((e) => e.id), ["e1"], "its edge too");
eq(mapAt(frames, 9).project.title, "P", "project");
ok(mapAt(frames, 1).stamp !== mapAt(frames, 2).stamp, "a stamp per week");

// compare: the same marks as the server's diff
const c1 = compare(mapAt(frames, 0), mapAt(frames, 1));
eq(c1.changes.added.map((r) => r.id), ["h", "x"], "added");
eq(c1.marks, { h: { mark: "added" }, x: { mark: "added" } }, "marks of added");
const c2 = compare(mapAt(frames, 1), mapAt(frames, 2));
eq(c2.marks.h, { mark: "status", from: "active", to: "verified" }, "status beats rename");
eq(c2.changes.retitled.map((r) => [r.id, r.from]), [["h", "H"]], "renamed");
const c3 = compare(mapAt(frames, 2), mapAt(frames, 3));
eq(c3.changes.removed.map((r) => r.id), ["x"], "removed");
ok(c3.view.nodes.find((n) => n.id === "x").gone, "a ghost node");
ok(c3.view.edges.find((e) => e.id === "e2").gone, "a ghost edge");
const dropped = compare({ nodes: [h], edges: [] }, { nodes: [{ ...h, status: "dropped" }], edges: [] });
eq(dropped.marks.h, { mark: "dropped", from: "active", to: "dropped" }, "dropped");
eq(compare(null, mapAt(frames, 0)).changes.added.length, 1, "from nothing");

// the metro layout
const w0 = "2026-09-07";
const t0 = dayMs(w0);
const day = (n) => new Date(2026, 8, 7 + n).getTime(); // local days, like the API's
const pad = (v) => String(v).padStart(2, "0");
const localDay = (ms) => { const d = new Date(ms); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const st = (n, kind = "verified", title = "Station " + n) => ({ day: "d" + n, ms: day(n), kind, items: [{ title, what: kind }] });
const ov = {
  weeks: [0, 1, 2, 3].map((i) => ({ i, label: "W" + (i + 1), start: localDay(t0 + i * WEEK_MS) })),
  now: day(27),
  lines: [
    { slug: "a", title: "A", start: day(0), end: day(27), stations: [st(2), st(3), st(4), st(20)] },
    { slug: "b", title: "B", start: day(9), end: day(27), stations: [st(10, "decision"), st(19)] },
  ],
  interchanges: [{ entity: "dataset:x", ms: day(19), projects: [{ slug: "a" }, { slug: "b" }] },
                 { entity: "paper:y", ms: day(20), projects: [{ slug: "a" }, { slug: "b" }] }],
};
const lay = metroLayout(ov, { weekW: 70, left: 20, laneH: 90, top: 60, labelW: 100 });
eq(lay.lines.map((l) => l.y), [60, 150], "lanes");
ok(Math.abs(lay.lines[0].x1 - 20) < 1e-6 && Math.abs(lay.lines[1].x1 - (20 + 90)) < 1e-6, "lines start at their first change");
eq(lay.lines[0].stations.slice(0, 3).map((s) => s.side), ["up", "down", null], "close labels alternate, then give way");
eq(lay.lines[1].stations[1].side, "down", "a lower lane's label next to an interchange goes below");
eq(lay.lines[0].stations[3].side, "up", "an upper lane's label next to an interchange goes above");
eq(lay.interchanges.map((ic) => ic.row), [0, 1], "names of close interchanges take two rows");
ok(lay.interchanges.every((ic) => ic.y1 === 60 && ic.y2 === 150), "interchanges join the lanes");
ok(lay.width === 20 + 4 * 70 + 56, "width");
ok(lay.height > 150 + 40, "room for the names");
out("ok");

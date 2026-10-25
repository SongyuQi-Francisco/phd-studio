// web/rmap/layout.js without a browser: run by tests/test_rmap_view.py with node, or with JavaScriptCore's
// jsc (WebKit's engine, the one the desktop window uses). Prints "ok" or throws.
// The test copies web/rmap/layout.js and util.js next to this file (as .mjs), so every engine loads them as modules.
import { layoutMap, overlaps, NODE } from "./layout.mjs";

const out = typeof print === "function" ? print : console.log.bind(console);

function ok(cond, what) {
  if (!cond) throw new Error(what);
}
function eq(actual, expected, what) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${what}: expected ${e}, got ${a}`);
}

const meta = {
  flow: ["refines", "depends"],
  node_types: [
    { key: "question", col: 0 }, { key: "hypothesis", col: 1 }, { key: "subline", col: 2 },
    { key: "experiment", col: 3 }, { key: "result", col: 4 }, { key: "decision", col: null },
    { key: "kill", col: null }, { key: "open", col: null },
  ],
};
let seq = 0;
const node = (id, type, extra = {}) => ({ id, type, title: id, status: "todo", created_at: `2026-10-26T10:00:${String(seq++).padStart(2, "0")}`, ...extra });
const edge = (from, to, kind = "refines", extra = {}) => ({ id: `e-${from}-${to}-${kind}`, from, to, kind, created_at: `2026-10-26T11:00:${String(seq++).padStart(2, "0")}`, ...extra });

function noOverlaps(layout, what) {
  const ids = Object.keys(layout.boxes);
  for (let i = 0; i < ids.length; i++) {
    for (let j = i + 1; j < ids.length; j++) {
      const a = layout.boxes[ids[i]], b = layout.boxes[ids[j]];
      const hit = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
      ok(!hit, `${what}: ${ids[i]} and ${ids[j]} overlap at ${JSON.stringify(a)} / ${JSON.stringify(b)}`);
    }
  }
}

// 1. a depends edge between experiments of two branches is a cross-link: it neither stacks them nor moves them
{
  const map = {
    nodes: [node("q", "question"), node("h1", "hypothesis"), node("h2", "hypothesis"), node("x1", "experiment"), node("x2", "experiment")],
    edges: [edge("q", "h1"), edge("q", "h2"), edge("h1", "x1"), edge("h2", "x2"), edge("x1", "x2", "depends")],
  };
  const l = layoutMap(map, meta);
  noOverlaps(l, "depends");
  eq([l.boxes.x1.col, l.boxes.x2.col], [3, 3], "both experiments stay in the experiment column");
  ok(l.boxes.x1.y < l.boxes.x2.y, "x1 stays with h1 (above), x2 with h2");
  ok(Math.abs((l.boxes.h1.y) - l.boxes.x1.y) < 1 && Math.abs(l.boxes.h2.y - l.boxes.x2.y) < 1, "each branch keeps its row");
  ok(l.edges.some((e) => e.kind === "depends" && e.d), "the depends edge is still drawn");
}

// 2. a node that only depends on another is a root of its own, not a child of it
{
  const map = { nodes: [node("a", "experiment"), node("b", "experiment")], edges: [edge("a", "b", "depends")] };
  const l = layoutMap(map, meta);
  noOverlaps(l, "depends only");
  eq(l.roots.sort(), ["a", "b"], "both are roots of the hierarchy");
}

// 3. new automatic nodes never land on a pinned one
{
  const pinAt = { x: 22 + 3 * (NODE.w + 62), y: 44 }; // exactly where the first experiment row would go
  const map = {
    nodes: [node("q", "question"), node("h", "hypothesis"), node("s", "subline"),
            node("p", "experiment", { pin: pinAt }), node("x", "experiment"), node("y", "experiment")],
    edges: [edge("q", "h"), edge("h", "s"), edge("s", "p"), edge("s", "x"), edge("s", "y")],
  };
  const l = layoutMap(map, meta);
  eq([l.boxes.p.x, l.boxes.p.y], [pinAt.x, pinAt.y], "a pinned node stays put");
  noOverlaps(l, "pins");
  ok(!overlaps(l.boxes.p, l.boxes.x) && !overlaps(l.boxes.p, l.boxes.y), "and the others keep clear of it");
}

// 4. edges the server marked as closing a cycle are drawn but not walked
{
  const map = {
    nodes: [node("a", "hypothesis"), node("b", "experiment")],
    edges: [edge("a", "b"), edge("b", "a", "depends", { cycle: true })],
  };
  const l = layoutMap(map, meta);
  noOverlaps(l, "cycle");
  ok(l.boxes.a.col < l.boxes.b.col, "the hierarchy decides the columns");
  eq(l.edges.length, 2, "both edges are drawn");
}

// 5. collapse hides the hierarchy below a node and counts it; focus keeps one line
{
  const map = {
    nodes: [node("q", "question"), node("h1", "hypothesis"), node("h2", "hypothesis"), node("x1", "experiment"), node("r1", "result")],
    edges: [edge("q", "h1"), edge("q", "h2"), edge("h1", "x1"), edge("x1", "r1")],
  };
  const c = layoutMap(map, meta, { collapsed: new Set(["h1"]) });
  eq(Object.keys(c.boxes).sort(), ["h1", "h2", "q"], "collapsed below h1");
  eq(c.hidden, { h1: 2 }, "two hidden under h1");
  const f = layoutMap(map, meta, { focus: "x1" });
  eq(Object.keys(f.boxes).sort(), ["h1", "q", "r1", "x1"], "focus keeps ancestors and descendants");
  noOverlaps(layoutMap(map, meta), "plain");
}

// 6. same input, same picture (both Macs draw the same map)
{
  const map = {
    nodes: [node("q", "question"), node("h1", "hypothesis"), node("h2", "hypothesis")],
    edges: [edge("q", "h1"), edge("q", "h2"), edge("h1", "h2", "alternative")],
  };
  eq(JSON.stringify(layoutMap(map, meta).boxes), JSON.stringify(layoutMap(JSON.parse(JSON.stringify(map)), meta).boxes), "deterministic");
}

out("ok");

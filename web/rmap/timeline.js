// Pure helpers of the research map's time travel (#46), shared by web/rmap/ext/history.js and the tests:
// the map of one week from the time scrubber's frames, what changed between two maps (the same marks as
// GET .../history/diff), and the layout of the all-projects metro map. No imports: also runs outside the
// browser (tests/js/rmap_history.test.mjs, in node and in JavaScriptCore).
// i18n: done

const FADED = ["refuted", "dropped"];
const FLOW = ["refines", "depends"];

// Flow edges that close a cycle, taken in the order the edges were made (`clock.from`, the same order the
// server marks them in, app/rmap/project.py back_edges): the edges get `cycle: true` (and lose a stale one),
// so the layout, which walks dagEdges, never meets a loop. Replay needs it: a week's map plus the edges
// deleted that week (ghosts) can hold A → B and B → A at once.
export function markCycles(edges, flow = FLOW) {
  const made = (e) => e.clock?.from || e.clock?.kind || e.created_at || "";
  const next = new Map();
  const cycle = new Set();
  const reaches = (from, goal) => {
    const seen = new Set();
    const stack = [from];
    while (stack.length) {
      const cur = stack.pop();
      if (cur === goal) return true;
      if (seen.has(cur)) continue;
      seen.add(cur);
      (next.get(cur) || []).forEach((n) => stack.push(n));
    }
    return false;
  };
  const sorted = edges.filter((e) => flow.includes(e.kind))
    .sort((a, b) => (made(a) < made(b) ? -1 : made(a) > made(b) ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  for (const e of sorted) {
    if (e.from === e.to || reaches(e.to, e.from)) cycle.add(e.id);
    else next.set(e.from, [...(next.get(e.from) || []), e.to]);
  }
  return edges.map((e) => {
    const { cycle: _old, ...rest } = e;
    return cycle.has(e.id) ? { ...rest, cycle: true } : rest;
  });
}

// Only the answer to the latest request counts: `const mine = latest(); … if (mine()) set(answer)`.
export function takeLatest() {
  let gen = 0;
  return () => { const id = ++gen; return () => id === gen; };
}

// A record's state at week wi: its last state up to that week (null: not on the map then).
function lastAt(states, wi) {
  let out = null;
  for (const [w, s] of states || []) {
    if (w > wi) break;
    out = s;
  }
  return out;
}

// The map at the end of week wi (GET .../history), shaped like GET /api/rmap/projects/{slug}.
export function mapAt(frames, wi) {
  const pick = (book) => Object.values(book || {}).map((s) => lastAt(s, wi)).filter(Boolean);
  const attachments = {};
  for (const [kind, book] of Object.entries(frames.attachments || {})) attachments[kind] = pick(book);
  const project = lastAt(frames.project, wi) || frames.project?.[0]?.[1] || {};
  return { slug: frames.slug, project, nodes: pick(frames.nodes), edges: pick(frames.edges), attachments,
    warnings: [], stamp: `${frames.stamp}@${wi}`, seq: 0 };
}

const row = (n) => ({ id: n.id, title: n.title, type: n.type, status: n.status || "todo" });

// What changed from map a to map b: added, removed (deleted), dropped (newly refuted or abandoned), other
// status changes, renames, and a mark per node; `view` is b with the removed nodes and edges as ghosts
// (`gone: true`). Mirrors app/rmap/history.py diff().
export function compare(a, b) {
  const na = new Map((a?.nodes || []).map((n) => [n.id, n]));
  const nb = new Map(b.nodes.map((n) => [n.id, n]));
  const added = b.nodes.filter((n) => !na.has(n.id)).map(row);
  const removed = [...na.values()].filter((n) => !nb.has(n.id)).map(row);
  const status = [], dropped = [], retitled = [];
  for (const n of b.nodes) {
    const o = na.get(n.id);
    if (!o) continue;
    const sa = o.status || "todo", sb = n.status || "todo";
    if (sa !== sb) (FADED.includes(sb) && !FADED.includes(sa) ? dropped : status).push({ ...row(n), from: sa, to: sb });
    if ((o.title || "") !== (n.title || "")) retitled.push({ ...row(n), from: o.title });
  }
  const marks = {};
  retitled.forEach((r) => { marks[r.id] = { mark: "retitled", title: r.from }; });
  status.forEach((r) => { marks[r.id] = { mark: "status", from: r.from, to: r.to }; });
  dropped.forEach((r) => { marks[r.id] = { mark: "dropped", from: r.from, to: r.to }; });
  added.forEach((r) => { marks[r.id] = { mark: "added" }; });
  removed.forEach((r) => { marks[r.id] = { mark: "removed" }; });
  const eb = new Set(b.edges.map((e) => e.id));
  const shown = new Set([...nb.keys(), ...na.keys()]);
  const ghosts = (a?.edges || []).filter((e) => !eb.has(e.id) && shown.has(e.from) && shown.has(e.to)).map((e) => ({ ...e, gone: true }));
  const view = { ...b, nodes: [...b.nodes, ...removed.map((r) => ({ ...na.get(r.id), gone: true }))], edges: markCycles([...b.edges, ...ghosts]) };
  return { marks, view, changes: { added, removed, status, dropped, retitled } };
}

// Local midnight of "YYYY-MM-DD", in ms (dates in the API are local days).
export function dayMs(iso) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).getTime();
}

export const WEEK_MS = 7 * 86400000;

// The all-projects map (GET /api/rmap/overview) laid out as a metro map on one time axis: a lane per
// project, its line from start to end, stations (with labels placed above or below so they don't collide,
// else left to the tooltip) and interchanges joining the lanes of the projects that share an entity.
export function metroLayout(ov, { weekW = 72, laneH = 92, left = 188, top = 64, right = 56, labelW = 112, minGap = 18 } = {}) {
  const t0 = ov.weeks.length ? dayMs(ov.weeks[0].start) : 0;
  const x = (ms) => left + ((ms - t0) / WEEK_MS) * weekW;
  const lanes = Object.fromEntries(ov.lines.map((ln, i) => [ln.slug, top + i * laneH]));
  const interchanges = (ov.interchanges || []).filter((ic) => ic.projects.every((p) => p.slug in lanes)).map((ic) => {
    const ys = ic.projects.map((p) => lanes[p.slug]).sort((a, b) => a - b);
    return { ...ic, x: x(ic.ms), y1: ys[0], y2: ys[ys.length - 1], ys };
  }).sort((a, b) => a.x - b.x);
  const ends = []; // name rows under the lines: a name goes one row down when it would run into the last one
  for (const ic of interchanges) {
    ic.row = ends.findIndex((e) => ic.x - e >= 170);
    if (ic.row < 0) { ic.row = ends.length; ends.push(ic.x); } else ends[ic.row] = ic.x;
  }
  // a station label goes above or below its line, where it neither runs into the last label on that side
  // nor crosses an interchange's bar (which runs down from the upper lanes and up from the lower ones)
  const crosses = (sx, y, side) => interchanges.some((ic) => Math.abs(ic.x - sx) < labelW / 2 + 8
    && (side === "down" ? y >= ic.y1 && y < ic.y2 : y > ic.y1 && y <= ic.y2));
  const lines = ov.lines.map((ln, i) => {
    const y = lanes[ln.slug];
    const lastLabel = { up: -Infinity, down: -Infinity };
    // stations closer than a station's width (days apart on a narrow axis) become one, spanning their days
    const groups = [];
    for (const s of ln.stations) {
      const sx = Math.max(x(ln.start), Math.min(x(ln.end), x(s.ms)));
      const g = groups[groups.length - 1];
      if (g && sx - g.xs[g.xs.length - 1] < minGap) {
        g.xs.push(sx);
        g.items = [...g.items, ...s.items];
        g.days.push(s.day);
        if (s.kind === "decision") g.kind = "decision";
      } else groups.push({ ...s, xs: [sx], days: [s.day] });
    }
    const stations = groups.map(({ xs, ...g }) => {
      const sx = xs.reduce((a, b) => a + b, 0) / xs.length;
      const side = ["up", "down"].find((sd) => sx - lastLabel[sd] >= labelW && !crosses(sx, y, sd)) || null;
      if (side) lastLabel[side] = sx;
      return { ...g, x: sx, y, side, dayEnd: g.days[g.days.length - 1] };
    });
    return { ...ln, color: i, y, x1: x(ln.start), x2: Math.max(x(ln.end), x(ln.start) + 4), stations };
  });
  const weeks = ov.weeks.map((w, i) => ({ ...w, x: x(dayMs(w.start)), i }));
  const width = left + Math.max(1, ov.weeks.length) * weekW + right;
  const height = top + Math.max(1, lines.length) * laneH - laneH / 2 + (interchanges.length ? 50 + 14 * ends.length : 40); // rows for interchange names
  return { lines, interchanges, weeks, width, height, x, now: x(ov.now) };
}

// Left-to-right layered layout of one research map, without a layout library.
// Columns: a node sits right of the nodes it refines and no further left than its type's column
// (question · hypothesis · branch · experiment · result). Rows follow the refines hierarchy only: a depends
// edge is a cross-link between branches, drawn but never used to place a node. Rows: a depth-first walk from the roots, so a branch
// stays together; a parent is centered on its children. Pinned nodes stay where they were put, and the
// automatic ones keep clear of them.
// Collapse hides what is only reachable through a collapsed node; focus keeps one node's ancestors and
// descendants. Pure and deterministic: the same map gives the same picture on both Macs.

// i18n: done
import { dagEdges } from "./util.js";

export const NODE = { w: 150, h: 46 };
const GAP = { x: 62, y: 14 };
const PAD = { x: 22, y: 44 }; // top padding leaves room for the column captions
const TREE_GAP = 0.6; // extra rows between separate trees

const created = (n) => n.created_at || "";
export const overlaps = (a, b) => a.x < b.x + b.w + GAP.x / 2 && b.x < a.x + a.w + GAP.x / 2
  && a.y < b.y + b.h + GAP.y / 2 && b.y < a.y + a.h + GAP.y / 2;
const byOrder = (a, b) => (a.order ?? 1e9) - (b.order ?? 1e9) || created(a).localeCompare(created(b)) || a.id.localeCompare(b.id);

// Flow edges between live nodes, without the ones the server marked as closing a cycle (two Macs can make
// one between them): the same DAG every view and metric walks (util.js dagEdges).
export function flowGraph(map, meta) {
  const ids = new Set(map.nodes.map((n) => n.id));
  const out = new Map(map.nodes.map((n) => [n.id, []]));
  const inn = new Map(map.nodes.map((n) => [n.id, []]));
  const edges = dagEdges(map, meta).filter((e) => ids.has(e.from) && ids.has(e.to) && e.from !== e.to)
    .sort((a, b) => created(a).localeCompare(created(b)) || a.id.localeCompare(b.id));
  for (const e of edges) {
    out.get(e.from).push(e);
    inn.get(e.to).push(e);
  }
  return { out, inn };
}

const walk = (start, next) => {
  const seen = new Set();
  const stack = [...start];
  while (stack.length) {
    const cur = stack.pop();
    if (seen.has(cur)) continue;
    seen.add(cur);
    next(cur).forEach((x) => stack.push(x));
  }
  return seen;
};
export const descendants = (g, id) => walk([id], (c) => g.out.get(c).map((e) => e.to));
export const ancestors = (g, id) => walk([id], (c) => g.inn.get(c).map((e) => e.from));

// Path of one edge between two node boxes, and the point where its label goes.
export function edgePath(a, b) {
  if (b.x > a.x + a.w / 2) {
    const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
    const dx = Math.max(22, (x2 - x1) / 2);
    return { d: `M${x1} ${y1} C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`, mid: { x: (x1 + x2) / 2, y: (y1 + y2) / 2 } };
  }
  if (a.x > b.x + b.w / 2) { // pointing back left (a lateral edge, or a pinned node moved)
    const x1 = a.x, y1 = a.y + a.h / 2, x2 = b.x + b.w, y2 = b.y + b.h / 2;
    return { d: `M${x1} ${y1} C${x1 - 30} ${y1} ${x2 + 30} ${y2} ${x2} ${y2}`, mid: { x: (x1 + x2) / 2, y: (y1 + y2) / 2 } };
  }
  // same column: a bow on the right
  const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x + b.w, y2 = b.y + b.h / 2;
  const bow = 26 + Math.min(40, Math.abs(y2 - y1) / 6);
  return { d: `M${x1} ${y1} C${x1 + bow} ${y1} ${x2 + bow} ${y2} ${x2} ${y2}`, mid: { x: Math.max(x1, x2) + bow * 0.75, y: (y1 + y2) / 2 } };
}

export function layoutMap(map, meta, { collapsed = new Set(), focus = null } = {}) {
  const hint = Object.fromEntries(meta.node_types.map((t) => [t.key, t.col]));
  const byId = new Map(map.nodes.map((n) => [n.id, n]));
  const g = flowGraph(map, meta);
  const tree = (id, dir) => g[dir].get(id).filter((e) => e.kind === "refines"); // the hierarchy: refines only

  // which nodes are shown: focus keeps a node's whole line (depends included); collapse folds the hierarchy
  let pool = new Set(byId.keys());
  if (focus && byId.has(focus)) pool = new Set([...ancestors(g, focus), ...descendants(g, focus)]);
  const kids = (id) => tree(id, "out").map((e) => e.to).filter((k) => pool.has(k)).map((k) => byId.get(k)).sort(byOrder);
  const roots = [...pool].map((id) => byId.get(id)).filter((n) => !tree(n.id, "inn").some((e) => pool.has(e.from))).sort(byOrder);
  const visible = walk(roots.map((r) => r.id), (c) => (collapsed.has(c) ? [] : kids(c).map((k) => k.id)));
  const hidden = {};
  for (const c of collapsed) {
    if (!visible.has(c)) continue;
    const below = walk([c], (x) => kids(x).map((k) => k.id));
    const n = [...below].filter((d) => d !== c && !visible.has(d)).length;
    if (n) hidden[c] = n;
  }
  const hasKids = new Set([...pool].filter((id) => kids(id).length));

  // columns, in topological order
  const col = {};
  const indeg = new Map([...visible].map((id) => [id, g.inn.get(id).filter((e) => visible.has(e.from)).length]));
  const queue = [...visible].filter((id) => !indeg.get(id)).map((id) => byId.get(id)).sort(byOrder).map((n) => n.id);
  while (queue.length) {
    const id = queue.shift();
    const parents = g.inn.get(id).filter((e) => e.kind === "refines" && visible.has(e.from)).map((e) => col[e.from] + 1);
    col[id] = Math.max(hint[byId.get(id).type] ?? 0, ...parents, 0);
    for (const e of g.out.get(id)) {
      if (!visible.has(e.to)) continue;
      indeg.set(e.to, indeg.get(e.to) - 1);
      if (!indeg.get(e.to)) queue.push(e.to);
    }
  }

  // rows: each node is placed under the first parent that refines it (a spanning forest)
  const row = {};
  const placed = new Set();
  let next = 0;
  const place = (n) => {
    placed.add(n.id);
    const own = collapsed.has(n.id) ? [] : kids(n.id).filter((k) => visible.has(k.id) && !placed.has(k.id));
    own.forEach((k) => placed.add(k.id));
    const rows = [];
    for (const k of own) { place(k); if (row[k.id] != null) rows.push(row[k.id]); }
    if (n.pin) return; // pinned: takes no row
    if (rows.length) row[n.id] = (Math.min(...rows) + Math.max(...rows)) / 2;
    else { row[n.id] = next; next += 1; }
  };
  roots.filter((r) => visible.has(r.id)).forEach((r, i) => { if (i && next) next += TREE_GAP; place(r); });

  const boxes = {};
  const taken = [];
  for (const id of visible) {
    const n = byId.get(id);
    if (!n.pin) continue;
    boxes[id] = { x: n.pin.x, y: n.pin.y, w: NODE.w, h: NODE.h, col: col[id], pinned: true };
    taken.push(boxes[id]);
  }
  // the automatic nodes, column by column and top down; one that would sit on a pinned node (or on a node
  // moved for one) goes down a row until it is clear
  const auto = [...visible].filter((id) => !byId.get(id).pin)
    .sort((a, b) => col[a] - col[b] || (row[a] ?? 0) - (row[b] ?? 0) || a.localeCompare(b));
  for (const id of auto) {
    const box = { x: PAD.x + col[id] * (NODE.w + GAP.x), y: PAD.y + (row[id] ?? 0) * (NODE.h + GAP.y), w: NODE.w, h: NODE.h, col: col[id], pinned: false };
    while (taken.some((o) => overlaps(o, box))) box.y += NODE.h + GAP.y;
    boxes[id] = box;
    taken.push(box);
  }

  // edges: flow ones left to right, lateral ones (alternative, conflicts) as arcs
  const edges = [];
  for (const e of map.edges) {
    const a = boxes[e.from], b = boxes[e.to];
    if (!a || !b) continue;
    const lateral = !meta.flow.includes(e.kind);
    edges.push({ ...e, ...edgePath(a, b), lateral, status: byId.get(e.to).status || "todo" });
  }

  // column captions: the type that owns the column, else the most common type in it
  const columns = [];
  const usedCols = [...new Set(Object.values(col))].sort((a, b) => a - b);
  for (const c of usedCols) {
    const types = [...visible].filter((id) => col[id] === c && !byId.get(id).pin).map((id) => byId.get(id).type);
    if (!types.length) continue;
    const own = Object.keys(hint).find((k) => hint[k] === c && types.includes(k));
    const common = types.sort((a, b) => types.filter((t) => t === b).length - types.filter((t) => t === a).length)[0];
    columns.push({ col: c, x: PAD.x + c * (NODE.w + GAP.x), type: own || common });
  }

  const all = Object.values(boxes);
  const width = Math.max(320, ...all.map((b) => b.x + b.w)) + PAD.x + 60;
  const height = Math.max(200, ...all.map((b) => b.y + b.h)) + PAD.y;
  return { boxes, edges, columns, width, height, hidden, hasKids, visible, roots: roots.map((r) => r.id), graph: g };
}

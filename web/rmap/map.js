// The single-project map: SVG on a grid canvas, laid out by layout.js. Click selects, double-click
// edits the title, right-click opens the menu (status, add child, connect, collapse, focus, pin,
// delete), dragging a node pins it. Keyboard: nodes are buttons (Tab reaches the selected one), arrows
// move the selection, Enter renames, A (or ⌘↵) adds a child, F focuses, Esc leaves focus or link mode.
// Layers registered by extensions draw on top (docs/research-map.md).
// i18n: done
// i18n-keys: rmap.status.{todo,active,verified,blocked,refuted,dropped}
// i18n-keys: rmap.type.{question,hypothesis,subline,experiment,result,decision,kill,open}
// i18n-keys: rmap.new.{question,hypothesis,subline,experiment,result,decision,kill,open}
// i18n-keys: rmap.col.{question,hypothesis,subline,experiment,result}
// i18n-keys: rmap.edge.{refines,depends,alternative,conflicts}
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, prefs, composing } from "/lib.js";
import { t } from "/i18n.js";
import { rmap } from "/rmap/registry.js";
import { layoutMap, edgePath, NODE } from "/rmap/layout.js";
import { useAutoFocus } from "/rmap/dom.js";

export const NEXT_TYPES = {
  question: ["hypothesis", "open"], hypothesis: ["subline", "experiment", "kill"], subline: ["experiment", "kill"],
  experiment: ["result", "decision"], result: ["decision", "open"], decision: ["hypothesis", "experiment"], kill: [], open: ["hypothesis"],
};
const FONT = '550 11.5px -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", sans-serif';
const SNAP = 10;

let measurer = null;
function fit(text, max) {
  measurer = measurer || document.createElement("canvas").getContext("2d");
  measurer.font = FONT;
  if (measurer.measureText(text).width <= max) return text;
  let lo = 0, hi = text.length;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (measurer.measureText(text.slice(0, m) + "…").width <= max) lo = m; else hi = m - 1; }
  return text.slice(0, lo).trimEnd() + "…";
}

export const columnLabel = (type) => (["question", "hypothesis", "subline", "experiment", "result"].includes(type)
  ? t("rmap.col." + type) : t("rmap.type." + type));
const ownColumn = (meta, type) => meta.node_types.find((x) => x.key === type)?.col != null;
const activeLayers = (ctx) => rmap.list("layer").filter((l) => ctx.layers[l.id] ?? l.default);

function Node({ n, box, ctx, clip, selected, tabbable, connecting, hidden, hasKids, collapsed, onDown, onMenu, onDbl, onFold, onFocus, layers }) {
  const st = n.status || "todo";
  const extra = layers.map((l) => l.nodeClass?.(n, ctx)).filter(Boolean).join(" ");
  const sub = (ownColumn(ctx.meta, n.type) ? "" : t("rmap.type." + n.type) + " · ") + t("rmap.status." + st);
  return html`<g class=${`mp-node st-${st} ty-${n.type}${selected ? " sel" : ""}${connecting ? " target" : ""}${box.pinned ? " pinned" : ""}${hidden ? " folded" : ""} ${extra}`}
      transform=${`translate(${box.x} ${box.y})`} data-id=${n.id} role="button" tabindex=${tabbable ? 0 : -1} aria-pressed=${selected ? "true" : "false"}
      aria-label=${t("rmap.node.label", { type: t("rmap.type." + n.type), title: n.title, status: t("rmap.status." + st) })}
      onMouseDown=${onDown} onContextMenu=${onMenu} onDblClick=${onDbl} onFocus=${onFocus}>
    <title>${n.title}</title>
    <rect class="mp-n-bg" width=${box.w} height=${box.h} rx="7" />
    ${st !== "todo" && html`<rect class="mp-n-bar" width="3" height=${box.h} clip-path=${`url(#${clip})`} />`}
    <text class="mp-n-title" x="12" y="19">${fit(n.title, box.w - (hasKids ? 34 : 20))}</text>
    <circle class="mp-n-dot" cx="15" cy="32" r="3" />
    <text class="mp-n-st" x="23" y="35.5">${sub}</text>
    ${box.pinned && html`<circle class="mp-n-pin" cx=${box.w - 6} cy="6" r="2" />`}
    ${hasKids && html`<g class="mp-n-fold" transform=${`translate(${box.w - 17} ${box.h / 2 - 8})`} onMouseDown=${(e) => e.stopPropagation()}
        onClick=${(e) => { e.stopPropagation(); onFold(); }}>
      <rect width="14" height="16" rx="4" />
      ${hidden ? html`<text x="7" y="11.5" text-anchor="middle">${hidden > 9 ? "9+" : hidden}</text>`
        : html`<path d=${collapsed ? "M5 5l4 3-4 3" : "M4 6l3 4 3-4"} />`}
    </g>`}
  </g>`;
}

function Menu({ menu, ctx, onClose, actions }) {
  const ref = useRef();
  useEffect(() => {
    const off = (e) => { if (!ref.current?.contains(e.target)) onClose(); };
    const key = (e) => { if (e.key === "Escape") onClose(); };
    setTimeout(() => { window.addEventListener("mousedown", off); window.addEventListener("keydown", key); });
    return () => { window.removeEventListener("mousedown", off); window.removeEventListener("keydown", key); };
  }, []);
  const [sub, setSub] = useState(null);
  const [sure, setSure] = useState(false);
  const n = menu.node && ctx.map.nodes.find((x) => x.id === menu.node);
  const e = menu.edge && ctx.map.edges.find((x) => x.id === menu.edge);
  const item = (label, run, { danger, icon, on } = {}) => html`<button class=${"mp-mi" + (danger ? " danger" : "") + (on ? " on" : "")}
    onClick=${() => { run(); }}>${icon}${label}</button>`;
  const dot = (s) => html`<i class=${"mp-dot st-" + s} />`;
  const close = (fn) => () => { onClose(); fn(); };
  let body;
  if (n) {
    const types = [...new Set([...NEXT_TYPES[n.type], ...ctx.meta.node_types.map((x) => x.key)])];
    body = html`
      <div class="mp-mrow">${ctx.meta.statuses.map((s) => html`<button class=${"mp-st-pick st-" + s + ((n.status || "todo") === s ? " on" : "")}
        title=${t("rmap.status." + s)} onClick=${close(() => actions.status(n, s))}>${dot(s)}<span>${t("rmap.status." + s)}</span></button>`)}</div>
      <div class="mp-msep" />
      ${item(t("rmap.menu.edit"), close(() => actions.edit(n)))}
      ${sub === "add" ? html`<div class="mp-msub">${types.map((k, i) => html`${i === NEXT_TYPES[n.type].length && i > 0 && html`<div class="mp-msep" />`}
          ${item(t("rmap.type." + k), close(() => actions.addChild(n, k)))}`)}</div>`
        : item(t("rmap.menu.add") + " ›", () => setSub("add"))}
      ${sub === "connect" ? html`<div class="mp-msub">${ctx.meta.edge_kinds.map((k) => item(t("rmap.edge." + k), close(() => actions.connect(n, k))))}</div>`
        : item(t("rmap.menu.connect") + " ›", () => setSub("connect"))}
      <div class="mp-msep" />
      ${actions.canFold(n) && item(actions.isFolded(n) ? t("rmap.menu.expand") : t("rmap.menu.collapse"), close(() => actions.fold(n)))}
      ${item(actions.isFocus(n) ? t("rmap.menu.unfocus") : t("rmap.menu.focus"), close(() => actions.focus(n)))}
      ${n.pin && item(t("rmap.menu.unpin"), close(() => actions.unpin(n)))}
      <div class="mp-msep" />
      ${sure ? item(t("rmap.menu.delete.sure"), close(() => actions.remove(n)), { danger: true })
        : item(t("rmap.menu.delete"), () => setSure(true), { danger: true })}`;
  } else if (e) {
    body = html`${ctx.meta.edge_kinds.map((k) => item(t("rmap.edge." + k), close(() => actions.edgeKind(e, k)), { on: e.kind === k }))}
      <div class="mp-msep" />
      ${item(t("rmap.menu.label"), close(() => actions.edgeLabel(e)))}
      ${item(t("rmap.menu.edge.delete"), close(() => actions.edgeDelete(e)), { danger: true })}`;
  } else {
    body = html`${["question", "hypothesis", "open"].map((k) => item(t("rmap.menu.new", { type: t("rmap.type." + k) }), close(() => actions.addRoot(k))))}
      ${(actions.focused || actions.anyFolded) && html`<div class="mp-msep" />`}
      ${actions.focused && item(t("rmap.menu.unfocus"), close(() => actions.focus(null)))}
      ${actions.anyFolded && item(t("rmap.menu.expand.all"), close(() => actions.expandAll()))}`;
  }
  return html`<div class="mp-menu" ref=${ref} style=${{ left: menu.x + "px", top: menu.y + "px" }} onContextMenu=${(ev) => ev.preventDefault()}>${body}</div>`;
}

let clipSeq = 0;

export function MapView({ ctx }) {
  const { map, meta, view } = ctx;
  const { collapsed, focus, setFocus, setCollapsed } = view;
  const [clip] = useState(() => "mp-clip-" + ++clipSeq);
  const [zoom, setZoom] = useState(prefs.get("rmap:zoom", 1));
  const [menu, setMenu] = useState(null); // {x, y, node?, edge?}
  const [editing, setEditing] = useState(null); // {id, kind: "node" | "edge", value}
  const [connect, setConnect] = useState(null); // {from, kind}
  const [drag, setDrag] = useState(null); // {id, x, y}
  const editRef = useAutoFocus(editing && editing.kind + editing.id, { select: true });
  const cancelled = useRef(false); // Esc: WebKit may still fire blur as the input goes, which must not save
  const wrap = useRef();
  const svg = useRef();
  const { pending, setPending } = view; // node id to edit once it shows up after an add

  useEffect(() => setConnect(null), [ctx.slug]);
  useEffect(() => prefs.set("rmap:zoom", zoom), [zoom]);

  const layout = useMemo(() => layoutMap(map, meta, { collapsed, focus }), [map, meta, collapsed, focus]);
  const lctx = useMemo(() => ({ ...ctx, layout }), [ctx, layout]);
  const layers = activeLayers(ctx);
  const byId = useMemo(() => new Map(map.nodes.map((n) => [n.id, n])), [map]);

  useEffect(() => {
    if (pending && layout.boxes[pending]) {
      cancelled.current = false;
      setEditing({ id: pending, kind: "node", value: byId.get(pending).title });
      placePanel(pending); ctx.select(pending); setPending(null);
    }
  }, [layout, pending]);

  // keep the floating detail panel (narrow page) off the selected node: it opens on the other half
  const scroller = useRef();
  const placePanel = (id) => {
    const box = layout.boxes[id], el = scroller.current;
    if (!box || !el) return;
    view.setSide((box.x + box.w / 2) * zoom - el.scrollLeft > el.clientWidth / 2 ? "left" : "right");
  };

  const point = (e) => {
    const r = svg.current.getBoundingClientRect();
    return { x: (e.clientX - r.left) / zoom, y: (e.clientY - r.top) / zoom };
  };
  const menuAt = (e, extra) => {
    e.preventDefault();
    e.stopPropagation();
    const r = wrap.current.getBoundingClientRect();
    setMenu({ x: Math.min(e.clientX - r.left, r.width - 230), y: e.clientY - r.top, ...extra });
  };
  const run = async (ops, summary) => {
    try { return await ctx.api.apply(ops, summary); } catch (err) { ctx.toast(err.message); return null; }
  };

  const actions = {
    status: (n, s) => run([{ op: "node.update", id: n.id, set: { status: s } }]),
    edit: (n) => { cancelled.current = false; setEditing({ id: n.id, kind: "node", value: n.title }); },
    addChild: async (n, type) => {
      if (collapsed.has(n.id)) view.fold(n.id);
      const r = await run([{ op: "node.add", ref: "$n", parent: n.id, set: { type, title: t("rmap.new." + type) } }]);
      if (r) setPending(r.refs.$n);
    },
    addRoot: async (type) => {
      const r = await run([{ op: "node.add", ref: "$n", set: { type, title: t("rmap.new." + type) } }]);
      if (r) setPending(r.refs.$n);
    },
    connect: (n, kind) => setConnect({ from: n.id, kind }),
    canFold: (n) => layout.hasKids.has(n.id),
    isFolded: (n) => collapsed.has(n.id),
    fold: (n) => view.fold(n.id),
    isFocus: (n) => focus === n.id,
    focus: (n) => setFocus(n && focus !== n.id ? n.id : null),
    focused: !!focus,
    anyFolded: collapsed.size > 0,
    expandAll: () => setCollapsed(new Set()),
    unpin: (n) => run([{ op: "node.update", id: n.id, set: { pin: null } }]),
    remove: (n) => { if (ctx.selected === n.id) ctx.select(null); run([{ op: "node.delete", id: n.id }]); },
    edgeKind: (e, kind) => run([{ op: "edge.update", id: e.id, set: { kind } }]),
    edgeLabel: (e) => { cancelled.current = false; setEditing({ id: e.id, kind: "edge", value: e.label || "" }); },
    edgeDelete: (e) => run([{ op: "edge.delete", id: e.id }]),
  };

  // press on a node: a click selects (or finishes a connection); a drag pins it where it is dropped
  const down = (n) => (e) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    if (connect) {
      const c = connect;
      setConnect(null);
      if (c.from !== n.id) run([{ op: "edge.add", from: c.from, to: n.id, kind: c.kind }]);
      return;
    }
    const start = point(e), box = layout.boxes[n.id];
    let moved = false;
    const move = (ev) => {
      const p = point(ev);
      if (!moved && Math.hypot(p.x - start.x, p.y - start.y) < 4) return;
      moved = true;
      setDrag({ id: n.id, x: Math.max(0, box.x + p.x - start.x), y: Math.max(0, box.y + p.y - start.y) });
    };
    const up = (ev) => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      if (!moved) { placePanel(n.id); ctx.select(n.id); return; }
      const p = point(ev);
      const pin = { x: Math.max(0, Math.round((box.x + p.x - start.x) / SNAP) * SNAP), y: Math.max(0, Math.round((box.y + p.y - start.y) / SNAP) * SNAP) };
      run([{ op: "node.update", id: n.id, set: { pin } }]).finally(() => setDrag(null));
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const boxOf = (id) => (drag?.id === id ? { ...layout.boxes[id], x: drag.x, y: drag.y, pinned: true } : layout.boxes[id]);
  const edges = drag ? layout.edges.map((e) => (e.from === drag.id || e.to === drag.id
    ? { ...e, ...edgePath(boxOf(e.from), boxOf(e.to)) } : e)) : layout.edges;

  const saveEdit = async () => {
    const ed = editing;
    setEditing(null);
    if (!ed || cancelled.current) return;
    const v = ed.value.trim();
    if (ed.kind === "node") {
      const n = byId.get(ed.id);
      if (n && v && v !== n.title) await run([{ op: "node.update", id: ed.id, set: { title: v } }]);
    } else {
      const e = map.edges.find((x) => x.id === ed.id);
      if (e && v !== (e.label || "")) await run([{ op: "edge.update", id: ed.id, set: { label: v || null } }]);
    }
  };
  const editBox = editing && (editing.kind === "node" ? layout.boxes[editing.id]
    : (() => { const e = layout.edges.find((x) => x.id === editing.id); return e && { x: e.mid.x - 60, y: e.mid.y - 12, w: 120, h: 24 }; })());

  // keyboard: only on the canvas itself or a node (not the zoom buttons, the menu or an input); Tab moves on
  const focusNode = (id) => setTimeout(() => wrap.current?.querySelector(`.mp-node[data-id="${CSS.escape(id)}"]`)?.focus({ preventScroll: false }));
  const step = (from, key) => {
    const ids = Object.keys(layout.boxes);
    if (!from) return ids.sort((a, b) => layout.boxes[a].x - layout.boxes[b].x || layout.boxes[a].y - layout.boxes[b].y)[0];
    const a = layout.boxes[from];
    const [axis, sign] = { ArrowRight: ["x", 1], ArrowLeft: ["x", -1], ArrowDown: ["y", 1], ArrowUp: ["y", -1] }[key];
    let best = null, score = Infinity;
    for (const id of ids) {
      const b = layout.boxes[id];
      const d = (b[axis] - a[axis]) * sign, side = Math.abs(axis === "x" ? b.y - a.y : b.x - a.x);
      if (id === from || d < (axis === "x" ? a.w / 2 : a.h / 2)) continue;
      if (d + 2 * side < score) { score = d + 2 * side; best = id; }
    }
    return best;
  };
  const keys = (e) => {
    if (editing || !(e.target === wrap.current || e.target.closest?.(".mp-node"))) return;
    const n = ctx.selected && byId.get(ctx.selected);
    const plain = !e.metaKey && !e.ctrlKey && !e.altKey;
    if (e.key === "Escape") { setConnect(null); setMenu(null); if (!connect && focus) setFocus(null); }
    else if (e.key.startsWith("Arrow") && plain) {
      e.preventDefault();
      const id = step(n?.id, e.key);
      if (id) { placePanel(id); ctx.select(id); focusNode(id); }
    } else if (n && e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); actions.addChild(n, NEXT_TYPES[n.type][0] || n.type); }
    else if (n && e.key === "Enter" && plain && !composing(e)) { e.preventDefault(); actions.edit(n); }
    else if (n && plain && e.key.toLowerCase() === "a") { e.preventDefault(); actions.addChild(n, NEXT_TYPES[n.type][0] || n.type); }
    else if (n && plain && e.key.toLowerCase() === "f") actions.focus(n);
    else if (n && plain && e.key === " ") { e.preventDefault(); placePanel(n.id); ctx.select(n.id); }
  };

  const W = layout.width, H = layout.height;
  const focused = focus && byId.get(focus);
  const shown = map.nodes.filter((n) => layout.boxes[n.id]);
  const tabStop = layout.boxes[ctx.selected] ? ctx.selected : null; // one node in the Tab order: the selected one
  return html`<div class="mp-map" ref=${wrap} tabIndex=${tabStop ? -1 : 0} aria-label=${t("rmap.title")} onKeyDown=${keys}>
    <div class="mp-scroll" ref=${scroller} style=${{ height: Math.max(400, Math.min(H * zoom + 24, window.innerHeight - 330)) + "px" }}
        onContextMenu=${(e) => menuAt(e, {})} onMouseDown=${(e) => { if (e.button === 0 && !e.target.closest(".mp-node, .mp-e-hit")) { ctx.select(null); setConnect(null); } }}>
      <svg ref=${svg} class=${"mp-svg" + (connect ? " connecting" : "")} width=${W * zoom} height=${H * zoom} viewBox=${`0 0 ${W} ${H}`}>
        <defs><clipPath id=${clip}><rect width=${NODE.w} height=${NODE.h} rx="7" /></clipPath></defs>
        <g class="mp-caps">${layout.columns.map((c) => html`<text x=${c.x + 2} y="24">${columnLabel(c.type)}</text>`)}</g>
        ${layers.filter((l) => l.svg && l.under).map((l) => html`<g class=${"mp-layer-g " + l.id} key=${l.id}>${l.svg(lctx)}</g>`)}
        <g class="mp-edges">
          ${edges.map((e) => html`<g key=${e.id} class=${`mp-e k-${e.kind} st-${e.status}${e.cycle ? " cyc" : ""}`}>
            ${e.cycle && html`<title>${t("rmap.cycle.edge")}</title>`}
            <path class="mp-e-line" d=${e.d} />
            <path class="mp-e-hit" d=${e.d} onContextMenu=${(ev) => menuAt(ev, { edge: e.id })} onDblClick=${() => actions.edgeLabel(e)} />
            ${e.label && html`<text class="mp-e-label" x=${e.mid.x} y=${e.mid.y - 5} text-anchor="middle">${e.label}</text>`}
          </g>`)}
        </g>
        <g class="mp-nodes">
          ${shown.map((n) => html`<${Node} key=${n.id} n=${n} box=${boxOf(n.id)} ctx=${lctx} clip=${clip}
            selected=${ctx.selected === n.id} tabbable=${tabStop === n.id} connecting=${!!connect && connect.from !== n.id} layers=${layers}
            onFocus=${() => { if (ctx.selected !== n.id) { placePanel(n.id); ctx.select(n.id); } }}
            hidden=${layout.hidden[n.id] || 0} hasKids=${layout.hasKids.has(n.id)} collapsed=${collapsed.has(n.id)}
            onDown=${down(n)} onMenu=${(e) => { placePanel(n.id); ctx.select(n.id); menuAt(e, { node: n.id }); }}
            onDbl=${(e) => { e.stopPropagation(); actions.edit(n); }} onFold=${() => actions.fold(n)} />`)}
        </g>
        ${layers.filter((l) => l.node).map((l) => html`<g class=${"mp-layer-g " + l.id} key=${l.id}>
          ${shown.map((n) => l.node(n, boxOf(n.id), lctx))}</g>`)}
        ${layers.filter((l) => l.svg && !l.under).map((l) => html`<g class=${"mp-layer-g " + l.id} key=${l.id}>${l.svg(lctx)}</g>`)}
      </svg>
      ${editBox && html`<input class="mp-edit" ref=${editRef} value=${editing.value}
        style=${{ left: editBox.x * zoom + "px", top: editBox.y * zoom + "px", width: editBox.w * zoom + "px", height: (editing.kind === "node" ? 28 : editBox.h) * zoom + "px" }}
        onInput=${(e) => setEditing({ ...editing, value: e.target.value })} onBlur=${saveEdit}
        onKeyDown=${(e) => {
          e.stopPropagation();
          if (e.key === "Enter" && !composing(e)) { e.preventDefault(); e.target.blur(); }
          if (e.key === "Escape") { cancelled.current = true; setEditing(null); if (editing.kind === "node") focusNode(editing.id); }
        }} />`}
    </div>
    ${(connect || focused) && html`<div class="mp-banner">
      ${connect ? html`<span>${t("rmap.connect.hint", { kind: t("rmap.edge." + connect.kind), from: byId.get(connect.from)?.title || "" })}</span>
          <button onClick=${() => setConnect(null)}>${t("rmap.cancel")}</button>`
        : html`<span>${t("rmap.focus.on", { title: focused.title })}</span><button onClick=${() => setFocus(null)}>${t("rmap.focus.off")}</button>`}
    </div>`}
    <div class="mp-zoom">
      <button title=${t("rmap.zoom.out")} onClick=${() => setZoom(Math.max(0.4, +(zoom - 0.1).toFixed(2)))}><${Icon} name="minus" sm /></button>
      <button class="mp-z" title=${t("rmap.zoom.reset")} onClick=${() => setZoom(1)}>${Math.round(zoom * 100)}%</button>
      <button title=${t("rmap.zoom.in")} onClick=${() => setZoom(Math.min(2, +(zoom + 0.1).toFixed(2)))}><${Icon} name="plus" sm /></button>
      <button title=${t("rmap.zoom.fit")} onClick=${() => { const w = wrap.current.clientWidth - 24; setZoom(Math.max(0.4, Math.min(1.4, +(w / W).toFixed(2)))); }}><${Icon} name="fit" sm /></button>
    </div>
    ${!map.nodes.length && html`<div class="mp-empty-map">${t("rmap.map.empty")}<button class="btn sm" onClick=${() => actions.addRoot("question")}>
      <${Icon} name="plus" sm />${t("rmap.menu.new", { type: t("rmap.type.question") })}</button></div>`}
    ${menu && html`<${Menu} menu=${menu} ctx=${ctx} actions=${actions} onClose=${() => setMenu(null)} />`}
  </div>`;
}


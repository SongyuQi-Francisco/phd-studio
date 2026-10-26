// The all-projects map (总图, #46): every research map on one time axis, metro style. A project is a line
// from its first change to now (or to its archiving); its stations are key validations and decisions
// (one per day, from the change log); an entity several projects share ("dataset:…", "paper:…", #53) is an
// interchange joining their lines, placed when it became shared. Data: GET /api/rmap/overview.
// i18n: done
// i18n-keys: rmap.status.{todo,active,verified,blocked,refuted,dropped}
// i18n-keys: rmap.type.{question,hypothesis,subline,experiment,result,decision,kill,open}
// i18n-keys: rmap_history.ov.what.{verified,decision,dropped,refuted,kill,kill_partial}
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "preact/hooks";
import { html } from "/lib.js";
import { t } from "/i18n.js";
import { Empty } from "/ui.js";
import { metroLayout } from "/rmap/timeline.js";

const LANE = 92, TOP = 64, NAMES = 184; // NAMES: the project column, as in history.css
const short = (day) => (day || "").slice(5);
const clip = (s, n) => { const x = s || ""; return x.length > n ? x.slice(0, n - 1).trimEnd() + "…" : x; };

function Station({ s, line, active, onPick }) {
  const label = s.items[0]?.title + (s.items.length > 1 ? ` +${s.items.length - 1}` : "");
  return html`<g class=${`mp-ov-st ${s.kind} ln-${line.color % 6}${active ? " on" : ""}`} transform=${`translate(${s.x} ${s.y})`}
      role="button" tabindex="0" aria-label=${`${s.day} ${label}`}
      onClick=${(e) => onPick(e, { type: "station", line, s })} onKeyDown=${(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(e, { type: "station", line, s }); } }}>
    <circle class="ring" r="7" />
    ${s.kind === "decision" ? html`<rect class="glyph" x="-2.8" y="-2.8" width="5.6" height="5.6" transform="rotate(45)" />` : html`<circle class="glyph" r="2.8" />`}
    ${s.side && html`<text class="lbl" y=${s.side === "up" ? -14 : 23} text-anchor="middle">${clip(label, 18)}</text>`}
  </g>`;
}

function Pop({ pick, onOpen, onClose }) {
  const ref = useRef();
  useEffect(() => {
    const off = (e) => { if (!ref.current?.contains(e.target) && !e.target.closest?.(".mp-ov-st, .mp-ov-ic")) onClose(); };
    const key = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("mousedown", off); window.addEventListener("keydown", key);
    return () => { window.removeEventListener("mousedown", off); window.removeEventListener("keydown", key); };
  }, []);
  const style = { left: pick.x + "px", top: pick.y + "px" };
  if (pick.type === "station") {
    const { s, line } = pick;
    return html`<div class="mp-ov-pop" ref=${ref} style=${style}>
      <div class="mp-ov-pop-h"><i class=${"ln-" + (line.color % 6)} /><b>${line.title}</b><span class="mp-num">${s.day}</span></div>
      <ul>${s.items.map((x, k) => html`<li key=${k}><span class=${"mp-ov-what " + x.what}>${t("rmap_history.ov.what." + x.what)}</span>
        <span class="mp-ov-ti">${x.title}</span><span class="mp-ov-ty">${t("rmap.type." + x.type)}</span></li>`)}</ul>
      ${onOpen && html`<button class="btn ghost sm" onClick=${() => onOpen(line.slug)}>${t("rmap_history.ov.open")}</button>`}
    </div>`;
  }
  const { ic, lines } = pick;
  return html`<div class="mp-ov-pop" ref=${ref} style=${style}>
    <div class="mp-ov-pop-h"><span class="mp-ov-icon" /><b class="mp-num">${ic.entity}</b></div>
    <p class="mp-quiet">${t("rmap_history.ov.shared", { n: ic.projects.length })}</p>
    <ul>${ic.projects.map((p) => { const ln = lines.find((l) => l.slug === p.slug); return html`<li key=${p.slug}>
      <i class=${"mp-ov-sw ln-" + ((ln?.color ?? 0) % 6)} /><span class="mp-ov-ti">${ln?.title || p.slug}</span>
      <span class="mp-ov-ty">${p.title ? clip(p.title, 36) : ""}${p.at ? " · " + t("rmap_history.ov.since", { date: p.at.slice(0, 10) }) : ""}</span></li>`; })}</ul>
  </div>`;
}

export function Overview({ ctx }) {
  const [ov, setOv] = useState(null);
  const [error, setError] = useState("");
  const [width, setWidth] = useState(0);
  const [pick, setPick] = useState(null);
  const scroller = useRef();
  const wrap = useRef();
  useEffect(() => {
    let live = true;
    const load = () => ctx.api.call("/api/rmap/overview").then((r) => { if (live) { setOv(r); setError(""); } }).catch((e) => live && setError(e.message));
    load();
    // the maps change underneath (an edit, the CLI, the other Mac): poll the cheap stamp while visible
    let stamp = null;
    const id = setInterval(async () => {
      if (document.hidden) return;
      try { const r = await ctx.api.call("/api/rmap/stamp"); if (stamp && r.stamp !== stamp) load(); stamp = r.stamp; } catch {}
    }, 4000);
    return () => { live = false; clearInterval(id); };
  }, [ctx.version]);
  useLayoutEffect(() => {
    if (!wrap.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, [!!ov]);
  const lay = useMemo(() => {
    if (!ov) return null;
    const n = Math.max(1, ov.weeks.length);
    const weekW = Math.max(64, Math.floor(((width || 900) - NAMES - 24 - 64) / n)); // fit the axis when it can
    return metroLayout(ov, { weekW, laneH: LANE, top: TOP, left: 24, right: 64 });
  }, [ov, width]);
  useEffect(() => { if (scroller.current) scroller.current.scrollLeft = scroller.current.scrollWidth; }, [lay?.width]);

  if (error) return html`<p class="muted">${t("rmap_history.error", { msg: error })}</p>`;
  if (!lay) return html`<p class="muted">${t("rmap_history.loading")}</p>`;
  if (!lay.lines.length) return html`<${Empty} icon="topology" title=${t("rmap_history.ov.empty")} hint=${t("rmap_history.ov.empty_hint")} />`;
  const every = Math.max(1, Math.ceil(64 / (lay.weeks[1] ? lay.weeks[1].x - lay.weeks[0].x : 64)));
  const nVal = lay.lines.reduce((s, l) => s + l.stations.reduce((a, st) => a + st.items.filter((x) => x.what === "verified").length, 0), 0);
  const nDec = lay.lines.reduce((s, l) => s + l.stations.reduce((a, st) => a + st.items.filter((x) => x.what !== "verified").length, 0), 0);
  const open = ctx.open ? (slug) => { setPick(null); ctx.open(slug); } : null;
  const onPick = (e, p) => {
    e.stopPropagation();
    const r = wrap.current.getBoundingClientRect(), el = e.currentTarget.getBoundingClientRect();
    const x = Math.min(Math.max(8, el.left - r.left - 20), r.width - 300);
    setPick({ ...p, x, y: el.top - r.top + el.height + 8 });
  };
  const H = lay.height;
  return html`<div class="mp-ov">
    <div class="mp-ov-head">
      <span class="mp-num">${t("rmap_history.ov.summary", { lines: lay.lines.length, val: nVal, dec: nDec, ic: lay.interchanges.length })}</span>
      <span class="mp-ov-legend">
        <span><svg width="16" height="16"><circle class="lg-ring" cx="8" cy="8" r="5.5" /><circle class="lg-val" cx="8" cy="8" r="2.2" /></svg>${t("rmap_history.ov.lg.verified")}</span>
        <span><svg width="16" height="16"><circle class="lg-ring" cx="8" cy="8" r="5.5" /><rect class="lg-dec" x="5.8" y="5.8" width="4.4" height="4.4" transform="rotate(45 8 8)" /></svg>${t("rmap_history.ov.lg.decision")}</span>
        <span><svg width="14" height="22"><rect class="lg-ic" x="2" y="2" width="10" height="18" rx="5" /></svg>${t("rmap_history.ov.lg.interchange")}</span>
      </span>
    </div>
    <div class="mp-ov-grid" ref=${wrap}>
      <div class="mp-ov-names" style=${{ height: H + "px" }}>
        ${lay.lines.map((l) => html`<div class="mp-ov-name" key=${l.slug} style=${{ top: l.y - 22 + "px" }}>
          <div class="mp-ov-nt"><i class=${"mp-ov-sw ln-" + (l.color % 6)} />${open ? html`<button class="mp-ov-go" onClick=${() => open(l.slug)}>${l.title}</button>` : html`<span>${l.title}</span>`}</div>
          <div class="mp-ov-nm mp-num">${t("rmap_history.ov.counts", { nodes: l.nodes, verified: l.verified })}${l.archived ? " · " + t("rmap_history.ov.archived") : ""}</div>
        </div>`)}
      </div>
      <div class="mp-ov-scroll" ref=${scroller} onScroll=${() => pick && setPick(null)}>
        <svg class="mp-ov-svg" width=${lay.width} height=${H} viewBox=${`0 0 ${lay.width} ${H}`}>
          <g class="mp-ov-weeks">${lay.weeks.map((w) => html`<g key=${w.i}>
            <line x1=${w.x} x2=${w.x} y1="40" y2=${H - 8} />
            ${(w.i % every === 0) && html`<text x=${w.x + 4} y="18"><title>${w.start} – ${w.end}</title>${w.label}</text><text class="d" x=${w.x + 4} y="31">${short(w.start)}</text>`}
          </g>`)}</g>
          <g class="mp-ov-now"><line x1=${lay.now} x2=${lay.now} y1="40" y2=${H - 8} /><text x=${lay.now + 4} y="50">${t("rmap_history.now")}</text></g>
          ${lay.lines.map((l) => html`<g key=${l.slug} class=${"mp-ov-line ln-" + (l.color % 6) + (l.archived ? " archived" : "")}>
            <line class="track" x1=${l.x1} x2=${l.x2} y1=${l.y} y2=${l.y} />
            <line class="term" x1=${l.x1} x2=${l.x1} y1=${l.y - 8} y2=${l.y + 8} />
            ${l.archived && html`<line class="term" x1=${l.x2} x2=${l.x2} y1=${l.y - 8} y2=${l.y + 8} />`}
          </g>`)}
          ${lay.interchanges.map((ic) => html`<g key=${ic.entity} class="mp-ov-ic" role="button" tabindex="0" aria-label=${ic.entity}
              onClick=${(e) => onPick(e, { type: "ic", ic, lines: lay.lines })}
              onKeyDown=${(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(e, { type: "ic", ic, lines: lay.lines }); } }}>
            <title>${ic.entity}</title>
            <rect x=${ic.x - 8} y=${ic.y1 - 8} width="16" height=${ic.y2 - ic.y1 + 16} rx="8" />
            ${ic.ys.map((y) => html`<circle cx=${ic.x} cy=${y} r="3" />`)}
            <line class="lead" x1=${ic.x} x2=${ic.x} y1=${ic.y2 + 8} y2=${ic.y2 + 30 + ic.row * 14} />
            <text x=${ic.x} y=${ic.y2 + 42 + ic.row * 14} text-anchor="middle">${clip(ic.entity, 26)}</text>
          </g>`)}
          ${lay.lines.map((l) => l.stations.map((s) => html`<${Station} key=${l.slug + s.day} s=${s} line=${l}
            active=${pick?.type === "station" && pick.s === s} onPick=${onPick} />`))}
        </svg>
      </div>
      ${pick && html`<${Pop} pick=${pick} onOpen=${open} onClose=${() => setPick(null)} />`}
    </div>
  </div>`;
}

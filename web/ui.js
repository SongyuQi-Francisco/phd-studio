// Small UI pieces shared by the dashboard pages: paper thumbnails, skeletons, empty states,
// animated collapse, view transitions and the days-left pill.
import { useEffect, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";

// A few extra icons in the same stroke style as lib.js Icon.
const GLYPHS = {
  chart: "M4 20h16M7 16v-5M12 16V6M17 16v-8",
  flag: "M5 21V4M5 4h11l-2 4 2 4H5",
  layers: "M12 3 2 8l10 5 10-5zM2 13l10 5 10-5",
};
export function Glyph({ name, sm }) {
  return html`<svg class=${"i" + (sm ? " sm" : "")} viewBox="0 0 24 24" aria-hidden="true"><path d=${GLYPHS[name]} /></svg>`;
}

export const daysTo = (iso) => Math.ceil((new Date(iso) - new Date()) / 86400000);

export function Days({ d }) {
  if (d == null) return null;
  const cls = d < 0 ? "past" : d <= 14 ? "hot" : d <= 45 ? "warm" : "";
  return html`<span class=${"days " + cls}>${d < 0 ? `${-d} 天前` : d === 0 ? "今天" : `${d} 天`}</span>`;
}

// Run a DOM-changing state update inside a view transition where WebKit supports it.
export function transition(fn) {
  if (!document.startViewTransition || document.hidden || matchMedia("(prefers-reduced-motion: reduce)").matches) { fn(); return; }
  const t = document.startViewTransition(() => { fn(); return new Promise((r) => setTimeout(r, 30)); });
  for (const p of [t.ready, t.finished, t.updateCallbackDone]) p?.catch(() => {}); // a skipped transition still applies fn
}

// Which papers have a local PDF (asked once per list, so missing ones never hit /api/thumb).
const have = new Set();
const failed = new Set();
export async function loadThumbs(keys) {
  const ask = keys.filter((k) => k && !have.has(k));
  if (!ask.length) return;
  try { (await api("/api/thumbs", { json: { keys: ask } })).have.forEach((k) => have.add(k)); } catch {}
}

// First page of the paper's local / cached PDF, or a quiet paper-shaped placeholder.
// `ready` changes once loadThumbs() has answered, so the placeholder can swap to the image.
export function Thumb({ k, title, size = "md", badge, ready }) {
  const ok = () => !!k && have.has(k) && !failed.has(k);
  const [bad, setBad] = useState(!ok());
  useEffect(() => setBad(!ok()), [k, ready]);
  const initials = (title || k || "?").replace(/[^A-Za-z0-9一-鿿 ]/g, " ").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  return html`<div class=${"thumb " + size + (bad ? " ph" : "")} aria-hidden="true">
    ${bad
      ? html`<div class="ph-sheet"><b>${initials}</b><i /><i /><i class="s" /><i /><i class="s" /></div>`
      : html`<img src=${`/api/thumb/${encodeURIComponent(k)}`} loading="lazy" decoding="async" alt=""
          onError=${() => { failed.add(k); setBad(true); }} />`}
    ${badge}
  </div>`;
}

export function Skeleton({ tiles = 4, rows = 5, chart = true }) {
  return html`<div class="page skel-page"><div class="page-inner wide">
    <div class="skel" style="width:140px;height:30px;margin-bottom:22px" />
    <div class="kpis">${Array.from({ length: tiles }, () => html`<div class="kpi skel-card"><div class="skel" style="width:40%;height:26px" /><div class="skel" style="width:70%;height:12px;margin-top:10px" /></div>`)}</div>
    ${chart && html`<div class="card skel-card" style="height:200px"><div class="skel" style="width:30%;height:14px" /></div>`}
    ${Array.from({ length: rows }, () => html`<div class="card skel-card row-skel"><div class="skel" style="width:44px;height:56px;border-radius:6px" />
      <div style="flex:1"><div class="skel" style="width:55%;height:14px" /><div class="skel" style="width:85%;height:11px;margin-top:9px" /></div></div>`)}
  </div></div>`;
}

export function Empty({ icon = "check", title, hint, children, compact }) {
  return html`<div class=${"empty-state" + (compact ? " compact" : "")}>
    <div class="es-icon"><${Icon} name=${icon} /></div>
    <div class="es-text"><b>${title}</b>${hint && html`<p>${hint}</p>`}</div>${children}
  </div>`;
}

// Height animation without measuring: a 0fr -> 1fr grid row. Children mount on first open and stay.
export function Collapse({ open, children }) {
  const [seen, setSeen] = useState(open);
  useEffect(() => { if (open) setSeen(true); }, [open]);
  return html`<div class=${"collapse" + (open ? " open" : "")}><div class="collapse-in">${(open || seen) && children}</div></div>`;
}

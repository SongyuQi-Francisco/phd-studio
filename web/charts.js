// Small SVG charts shared by the pages: Sparkline, Bars, Ring, Progress, Donut, HeatStrip, Funnel.
// Colors are CSS variables (--series-*, --ord-*, --heat-*), so light / dark mode follow the theme.
// Every chart has a hover tooltip and a visually hidden table, so no value is reachable by color alone.
import { useEffect, useRef, useState } from "preact/hooks";
import { html } from "/lib.js";

export const C = { blue: "var(--series-1)", orange: "var(--series-2)", aqua: "var(--series-3)", gray: "var(--series-0)" };
const nice = (v) => (v >= 1000 ? (v / 1000).toFixed(v >= 10000 ? 0 : 1) + "K" : String(Math.round(v * 10) / 10));

function useWidth(min = 60) {
  const ref = useRef();
  const [w, setW] = useState(0);
  useEffect(() => {
    let raf = 0;
    const ro = new ResizeObserver(([e]) => {
      cancelAnimationFrame(raf); // measure now, re-render next frame: avoids ResizeObserver loop warnings
      const v = Math.max(min, Math.floor(e.contentRect.width));
      raf = requestAnimationFrame(() => setW(v));
    });
    ro.observe(ref.current);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, []);
  return [ref, w];
}

// One tooltip per chart, placed inside the chart box and kept within it.
function useTip(boxRef) {
  const [tip, setTip] = useState(null);
  const show = (e, body) => {
    const r = boxRef.current.getBoundingClientRect();
    setTip({ x: e.clientX - r.left, y: e.clientY - r.top, w: r.width, body });
  };
  const el = tip && html`<div class="ch-tip" style=${{ left: Math.max(4, Math.min(tip.x + 12, tip.w - 172)) + "px", top: tip.y + 14 + "px" }}>${tip.body}</div>`;
  return [el, show, () => setTip(null)];
}

const TipRow = ({ color, value, label, line }) => html`<div class="ch-tip-row">
  ${color && html`<i class=${line ? "key line" : "key"} style=${{ background: color }} />`}<b>${value}</b><span>${label}</span></div>`;

function SrTable({ head, rows }) {
  return html`<table class="sr-only"><thead><tr>${head.map((h) => html`<th>${h}</th>`)}</tr></thead>
    <tbody>${rows.map((r) => html`<tr>${r.map((c) => html`<td>${c}</td>`)}</tr>`)}</tbody></table>`;
}

export function Legend({ items, shape = "rect" }) {
  return html`<div class="ch-legend">${items.map((it) => html`<span class=${it.muted ? "muted" : ""}>
    <i class=${"key " + shape} style=${{ background: it.color }} />${it.label}${it.value != null && html`<b>${it.value}</b>`}</span>`)}</div>`;
}

// ---------- sparkline: one series, area wash, end dot, crosshair ----------
export function Sparkline({ values, labels = [], height = 36, color = C.blue, format = nice, unit = "" }) {
  const [ref, w] = useWidth(60);
  const [tip, show, hide] = useTip(ref);
  const [hi, setHi] = useState(null);
  const n = values.length, pad = 5;
  const max = Math.max(1, ...values);
  const x = (i) => pad + (n <= 1 ? 0 : (i / (n - 1)) * (w - 2 * pad));
  const y = (v) => height - pad - (v / max) * (height - 2 * pad);
  const pts = values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  const move = (e) => {
    if (!n) return;
    const r = ref.current.getBoundingClientRect();
    const i = Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left - pad) / Math.max(1, w - 2 * pad)) * (n - 1))));
    setHi(i);
    show(e, html`<${TipRow} color=${color} line value=${format(values[i]) + unit} label=${labels[i] || ""} />`);
  };
  return html`<div class="ch spark" ref=${ref} style=${{ height: height + "px" }} onMouseMove=${move} onMouseLeave=${() => { setHi(null); hide(); }}>
    ${w > 0 && n > 0 && html`<svg width=${w} height=${height} aria-hidden="true">
      <polygon points=${`${x(0)},${height - pad} ${pts} ${x(n - 1)},${height - pad}`} class="ch-area" style=${{ fill: color }} />
      <polyline points=${pts} class="ch-line" style=${{ stroke: color }} />
      ${hi != null && html`<line x1=${x(hi)} x2=${x(hi)} y1="0" y2=${height} class="ch-cross" />`}
      <circle cx=${x(hi ?? n - 1)} cy=${y(values[hi ?? n - 1])} r="4" class="ch-dot" style=${{ fill: color }} />
    </svg>`}
    ${tip}
    <${SrTable} head=${["", ""]} rows=${values.map((v, i) => [labels[i] || i + 1, v])} />
  </div>`;
}

// ---------- columns: one or more series per band, shared hover per band ----------
export function Bars({ labels, series, height = 132, format = nice, every, legend = true }) {
  const [ref, w] = useWidth(160);
  const [tip, show, hide] = useTip(ref);
  const [hi, setHi] = useState(null);
  const L = 26, B = 20, T = 8;
  const n = labels.length, k = series.length;
  const max = Math.max(1, ...series.flatMap((s) => s.values));
  const frac = series.every((s) => s.values.every((v) => v <= 1 && Math.round(v) !== v || v === 0));
  const fmax = Math.max(...series.flatMap((s) => s.values));
  const top = frac ? Math.min(1, Math.ceil((fmax * 10) / 0.98) / 10 || 1) : max <= 2 ? max : Math.ceil(max / 2) * 2;
  const ticks = top <= 2 && !frac ? [0, top] : [0, top / 2, top];
  const ih = height - B - T, band = (w - L) / Math.max(1, n);
  const bw = Math.max(3, Math.min(12, (band * 0.72 - (k - 1) * 2) / k));
  const y = (v) => T + ih - (v / top) * ih;
  const step = every || Math.max(1, Math.ceil(n / Math.max(1, Math.floor((w - L) / 46))));
  const col = (x0, v, color, key) => {
    const h = Math.max(v > 0 ? 2 : 0, (v / top) * ih), r = Math.min(4, bw / 2, h);
    if (!h) return null;
    const yb = T + ih;
    return html`<path key=${key} style=${{ fill: color }} d=${`M${x0} ${yb} V${yb - h + r} Q${x0} ${yb - h} ${x0 + r} ${yb - h} H${x0 + bw - r} Q${x0 + bw} ${yb - h} ${x0 + bw} ${yb - h + r} V${yb} Z`} />`;
  };
  return html`<div class="ch bars">
    ${legend && k > 1 && html`<${Legend} items=${series.map((s) => ({ label: s.name, color: s.color }))} />`}
    <div class="ch-box" ref=${ref} onMouseLeave=${() => { setHi(null); hide(); }}>
      ${w > 0 && html`<svg width=${w} height=${height} aria-hidden="true">
        ${ticks.map((t) => html`<line x1=${L} x2=${w} y1=${y(t)} y2=${y(t)} class="ch-grid" />
          <text x=${L - 6} y=${y(t) + 3.5} text-anchor="end" class="ch-tick">${format(t)}</text>`)}
        ${labels.map((lb, i) => {
          const x0 = L + i * band, g0 = x0 + (band - (k * bw + (k - 1) * 2)) / 2;
          return html`<g key=${lb}>
            ${hi === i && html`<rect x=${x0 + 1} y=${T} width=${band - 2} height=${ih} rx="4" class="ch-band" />`}
            ${series.map((s, j) => col(g0 + j * (bw + 2), s.values[i], s.color, j))}
            ${(i % step === 0 || i === n - 1) && (i === n - 1 || n - 1 - i >= step) && html`<text x=${x0 + band / 2} y=${height - 5} text-anchor="middle" class=${"ch-tick" + (i === n - 1 ? " strong" : "")}>${lb}</text>`}
            <rect x=${x0} y="0" width=${band} height=${height} fill="transparent"
              onMouseMove=${(e) => { setHi(i); show(e, html`<div class="ch-tip-h">${lb}</div>${series.map((s) => html`<${TipRow} color=${s.color} value=${format(s.values[i])} label=${s.name} />`)}`); }} />
          </g>`;
        })}
      </svg>`}
      ${tip}
    </div>
    <${SrTable} head=${["", ...series.map((s) => s.name)]} rows=${labels.map((lb, i) => [lb, ...series.map((s) => s.values[i])])} />
  </div>`;
}

// ---------- ring: progress or countdown, center slot for the number ----------
export function Ring({ value, max = 1, size = 76, stroke = 7, color = C.blue, children, title }) {
  const r = (size - stroke) / 2, len = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(1, max ? value / max : 0));
  const [on, setOn] = useState(false);
  useEffect(() => { const t = requestAnimationFrame(() => setOn(true)); return () => cancelAnimationFrame(t); }, []);
  return html`<div class="ring" style=${{ width: size + "px", height: size + "px" }} title=${title || ""}>
    <svg width=${size} height=${size} viewBox=${`0 0 ${size} ${size}`} aria-hidden="true">
      <circle cx=${size / 2} cy=${size / 2} r=${r} class="ring-track" style=${{ stroke: color, strokeWidth: stroke }} />
      <circle cx=${size / 2} cy=${size / 2} r=${r} class="ring-arc" transform=${`rotate(-90 ${size / 2} ${size / 2})`}
        style=${{ stroke: color, strokeWidth: stroke, strokeDasharray: len, strokeDashoffset: on ? len * (1 - frac) : len }} />
    </svg>
    <div class="ring-c">${children}</div>
  </div>`;
}

// ---------- linear meter; the track is a light step of the same hue ----------
export function Progress({ value, max = 1, color = C.blue, over, label, thin }) {
  const frac = Math.max(0, Math.min(1, max ? value / max : 0));
  const c = over ? "var(--red)" : color;
  return html`<div class=${"meter" + (thin ? " thin" : "")} title=${label || `${value}/${max}`} role="meter" aria-valuenow=${value} aria-valuemax=${max}>
    <i class="meter-track" style=${{ background: c }} /><i class="meter-fill" style=${{ width: frac * 100 + "%", background: c }} /></div>`;
}

// ---------- donut: part-to-whole, <= 6 segments, 2px surface gaps ----------
export function Donut({ data, size = 128, thickness = 14, children, onPick }) {
  const boxRef = useRef();
  const [tip, show, hide] = useTip(boxRef);
  const [hi, setHi] = useState(null);
  const total = data.reduce((a, d) => a + d.value, 0);
  const r = size / 2 - thickness / 2 - 2, cx = size / 2;
  const segs = data.filter((d) => d.value > 0);
  const gap = segs.length > 1 ? 2 / r : 0;
  let a0 = -Math.PI / 2;
  const arc = (a, b) => {
    const p = (t) => `${cx + r * Math.cos(t)} ${cx + r * Math.sin(t)}`;
    return `M${p(a)} A${r} ${r} 0 ${b - a > Math.PI ? 1 : 0} 1 ${p(b)}`;
  };
  return html`<div class="ch donut" ref=${boxRef} style=${{ width: size + "px", height: size + "px" }} onMouseLeave=${() => { setHi(null); hide(); }}>
    <svg width=${size} height=${size} aria-hidden="true">
      <circle cx=${cx} cy=${cx} r=${r} class="donut-track" style=${{ strokeWidth: thickness }} />
      ${segs.map((d) => {
        const sweep = (d.value / total) * Math.PI * 2;
        const a = a0 + gap / 2, b = a0 + sweep - gap / 2;
        a0 += sweep;
        if (segs.length === 1) return html`<circle cx=${cx} cy=${cx} r=${r} class="donut-seg" style=${{ stroke: d.color, strokeWidth: thickness }} />`;
        return html`<path d=${arc(a, Math.max(a + 0.001, b))} class=${"donut-seg" + (hi === d.label ? " hi" : "")}
          style=${{ stroke: d.color, strokeWidth: hi === d.label ? thickness + 3 : thickness }}
          onMouseMove=${(e) => { setHi(d.label); show(e, html`<${TipRow} color=${d.color} value=${`${d.value} · ${Math.round((d.value / total) * 100)}%`} label=${d.label} />`); }}
          onClick=${() => onPick?.(d)} />`;
      })}
    </svg>
    <div class="donut-c">${children}</div>
    ${tip}
    <${SrTable} head=${["", ""]} rows=${data.map((d) => [d.label, d.value])} />
  </div>`;
}

// ---------- heat strip: days x weeks, one hue light -> dark ----------
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export function HeatStrip({ counts, weeks: fixed, unit = "件", cell = 11 }) {
  const [boxRef, w] = useWidth(120);
  const [tip, show, hide] = useTip(boxRef);
  const weeks = fixed || Math.max(8, Math.min(53, Math.floor((w + 3) / (cell + 3))));
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const start = new Date(today); start.setDate(today.getDate() - today.getDay() - (weeks - 1) * 7); // Sunday, weeks ago
  const max = Math.max(1, ...Object.values(counts));
  const level = (v) => (!v ? 0 : Math.min(4, Math.ceil((v / max) * 4)));
  const g = 3, cols = [];
  for (let wk = 0; wk < weeks; wk++) {
    const days = [];
    for (let d = 0; d < 7; d++) {
      const t = new Date(start); t.setDate(start.getDate() + wk * 7 + d);
      days.push(t > today ? null : { key: iso(t), date: t, v: counts[iso(t)] || 0 });
    }
    cols.push(days);
  }
  const months = cols.map((c, i) => (c[0] && (i === 0 || c[0].date.getDate() <= 7) ? `${c[0].date.getMonth() + 1}月` : ""));
  const W = weeks * (cell + g), H = 7 * (cell + g) + 14;
  const total = Object.entries(counts).filter(([k]) => k >= iso(start)).reduce((a, [, v]) => a + v, 0);
  return html`<div class="ch heat" ref=${boxRef} onMouseLeave=${hide}>
    ${w > 0 && html`<svg width=${W} height=${H} aria-hidden="true">
      ${months.map((m, i) => m && html`<text x=${i * (cell + g)} y="9" class="ch-tick">${m}</text>`)}
      ${cols.map((c, i) => c.map((d, j) => d && html`<rect x=${i * (cell + g)} y=${14 + j * (cell + g)} width=${cell} height=${cell} rx="2.5"
        class=${"heat-" + level(d.v) + (d.key === iso(today) ? " today" : "")}
        onMouseMove=${(e) => show(e, html`<${TipRow} value=${`${d.v} ${unit}`} label=${d.date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric", weekday: "short" })} />`)} />`))}
    </svg>`}
    <div class="heat-foot"><span>${weeks} 周共 <b>${total}</b> ${unit}</span>
      <span class="heat-scale">少${[0, 1, 2, 3, 4].map((l) => html`<i class=${"heat-" + l} />`)}多</span></div>
    ${tip}
  </div>`;
}

// ---------- funnel: ordered stages, conversion between them ----------
export function Funnel({ steps, onPick, active }) {
  const max = Math.max(1, ...steps.map((s) => s.value));
  return html`<div class="funnel">${steps.map((s, i) => {
    const prev = i > 0 ? steps[i - 1].value : null;
    return html`<button class=${"fn-row" + (active === s.key ? " on" : "")} onClick=${() => onPick?.(s)}>
      <span class="fn-label">${s.label}</span>
      <span class="fn-bar"><i style=${{ width: Math.max(s.value ? 3 : 0, (s.value / max) * 100) + "%", background: s.color || `var(--ord-${Math.min(3, i + 1)})` }} /></span>
      <b class="fn-v">${s.value}</b>
      <span class="fn-rate">${prev ? Math.round((s.value / prev) * 100) + "%" : ""}</span>
    </button>`;
  })}</div>`;
}

// ---------- shared date bucketing for the pages ----------
export function weekStarts(n = 12) {
  const d = new Date(); d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); // Monday of this week
  const out = [];
  for (let i = n - 1; i >= 0; i--) { const t = new Date(d); t.setDate(d.getDate() - i * 7); out.push(t); }
  return out;
}
export function perWeek(dates, n = 12) {
  const starts = weekStarts(n);
  const counts = starts.map(() => 0);
  const end = new Date(); end.setHours(23, 59, 59, 999);
  for (const s of dates) {
    if (!s) continue;
    const t = new Date(s.length <= 10 ? s + "T12:00:00" : s);
    if (t > end) continue;
    for (let i = n - 1; i >= 0; i--) if (t >= starts[i]) { counts[i]++; break; }
  }
  return { labels: starts.map((t) => `${t.getMonth() + 1}/${t.getDate()}`), counts };
}
export const dayKey = (s) => (s || "").slice(0, 10);

// Small SVG charts for the 追踪 page: sparkline, heatmap, stacked/single columns, horizontal bars, avatars.
// Marks follow the TimelineChart palette (--series-1/2/3); sequential = one blue ramp (--heat-1..6);
// text always wears text tokens. Every chart has a hover readout; values stay reachable in lists/tooltips.
// i18n: done
import { useEffect, useRef, useState } from "preact/hooks";
import { html } from "/lib.js";
import { t, formatNumber } from "/i18n.js";

export const fmtN = (n) => n == null ? "—" : n >= 1e5 ? (n / 1e3).toFixed(0) + "K" : n >= 1e4 ? (n / 1e3).toFixed(1) + "K" : formatNumber(n);
// "12 papers": what the charts print next to a count. Callers pass their own for other units.
const papers = (n) => t("common.unit.papers", { n });

function useWidth(min = 200) {
  const ref = useRef();
  const [w, setW] = useState(0);
  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setW(Math.max(min, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function Tip({ tip, w }) {
  if (!tip) return null;
  return html`<div class="tk-tip" style=${{ left: Math.max(0, Math.min(tip.x + 10, w - 190)) + "px", top: tip.y + 12 + "px" }}>${tip.body}</div>`;
}

// One series over time; the last point carries an end-dot. Hover snaps to the nearest point.
export function Sparkline({ values, labels = [], w = 112, h = 30, fmt = fmtN }) {
  const [hover, setHover] = useState(null);
  if (!values?.length) return html`<svg class="tk-spark" width=${w} height=${h} />`;
  const max = Math.max(1, ...values), pad = 5;
  const x = (i) => pad + (values.length === 1 ? (w - 2 * pad) / 2 : (i / (values.length - 1)) * (w - 2 * pad));
  const y = (v) => h - pad - (v / max) * (h - 2 * pad);
  const pts = values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  const area = `M${x(0)},${h - pad} L${pts.replaceAll(" ", " L")} L${x(values.length - 1)},${h - pad} Z`;
  const move = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left - pad) / (w - 2 * pad)) * (values.length - 1));
    setHover(Math.max(0, Math.min(values.length - 1, i)));
  };
  const i = hover ?? values.length - 1;
  return html`<span class="tk-spark-wrap">
    <svg class="tk-spark" width=${w} height=${h} onMouseMove=${move} onMouseLeave=${() => setHover(null)}>
      <path d=${area} class="tk-spark-area" />
      <polyline points=${pts} class="tk-spark-line" />
      ${hover != null && html`<line x1=${x(i)} x2=${x(i)} y1="2" y2=${h - 2} class="tk-cross" />`}
      <circle cx=${x(i)} cy=${y(values[i])} r="4" class="tk-spark-dot" />
    </svg>
    <span class="tk-spark-read">${labels[i] != null ? html`<em>${labels[i]}</em> ` : ""}<b>${fmt(values[i])}</b></span>
  </span>`;
}

const heatStep = (v, max) => (v <= 0 ? 0 : Math.max(1, Math.min(6, Math.ceil((v / Math.max(1, max)) * 6))));

// rows: [{key, label, sub?, values[], dim?}], cols: column labels. Cell = sequential blue by count.
export function Heatmap({ rows, cols, colTitles, onCell, onRow, active, fmt = papers, labelW = 150 }) {
  const [ref, w] = useWidth(320);
  const [tip, setTip] = useState(null);
  const max = Math.max(1, ...rows.flatMap((r) => r.values));
  const cell = Math.max(14, Math.min(cols.length <= 8 ? 120 : 34, Math.floor((w - labelW - 8) / Math.max(1, cols.length))));
  const ch = Math.min(22, Math.round(cell * 0.72));
  const gap = 2, top = 22, H = top + rows.length * (ch + gap) + 4;
  return html`<div class="tk-heat" ref=${ref}>
    ${w > 0 && html`<svg width=${w} height=${H} onMouseLeave=${() => setTip(null)}>
      ${cols.map((c, j) => (cols.length <= 14 || (cols.length - 1 - j) % 2 === 0) && html`
        <text x=${labelW + j * cell + (cell - gap) / 2} y="14" text-anchor="middle" class="tk-tick">${c}</text>`)}
      ${rows.map((r, i) => {
        const y = top + i * (ch + gap);
        return html`<g class=${"tk-heat-row" + (r.dim ? " dim" : "") + (active === r.key ? " on" : "")}>
          <text x=${labelW - 8} y=${y + ch / 2 + 4} text-anchor="end" class="tk-row-label" onClick=${() => onRow?.(r)}>${r.label.length > 20 ? r.label.slice(0, 19) + "…" : r.label}</text>
          ${r.values.map((v, j) => html`<rect x=${labelW + j * cell} y=${y} width=${cell - gap} height=${ch} rx="3"
            class=${"tk-cell h" + heatStep(v, max)}
            onMouseEnter=${() => setTip({ x: labelW + j * cell, y: y + ch, body: html`<b>${fmt(v)}</b><span>${r.label} · ${(colTitles || cols)[j]}</span>` })}
            onClick=${() => onCell?.(r, j, v)} />`)}
        </g>`;
      })}
    </svg>`}
    <div class="tk-heat-legend"><span>0</span>${[1, 2, 3, 4, 5, 6].map((k) => html`<i class=${"tk-cell-key h" + k} />`)}<span>${fmt(max)}</span></div>
    <${Tip} tip=${tip} w=${w} />
  </div>`;
}

// Columns over categories. series: [{key, label, cls}] stacked bottom-up with a 2px surface gap.
export function Columns({ data, series, height = 150, onPick, picked, showLegend = true, fmt = papers }) {
  const [ref, w] = useWidth(240);
  const [tip, setTip] = useState(null);
  const tot = (d) => series.reduce((s, k) => s + (d.parts[k.key] || 0), 0);
  const max = Math.max(1, ...data.map(tot));
  const L = 26, B = 20, T = 14;
  const band = (w - L) / Math.max(1, data.length);
  const bw = Math.min(24, Math.max(6, band * 0.62));
  const y = (v) => T + (1 - v / max) * (height - T - B);
  const ticks = [0, Math.round(max / 2), max].filter((v, i, a) => a.indexOf(v) === i);
  const maxI = data.reduce((b, d, i) => (tot(d) > tot(data[b]) ? i : b), 0);
  return html`<div class="tk-cols" ref=${ref}>
    ${showLegend && series.length > 1 && html`<div class="tk-legend">${series.map((s) => html`<span><i class=${"tk-key " + s.cls} />${s.label}</span>`)}</div>`}
    ${w > 0 && html`<svg width=${w} height=${height} onMouseLeave=${() => setTip(null)}>
      ${ticks.map((tick) => html`<line x1=${L} x2=${w} y1=${y(tick)} y2=${y(tick)} class="tk-grid" /><text x=${L - 6} y=${y(tick) + 4} text-anchor="end" class="tk-tick">${tick}</text>`)}
      ${data.map((d, i) => {
        const cx = L + i * band + band / 2;
        let base = 0;
        const total = tot(d);
        const segs = series.map((s, k) => {
          const v = d.parts[s.key] || 0;
          if (!v) return null;
          const y0 = y(base), y1 = y(base + v);
          base += v;
          const isTop = series.slice(k + 1).every((n) => !(d.parts[n.key] > 0));
          const h = Math.max(1, y0 - y1 - (base > v ? 2 : 0));
          const r = isTop ? Math.min(4, h, bw / 2) : 0;
          const x0 = cx - bw / 2, yt = y1;
          const path = `M${x0},${yt + h} L${x0},${yt + r} Q${x0},${yt} ${x0 + r},${yt} L${x0 + bw - r},${yt} Q${x0 + bw},${yt} ${x0 + bw},${yt + r} L${x0 + bw},${yt + h} Z`;
          return html`<path d=${path} class=${"tk-bar " + s.cls} />`;
        });
        return html`<g class=${"tk-col" + (picked != null && picked !== i ? " dim" : "")}
          onMouseEnter=${() => setTip({ x: cx, y: y(total) - 4, body: html`<b>${fmt(total)}</b><span>${d.title || d.label}</span>
            ${series.length > 1 && series.map((s) => html`<span class="tk-tip-row"><i class=${"tk-line-key " + s.cls} />${s.label} <b>${d.parts[s.key] || 0}</b></span>`)}` })}
          onClick=${() => onPick?.(i, d)}>
          <rect x=${cx - band / 2} y=${T} width=${band} height=${height - T - B} fill="transparent" />
          ${segs}
          ${total > 0 && (i === maxI || i === data.length - 1) && html`<text x=${cx} y=${y(total) - 4} text-anchor="middle" class="tk-val">${total}</text>`}
          <text x=${cx} y=${height - 5} text-anchor="middle" class="tk-tick">${d.label}</text>
        </g>`;
      })}
    </svg>`}
    <${Tip} tip=${tip} w=${w} />
  </div>`;
}

// Ranked items as horizontal bars, value at the tip. items: [[label, n]].
export function HBars({ items, onPick, picked, max: maxIn, cls = "s1" }) {
  if (!items?.length) return html`<div class="muted small">${t("track.none_yet")}</div>`;
  const max = maxIn || Math.max(1, ...items.map((x) => x[1]));
  return html`<div class="tk-hbars">${items.map(([label, n]) => html`
    <button class=${"tk-hbar" + (picked === label ? " on" : "") + (onPick ? "" : " static")} title=${t("track.hbar_title", { label, n })} onClick=${() => onPick?.(label)}>
      <span class="tk-hbar-l">${label}</span>
      <span class="tk-hbar-track"><i class=${cls} style=${{ width: Math.max(3, (n / max) * 100) + "%" }} /></span>
      <span class="tk-hbar-n">${n}</span>
    </button>`)}</div>`;
}

const initials = (name) => {
  const parts = (name || "?").replace(/[()（）]/g, " ").split(/[\s-]+/).filter(Boolean);  // i18n-ignore: full-width brackets in names
  if (/[一-鿿]/.test(name)) return name.slice(0, 1); // i18n-ignore: a Chinese name shows its first character
  return ((parts[0]?.[0] || "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
};

// Photo, else institution logo, else initials.
export function Avatar({ name, srcs = [], size = 44, square }) {
  const [i, setI] = useState(0);
  const list = srcs.filter(Boolean);
  useEffect(() => setI(0), [list.join("|")]);
  const style = { width: size + "px", height: size + "px", fontSize: Math.round(size * 0.38) + "px" };
  if (i < list.length) {
    return html`<img class=${"tk-avatar" + (square ? " sq" : "") + (list[i].includes("/logo/") ? " logo" : "")} style=${style}
      src=${list[i]} alt="" loading="lazy" onError=${() => setI(i + 1)} />`;
  }
  return html`<span class=${"tk-avatar ini" + (square ? " sq" : "")} style=${style} aria-hidden="true">${initials(name)}</span>`;
}

// Three-tier strip (A/B/C) as an ordinal ramp, with counts in the tooltip.
export function TierStrip({ tiers, w = 90 }) {
  const a = tiers.A || 0, b = tiers.B || 0, c = tiers.C || 0;
  if (!(a + b + c)) return html`<span class="tk-tierstrip empty" style=${{ width: w + "px" }} />`;
  return html`<span class="tk-tierstrip" style=${{ width: w + "px" }} title=${`Tier A ${a} · B ${b} · C ${c}`}>
    ${[["A", a], ["B", b], ["C", c]].map(([k, n]) => n > 0 && html`<i class=${"t" + k} style=${{ flexGrow: n }} />`)}
  </span>`;
}

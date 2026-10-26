// Charts of the results panel, on the shared look of /charts.js: a ranked comparison (configs against the
// baselines, drawn with its Progress meter), grouped columns across datasets (its Bars) and a trend line per
// config. Colors are the page's series tokens, so light and dark follow the theme.
// i18n: done
import { useEffect, useRef, useState } from "preact/hooks";
import { html } from "/lib.js";
import { Bars, C, Progress } from "/charts.js";
import { formatDate, t } from "/i18n.js";
import { fmt, signed } from "/rmap/results/data.js";

const tick = (v) => String(Number(v.toPrecision(3)));
const LINES = [C.aqua, C.blue, C.orange, "var(--mp-blocked)", "var(--red)"];
const DASH = ["5 3", "1.5 3", "7 2 1.5 2", "3 3"];

function useWidth(min = 120) {
  const ref = useRef();
  const [w, setW] = useState(0);
  useEffect(() => {
    let raf = 0;
    const ro = new ResizeObserver(([e]) => {
      cancelAnimationFrame(raf);
      const v = Math.max(min, Math.floor(e.contentRect.width));
      raf = requestAnimationFrame(() => setW(v));
    });
    ro.observe(ref.current);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, []);
  return [ref, w];
}

// Share of the bar: zero based while every value is positive (a bar is a ratio), min-max otherwise.
function share(rows) {
  const vals = rows.map((r) => r.value);
  const hi = Math.max(...vals), lo = Math.min(...vals);
  if (lo > 0) return (v) => v / hi;
  return (v) => (hi === lo ? 1 : 0.08 + 0.92 * ((v - lo) / (hi - lo)));
}

// One metric on one dataset: a row per config, best first. Baselines grey, the best aqua, the rest blue;
// the gap to the best baseline in the right-hand column.
export function Compare({ data, limit }) {
  const { rows: all, dir, ref } = data;
  if (!all.length) return null;
  const of = share(all);
  // baselines are grey; the best of the others is aqua once it beats the best baseline (or there is none)
  const win = all.find((r) => !r.baseline);
  const best = win && (!ref || win.good) ? win : null;
  // a short list still shows the baseline the gaps are measured against
  const rows = limit && all.length > limit ? [...all.slice(0, limit - 1), ...(ref && all.indexOf(ref) >= limit - 1 ? [ref] : all.slice(limit - 1, limit))] : all;
  return html`<div class="rs-cmp" role="table">
    ${rows.map((r) => {
      const color = r.baseline ? C.gray : r === best ? C.aqua : C.blue;
      const runs = r.n > 1 ? t("rmap_results.runs", { n: r.n, lo: fmt(r.lo), hi: fmt(r.hi) }) : "";
      return html`<div class=${"rs-row" + (r.baseline ? " base" : "") + (r === best ? " best" : "")} key=${r.config} role="row" title=${[r.date, runs].filter(Boolean).join(" · ")}>
        <span class="rs-cfg" role="cell"><span class="rs-name" title=${r.config}>${r.config}</span>${r.baseline && html`<em>${t("rmap_results.baseline")}</em>`}</span>
        <span class="rs-bar" role="cell"><${Progress} value=${of(r.value)} max=${1} color=${color} thin label=${fmt(r.value)} /></span>
        <b class="rs-val mp-num" role="cell">${fmt(r.value)}${r.n > 1 && html`<small>×${r.n}</small>`}</b>
        <span class=${"rs-delta mp-num" + (r.delta == null ? "" : r.good ? " good" : " bad")} role="cell"
          title=${r.delta == null ? "" : t(r.good ? "rmap_results.delta.better" : "rmap_results.delta.worse")}>
          ${r.delta == null ? "" : signed(r.delta)}${r.rel != null && Math.abs(r.rel) < 10 && html`<small>${(r.rel > 0 ? "+" : r.rel < 0 ? "−" : "") + Math.abs(r.rel * 100).toFixed(1)}%</small>`}</span>
      </div>`;
    })}
    <div class="rs-dir">${t(dir === "lower" ? "rmap_results.dir.lower" : "rmap_results.dir.higher")}${rows.some((r) => r.baseline) ? " · " + t("rmap_results.legend.baseline") : ""}</div>
  </div>`;
}

// The same metric over several datasets: columns grouped by dataset, one series per config.
export function Grouped({ datasets, configs, value, height = 150 }) {
  const shown = configs.slice(0, 6);
  const series = shown.map((c, i) => ({ name: c.config, color: c.baseline ? C.gray : LINES[i % LINES.length], values: datasets.map((d) => value(c.config, d) ?? 0) }));
  return html`<${Bars} labels=${datasets.map((d) => d || "—")} series=${series} height=${height} format=${fmt} />`;
}

// Lines over time: one per config, hover shows every config's value on the nearest date.
export function Trend({ data, height = 190 }) {
  const [ref, w] = useWidth(200);
  const [hot, setHot] = useState(null); // index into dates
  const [off, setOff] = useState(new Set()); // configs switched off in the legend
  const { series, dates } = data;
  const L = 44, R = 12, T = 10, B = 22;
  const live = series.filter((s) => !off.has(s.config));
  const vals = live.flatMap((s) => s.points.map((p) => p.value));
  let lo = vals.length ? Math.min(...vals) : 0, hi = vals.length ? Math.max(...vals) : 1;
  if (hi === lo) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.1;
  lo -= pad; hi += pad;
  const ms = (d) => new Date(d + "T12:00:00").getTime();
  const t0 = dates.length ? ms(dates[0]) : 0, t1 = dates.length ? ms(dates[dates.length - 1]) : 1;
  const x = (d) => (dates.length < 2 || t1 === t0 ? L + (w - L - R) / 2 : L + ((ms(d) - t0) / (t1 - t0)) * (w - L - R));
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (height - T - B);
  const move = (e) => {
    if (!dates.length) return;
    const r = ref.current.getBoundingClientRect();
    const px = e.clientX - r.left;
    let best = 0;
    dates.forEach((d, i) => { if (Math.abs(x(d) - px) < Math.abs(x(dates[best]) - px)) best = i; });
    setHot(best);
  };
  const ticks = [lo + pad, (lo + hi) / 2, hi - pad];
  const hotDate = hot != null ? dates[hot] : null;
  const label = (d) => formatDate(d + "T12:00:00", { month: "numeric", day: "numeric" });
  let baseIdx = 0, ownIdx = 0;
  const styled = series.map((s) => ({ s, color: s.baseline ? C.gray : LINES[ownIdx++ % LINES.length], dash: s.baseline ? DASH[baseIdx++ % DASH.length] : null }));
  return html`<div class="rs-trend">
    <div class="ch-legend rs-legend">${styled.map(({ s, color, dash }) => html`<button type="button" class=${off.has(s.config) ? "muted" : ""} key=${s.config}
      aria-pressed=${!off.has(s.config)} onClick=${() => setOff((o) => { const n = new Set(o); if (n.has(s.config)) n.delete(s.config); else n.add(s.config); return n; })}>
      <i class="key line" style=${{ background: color, ...(dash ? { opacity: 0.7 } : null) }} />${s.config}</button>`)}</div>
    <div class="ch-box" ref=${ref} onMouseMove=${move} onMouseLeave=${() => setHot(null)}>
      ${w > 0 && html`<svg width=${w} height=${height} aria-hidden="true">
        ${ticks.map((v) => html`<line x1=${L} x2=${w - R} y1=${y(v)} y2=${y(v)} class="ch-grid" />
          <text x=${L - 6} y=${y(v) + 3.5} text-anchor="end" class="ch-tick rs-mono">${tick(v)}</text>`)}
        ${dates.length > 0 && html`<text x=${x(dates[0])} y=${height - 5} text-anchor=${dates.length > 1 ? "start" : "middle"} class="ch-tick">${label(dates[0])}</text>`}
        ${dates.length > 1 && html`<text x=${x(dates[dates.length - 1])} y=${height - 5} text-anchor="end" class="ch-tick strong">${label(dates[dates.length - 1])}</text>`}
        ${hotDate && html`<line x1=${x(hotDate)} x2=${x(hotDate)} y1=${T} y2=${height - B} class="ch-cross" />`}
        ${styled.filter(({ s }) => !off.has(s.config)).map(({ s, color, dash }) => html`<g key=${s.config}>
          ${s.points.length > 1 && html`<polyline points=${s.points.map((p) => `${x(p.date)},${y(p.value)}`).join(" ")} class="ch-line" style=${{ stroke: color, strokeWidth: s.baseline ? 1.5 : 2 }} stroke-dasharray=${dash} />`}
          ${s.points.map((p) => html`<circle cx=${x(p.date)} cy=${y(p.value)} r=${p.date === hotDate ? 4.5 : 3} class="ch-dot" style=${{ fill: color }} />`)}</g>`)}
      </svg>`}
      ${hotDate && html`<div class="ch-tip rs-tip" style=${{ left: Math.max(4, Math.min(x(hotDate) + 12, w - 190)) + "px", top: "6px" }}>
        <div class="ch-tip-h">${hotDate}</div>
        ${styled.filter(({ s }) => !off.has(s.config)).map(({ s, color }) => {
          const p = s.points.find((q) => q.date === hotDate);
          return p ? html`<div class="ch-tip-row" key=${s.config}><i class="key line" style=${{ background: color }} /><b class="rs-mono">${fmt(p.value)}</b><span>${s.config}</span></div>` : null;
        })}</div>`}
      <table class="sr-only"><thead><tr><th></th>${series.map((s) => html`<th>${s.config}</th>`)}</tr></thead>
        <tbody>${dates.map((d) => html`<tr><td>${d}</td>${series.map((s) => html`<td>${s.points.find((p) => p.date === d)?.value ?? ""}</td>`)}</tr>`)}</tbody></table>
    </div>
  </div>`;
}

// A min-max scaled line of one config over time, with its latest value: for the detail panel.
export function Spark({ values, labels = [], color = C.aqua, height = 34 }) {
  const [ref, w] = useWidth(60);
  const n = values.length, pad = 5;
  const lo = Math.min(...values), hi = Math.max(...values);
  const x = (i) => pad + (n <= 1 ? 0 : (i / (n - 1)) * (w - 2 * pad));
  const y = (v) => (hi === lo ? height / 2 : height - pad - ((v - lo) / (hi - lo)) * (height - 2 * pad));
  const pts = values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  return html`<div class="ch spark rs-spark-box" ref=${ref} style=${{ height: height + "px" }} title=${labels.length ? `${labels[0]} \u2192 ${labels[labels.length - 1]}` : ""}>
    ${w > 0 && n > 0 && html`<svg width=${w} height=${height} aria-hidden="true">
      <polygon points=${`${x(0)},${height - pad} ${pts} ${x(n - 1)},${height - pad}`} class="ch-area" style=${{ fill: color }} />
      <polyline points=${pts} class="ch-line" style=${{ stroke: color }} />
      <circle cx=${x(n - 1)} cy=${y(values[n - 1])} r="3.5" class="ch-dot" style=${{ fill: color }} />
    </svg>`}
  </div>`;
}

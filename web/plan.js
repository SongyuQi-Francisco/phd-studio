// Overview, timeline (venues + plan), venue detail sheet, quick idea capture.
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { C, Ring, Progress, Sparkline, Bars, HeatStrip, perWeek, dayKey } from "/charts.js";
import { Days, daysTo, Skeleton, Empty, Collapse, transition } from "/ui.js";

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
const TZ_LABEL = TZ.split("/").pop().replace(/_/g, " ");
const fmt = (iso, withTime = true) => new Date(iso).toLocaleString("zh-CN", withTime
  ? { year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }
  : { year: "numeric", month: "numeric", day: "numeric" });
const md = (iso) => new Date(iso).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
const TYPE = { venue: "会议截稿", target: "我的目标", milestone: "里程碑" };
const TYPE_COLOR = { venue: C.blue, target: C.orange, milestone: C.aqua };
const M_KIND = { milestone: "里程碑", gate: "Gate", kill: "Kill 检查", deadline: "截止" };
const M_STATUS = { todo: "待开始", doing: "进行中", done: "完成", warn: "预警" };
const T_STATUS = { planning: "计划中", writing: "写作中", submitted: "已投稿", accepted: "已录用", rejected: "被拒", dropped: "放弃" };
const T_STEPS = ["planning", "writing", "submitted", "accepted"];
const KILL = { clear: "未触发", partial: "部分触发", triggered: "已触发" };
const HORIZON = 120;

function Ranks({ core, ccf }) {
  return html`<span class="ranks">
    ${core && html`<span class=${"rk core " + (core === "A*" ? "top" : "")} title="CORE ICORE2026">CORE ${core}</span>`}
    ${ccf && html`<span class=${"rk ccf " + (ccf === "A" ? "top" : "")} title="CCF 推荐">CCF ${ccf}</span>`}
  </span>`;
}

function Mark({ type }) {
  return html`<svg class="mark" viewBox="0 0 12 12" aria-hidden="true">${type === "venue"
    ? html`<circle cx="6" cy="6" r="4.5" class=${"m-" + type} />`
    : type === "target" ? html`<path d="M6 1 L11 6 L6 11 L1 6 Z" class=${"m-" + type} />`
    : html`<rect x="1.5" y="1.5" width="9" height="9" rx="2" class=${"m-" + type} />`}</svg>`;
}

// Days-left ring: a full ring is `max` days or more away; it drains as the date gets close.
// An overdue date shows a full ring in the alert color.
function DaysRing({ d, max = HORIZON, size = 64, stroke = 6, color }) {
  const late = d != null && d < 0;
  return html`<${Ring} value=${d == null ? 0 : late ? max : Math.min(d, max)} max=${max} size=${size} stroke=${stroke} color=${late ? "var(--red)" : color}
    title=${d == null ? "日期未公布" : late ? `已逾期 ${-d} 天` : `还剩 ${d} 天（满环 = ${max} 天）`}>
    <b class=${d != null && d <= 14 ? "hot" + (late ? " late" : "") : ""}>${d == null ? "—" : late ? "逾期" : d}</b><small>${d == null ? "未公布" : late ? `${-d} 天` : "天"}</small>
  </${Ring}>`;
}

// ---------------- timeline chart (next N days) ----------------

export function TimelineChart({ events, days = 120, onPick }) {
  const ref = useRef();
  const [w, setW] = useState(800);
  const [tip, setTip] = useState(null);
  useEffect(() => {
    let raf = 0;
    const ro = new ResizeObserver(([e]) => { const v = Math.max(320, e.contentRect.width); cancelAnimationFrame(raf); raf = requestAnimationFrame(() => setW(v)); });
    ro.observe(ref.current);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, []);
  const now = new Date(); now.setHours(0, 0, 0, 0);
  const end = new Date(now.getTime() + days * 86400000);
  const narrow = w < 560;
  const L = narrow ? 14 : 92, R = 16, H = 176, lanes = { venue: 44, target: 92, milestone: 140 };
  const x = (d) => L + ((new Date(d) - now) / (end - now)) * (w - L - R);
  const shown = events.filter((e) => new Date(e.at) >= now && new Date(e.at) <= end);
  const months = [];
  for (let m = new Date(now.getFullYear(), now.getMonth() + 1, 1); m <= end; m = new Date(m.getFullYear(), m.getMonth() + 1, 1)) months.push(new Date(m));
  // Label a point only if its text has room in its lane and the same name was not just labelled.
  const labelled = new Set();
  const lastEnd = {}, lastName = {};
  for (const e of shown) {
    const name = (e.acronym || e.title).slice(0, 12), cx = x(e.at), half = name.length * 3.4 + 4;
    if (cx - half > (lastEnd[e.type] ?? -1e9) && lastName[e.type] !== name) { labelled.add(e); lastEnd[e.type] = cx + half; lastName[e.type] = name; }
    else if (lastName[e.type] === name) lastEnd[e.type] = Math.max(lastEnd[e.type], cx + 8);
  }
  return html`<div class="tl-chart" ref=${ref}>
    <div class="legend">${["venue", "target", "milestone"].map((t) => html`<span><${Mark} type=${t} />${TYPE[t]}</span>`)}</div>
    <svg width=${w} height=${H} onMouseLeave=${() => setTip(null)}>
      ${Object.entries(lanes).map(([t, y]) => html`<line x1=${L} x2=${w - R} y1=${y} y2=${y} class="lane" />
        ${!narrow && html`<text x=${L - 10} y=${y + 4} text-anchor="end" class="lane-label">${TYPE[t]}</text>`}`)}
      ${months.map((m) => html`<line x1=${x(m)} x2=${x(m)} y1="22" y2=${H - 16} class="grid" />
        <text x=${x(m) + 4} y=${H - 2} class="tick">${m.getMonth() + 1}月</text>`)}
      <line x1=${L} x2=${L} y1="18" y2=${H - 16} class="today" />
      <text x=${L + 4} y="13" class="tick strong">今天</text>
      ${shown.map((e, i) => {
        const cx = x(e.at), cy = lanes[e.type];
        const enter = () => setTip({ e, x: cx, y: cy });
        const shape = e.type === "venue" ? html`<circle cx=${cx} cy=${cy} r="6" class=${"m-" + e.type} />`
          : e.type === "target" ? html`<path d=${`M${cx} ${cy - 8} L${cx + 8} ${cy} L${cx} ${cy + 8} L${cx - 8} ${cy} Z`} class=${"m-" + e.type} />`
          : html`<rect x=${cx - 6} y=${cy - 6} width="12" height="12" rx="3" class=${"m-" + e.type} />`;
        return html`<g class="pt" style=${{ animationDelay: i * 25 + "ms" }} onMouseEnter=${enter} onClick=${() => onPick?.(e)}>
          <circle cx=${cx} cy=${cy} r="14" fill="transparent" /><g class="pt-mark">${shape}</g>
          ${labelled.has(e) && html`<text x=${Math.max(cx, L + (e.acronym || e.title).slice(0, 12).length * 3.4 + 6)} y=${cy - 13} text-anchor="middle" class="plabel">${(e.acronym || e.title).slice(0, 12)}</text>`}
        </g>`;
      })}
    </svg>
    ${tip && html`<div class="tip" style=${{ left: Math.max(0, Math.min(tip.x + 12, w - 230)) + "px", top: tip.y + 10 + "px" }}>
      <b>${tip.e.title}</b><div>${fmt(tip.e.at)} · <${Days} d=${daysTo(tip.e.at)} /></div>
      ${tip.e.target && html`<div class="muted">${tip.e.target.title}</div>`}
      ${tip.e.notes && html`<div class="muted">${tip.e.notes}</div>`}
    </div>`}
  </div>`;
}

// ---------------- overview ----------------

function Countdown({ c, onClick, i }) {
  const d = c.ev ? daysTo(c.ev.at) : null;
  const kicker = c.kind === "target" ? `投稿目标 · ${(c.t.venue || "").toUpperCase()}` : c.kind === "milestone" ? (c.ev.kind === "gate" ? "下一个 Gate" : "下一个里程碑") : "最近会议截稿";
  const title = c.kind === "target" ? c.t.title : c.ev.title;
  return html`<button class="cd click" style=${{ "--i": i }} onClick=${onClick}>
    <${DaysRing} d=${d} color=${TYPE_COLOR[c.kind]} />
    <div class="cd-body">
      <span class="cd-kicker"><${Mark} type=${c.kind} />${kicker}</span>
      <span class="cd-title" title=${title}>${title}</span>
      <span class="cd-date">${c.ev ? `${c.kind === "target" ? c.ev.title.replace(/^\S+\s\d{4}\s/, "") + " · " : ""}${fmt(c.ev.at, c.kind !== "milestone")}` : "下一届截稿未公布"}</span>
    </div>
  </button>`;
}

function Kpi({ label, value, sub, children, onClick, warn, i }) {
  return html`<button class="kpi click" style=${{ "--i": i }} onClick=${onClick}>
    <span class="kpi-label">${label}</span>
    <span class=${"kpi-value" + (warn ? " warn" : "")}>${value}</span>
    ${sub && html`<span class="kpi-sub">${sub}</span>`}
    ${children && html`<div class="kpi-viz">${children}</div>`}
  </button>`;
}

export function KillLights({ kills }) {
  if (!kills?.length) return html`<span class="lights none" title="没有 Kill criteria">—</span>`;
  return html`<span class="lights">${kills.map((k, i) => html`<i class=${"kl " + k.state} title=${`${k.id || "K" + (i + 1)} · ${KILL[k.state]}：${k.text}`} />`)}</span>`;
}

const shortLine = (name) => name.replace(/^Research Line - /, "");

export function OverviewPage({ version, onOpenVenue, go }) {
  const [o, setO] = useState(null);
  const [x, setX] = useState({});
  const [more, setMore] = useState(false);
  useEffect(() => {
    api("/api/overview").then(setO);
    Promise.allSettled([api("/api/reading"), api("/api/ideas"), api("/api/track/runs"), api("/api/direction")])
      .then(([r, i, t, d]) => setX({ items: r.value?.items || [], ideas: i.value?.ideas || [], runs: t.value?.runs || [], lines: d.value?.lines || [] }));
  }, [version]);
  if (!o) return html`<${Skeleton} tiles=${5} rows=${2} />`;
  const up = o.events.filter((e) => e.days >= 0);
  const cds = o.targets.map((t) => ({ kind: "target", t, ev: up.find((e) => e.type === "target" && e.target?.id === t.id) }));
  if (o.next_milestone) cds.push({ kind: "milestone", ev: o.next_milestone });
  const nv = up.find((e) => e.type === "venue");
  if (nv) cds.push({ kind: "venue", ev: nv });

  const items = x.items || [], ideas = x.ideas || [], runs = x.runs || [];
  const reads = perWeek(items.filter((i) => i.status === "done").map((i) => i.read_at));
  const ins = perWeek(items.map((i) => i.pushed_at));
  const run = runs[0];
  const runDays = run ? Math.floor((Date.now() - new Date(run.run + "T12:00:00")) / 86400000) : null;
  const tiers = run?.stats?.by_tier || {};
  const ideaN = (s) => ideas.filter((i) => i.status === s).length;
  const heat = {};
  const bump = (s) => { const k = dayKey(s); if (k) heat[k] = (heat[k] || 0) + 1; };
  items.forEach((i) => { bump(i.pushed_at); bump(i.opened_at); bump(i.read_at); });
  ideas.forEach((i) => bump(i.created_at));
  const lines = (x.lines || []).filter((l) => l.kind === "research-line");
  const r = o.reading;

  return html`<div class="page"><div class="page-inner wide">
    <div class="page-head"><div><h1>总览</h1>
      <div class="sub">${new Date().toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "long" })} · 时间按 ${TZ_LABEL}</div></div></div>

    ${cds.length > 0 && html`<div class="countdowns stagger" style=${{ "--n": cds.length }}>${cds.map((c, i) => html`<${Countdown} c=${c} i=${i}
      onClick=${() => c.kind === "milestone" ? go("plan") : (c.ev?.venue || c.t?.venue) ? onOpenVenue(c.ev?.venue || c.t.venue) : go("timeline")} />`)}</div>`}

    <div class="kpis stagger">
      <${Kpi} i=${0} label="本周读完" value=${r.done_week} sub=${`近 12 周共 ${reads.counts.reduce((a, b) => a + b, 0)} 篇 · 在读 ${r.reading}`} onClick=${() => go("list")}>
        <${Sparkline} values=${reads.counts} labels=${reads.labels.map((l) => l + " 那周")} unit=" 篇" height=${30} /></${Kpi}>
      <${Kpi} i=${1} label="本周 T1" value=${html`${r.T1}<small>/${r.caps.T1}</small>`} warn=${r.T1 > r.caps.T1} sub=${`待读 ${r.queued}/${r.caps.queued}`} onClick=${() => go("list")}>
        <${Progress} value=${r.T1} max=${r.caps.T1} over=${r.T1 > r.caps.T1} label=${`T1 ${r.T1}/${r.caps.T1}`} /></${Kpi}>
      <${Kpi} i=${2} label="收件箱" value=${r.inbox} sub=${`近 12 周进来 ${ins.counts.reduce((a, b) => a + b, 0)} 篇`} onClick=${() => go("list")}>
        <${Sparkline} values=${ins.counts} labels=${ins.labels.map((l) => l + " 那周")} unit=" 篇" color=${C.gray} height=${30} /></${Kpi}>
      <${Kpi} i=${3} label="新想法" value=${o.ideas_new} sub=${ideas.length ? `保留 ${ideaN("kept")} · 已整理 ${ideaN("promoted")}` : "⌘I 随手记"} onClick=${() => go("ideas")}>
        <div class="segbar" title=${`新 ${ideaN("new")} · 保留 ${ideaN("kept")} · 已整理 ${ideaN("promoted")}`}>
          ${ideas.length ? ["new", "kept", "promoted"].map((s, k) => ideaN(s) > 0 && html`<i style=${{ flex: ideaN(s), background: `var(--ord-${k + 1})` }} />`) : html`<i class="none" />`}
        </div></${Kpi}>
      <${Kpi} i=${4} label=${runDays != null && runDays > 7 ? "最近一次追踪" : "本周追踪新论文"} value=${run ? run.stats?.kept ?? 0 : "—"}
        sub=${run ? `${md(run.run + "T12:00:00")} 运行 · 从 ${run.stats?.input ?? "?"} 篇里留下` : "还没有追踪运行"} onClick=${() => go("track")}>
        ${run && html`<div class="tier-mini">${["A", "B", "C"].map((t, k) => html`<span title=${`Tier ${t}：${tiers[t] || 0} 篇`}><i style=${{ background: `var(--ord-${3 - k})` }} />${t}<b>${tiers[t] || 0}</b></span>`)}
</div>`}
      </${Kpi}>
    </div>

    <section class="card">
      <div class="card-head"><h2>未来 ${HORIZON} 天</h2><button class="tb-btn" onClick=${() => go("timeline")}>全部会议<${Icon} name="right" sm /></button></div>
      <${TimelineChart} events=${o.events} days=${HORIZON} onPick=${(e) => e.venue ? onOpenVenue(e.venue) : go("plan")} />
    </section>

    <div class="two-col">
      <section class="card">
        <div class="card-head"><h2>研究线</h2><button class="tb-btn" onClick=${() => go("direction")}>方向<${Icon} name="right" sm /></button></div>
        ${lines.length === 0 && html`<${Empty} compact icon="compass" title="还没有研究线" hint="在 vault 的 Ideas 文件夹里建 Research Line 笔记" />`}
        ${lines.map((l) => { const next = (l.deadlines || [])[0];
          return html`<button class="lrow" onClick=${() => go("direction")}>
            <span class=${"st-dot " + (l.status || "")} title=${l.status || ""} />
            <span class="lr-name">${shortLine(l.name)}</span>
            <${KillLights} kills=${l.kills} />
            <span class="lr-n" title="关联论文"><b>${l.papers}</b> 篇</span>
            ${next ? html`<${Days} d=${daysTo(next.at)} />` : html`<span class="days past">无截稿</span>`}
          </button>`; })}
      </section>
      <section class="card">
        <div class="card-head"><h2>接下来</h2><span class="muted small">${up.length} 件</span></div>
        ${up.length === 0 && html`<${Empty} compact icon="calendar" title="${HORIZON} 天内没有事件" hint="去时间线关注会议，或加一个里程碑" />`}
        ${up.slice(0, 5).map((e) => html`<${EvRow} e=${e} onOpenVenue=${onOpenVenue} go=${go} />`)}
        ${up.length > 5 && html`<${Collapse} open=${more}>${up.slice(5).map((e) => html`<${EvRow} e=${e} onOpenVenue=${onOpenVenue} go=${go} />`)}</${Collapse}>
          <button class="more" onClick=${() => setMore(!more)}>${more ? "收起" : `再显示 ${up.length - 5} 件`}<${Icon} name=${more ? "up" : "down"} sm /></button>`}
      </section>
    </div>

    <section class="card">
      <div class="card-head"><h2>研究节奏</h2><span class="muted small">每天在阅读清单和想法里的动作：加入、打开、读完、记想法</span></div>
      <${HeatStrip} counts=${heat} unit="次" />
    </section>
  </div></div>`;
}

function EvRow({ e, onOpenVenue, go }) {
  return html`<button class="ev" onClick=${() => e.venue ? onOpenVenue(e.venue) : go("plan")}>
    <${Mark} type=${e.type} /><span class="ev-t">${e.title}${e.target ? html` <em>· ${e.target.title}</em>` : ""}</span>
    <span class="ev-d">${fmt(e.at, e.type !== "milestone")}</span><${Days} d=${daysTo(e.at)} />
  </button>`;
}

// ---------------- venue detail sheet ----------------

export function VenueSheet({ vkey, onClose, toast, onChanged }) {
  const [v, setV] = useState(null);
  const [tgt, setTgt] = useState(null);
  const [rules, setRules] = useState(false);
  useEffect(() => { setV(null); api(`/api/venues/${encodeURIComponent(vkey)}`).then(setV).catch((e) => toast(e.message)); }, [vkey]);
  if (!v) return html`<div class="sheet-bg" onClick=${onClose}><div class="sheet"><div class="skel" style="width:120px;height:28px" />
    <div class="skel" style="width:70%;height:14px;margin-top:12px" /><div class="skel" style="width:100%;height:96px;margin-top:22px;border-radius:14px" /></div></div>`;
  const d = v.detail;
  const ed = v.editions[v.editions.length - 1];
  const toggle = async () => { await api(`/api/venues/${v.key}/watch`, { json: { on: !v.watched } }); setV({ ...v, watched: !v.watched }); onChanged?.(); };
  const saveTarget = async () => {
    try { await api("/api/plan/targets", { json: { title: tgt.title, venue: v.key, line: tgt.line || null } }); toast("已设为投稿目标"); setTgt(null); onChanged?.(); }
    catch (e) { toast(e.message); }
  };
  const links = [["官网", d?.homepage || ed?.link], ["CFP", d?.cfp_url], ["CORE", v.core_url], ["DBLP", v.dblp_url]].filter(([, u]) => u);
  const n = v.next, live = n && !n.past;
  const rates = v.accept_rates || [];
  const ruleList = d?.format?.notable_rules || [];
  return html`<div class="sheet-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}><div class="sheet">
    <div class="sheet-head">
      <div><h2>${v.acronym}</h2><div class="muted">${v.title}</div></div>
      <button class="tb-btn" onClick=${onClose}><${Icon} name="x" /></button>
    </div>
    <div class="row-wrap"><${Ranks} core=${v.core} ccf=${v.ccf} />${v.field && html`<span class="tag">${v.field}</span>`}</div>

    <div class="v-hero">
      <${DaysRing} d=${live ? daysTo(n.at) : null} max=${180} size=${86} stroke=${7} color=${C.blue} />
      <div class="v-hero-b">
        ${n ? html`<span class="muted small">${n.past ? "上一届（新一届未公布）" : `${n.year} ${n.kind === "abstract" ? "摘要截止" : "全文截止"}`}</span>
          <b class="v-when">${fmt(n.at)}</b>
          ${n.paper_at && n.kind === "abstract" && html`<span class="small">全文 ${fmt(n.paper_at)} <${Days} d=${daysTo(n.paper_at)} /></span>`}
          <span class="muted small">${[n.date, n.place].filter(Boolean).join(" · ")}</span>
          <span class="muted small">原始时区 ${n.timezone} · 来源 ccfddl${n.comment ? " · " + n.comment : ""}</span>` : html`<span class="muted">暂无截稿数据</span>`}
      </div>
      ${rates.length > 0 && html`<div class="v-rate"><b>${(rates[rates.length - 1].rate * 100).toFixed(1)}%</b><span class="muted small">${rates[rates.length - 1].year} 录用率</span></div>`}
    </div>

    <div class="row-wrap">${links.map(([l, u]) => html`<a class="chip" href=${u} target="_blank" rel="noreferrer"><${Icon} name="external" sm />${l}</a>`)}</div>
    <div class="row-wrap">
      <button class=${"btn " + (v.watched ? "" : "primary")} onClick=${toggle}><${Icon} name="star" sm /> ${v.watched ? "已关注" : "关注（上时间线）"}</button>
      <button class="btn" onClick=${() => setTgt(tgt ? null : { title: "", line: "" })}>设为投稿目标</button>
    </div>
    <${Collapse} open=${!!tgt}>${tgt && html`<div class="inline-form"><input placeholder="论文名，如 Paper 1 · 题目" value=${tgt.title} onInput=${(e) => setTgt({ ...tgt, title: e.target.value })} />
      <input placeholder="研究线（可选）" value=${tgt.line} onInput=${(e) => setTgt({ ...tgt, line: e.target.value })} />
      <button class="btn primary" disabled=${!tgt.title.trim()} onClick=${saveTarget}>保存</button></div>`}</${Collapse}>

    ${rates.length > 1 && html`<h3>历年录用率</h3>
      <${Bars} labels=${rates.map((r) => String(r.year))} height=${120} format=${(x) => Math.round(x * 100) + "%"}
        series=${[{ name: "录用率", color: C.blue, values: rates.map((r) => r.rate) }]} />
      <div class="muted small">${rates.map((r) => `${r.year}：${r.accepted}/${r.submitted}`).join(" · ")}</div>`}

    ${d && html`<h3>赛道与投稿要求 <span class=${"tag " + (d.status === "confirmed" ? "ok" : "warn")}>${d.status === "confirmed" ? "官网核实" : d.status === "previous-edition" ? "沿用上一届" : "未核实"}</span> <span class="muted small">${d.edition || ""}</span></h3>
      ${(d.deadlines || []).length > 0 && html`<div class="mini-table">${d.deadlines.map((x) => html`<div><span>${x.track || "主会"}</span><span>${[x.abstract && "摘要 " + x.abstract, x.paper && "全文 " + x.paper].filter(Boolean).join(" · ")}</span></div>`)}</div>`}
      ${(d.tracks || []).length > 0 && html`<div class="mini-table">${d.tracks.map((t) => html`<div><span><b>${t.name}</b></span><span>${t.page_limit || ""}${t.notes ? html`<em class="muted"> ${t.notes}</em>` : ""}</span></div>`)}</div>`}
      ${(d.location || d.dates) && html`<div class="muted small">${[d.dates, d.location].filter(Boolean).join(" · ")}</div>`}
      ${d.format && html`<div class="fmt">
        ${d.format.template && html`<div><span class="muted">模板</span> ${d.format.template}</div>`}
        ${d.format.review_model && html`<div><span class="muted">审稿</span> ${d.format.review_model}</div>`}
        ${ruleList.length > 0 && html`<button class="more" onClick=${() => setRules(!rules)}>${rules ? "收起" : `投稿规则 ${ruleList.length} 条`}<${Icon} name=${rules ? "up" : "down"} sm /></button>
          <${Collapse} open=${rules}><ul class="rules">${ruleList.map((r) => html`<li>${r}</li>`)}</ul></${Collapse}>`}
      </div>`}
      ${d.notes && html`<div class="muted small">${Array.isArray(d.notes) ? d.notes.join("；") : d.notes}</div>`}
      ${(d.sources || []).length > 0 && html`<div class="muted small srcs">来源：${d.sources.map((u) => html`<a href=${u} target="_blank" rel="noreferrer">${new URL(u).hostname}</a> `)}</div>`}`}
  </div></div>`;
}

// ---------------- timeline page ----------------

function VenueRow({ v, onOpen, onStar }) {
  const n = v.next;
  return html`<div class="vrow" onClick=${() => onOpen(v.key)}>
    <button class=${"star" + (v.watched ? " on" : "")} title=${v.watched ? "取消关注" : "关注"} onClick=${(e) => { e.stopPropagation(); onStar(v); }}><${Icon} name="star" sm /></button>
    <div class="v-main"><b>${v.acronym}</b><span class="muted">${v.title}</span></div>
    <${Ranks} core=${v.core} ccf=${v.ccf} />
    <div class="v-dl">${n ? (n.past ? html`<span class="muted">上届 ${fmt(n.at, false)}</span>` : html`<span>${n.kind === "abstract" ? "摘要 " : ""}${fmt(n.at)}</span><${Days} d=${daysTo(n.at)} />`) : html`<span class="muted">—</span>`}</div>
  </div>`;
}

function VenueCard({ v, target, onOpen, onStar, i }) {
  const n = v.next, live = n && !n.past;
  const rate = v.accept_rate;
  return html`<div class=${"vcard click" + (live ? "" : " past")} style=${{ "--i": i }} role="button" tabindex="0"
    onClick=${() => onOpen(v.key)} onKeyDown=${(e) => e.key === "Enter" && onOpen(v.key)}>
    <div class="vc-top"><b>${v.acronym}</b>${target && html`<span class="vc-tgt" title=${target.title}><${Mark} type="target" />目标</span>`}<span class="grow" />
      <button class=${"star" + (v.watched ? " on" : "")} title="取消关注" onClick=${(e) => { e.stopPropagation(); onStar(v); }}><${Icon} name="star" sm /></button></div>
    <div class="vc-name" title=${v.title}>${v.title}</div>
    <div class="vc-mid">
      <${DaysRing} d=${live ? daysTo(n.at) : null} max=${180} size=${58} stroke=${5} color=${target ? C.orange : C.blue} />
      <div class="vc-dl">${live
        ? html`<span class="muted small">${n.year} ${n.kind === "abstract" ? "摘要截止" : "全文截止"}</span><b>${fmt(n.at)}</b><span class="muted small vc-place">${n.place || ""}</span>`
        : html`<span class="muted small">下一届未公布</span><span class="small">上届 ${n ? fmt(n.at, false) : "—"}</span>`}</div>
    </div>
    <div class="vc-foot"><${Ranks} core=${v.core} ccf=${v.ccf} /><span class="grow" />
      ${rate && html`<span class="vc-rate" title=${`${rate.year} 录用率 ${rate.accepted}/${rate.submitted}`}><span class="muted small">录用</span><b>${(rate.rate * 100).toFixed(0)}%</b></span>`}</div>
  </div>`;
}

export function TimelinePage({ version, tab: initialTab, onOpenVenue, toast, onChanged }) {
  const [tab, setTab] = useState(initialTab || "watched");
  const [rows, setRows] = useState(null);
  const [stats, setStats] = useState(null);
  const [q, setQ] = useState("");
  const [core, setCore] = useState("");
  const [ccf, setCcf] = useState("");
  const [plan, setPlan] = useState(null);
  useEffect(() => { if (initialTab) setTab(initialTab); }, [initialTab]);
  const loadVenues = () => api(`/api/venues?scope=${tab === "watched" ? "watched" : "all"}&q=${encodeURIComponent(q)}&core=${encodeURIComponent(core)}&ccf=${ccf}`)
    .then((r) => { setStats(r.stats); setRows(tab === "watched" ? r.venues.sort((a, b) => (a.next && !a.next.past ? a.next.at : "9") .localeCompare(b.next && !b.next.past ? b.next.at : "9")) : r.venues); });
  const loadPlan = () => api("/api/plan").then(setPlan);
  useEffect(() => { if (tab !== "plan") loadVenues(); if (tab !== "all") loadPlan(); }, [tab, q, core, ccf, version]);
  const star = async (v) => { await api(`/api/venues/${v.key}/watch`, { json: { on: !v.watched } }); toast(v.watched ? `已取消关注 ${v.acronym}` : `已关注 ${v.acronym}`); loadVenues(); onChanged?.(); };
  const refresh = async () => { toast("正在更新会议数据…"); try { await api("/api/venues/refresh", { method: "POST" }); toast("会议数据已更新"); loadVenues(); } catch (e) { toast(e.message); } };
  const targetOf = Object.fromEntries((plan?.targets || []).flatMap((t) => [[t.venue, t]]));
  const live = (rows || []).filter((v) => v.next && !v.next.past).length;

  return html`<div class="page"><div class="page-inner wide">
    <div class="page-head"><div><h1>时间线</h1>
      ${tab !== "plan" && stats && html`<div class="sub">CORE ${stats.core_source} ${stats.core} 个 · CCF ${stats.ccf} 个 · 截稿数据 ccfddl · 更新于 ${(stats.fetched_at || "").replace("T", " ").slice(5, 16)} · 时间按 ${TZ_LABEL}
        <button class="linkbtn" onClick=${refresh}>立即更新</button></div>`}
      ${tab === "plan" && html`<div class="sub">投稿目标与里程碑，日期按 ${TZ_LABEL}</div>`}</div>
      <div class="seg">${[["watched", "关注会议"], ["all", "全部会议"], ["plan", "我的规划"]].map(([k, l]) => html`<button class=${tab === k ? "on" : ""} onClick=${() => transition(() => setTab(k))}>${l}</button>`)}</div>
    </div>
    ${tab === "watched" && plan && html`<section class="card">
      <div class="card-head"><h2>未来 180 天</h2><span class="muted small">${live} 个会议已公布下一届截稿</span></div>
      <${TimelineChart} events=${plan.events} days=${180} onPick=${(e) => e.venue && onOpenVenue(e.venue)} /></section>`}
    ${tab === "all" && html`<div class="filters">
      <label class="search"><${Icon} name="search" sm /><input placeholder="搜索会议简称或全称" value=${q} onInput=${(e) => setQ(e.target.value)} /></label>
      <div class="seg">${["", "A*", "A", "B", "C"].map((r) => html`<button class=${core === r ? "on" : ""} onClick=${() => setCore(r)}>${r ? "CORE " + r : "CORE 全部"}</button>`)}</div>
      <div class="seg">${["", "A", "B", "C"].map((r) => html`<button class=${ccf === r ? "on" : ""} onClick=${() => setCcf(r)}>${r ? "CCF " + r : "CCF 全部"}</button>`)}</div>
    </div>`}
    ${tab === "watched" && rows && (rows.length === 0
      ? html`<div class="card"><${Empty} icon="star" title="还没有关注的会议" hint="去「全部会议」给常投的会议点星标，它们的截稿就会出现在这里和总览的时间轴上。">
          <button class="btn primary" onClick=${() => setTab("all")}>浏览全部会议</button></${Empty}></div>`
      : html`<div class="vgrid stagger">${rows.map((v, i) => html`<${VenueCard} key=${v.key} v=${v} i=${Math.min(i, 12)} target=${targetOf[v.key]} onOpen=${onOpenVenue} onStar=${star} />`)}</div>`)}
    ${tab === "all" && rows && html`<div class="card list-card">
      ${rows.length === 0 && html`<${Empty} compact icon="search" title="没有匹配的会议" hint="换个关键词，或放宽 CORE / CCF 筛选" />`}
      ${rows.map((v) => html`<${VenueRow} v=${v} onOpen=${onOpenVenue} onStar=${star} />`)}
      ${rows.length >= 300 && html`<div class="muted pad small">只显示前 300 个，输入关键词缩小范围</div>`}
    </div>`}
    ${tab === "plan" && plan && html`<${PlanEditor} plan=${plan} reload=${() => { loadPlan(); onChanged?.(); }} toast=${toast} onOpenVenue=${onOpenVenue} />`}
  </div></div>`;
}

function PlanEditor({ plan, reload, toast, onOpenVenue }) {
  const [m, setM] = useState({ title: "", date: "", line: "", kind: "milestone" });
  const [t, setT] = useState({ title: "", venue: "", line: "" });
  const [adding, setAdding] = useState({ t: false, m: false });
  const call = async (fn) => { try { await fn(); reload(); } catch (e) { toast(e.message); } };
  const ms = [...plan.milestones].sort((a, b) => a.date.localeCompare(b.date));
  const today = new Date().toISOString().slice(0, 10);
  const firstFuture = ms.findIndex((x) => x.date >= today);
  const done = ms.filter((x) => x.status === "done").length;
  const gates = ms.filter((x) => x.kind === "gate");
  const nextOf = (x) => plan.events.find((e) => e.type === "target" && e.target?.id === x.id && e.days >= 0);
  return html`<div class="two-col">
    <section class="card">
      <div class="card-head"><h2>投稿目标</h2><button class="tb-btn" onClick=${() => setAdding({ ...adding, t: !adding.t })}><${Icon} name="plus" sm />添加</button></div>
      ${plan.targets.length === 0 && html`<${Empty} compact icon="star" title="还没有投稿目标" hint="对右侧助手说「把 X 论文的目标设为 SIGIR 2027」，或点添加" />`}
      ${plan.targets.map((x) => { const ev = nextOf(x), step = T_STEPS.indexOf(x.status);
        return html`<div class="tcard">
          <${DaysRing} d=${ev ? daysTo(ev.at) : null} max=${180} size=${58} stroke=${5} color=${C.orange} />
          <div class="tc-body">
            <div class="tc-top"><b>${x.title}</b>
              <button class="abtn" title="删除" onClick=${() => confirm("删除这个目标？") && call(() => api(`/api/plan/targets/${x.id}`, { method: "DELETE" }))}><${Icon} name="x" sm /></button></div>
            <div class="row-wrap tight"><button class="vlink" onClick=${() => onOpenVenue(x.venue)}>${x.venue.toUpperCase()}</button>
              ${(x.alternatives || []).map((a) => html`<button class="vlink alt" onClick=${() => onOpenVenue(a)}>${a.toUpperCase()}</button>`)}
              ${x.line && html`<span class="tag">${x.line}</span>`}
              <span class="muted small">${ev ? ev.title.replace(/^\S+\s\d{4}\s/, "") + " " + fmt(ev.at) : "下一届截稿未公布"}</span></div>
            <div class="stepper">${T_STEPS.map((s, k) => html`<button class=${"step" + (k < step ? " done" : k === step ? " on" : "")} title=${"设为" + T_STATUS[s]}
                onClick=${() => call(() => api(`/api/plan/targets/${x.id}`, { method: "PATCH", json: { status: s } }))}><i />${T_STATUS[s]}</button>`)}
              ${step < 0 && html`<span class="tag warn">${T_STATUS[x.status] || x.status}</span>`}
              <select value=${x.status} title="全部状态" onChange=${(e) => call(() => api(`/api/plan/targets/${x.id}`, { method: "PATCH", json: { status: e.target.value } }))}>
                ${Object.entries(T_STATUS).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select></div>
            ${x.notes && html`<div class="muted small clamp2">${x.notes}</div>`}
          </div>
        </div>`; })}
      <${Collapse} open=${adding.t}><div class="inline-form">
        <input placeholder="论文" value=${t.title} onInput=${(e) => setT({ ...t, title: e.target.value })} />
        <input placeholder="会议，如 SIGIR" value=${t.venue} onInput=${(e) => setT({ ...t, venue: e.target.value })} style="max-width:130px" />
        <input placeholder="研究线" value=${t.line} onInput=${(e) => setT({ ...t, line: e.target.value })} style="max-width:120px" />
        <button class="btn primary" disabled=${!t.title.trim() || !t.venue.trim()} onClick=${() => call(async () => { await api("/api/plan/targets", { json: t }); setT({ title: "", venue: "", line: "" }); })}>添加</button>
      </div></${Collapse}>
    </section>
    <section class="card">
      <div class="card-head"><h2>里程碑 · Gate</h2><button class="tb-btn" onClick=${() => setAdding({ ...adding, m: !adding.m })}><${Icon} name="plus" sm />添加</button></div>
      ${ms.length > 0 && html`<div class="ms-sum">
        <div><b>${done}</b><span class="muted small">/${ms.length} 完成</span></div><div class="grow"><${Progress} value=${done} max=${ms.length} color=${C.aqua} label=${`完成 ${done}/${ms.length}`} /></div>
        <span class="muted small">Gate ${gates.filter((g) => g.status === "done").length}/${gates.length}</span></div>`}
      ${ms.length === 0 && html`<${Empty} compact icon="calendar" title="还没有里程碑" hint="Gate、Kill 检查和个人截止都可以记在这里" />`}
      <div class="mtl">
        ${ms.map((x, k) => html`${k === (firstFuture < 0 ? ms.length : firstFuture) && html`<div class="mtl-today"><span>今天 ${md(new Date())}</span></div>`}
          <div class=${"mtl-row st-" + x.status + (x.date < today ? " past" : "")}>
            <span class="mtl-dot" />
            <div class="mtl-date"><b>${md(x.date + "T12:00:00")}</b><${Days} d=${daysTo(x.date + "T23:59:00")} /></div>
            <div class="p-main"><b>${x.title}</b> <span class="tag">${M_KIND[x.kind] || x.kind}</span>${x.line ? html` <span class="tag">${x.line}</span>` : ""}
              ${x.notes && html`<div class="muted small clamp2">${x.notes}</div>`}</div>
            <select value=${x.status} onChange=${(e) => call(() => api(`/api/plan/milestones/${x.id}`, { method: "PATCH", json: { status: e.target.value } }))}>
              ${Object.entries(M_STATUS).map(([s, l]) => html`<option value=${s}>${l}</option>`)}</select>
            <button class="abtn" title="删除" onClick=${() => confirm("删除这个里程碑？") && call(() => api(`/api/plan/milestones/${x.id}`, { method: "DELETE" }))}><${Icon} name="x" sm /></button>
          </div>`)}
        ${ms.length > 0 && firstFuture < 0 && html`<div class="mtl-today"><span>今天 ${md(new Date())}</span></div>`}
      </div>
      <${Collapse} open=${adding.m}><div class="inline-form">
        <input placeholder="里程碑" value=${m.title} onInput=${(e) => setM({ ...m, title: e.target.value })} />
        <input type="date" value=${m.date} onInput=${(e) => setM({ ...m, date: e.target.value })} style="max-width:150px" />
        <select value=${m.kind} onChange=${(e) => setM({ ...m, kind: e.target.value })}>${Object.entries(M_KIND).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
        <button class="btn primary" disabled=${!m.title.trim() || !m.date} onClick=${() => call(async () => { await api("/api/plan/milestones", { json: m }); setM({ title: "", date: "", line: "", kind: "milestone" }); })}>添加</button>
      </div></${Collapse}>
    </section>
  </div>`;
}

// ---------------- quick idea capture ----------------

export function IdeaCapture({ context, lines, onClose, onSaved }) {
  const [text, setText] = useState("");
  const [line, setLine] = useState("");
  const ref = useRef();
  useEffect(() => ref.current?.focus(), []);
  const save = async () => {
    await api("/api/ideas", { json: { text, line: line || null, paper: context?.paper || null, page: context?.page || null } });
    onSaved();
  };
  return html`<div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <div class="modal">
      <h2>记一个想法</h2>
      ${context?.paper && html`<div class="muted small" style="margin:-8px 0 8px">关联：${context.paper}${context.page ? " · p." + context.page : ""}</div>`}
      <textarea ref=${ref} class="ta" rows="4" placeholder="一句话就行，之后再整理" value=${text} onInput=${(e) => setText(e.target.value)}
        onKeyDown=${(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) save(); if (e.key === "Escape") onClose(); }} />
      ${lines.length > 0 && html`<div class="seg" style="margin-top:10px"><button class=${!line ? "on" : ""} onClick=${() => setLine("")}>不分组</button>
        ${lines.map((l) => html`<button class=${line === l ? "on" : ""} onClick=${() => setLine(l)}>${l}</button>`)}</div>`}
      <div class="row-end" style="margin-top:14px"><button class="btn ghost" onClick=${onClose}>取消</button>
        <button class="btn primary" disabled=${!text.trim()} onClick=${save}>保存 ⌘↵</button></div>
    </div></div>`;
}

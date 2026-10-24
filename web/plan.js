// Overview, timeline (venues + plan), venue detail sheet, quick idea capture.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
const TZ_LABEL = TZ.split("/").pop().replace(/_/g, " ");
const fmt = (iso, withTime = true) => new Date(iso).toLocaleString("zh-CN", withTime
  ? { year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }
  : { year: "numeric", month: "numeric", day: "numeric" });
const daysTo = (iso) => Math.ceil((new Date(iso) - new Date()) / 86400000);
const TYPE = { venue: "会议截稿", target: "我的目标", milestone: "里程碑" };
const M_KIND = { milestone: "里程碑", gate: "Gate", kill: "Kill 检查", deadline: "截止" };
const M_STATUS = { todo: "待开始", doing: "进行中", done: "完成", warn: "预警" };
const T_STATUS = { planning: "计划中", writing: "写作中", submitted: "已投稿", accepted: "已录用", rejected: "被拒", dropped: "放弃" };

function Days({ d }) {
  if (d == null) return null;
  const cls = d < 0 ? "past" : d <= 14 ? "hot" : d <= 45 ? "warm" : "";
  return html`<span class=${"days " + cls}>${d < 0 ? `${-d} 天前` : d === 0 ? "今天" : `${d} 天`}</span>`;
}

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

// ---------------- timeline chart (next N days) ----------------

function TimelineChart({ events, days = 120, onPick }) {
  const ref = useRef();
  const [w, setW] = useState(800);
  const [tip, setTip] = useState(null);
  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setW(Math.max(320, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  const now = new Date(); now.setHours(0, 0, 0, 0);
  const end = new Date(now.getTime() + days * 86400000);
  const L = 92, R = 16, H = 168, lanes = { venue: 40, target: 86, milestone: 132 };
  const x = (d) => L + ((new Date(d) - now) / (end - now)) * (w - L - R);
  const shown = events.filter((e) => new Date(e.at) >= now && new Date(e.at) <= end);
  const months = [];
  for (let m = new Date(now.getFullYear(), now.getMonth() + 1, 1); m <= end; m = new Date(m.getFullYear(), m.getMonth() + 1, 1)) months.push(new Date(m));
  const labelled = new Set(shown.filter((e) => e.type !== "venue").map((e) => e.title + e.at));
  return html`<div class="tl-chart" ref=${ref}>
    <div class="legend">${["venue", "target", "milestone"].map((t) => html`<span><${Mark} type=${t} />${TYPE[t]}</span>`)}</div>
    <svg width=${w} height=${H} onMouseLeave=${() => setTip(null)}>
      ${Object.entries(lanes).map(([t, y]) => html`<line x1=${L} x2=${w - R} y1=${y} y2=${y} class="lane" />
        <text x=${L - 10} y=${y + 4} text-anchor="end" class="lane-label">${TYPE[t]}</text>`)}
      ${months.map((m) => html`<line x1=${x(m)} x2=${x(m)} y1="18" y2=${H - 14} class="grid" />
        <text x=${x(m) + 4} y=${H - 2} class="tick">${m.getMonth() + 1}月</text>`)}
      <line x1=${L} x2=${L} y1="14" y2=${H - 14} class="today" />
      <text x=${L + 4} y="12" class="tick strong">今天</text>
      ${shown.map((e) => {
        const cx = x(e.at), cy = lanes[e.type];
        const enter = () => setTip({ e, x: cx, y: cy });
        const shape = e.type === "venue" ? html`<circle cx=${cx} cy=${cy} r="6" class=${"m-" + e.type} />`
          : e.type === "target" ? html`<path d=${`M${cx} ${cy - 8} L${cx + 8} ${cy} L${cx} ${cy + 8} L${cx - 8} ${cy} Z`} class=${"m-" + e.type} />`
          : html`<rect x=${cx - 6} y=${cy - 6} width="12" height="12" rx="3" class=${"m-" + e.type} />`;
        return html`<g class="pt" onMouseEnter=${enter} onClick=${() => onPick?.(e)}>
          <circle cx=${cx} cy=${cy} r="14" fill="transparent" />${shape}
          ${labelled.has(e.title + e.at) && html`<text x=${cx} y=${cy - 13} text-anchor="middle" class="plabel">${(e.acronym || e.title).slice(0, 14)}</text>`}
        </g>`;
      })}
    </svg>
    ${tip && html`<div class="tip" style=${{ left: Math.min(tip.x + 12, w - 230) + "px", top: tip.y + 10 + "px" }}>
      <b>${tip.e.title}</b><div>${fmt(tip.e.at)} · <${Days} d=${daysTo(tip.e.at)} /></div>
      ${tip.e.target && html`<div class="muted">${tip.e.target.title}</div>`}
      ${tip.e.notes && html`<div class="muted">${tip.e.notes}</div>`}
    </div>`}
  </div>`;
}

// ---------------- overview ----------------

export function OverviewPage({ version, onOpenVenue, go }) {
  const [o, setO] = useState(null);
  useEffect(() => { api("/api/overview").then(setO); }, [version]);
  if (!o) return html`<div class="page"><div class="loading">载入中…</div></div>`;
  const tile = (big, label, sub, onClick, cls = "") => html`<button class=${"stat tile " + cls} onClick=${onClick}>
    <b>${big}</b><span>${label}</span>${sub && html`<em>${sub}</em>`}</button>`;
  const nt = o.next_target, na = o.next_any, nm = o.next_milestone;
  const upcoming = o.events.filter((e) => e.days >= 0).slice(0, 12);
  return html`<div class="page"><div class="page-inner wide">
    <div class="page-head"><h1>总览</h1><span class="muted">${new Date().toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "long" })}</span></div>
    <div class="stats six">
      ${tile(nt ? html`${daysTo(nt.at)}<small> 天</small>` : "—", "下一个目标截稿", nt ? `${nt.acronym} · ${nt.target?.title || ""}` : "还没设目标", () => go("timeline"), nt && daysTo(nt.at) <= 30 ? "alert" : "")}
      ${tile(na ? html`${daysTo(na.at)}<small> 天</small>` : "—", "最近会议截稿", na ? na.title : "", () => na && onOpenVenue(na.venue))}
      ${tile(nm ? html`${daysTo(nm.at)}<small> 天</small>` : "—", "下一个里程碑", nm ? nm.title : "", () => go("plan"))}
      ${tile(html`${o.reading.T1}<small>/${o.reading.caps.T1}</small>`, "本周 T1", null, () => go("list"))}
      ${tile(o.reading.inbox, "收件箱", null, () => go("list"))}
      ${tile(o.ideas_new, "新想法", "⌘I 随手记", () => go("ideas"))}
    </div>
    <section class="card">
      <div class="card-head"><h2>未来 120 天</h2><span class="muted">时间按 ${TZ_LABEL} 显示</span></div>
      <${TimelineChart} events=${o.events} onPick=${(e) => e.venue && onOpenVenue(e.venue)} />
    </section>
    <div class="two-col">
      <section class="card">
        <div class="card-head"><h2>接下来</h2></div>
        ${upcoming.length === 0 && html`<div class="muted pad">120 天内没有事件</div>`}
        ${upcoming.map((e) => html`<button class="ev" onClick=${() => e.venue ? onOpenVenue(e.venue) : go("plan")}>
          <${Mark} type=${e.type} /><span class="ev-t">${e.title}${e.target ? html` <em>· ${e.target.title}</em>` : ""}</span>
          <span class="ev-d">${fmt(e.at, e.type !== "milestone")}</span><${Days} d=${daysTo(e.at)} />
        </button>`)}
      </section>
      <section class="card">
        <div class="card-head"><h2>投稿目标</h2><button class="tb-btn" onClick=${() => go("plan")}>管理</button></div>
        ${o.targets.length === 0 && html`<div class="muted pad">还没有目标。对右侧助手说"把 X 论文的目标设为 SIGIR 2027"</div>`}
        ${o.targets.map((t) => { const ev = o.events.find((e) => e.type === "target" && e.target?.id === t.id && e.days >= 0);
          return html`<div class="target">
            <div class="t-top"><b>${t.title}</b><span class="tag">${T_STATUS[t.status] || t.status}</span></div>
            <div class="t-mid"><button class="vlink" onClick=${() => onOpenVenue(t.venue)}>${t.venue.toUpperCase()}</button>
              ${(t.alternatives || []).map((a) => html`<button class="vlink alt" onClick=${() => onOpenVenue(a)}>${a.toUpperCase()}</button>`)}
              ${t.line && html`<span class="tag">${t.line}</span>`}</div>
            <div class="t-bot">${ev ? html`${ev.title} · ${fmt(ev.at)} <${Days} d=${daysTo(ev.at)} />` : html`<span class="muted">下一届截稿未公布</span>`}</div>
            ${t.notes && html`<div class="muted small">${t.notes}</div>`}
          </div>`; })}
      </section>
    </div>
  </div></div>`;
}

// ---------------- venue detail sheet ----------------

function RateChart({ rates }) {
  if (!rates?.length) return null;
  const w = 260, h = 70, pad = 6;
  const max = Math.max(...rates.map((r) => r.rate)) * 1.15;
  const x = (i) => pad + (i / Math.max(1, rates.length - 1)) * (w - 2 * pad);
  const y = (r) => h - pad - (r / max) * (h - 2 * pad);
  const [tip, setTip] = useState(null);
  return html`<div class="rate"><svg width=${w} height=${h}>
    <polyline points=${rates.map((r, i) => `${x(i)},${y(r.rate)}`).join(" ")} class="rate-line" />
    ${rates.map((r, i) => html`<g onMouseEnter=${() => setTip(r)} onMouseLeave=${() => setTip(null)}>
      <circle cx=${x(i)} cy=${y(r.rate)} r="10" fill="transparent" /><circle cx=${x(i)} cy=${y(r.rate)} r="4" class="rate-dot" /></g>`)}
  </svg><div class="muted small">${tip ? `${tip.year}：${(tip.rate * 100).toFixed(1)}%（${tip.accepted}/${tip.submitted}）` : `录用率 ${rates[0].year}–${rates[rates.length - 1].year}，最近 ${(rates[rates.length - 1].rate * 100).toFixed(1)}%`}</div></div>`;
}

export function VenueSheet({ vkey, onClose, toast, onChanged }) {
  const [v, setV] = useState(null);
  const [tgt, setTgt] = useState(null);
  useEffect(() => { setV(null); api(`/api/venues/${encodeURIComponent(vkey)}`).then(setV).catch((e) => toast(e.message)); }, [vkey]);
  if (!v) return html`<div class="sheet-bg" onClick=${onClose}><div class="sheet"><div class="loading">载入中…</div></div></div>`;
  const d = v.detail;
  const ed = v.editions[v.editions.length - 1];
  const toggle = async () => { await api(`/api/venues/${v.key}/watch`, { json: { on: !v.watched } }); setV({ ...v, watched: !v.watched }); onChanged?.(); };
  const saveTarget = async () => {
    try { await api("/api/plan/targets", { json: { title: tgt.title, venue: v.key, line: tgt.line || null } }); toast("已设为投稿目标"); setTgt(null); onChanged?.(); }
    catch (e) { toast(e.message); }
  };
  const links = [["官网", d?.homepage || ed?.link], ["CFP", d?.cfp_url], ["CORE", v.core_url], ["DBLP", v.dblp_url]].filter(([, u]) => u);
  return html`<div class="sheet-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}><div class="sheet">
    <div class="sheet-head">
      <div><h2>${v.acronym}</h2><div class="muted">${v.title}</div></div>
      <button class="tb-btn" onClick=${onClose}><${Icon} name="x" /></button>
    </div>
    <div class="row-wrap"><${Ranks} core=${v.core} ccf=${v.ccf} />${v.field && html`<span class="tag">${v.field}</span>`}</div>
    <div class="row-wrap">${links.map(([l, u]) => html`<a class="chip" href=${u} target="_blank" rel="noreferrer"><${Icon} name="external" sm />${l}</a>`)}</div>
    <div class="row-wrap">
      <button class=${"btn " + (v.watched ? "" : "primary")} onClick=${toggle}>${v.watched ? "取消关注" : "关注（上时间线）"}</button>
      <button class="btn" onClick=${() => setTgt({ title: "", line: "" })}>设为投稿目标</button>
    </div>
    ${tgt && html`<div class="inline-form"><input placeholder="论文名，如 Paper 1 · 题目" value=${tgt.title} onInput=${(e) => setTgt({ ...tgt, title: e.target.value })} />
      <input placeholder="研究线（可选）" value=${tgt.line} onInput=${(e) => setTgt({ ...tgt, line: e.target.value })} />
      <button class="btn primary" disabled=${!tgt.title.trim()} onClick=${saveTarget}>保存</button></div>`}

    <h3>截稿</h3>
    ${v.next ? html`<div class="dl">
        <div><span class="muted">${v.next.past ? "上一届（新一届未公布）" : `${v.next.year} ${v.next.kind === "abstract" ? "摘要" : "全文"}`}</span>
          <b>${fmt(v.next.at)}</b>${!v.next.past && html`<${Days} d=${daysTo(v.next.at)} />`}</div>
        ${v.next.paper_at && v.next.kind === "abstract" && html`<div><span class="muted">全文</span><b>${fmt(v.next.paper_at)}</b><${Days} d=${daysTo(v.next.paper_at)} /></div>`}
        <div class="muted small">${[v.next.date, v.next.place].filter(Boolean).join(" · ")} · 原始时区 ${v.next.timezone} · 来源 ccfddl</div>
        ${v.next.comment && html`<div class="muted small">${v.next.comment}</div>`}
      </div>` : html`<div class="muted">暂无截稿数据</div>`}

    ${d && html`<h3>赛道与投稿要求 <span class="muted small">${d.status === "confirmed" ? "官网核实" : d.status === "previous-edition" ? "沿用上一届，新一届待公布" : "未核实"} · ${d.edition || ""}</span></h3>
      ${(d.deadlines || []).length > 0 && html`<div class="mini-table">${d.deadlines.map((x) => html`<div><span>${x.track || "主会"}</span><span>${[x.abstract && "摘要 " + x.abstract, x.paper && "全文 " + x.paper].filter(Boolean).join(" · ")}</span></div>`)}</div>`}
      ${(d.tracks || []).length > 0 && html`<div class="mini-table">${d.tracks.map((t) => html`<div><span><b>${t.name}</b></span><span>${t.page_limit || ""}${t.notes ? html`<em class="muted"> ${t.notes}</em>` : ""}</span></div>`)}</div>`}
      ${(d.location || d.dates) && html`<div class="muted small">${[d.dates, d.location].filter(Boolean).join(" · ")}</div>`}
      ${d.format && html`<div class="fmt">
        ${d.format.template && html`<div><span class="muted">模板</span> ${d.format.template}</div>`}
        ${d.format.review_model && html`<div><span class="muted">审稿</span> ${d.format.review_model}</div>`}
        ${(d.format.notable_rules || []).length > 0 && html`<ul class="rules">${d.format.notable_rules.map((r) => html`<li>${r}</li>`)}</ul>`}
      </div>`}
      ${d.notes && html`<div class="muted small">${Array.isArray(d.notes) ? d.notes.join("；") : d.notes}</div>`}
      ${(d.sources || []).length > 0 && html`<div class="muted small srcs">来源：${d.sources.map((u) => html`<a href=${u} target="_blank" rel="noreferrer">${new URL(u).hostname}</a> `)}</div>`}`}

    ${v.accept_rates?.length > 0 && html`<h3>历年录用率</h3><${RateChart} rates=${v.accept_rates} />`}
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

export function TimelinePage({ version, tab: initialTab, onOpenVenue, toast, onChanged }) {
  const [tab, setTab] = useState(initialTab || "watched");
  const [rows, setRows] = useState([]);
  const [stats, setStats] = useState(null);
  const [q, setQ] = useState("");
  const [core, setCore] = useState("");
  const [ccf, setCcf] = useState("");
  const [plan, setPlan] = useState(null);
  useEffect(() => { if (initialTab) setTab(initialTab); }, [initialTab]);
  const loadVenues = () => api(`/api/venues?scope=${tab === "watched" ? "watched" : "all"}&q=${encodeURIComponent(q)}&core=${encodeURIComponent(core)}&ccf=${ccf}`)
    .then((r) => { setStats(r.stats); setRows(tab === "watched" ? r.venues.sort((a, b) => (a.next && !a.next.past ? a.next.at : "9") .localeCompare(b.next && !b.next.past ? b.next.at : "9")) : r.venues); });
  const loadPlan = () => api("/api/plan").then(setPlan);
  useEffect(() => { if (tab !== "plan") loadVenues(); else loadPlan(); }, [tab, q, core, ccf, version]);
  const star = async (v) => { await api(`/api/venues/${v.key}/watch`, { json: { on: !v.watched } }); toast(v.watched ? `已取消关注 ${v.acronym}` : `已关注 ${v.acronym}`); loadVenues(); onChanged?.(); };
  const refresh = async () => { toast("正在更新会议数据…"); try { await api("/api/venues/refresh", { method: "POST" }); toast("会议数据已更新"); loadVenues(); } catch (e) { toast(e.message); } };

  return html`<div class="page"><div class="page-inner wide">
    <div class="page-head"><h1>时间线</h1>
      <div class="seg">${[["watched", "关注会议"], ["all", "全部会议"], ["plan", "我的规划"]].map(([k, l]) => html`<button class=${tab === k ? "on" : ""} onClick=${() => setTab(k)}>${l}</button>`)}</div>
    </div>
    ${tab !== "plan" && stats && html`<div class="muted small src-line">CORE ${stats.core_source} ${stats.core} 个 · CCF ${stats.ccf} 个 · 截稿数据 ccfddl · 更新于 ${(stats.fetched_at || "").replace("T", " ").slice(0, 16)} · 时间按 ${TZ_LABEL}
      <button class="linkbtn" onClick=${refresh}>立即更新</button></div>`}
    ${tab === "all" && html`<div class="filters">
      <label class="search"><${Icon} name="search" sm /><input placeholder="搜索会议简称或全称" value=${q} onInput=${(e) => setQ(e.target.value)} /></label>
      <div class="seg">${["", "A*", "A", "B", "C"].map((r) => html`<button class=${core === r ? "on" : ""} onClick=${() => setCore(r)}>${r ? "CORE " + r : "CORE 全部"}</button>`)}</div>
      <div class="seg">${["", "A", "B", "C"].map((r) => html`<button class=${ccf === r ? "on" : ""} onClick=${() => setCcf(r)}>${r ? "CCF " + r : "CCF 全部"}</button>`)}</div>
    </div>`}
    ${tab !== "plan" && html`<div class="card list-card">
      ${rows.length === 0 && html`<div class="muted pad">${tab === "watched" ? "还没有关注的会议，去“全部会议”点星标" : "没有匹配的会议"}</div>`}
      ${rows.map((v) => html`<${VenueRow} v=${v} onOpen=${onOpenVenue} onStar=${star} />`)}
      ${tab === "all" && rows.length >= 300 && html`<div class="muted pad small">只显示前 300 个，输入关键词缩小范围</div>`}
    </div>`}
    ${tab === "plan" && plan && html`<${PlanEditor} plan=${plan} reload=${() => { loadPlan(); onChanged?.(); }} toast=${toast} onOpenVenue=${onOpenVenue} />`}
  </div></div>`;
}

function PlanEditor({ plan, reload, toast, onOpenVenue }) {
  const [m, setM] = useState({ title: "", date: "", line: "", kind: "milestone" });
  const [t, setT] = useState({ title: "", venue: "", line: "" });
  const call = async (fn) => { try { await fn(); reload(); } catch (e) { toast(e.message); } };
  const ms = [...plan.milestones].sort((a, b) => a.date.localeCompare(b.date));
  return html`<div class="two-col">
    <section class="card">
      <div class="card-head"><h2>投稿目标</h2></div>
      ${plan.targets.map((x) => html`<div class="prow">
        <div class="p-main"><b>${x.title}</b><div class="muted small">${x.line || ""} ${x.notes ? "· " + x.notes : ""}</div></div>
        <button class="vlink" onClick=${() => onOpenVenue(x.venue)}>${x.venue.toUpperCase()}</button>
        ${(x.alternatives || []).map((a) => html`<button class="vlink alt" onClick=${() => onOpenVenue(a)}>${a.toUpperCase()}</button>`)}
        <select value=${x.status} onChange=${(e) => call(() => api(`/api/plan/targets/${x.id}`, { method: "PATCH", json: { status: e.target.value } }))}>
          ${Object.entries(T_STATUS).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
        <button class="abtn" title="删除" onClick=${() => confirm("删除这个目标？") && call(() => api(`/api/plan/targets/${x.id}`, { method: "DELETE" }))}><${Icon} name="x" sm /></button>
      </div>`)}
      <div class="inline-form">
        <input placeholder="论文" value=${t.title} onInput=${(e) => setT({ ...t, title: e.target.value })} />
        <input placeholder="会议，如 SIGIR" value=${t.venue} onInput=${(e) => setT({ ...t, venue: e.target.value })} style="max-width:130px" />
        <input placeholder="研究线" value=${t.line} onInput=${(e) => setT({ ...t, line: e.target.value })} style="max-width:120px" />
        <button class="btn primary" disabled=${!t.title.trim() || !t.venue.trim()} onClick=${() => call(async () => { await api("/api/plan/targets", { json: t }); setT({ title: "", venue: "", line: "" }); })}>添加</button>
      </div>
    </section>
    <section class="card">
      <div class="card-head"><h2>里程碑 · Gate</h2></div>
      ${ms.map((x) => html`<div class=${"prow" + (x.status === "done" ? " done" : "")}>
        <${Mark} type="milestone" />
        <div class="p-main"><b>${x.title}</b> <span class="tag">${M_KIND[x.kind] || x.kind}</span>
          <div class="muted small">${x.date} · <${Days} d=${daysTo(x.date + "T23:59:00")} /> ${x.line ? "· " + x.line : ""} ${x.notes ? "· " + x.notes : ""}</div></div>
        <select value=${x.status} onChange=${(e) => call(() => api(`/api/plan/milestones/${x.id}`, { method: "PATCH", json: { status: e.target.value } }))}>
          ${Object.entries(M_STATUS).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
        <button class="abtn" title="删除" onClick=${() => confirm("删除这个里程碑？") && call(() => api(`/api/plan/milestones/${x.id}`, { method: "DELETE" }))}><${Icon} name="x" sm /></button>
      </div>`)}
      <div class="inline-form">
        <input placeholder="里程碑" value=${m.title} onInput=${(e) => setM({ ...m, title: e.target.value })} />
        <input type="date" value=${m.date} onInput=${(e) => setM({ ...m, date: e.target.value })} style="max-width:150px" />
        <select value=${m.kind} onChange=${(e) => setM({ ...m, kind: e.target.value })}>${Object.entries(M_KIND).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
        <button class="btn primary" disabled=${!m.title.trim() || !m.date} onClick=${() => call(async () => { await api("/api/plan/milestones", { json: m }); setM({ title: "", date: "", line: "", kind: "milestone" }); })}>添加</button>
      </div>
    </section>
  </div>`;
}

// ---------------- ideas (page stub + quick capture) ----------------

export function IdeasPage({ version }) {
  const [ideas, setIdeas] = useState([]);
  useEffect(() => { api("/api/ideas").then((r) => setIdeas(r.ideas)); }, [version]);
  return html`<div class="page"><div class="page-inner">
    <div class="page-head"><h1>想法</h1><span class="muted">⌘I 随时记 · 整理成 idea 笔记的功能在下一步</span></div>
    <div class="card list-card">
      ${ideas.length === 0 && html`<div class="muted pad">还没有想法。任何页面按 ⌘I，或对助手说“记一个想法：…”</div>`}
      ${ideas.map((i) => html`<div class="prow"><div class="p-main"><div>${i.text}</div>
        <div class="muted small">${(i.created_at || "").replace("T", " ").slice(0, 16)} ${i.line ? "· " + i.line : ""} ${i.paper ? "· " + i.paper + (i.page ? " p." + i.page : "") : ""} ${i.source === "assistant" ? "· 助手记录" : ""}</div></div></div>`)}
    </div>
  </div></div>`;
}

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

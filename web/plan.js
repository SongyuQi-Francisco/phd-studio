// Overview, timeline (venues + plan), venue detail sheet, quick idea capture.
// i18n: done
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { t, formatDate } from "/i18n.js";
import { C, Ring, Progress, Sparkline, Bars, HeatStrip, perWeek, dayKey, weekLabel } from "/charts.js";
import { Days, daysTo, Skeleton, Empty, Collapse, transition, monthDay, dayYear, dayTime } from "/ui.js";
import { FreshTag, CheckPanel, StaleCard, StaleNote, httpUrl, hostOf } from "/venue-check.js";

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
const TZ_LABEL = TZ.split("/").pop().replace(/_/g, " ");
const fmt = (iso, withTime = true) => (withTime ? dayTime(iso) : dayYear(iso));
// A date-only string ("2026-10-12") is read at noon, so no time zone moves it to another day.
const md = (date) => monthDay(typeof date === "string" && date.length <= 10 ? date + "T12:00:00" : date);
const TYPES = ["venue", "target", "milestone"]; // i18n-keys: plan.type.{venue,target,milestone} plan.lane.{venue,target,milestone}
const TYPE_COLOR = { venue: C.blue, target: C.orange, milestone: C.aqua };
const KINDS = ["milestone", "gate", "kill", "deadline"];
const kindName = (k) => (k === "milestone" ? t("common.term.milestone") : k === "gate" ? t("common.term.gate")
  : k === "kill" ? t("common.term.kill_check") : k === "deadline" ? t("plan.kind.deadline") : k);
const M_STATUSES = ["todo", "doing", "done", "warn"]; // i18n-keys: plan.mstatus.{todo,doing,done,warn}
const T_STATUSES = ["planning", "writing", "submitted", "accepted", "rejected", "dropped"]; // i18n-keys: plan.tstatus.{planning,writing,submitted,accepted,rejected,dropped}
const KILLS = ["clear", "partial", "triggered"]; // i18n-keys: plan.kill.state.{clear,partial,triggered}
const mStatus = (s) => (M_STATUSES.includes(s) ? t("plan.mstatus." + s) : s);
const tStatus = (s) => (T_STATUSES.includes(s) ? t("plan.tstatus." + s) : s);
const killState = (s) => (KILLS.includes(s) ? t("plan.kill.state." + s) : s);
const T_STEPS = ["planning", "writing", "submitted", "accepted"];
const HORIZON = 120;
const SENT = ["submitted", "accepted"];  // sent: the rest of that edition's deadlines no longer remind for this paper
const names = (list) => (list || []).map((x) => x.title).join(t("plan.sep"));
// the deadline event of a target still being prepared (an edition can hold several papers)
const evOf = (events, target) => events.find((e) => e.type === "target" && e.days >= 0 && (e.targets || []).some((x) => x.id === target.id));
// what to say about a target's deadlines when the calendar has no event for it to count down to (an event past the
// window, or at a venue that is not watched): go by the target's own state, never "not published" for a published one
const noDeadline = (target) => SENT.includes(target.status) ? t("plan.nodl.sent", { status: tStatus(target.status) })
  : target.state === "upcoming" ? t(target.edition ? "plan.nodl.upcoming_edition" : "plan.nodl.upcoming", { edition: target.edition, at: fmt(target.next_at) })
  : !target.edition ? t("plan.nodl.next_tba") : target.state === "past" ? t("plan.nodl.past", { edition: target.edition }) : t("plan.nodl.tba", { edition: target.edition });
// days to the target's next deadline: its calendar event, else the date the target itself reports
const daysOf = (target, ev) => ev ? daysTo(ev.at) : target.state === "upcoming" && target.next_at ? daysTo(target.next_at) : null;
// A venue deadline's title and its part after "ACME 2027", worded here: the server's own title is Chinese.
const evStage = (e) => [e.comment?.includes("官网") && e.comment.replace("（官网）", "").trim(), // i18n-ignore: the data's own tag
  t(e.stage === "abstract" ? "plan.stage.abstract" : "plan.stage.full")].filter(Boolean).join(" ");
const evTitle = (e) => (e.stage ? `${e.acronym} ${e.edition} ${evStage(e)}` : e.title);
const roundLabel = (r) => [r.comment ? r.comment.replace("（官网）", "") : t("plan.main_track"), // i18n-ignore: strips the data's own tag
  r.abstract && t("plan.abstract_at", { at: md(r.abstract) }), r.deadline ? t("plan.full_at", { at: md(r.deadline) }) : t("plan.full_tba")].filter(Boolean).join(" · ");

function Ranks({ core, ccf }) {
  return html`<span class="ranks">
    ${core && html`<span class=${"rk core " + (core === "A*" ? "top" : "")} title="CORE ICORE2026">CORE ${core}</span>`}
    ${ccf && html`<span class=${"rk ccf " + (ccf === "A" ? "top" : "")} title=${t("plan.ccf_title")}>CCF ${ccf}</span>`}
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
    title=${d == null ? t("plan.ring.tba_title") : late ? t("plan.ring.late_title", { n: -d }) : t("plan.ring.left_title", { n: d, max })}>
    <b class=${d != null && d <= 14 ? "hot" + (late ? " late" : "") : ""}>${d == null ? "—" : late ? t("plan.ring.late") : d}</b><small>${d == null ? t("plan.ring.tba") : late ? t("common.n_days", { n: -d }) : t(d === 1 ? "plan.ring.day" : "plan.ring.days")}</small>
  </${Ring}>`;
}

// The ring of a paper that is already sent: a check mark and its status.
function SentRing({ status, size, stroke }) {
  const label = tStatus(status);
  return html`<${Ring} value=${1} max=${1} size=${size} stroke=${stroke} color=${C.aqua} title=${label}><b>✓</b><small class=${label.length > 5 ? "sm" : ""}>${label}</small></${Ring}>`;
}

// ---------------- edition + round picker for a submission target ----------------

// Chooses which edition (year) of a venue a paper aims at, and optionally one round of it. `onChange({edition, deadline})`
// gets "" for "automatic" / "the whole edition"; `auto` is the edition "automatic" gives this target (default: the
// nearest upcoming one); a venue the catalog does not know shows nothing.
export function EditionPicker({ venue, edition, deadline, auto, onChange }) {
  const [opts, setOpts] = useState(null);
  useEffect(() => {
    setOpts(null);
    const v = (venue || "").trim();
    if (!v) return;
    let live = true;
    const timer = setTimeout(() => api(`/api/venues/${encodeURIComponent(v)}/editions`).then((o) => live && setOpts(o)).catch(() => live && setOpts(null)), 250);
    return () => { live = false; clearTimeout(timer); };
  }, [venue]);
  if (!opts) return null;
  const eds = [...opts.editions];
  if (edition && !eds.some((e) => e.year === edition)) eds.push({ year: edition, rounds: [], extra: true });
  eds.sort((a, b) => b.year - a.year);
  const cur = eds.find((e) => e.year === (edition || opts.default));
  const autoYear = auto || opts.default;
  const edLabel = (e) => `${e.year}${e.year === opts.default ? " · " + t("plan.ed.latest") : e.past ? " · " + t("plan.ed.closed") : e.rounds.length || e.extra ? "" : " · " + t("plan.ed.tba")}`;
  return html`<span class="ed-pick">
    <select value=${edition || ""} title=${t("plan.ed.which")} onChange=${(e) => onChange({ edition: e.target.value ? Number(e.target.value) : "", deadline: "" })}>
      <option value="">${autoYear ? t("plan.ed.auto", { year: autoYear }) : t("plan.ed.auto_next")}</option>
      ${eds.map((e) => html`<option value=${e.year}>${edLabel(e)}</option>`)}</select>
    ${edition && cur && cur.rounds.length > 0 && html`<select value=${deadline || ""} title=${t("plan.ed.round_title")} onChange=${(e) => onChange({ edition, deadline: e.target.value })}>
      <option value="">${t("plan.ed.whole")}</option>
      ${cur.rounds.map((r) => html`<option value=${r.key}>${roundLabel(r)}${r.past ? " · " + t("plan.ed.passed") : ""}</option>`)}</select>`}
  </span>`;
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
    <div class="legend">${TYPES.map((kind) => html`<span><${Mark} type=${kind} />${t("plan.type." + kind)}</span>`)}</div>
    <svg width=${w} height=${H} onMouseLeave=${() => setTip(null)}>
      ${Object.entries(lanes).map(([kind, y]) => html`<line x1=${L} x2=${w - R} y1=${y} y2=${y} class="lane" />
        ${!narrow && html`<text x=${L - 10} y=${y + 4} text-anchor="end" class="lane-label">${t("plan.lane." + kind)}</text>`}`)}
      ${months.map((m) => html`<line x1=${x(m)} x2=${x(m)} y1="22" y2=${H - 16} class="grid" />
        <text x=${x(m) + 4} y=${H - 2} class="tick">${formatDate(m, { month: "short" })}</text>`)}
      <line x1=${L} x2=${L} y1="18" y2=${H - 16} class="today" />
      <text x=${L + 4} y="13" class="tick strong">${t("common.today")}</text>
      ${shown.map((e, i) => {
        const cx = x(e.at), cy = lanes[e.type];
        const enter = () => setTip({ e, x: cx, y: cy });
        const shape = e.type === "venue" ? html`<circle cx=${cx} cy=${cy} r="6" class=${"m-" + e.type} />`
          : e.type === "target" ? html`<path d=${`M${cx} ${cy - 8} L${cx + 8} ${cy} L${cx} ${cy + 8} L${cx - 8} ${cy} Z`} class=${"m-" + e.type} />`
          : html`<rect x=${cx - 6} y=${cy - 6} width="12" height="12" rx="3" class=${"m-" + e.type} />`;
        return html`<g class="pt" style=${{ animationDelay: i * 25 + "ms" }} onMouseEnter=${enter} onClick=${() => onPick?.(e)}>
          <circle cx=${cx} cy=${cy} r="14" fill="transparent" /><g class=${"pt-mark" + (e.muted ? " muted" : "")}>${shape}</g>
          ${labelled.has(e) && html`<text x=${Math.max(cx, L + (e.acronym || e.title).slice(0, 12).length * 3.4 + 6)} y=${cy - 13} text-anchor="middle" class="plabel">${(e.acronym || e.title).slice(0, 12)}</text>`}
        </g>`;
      })}
    </svg>
    ${tip && html`<div class="tip" style=${{ left: Math.max(0, Math.min(tip.x + 12, w - 230)) + "px", top: tip.y + 10 + "px" }}>
      <b>${evTitle(tip.e)}</b><div>${fmt(tip.e.at)} · <${Days} d=${daysTo(tip.e.at)} /></div>
      ${tip.e.targets?.length > 0 && html`<div class="muted">${names(tip.e.targets)}</div>`}
      ${tip.e.closed?.length > 0 && html`<div class="muted">${t(tip.e.muted ? "plan.tip.sent_muted" : "plan.tip.sent", { names: names(tip.e.closed) })}</div>`}
      ${tip.e.notes && html`<div class="muted">${tip.e.notes}</div>`}
    </div>`}
  </div>`;
}

// ---------------- overview ----------------

function Countdown({ c, onClick, i }) {
  const target = c.target;
  const sent = c.kind === "target" && SENT.includes(target.status);
  const d = c.kind === "target" ? daysOf(target, c.ev) : c.ev ? daysTo(c.ev.at) : null;
  const kicker = c.kind === "target" ? `${t("plan.cd.target")} · ${(target.venue || "").toUpperCase()}${target.edition ? " " + target.edition : ""}`
    : c.kind === "milestone" ? t(c.ev.kind === "gate" ? "plan.cd.next_gate" : "plan.cd.next_milestone") : t("plan.cd.next_venue");
  const title = c.kind === "target" ? target.title : evTitle(c.ev);
  const dateLine = sent ? noDeadline(target) : c.ev ? `${c.kind === "target" ? evStage(c.ev) + " · " : ""}${fmt(c.ev.at, c.kind !== "milestone")}`
    : c.kind === "target" ? noDeadline(target) : t("plan.nodl.next_tba");
  return html`<button class="cd click" style=${{ "--i": i }} onClick=${onClick}>
    ${sent ? html`<${SentRing} status=${target.status} size=${64} stroke=${6} />`
      : html`<${DaysRing} d=${d} color=${TYPE_COLOR[c.kind]} />`}
    <div class="cd-body">
      <span class="cd-kicker" title=${kicker}><${Mark} type=${c.kind} /><span>${kicker}</span></span>
      <span class="cd-title" title=${title}>${title}</span>
      <span class="cd-date" title=${dateLine}>${dateLine}</span>
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
  if (!kills?.length) return html`<span class="lights none" title=${t("plan.kill.none")}>—</span>`;
  return html`<span class="lights">${kills.map((k, i) => html`<i class=${"kl " + k.state} title=${t("plan.kill.item", { id: k.id || "K" + (i + 1), state: killState(k.state), text: k.text })} />`)}</span>`;
}

const shortLine = (name) => name.replace(/^Research Line - /, "");

export function OverviewPage({ version, onOpenVenue, go }) {
  const [o, setO] = useState(null);
  const [x, setX] = useState({});
  const [more, setMore] = useState(false);
  useEffect(() => {
    api("/api/overview").then(setO);
    Promise.allSettled([api("/api/reading"), api("/api/ideas"), api("/api/track/runs"), api("/api/direction"), api("/api/venue-check/stale")])
      .then(([reading, ideaList, trackRuns, direction, stale]) => setX({ items: reading.value?.items || [], ideas: ideaList.value?.ideas || [],
        runs: trackRuns.value?.runs || [], lines: direction.value?.lines || [], stale: stale.value?.venues || [] }));
  }, [version]);
  if (!o) return html`<${Skeleton} tiles=${5} rows=${2} />`;
  const up = o.events.filter((e) => e.days >= 0);
  const cds = o.targets.map((target) => ({ kind: "target", target, ev: evOf(up, target) }));
  if (o.next_milestone) cds.push({ kind: "milestone", ev: o.next_milestone });
  const nv = up.find((e) => e.type === "venue" && !e.muted);  // not the deadlines of an edition whose paper is already sent
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
    <div class="page-head"><div><h1>${t("common.nav.overview")}</h1>
      <div class="sub">${t("plan.overview.sub", { date: formatDate(new Date(), { month: "long", day: "numeric", weekday: "long" }), tz: TZ_LABEL })}</div></div></div>

    ${cds.length > 0 && html`<div class="countdowns stagger" style=${{ "--n": cds.length }}>${cds.map((c, i) => html`<${Countdown} c=${c} i=${i}
      onClick=${() => c.kind === "milestone" ? go("plan") : (c.ev?.venue || c.target?.venue) ? onOpenVenue(c.ev?.venue || c.target.venue) : go("timeline")} />`)}</div>`}

    <${StaleNote} items=${x.stale} onOpen=${() => go("timeline")} />

    <div class="kpis stagger">
      <${Kpi} i=${0} label=${t("plan.kpi.read_week")} value=${r.done_week} sub=${t("plan.kpi.read_sub", { n: reads.counts.reduce((a, b) => a + b, 0), reading: r.reading })} onClick=${() => go("list")}>
        <${Sparkline} values=${reads.counts} labels=${reads.labels.map(weekLabel)} unit=${(n) => t("common.unit.papers", { n })} height=${30} /></${Kpi}>
      <${Kpi} i=${1} label=${t("common.tier.t1_week")} value=${html`${r.T1}<small>/${r.caps.T1}</small>`} warn=${r.T1 > r.caps.T1} sub=${t("plan.kpi.queued_sub", { n: r.queued, cap: r.caps.queued })} onClick=${() => go("list")}>
        <${Progress} value=${r.T1} max=${r.caps.T1} over=${r.T1 > r.caps.T1} label=${`T1 ${r.T1}/${r.caps.T1}`} /></${Kpi}>
      <${Kpi} i=${2} label=${t("common.status.inbox")} value=${r.inbox} sub=${t("plan.kpi.inbox_sub", { n: ins.counts.reduce((a, b) => a + b, 0) })} onClick=${() => go("list")}>
        <${Sparkline} values=${ins.counts} labels=${ins.labels.map(weekLabel)} unit=${(n) => t("common.unit.papers", { n })} color=${C.gray} height=${30} /></${Kpi}>
      <${Kpi} i=${3} label=${t("plan.kpi.new_ideas")} value=${o.ideas_new} sub=${ideas.length ? t("plan.kpi.ideas_sub", { kept: ideaN("kept"), promoted: ideaN("promoted") }) : t("plan.kpi.ideas_hint")} onClick=${() => go("ideas")}>
        <div class="segbar" title=${t("plan.kpi.ideas_title", { new: ideaN("new"), kept: ideaN("kept"), promoted: ideaN("promoted") })}>
          ${ideas.length ? ["new", "kept", "promoted"].map((s, k) => ideaN(s) > 0 && html`<i style=${{ flex: ideaN(s), background: `var(--ord-${k + 1})` }} />`) : html`<i class="none" />`}
        </div></${Kpi}>
      <${Kpi} i=${4} label=${t(runDays != null && runDays > 7 ? "plan.kpi.run_latest" : "plan.kpi.run_week")} value=${run ? run.stats?.kept ?? 0 : "—"}
        sub=${run ? t("plan.kpi.run_sub", { date: md(run.run), input: run.stats?.input ?? "?" }) : t("plan.kpi.run_none")} onClick=${() => go("track")}>
        ${run && html`<div class="tier-mini">${["A", "B", "C"].map((tier, k) => html`<span title=${t("plan.kpi.tier_title", { tier, n: tiers[tier] || 0 })}><i style=${{ background: `var(--ord-${3 - k})` }} />${tier}<b>${tiers[tier] || 0}</b></span>`)}
</div>`}
      </${Kpi}>
    </div>

    <section class="card">
      <div class="card-head"><h2>${t("plan.next_days", { n: HORIZON })}</h2><button class="tb-btn" onClick=${() => go("timeline")}>${t("plan.tab.all")}<${Icon} name="right" sm /></button></div>
      <${TimelineChart} events=${o.events} days=${HORIZON} onPick=${(e) => e.venue ? onOpenVenue(e.venue) : go("plan")} />
    </section>

    <div class="two-col">
      <section class="card">
        <div class="card-head"><h2>${t("plan.lines.title")}</h2><button class="tb-btn" onClick=${() => go("direction")}>${t("common.nav.direction")}<${Icon} name="right" sm /></button></div>
        ${lines.length === 0 && html`<${Empty} compact icon="compass" title=${t("plan.lines.empty")} hint=${t("plan.lines.empty_hint")} />`}
        ${lines.map((l) => { const next = (l.deadlines || [])[0];
          return html`<button class="lrow" onClick=${() => go("direction")}>
            <span class=${"st-dot " + (l.status || "")} title=${l.status || ""} />
            <span class="lr-name">${shortLine(l.name)}</span>
            <${KillLights} kills=${l.kills} />
            <span class="lr-n" title=${t("plan.lines.papers_title")}>${t("plan.lines.papers", { n: l.papers })}</span>
            ${next ? html`<${Days} d=${daysTo(next.at)} />` : html`<span class="days past">${t("plan.lines.no_deadline")}</span>`}
          </button>`; })}
      </section>
      <section class="card">
        <div class="card-head"><h2>${t("plan.coming")}</h2><span class="muted small">${t("plan.coming_n", { n: up.length })}</span></div>
        ${up.length === 0 && html`<${Empty} compact icon="calendar" title=${t("plan.coming_empty", { n: HORIZON })} hint=${t("plan.coming_empty_hint")} />`}
        ${up.slice(0, 5).map((e) => html`<${EvRow} e=${e} onOpenVenue=${onOpenVenue} go=${go} />`)}
        ${up.length > 5 && html`<${Collapse} open=${more}>${up.slice(5).map((e) => html`<${EvRow} e=${e} onOpenVenue=${onOpenVenue} go=${go} />`)}</${Collapse}>
          <button class="more" onClick=${() => setMore(!more)}>${more ? t("plan.less") : t("plan.more", { n: up.length - 5 })}<${Icon} name=${more ? "up" : "down"} sm /></button>`}
      </section>
    </div>

    <section class="card">
      <div class="card-head"><h2>${t("plan.activity")}</h2><span class="muted small">${t("plan.activity_hint")}</span></div>
      <${HeatStrip} counts=${heat} unit=${(n) => t("plan.activity_unit", { n })} />
    </section>
  </div></div>`;
}

function EvRow({ e, onOpenVenue, go }) {
  return html`<button class="ev" onClick=${() => e.venue ? onOpenVenue(e.venue) : go("plan")}>
    <${Mark} type=${e.type} /><span class="ev-t">${evTitle(e)}${e.targets?.length ? html` <em>· ${names(e.targets)}</em>` : e.muted ? html` <em>· ${t("plan.ev.sent", { names: names(e.closed) })}</em>` : ""}</span>
    <span class="ev-d">${fmt(e.at, e.type !== "milestone")}</span><${Days} d=${daysTo(e.at)} />
  </button>`;
}

// ---------------- venue detail sheet ----------------

export function VenueSheet({ vkey, onClose, toast, onChanged, onAsk }) {
  const [v, setV] = useState(null);
  const [chk, setChk] = useState(null);
  const [tgt, setTgt] = useState(null);
  const [rules, setRules] = useState(false);
  const reload = () => Promise.all([api(`/api/venues/${encodeURIComponent(vkey)}`), api(`/api/venue-check/${encodeURIComponent(vkey)}`)])
    .then(([venue, check]) => { setV(venue); setChk(check); });
  useEffect(() => { setV(null); setChk(null); reload().catch((e) => toast(e.message)); }, [vkey]);
  if (!v) return html`<div class="sheet-bg" onClick=${onClose}><div class="sheet"><div class="skel" style="width:120px;height:28px" />
    <div class="skel" style="width:70%;height:14px;margin-top:12px" /><div class="skel" style="width:100%;height:96px;margin-top:22px;border-radius:14px" /></div></div>`;
  const d = v.detail;
  const ed = v.editions[v.editions.length - 1];
  const toggle = async () => { await api(`/api/venues/${v.key}/watch`, { json: { on: !v.watched } }); setV({ ...v, watched: !v.watched }); onChanged?.(); };
  const saveTarget = async () => {
    try {
      await api("/api/plan/targets", { json: { title: tgt.title, venue: v.key, line: tgt.line || null, edition: tgt.edition || null, deadline: tgt.deadline || null } });
      toast(t("plan.sheet.target_set")); setTgt(null); onChanged?.();
    } catch (e) { toast(e.message); }
  };
  const links = [[t("plan.sheet.website"), d?.homepage || ed?.link], ["CFP", d?.cfp_url], ["CORE", v.core_url], ["DBLP", v.dblp_url]].filter(([, u]) => httpUrl(u));
  const n = v.next, live = n && !n.past;
  const rates = v.accept_rates || [];
  const ruleList = d?.format?.notable_rules || [];
  return html`<div class="sheet-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}><div class="sheet">
    <div class="sheet-head">
      <div><h2>${v.acronym}</h2><div class="muted">${v.title}</div></div>
      <button class="tb-btn" onClick=${onClose}><${Icon} name="x" /></button>
    </div>
    <div class="row-wrap"><${Ranks} core=${v.core} ccf=${v.ccf} />${v.field && html`<span class="tag">${v.field}</span>`}<${FreshTag} f=${v.freshness} /></div>

    <div class="v-hero">
      <${DaysRing} d=${live ? daysTo(n.at) : null} max=${180} size=${86} stroke=${7} color=${C.blue} />
      <div class="v-hero-b">
        ${n ? html`<span class="muted small">${n.past ? t("plan.sheet.last_edition") : t(n.kind === "abstract" ? "plan.sheet.abstract_deadline" : "plan.sheet.full_deadline", { year: n.year })}</span>
          <b class="v-when">${fmt(n.at)}</b>
          ${n.paper_at && n.kind === "abstract" && html`<span class="small">${t("plan.full_at", { at: fmt(n.paper_at) })} <${Days} d=${daysTo(n.paper_at)} /></span>`}
          <span class="muted small">${[n.date, n.place].filter(Boolean).join(" · ")}</span>
          <span class="muted small">${t("plan.sheet.tz_source", { tz: n.timezone })}${n.comment ? " · " + n.comment : ""}</span>` : html`<span class="muted">${t("plan.sheet.no_deadline")}</span>`}
      </div>
      ${rates.length > 0 && html`<div class="v-rate"><b>${(rates[rates.length - 1].rate * 100).toFixed(1)}%</b><span class="muted small">${rates[rates.length - 1].year} ${t("common.term.acceptance_rate")}</span></div>`}
    </div>

    <div class="row-wrap">${links.map(([l, u]) => html`<a class="chip" href=${u} target="_blank" rel="noreferrer"><${Icon} name="external" sm />${l}</a>`)}</div>
    <${CheckPanel} vkey=${v.key} v=${v} chk=${chk} reload=${reload} onAsk=${onAsk} onClose=${onClose} toast=${toast} onChanged=${onChanged} />
    <div class="row-wrap">
      <button class=${"btn " + (v.watched ? "" : "primary")} onClick=${toggle}><${Icon} name="star" sm /> ${v.watched ? t("plan.sheet.watching") : t("plan.sheet.watch")}</button>
      <button class="btn" onClick=${() => setTgt(tgt ? null : { title: "", line: "", edition: "", deadline: "" })}>${t("plan.sheet.set_target")}</button>
    </div>
    <${Collapse} open=${!!tgt}>${tgt && html`<div class="inline-form"><input placeholder=${t("plan.sheet.paper_hint")} value=${tgt.title} onInput=${(e) => setTgt({ ...tgt, title: e.target.value })} />
      <input placeholder=${t("plan.sheet.line_hint")} value=${tgt.line} onInput=${(e) => setTgt({ ...tgt, line: e.target.value })} />
      <${EditionPicker} venue=${v.key} edition=${tgt.edition} deadline=${tgt.deadline} onChange=${(p) => setTgt({ ...tgt, ...p })} />
      <button class="btn primary" disabled=${!tgt.title.trim()} onClick=${saveTarget}>${t("common.save")}</button></div>`}</${Collapse}>

    ${rates.length > 1 && html`<h3>${t("plan.sheet.rates")}</h3>
      <${Bars} labels=${rates.map((r) => String(r.year))} height=${120} format=${(x) => Math.round(x * 100) + "%"}
        series=${[{ name: t("common.term.acceptance_rate"), color: C.blue, values: rates.map((r) => r.rate) }]} />
      <div class="muted small">${rates.map((r) => `${r.year}: ${r.accepted}/${r.submitted}`).join(" · ")}</div>`}

    ${d && html`<h3>${t("plan.sheet.tracks")} <span class=${"tag " + (d.status === "confirmed" ? "ok" : "warn")}>${t(d.status === "confirmed" ? "plan.sheet.verified" : d.status === "previous-edition" ? "plan.sheet.from_last" : "plan.sheet.unverified")}</span> <span class="muted small">${[d.edition, d.verified_at && t("plan.sheet.checked", { at: d.verified_at })].filter(Boolean).join(" · ")}</span></h3>
      ${(d.deadlines || []).length > 0 && html`<div class="mini-table">${d.deadlines.map((x) => html`<div><span>${x.track || t("plan.main_track")}</span><span>${[x.abstract && t("plan.abstract_at", { at: x.abstract }), x.paper && t("plan.full_at", { at: x.paper })].filter(Boolean).join(" · ")}</span></div>`)}</div>`}
      ${(d.tracks || []).length > 0 && html`<div class="mini-table">${d.tracks.map((track) => html`<div><span><b>${track.name}</b></span><span>${track.page_limit || ""}${track.notes ? html`<em class="muted"> ${track.notes}</em>` : ""}</span></div>`)}</div>`}
      ${(d.location || d.dates) && html`<div class="muted small">${[d.dates, d.location].filter(Boolean).join(" · ")}</div>`}
      ${d.format && html`<div class="fmt">
        ${d.format.template && html`<div><span class="muted">${t("plan.sheet.template")}</span> ${d.format.template}</div>`}
        ${d.format.review_model && html`<div><span class="muted">${t("plan.sheet.review")}</span> ${d.format.review_model}</div>`}
        ${ruleList.length > 0 && html`<button class="more" onClick=${() => setRules(!rules)}>${rules ? t("plan.less") : t("plan.sheet.rules", { n: ruleList.length })}<${Icon} name=${rules ? "up" : "down"} sm /></button>
          <${Collapse} open=${rules}><ul class="rules">${ruleList.map((r) => html`<li>${r}</li>`)}</ul></${Collapse}>`}
      </div>`}
      ${d.notes && html`<div class="muted small">${Array.isArray(d.notes) ? d.notes.join(t("plan.sep_semi")) : d.notes}</div>`}
      ${(d.sources || []).length > 0 && html`<div class="muted small srcs">${t("plan.sheet.sources")}${d.sources.filter(httpUrl).map((u) => html`<a href=${u} target="_blank" rel="noreferrer">${hostOf(u)}</a> `)}</div>`}`}
  </div></div>`;
}

// ---------------- timeline page ----------------

function VenueRow({ v, onOpen, onStar }) {
  const n = v.next;
  return html`<div class="vrow" onClick=${() => onOpen(v.key)}>
    <button class=${"star" + (v.watched ? " on" : "")} title=${t(v.watched ? "plan.unwatch" : "plan.watch")} onClick=${(e) => { e.stopPropagation(); onStar(v); }}><${Icon} name="star" sm /></button>
    <div class="v-main"><b>${v.acronym}</b><span class="muted">${v.title}</span></div>
    <${Ranks} core=${v.core} ccf=${v.ccf} />
    <div class="v-dl">${n ? (n.past ? html`<span class="muted">${t("plan.row.last", { at: fmt(n.at, false) })}</span>` : html`<span>${n.kind === "abstract" ? t("plan.abstract_at", { at: fmt(n.at) }) : fmt(n.at)}</span><${Days} d=${daysTo(n.at)} />`) : html`<span class="muted">—</span>`}</div>
  </div>`;
}

function VenueCard({ v, targets, onOpen, onStar, i }) {
  const n = v.next, live = n && !n.past, target = targets.length > 0;
  const rate = v.accept_rate;
  return html`<div class=${"vcard click" + (live ? "" : " past")} style=${{ "--i": i }} role="button" tabindex="0"
    onClick=${() => onOpen(v.key)} onKeyDown=${(e) => e.key === "Enter" && onOpen(v.key)}>
    <div class="vc-top"><b>${v.acronym}</b>${target && html`<span class="vc-tgt" title=${targets.map((x) => `${x.title}${x.edition ? " (" + x.edition + ")" : ""}`).join("\n")}><${Mark} type="target" />${t("plan.card.target")}${targets.length > 1 ? " ×" + targets.length : targets[0].edition ? " " + targets[0].edition : ""}</span>`}<span class="grow" />
      <button class=${"star" + (v.watched ? " on" : "")} title=${t("plan.unwatch")} onClick=${(e) => { e.stopPropagation(); onStar(v); }}><${Icon} name="star" sm /></button></div>
    <div class="vc-name" title=${v.title}>${v.title}</div>
    <div class="vc-mid">
      <${DaysRing} d=${live ? daysTo(n.at) : null} max=${180} size=${58} stroke=${5} color=${target ? C.orange : C.blue} />
      <div class="vc-dl">${live
        ? html`<span class="muted small">${t(n.kind === "abstract" ? "plan.sheet.abstract_deadline" : "plan.sheet.full_deadline", { year: n.year })}</span><b>${fmt(n.at)}</b><span class="muted small vc-place">${n.place || ""}</span>`
        : html`<span class="muted small">${t("plan.card.next_tba")}</span><span class="small">${t("plan.row.last", { at: n ? fmt(n.at, false) : "—" })}</span>`}</div>
    </div>
    <div class="vc-foot"><${Ranks} core=${v.core} ccf=${v.ccf} /><${FreshTag} f=${v.freshness} /><span class="grow" />
      ${rate && html`<span class="vc-rate" title=${`${rate.year} ${t("common.term.acceptance_rate")} ${rate.accepted}/${rate.submitted}`}><span class="muted small">${t("plan.card.accepted")}</span><b>${(rate.rate * 100).toFixed(0)}%</b></span>`}</div>
  </div>`;
}

const TABS = ["watched", "all", "plan"]; // i18n-keys: plan.tab.{watched,all,plan}

export function TimelinePage({ version, tab: initialTab, onOpenVenue, toast, onChanged }) {
  const [tab, setTab] = useState(initialTab || "watched");
  const [rows, setRows] = useState(null);
  const [stats, setStats] = useState(null);
  const [q, setQ] = useState("");
  const [core, setCore] = useState("");
  const [ccf, setCcf] = useState("");
  const [plan, setPlan] = useState(null);
  const [stale, setStale] = useState(null);
  useEffect(() => { if (initialTab) setTab(initialTab); }, [initialTab]);
  const loadVenues = () => api(`/api/venues?scope=${tab === "watched" ? "watched" : "all"}&q=${encodeURIComponent(q)}&core=${encodeURIComponent(core)}&ccf=${ccf}`)
    .then((r) => { setStats(r.stats); setRows(tab === "watched" ? r.venues.sort((a, b) => (a.next && !a.next.past ? a.next.at : "9") .localeCompare(b.next && !b.next.past ? b.next.at : "9")) : r.venues); });
  const loadPlan = () => api("/api/plan").then(setPlan);
  useEffect(() => { if (tab !== "plan") loadVenues(); if (tab !== "all") loadPlan(); if (tab === "watched") api("/api/venue-check/stale").then(setStale).catch(() => {}); },
    [tab, q, core, ccf, version]);
  const star = async (v) => { await api(`/api/venues/${v.key}/watch`, { json: { on: !v.watched } }); toast(t(v.watched ? "plan.unwatched" : "plan.watched", { name: v.acronym })); loadVenues(); onChanged?.(); };
  const refresh = async () => { toast(t("plan.refreshing")); try { await api("/api/venues/refresh", { method: "POST" }); toast(t("plan.refreshed")); loadVenues(); } catch (e) { toast(e.message); } };
  const targetsOf = {};  // venue -> the papers still being prepared for it (a sent paper no longer marks the venue)
  for (const target of plan?.targets || []) if (!SENT.includes(target.status)) (targetsOf[target.venue] ||= []).push(target);
  const live = (rows || []).filter((v) => v.next && !v.next.past).length;

  return html`<div class="page"><div class="page-inner wide">
    <div class="page-head"><div><h1>${t("common.nav.timeline")}</h1>
      ${tab !== "plan" && stats && html`<div class="sub">${t("plan.sub.stats", { source: stats.core_source, core: stats.core, ccf: stats.ccf, at: (stats.fetched_at || "").replace("T", " ").slice(5, 16), tz: TZ_LABEL })}
        <button class="linkbtn" onClick=${refresh}>${t("plan.refresh")}</button></div>`}
      ${tab === "plan" && html`<div class="sub">${t("plan.sub.plan", { tz: TZ_LABEL })}</div>`}</div>
      <div class="seg">${TABS.map((k) => html`<button class=${tab === k ? "on" : ""} onClick=${() => transition(() => setTab(k))}>${t("plan.tab." + k)}</button>`)}</div>
    </div>
    ${tab === "watched" && plan && html`<section class="card">
      <div class="card-head"><h2>${t("plan.next_days", { n: 180 })}</h2><span class="muted small">${t("plan.announced", { n: live })}</span></div>
      <${TimelineChart} events=${plan.events} days=${180} onPick=${(e) => e.venue && onOpenVenue(e.venue)} /></section>`}
    ${tab === "watched" && html`<${StaleCard} items=${stale?.venues} months=${stale?.months} onOpen=${onOpenVenue} />`}
    ${tab === "all" && html`<div class="filters">
      <label class="search"><${Icon} name="search" sm /><input placeholder=${t("plan.filter.search")} value=${q} onInput=${(e) => setQ(e.target.value)} /></label>
      <div class="seg">${["", "A*", "A", "B", "C"].map((r) => html`<button class=${core === r ? "on" : ""} onClick=${() => setCore(r)}>${r ? "CORE " + r : t("plan.filter.core_all")}</button>`)}</div>
      <div class="seg">${["", "A", "B", "C"].map((r) => html`<button class=${ccf === r ? "on" : ""} onClick=${() => setCcf(r)}>${r ? "CCF " + r : t("plan.filter.ccf_all")}</button>`)}</div>
    </div>`}
    ${tab === "watched" && rows && (rows.length === 0
      ? html`<div class="card"><${Empty} icon="star" title=${t("plan.watched_empty")} hint=${t("plan.watched_empty_hint")}>
          <button class="btn primary" onClick=${() => setTab("all")}>${t("plan.browse_all")}</button></${Empty}></div>`
      : html`<div class="vgrid stagger">${rows.map((v, i) => html`<${VenueCard} key=${v.key} v=${v} i=${Math.min(i, 12)} targets=${targetsOf[v.key] || []} onOpen=${onOpenVenue} onStar=${star} />`)}</div>`)}
    ${tab === "all" && rows && html`<div class="card list-card">
      ${rows.length === 0 && html`<${Empty} compact icon="search" title=${t("plan.none")} hint=${t("plan.none_hint")} />`}
      ${rows.map((v) => html`<${VenueRow} v=${v} onOpen=${onOpenVenue} onStar=${star} />`)}
      ${rows.length >= 300 && html`<div class="muted pad small">${t("plan.first_300")}</div>`}
    </div>`}
    ${tab === "plan" && plan && html`<${PlanEditor} plan=${plan} reload=${() => { loadPlan(); onChanged?.(); }} toast=${toast} onOpenVenue=${onOpenVenue} />`}
  </div></div>`;
}

function TargetCard({ x, ev, call, onOpenVenue }) {
  const [editing, setEditing] = useState(false);
  const step = T_STEPS.indexOf(x.status), sent = SENT.includes(x.status);
  const patch = (json) => call(() => api(`/api/plan/targets/${x.id}`, { method: "PATCH", json }));
  return html`<div class=${"tcard" + (sent ? " sent" : "")}>
    ${sent ? html`<${SentRing} status=${x.status} size=${58} stroke=${5} />`
      : html`<${DaysRing} d=${daysOf(x, ev)} max=${180} size=${58} stroke=${5} color=${C.orange} />`}
    <div class="tc-body">
      <div class="tc-top"><b>${x.title}</b>
        <button class="abtn" title=${t("common.delete")} onClick=${() => confirm(t("plan.target.confirm_delete")) && call(() => api(`/api/plan/targets/${x.id}`, { method: "DELETE" }))}><${Icon} name="x" sm /></button></div>
      <div class="row-wrap tight"><button class="vlink" onClick=${() => onOpenVenue(x.venue)}>${x.venue.toUpperCase()}</button>
        <button class=${"ed-chip" + (x.edition_from === "set" ? "" : " guess")} title=${t(x.edition_from === "set" ? "plan.target.edition_set" : "plan.target.edition_guess")}
          onClick=${() => setEditing(!editing)}>${x.edition ? x.edition : t("plan.target.edition_unset")}${x.deadline ? html` · ${t("plan.target.round", { date: md(x.deadline) })}` : ""}<${Icon} name="down" sm /></button>
        ${(x.alternatives || []).map((a) => html`<button class="vlink alt" onClick=${() => onOpenVenue(a)}>${a.toUpperCase()}</button>`)}
        ${x.line && html`<span class="tag">${x.line}</span>`}
        <span class="muted small">${ev ? evStage(ev) + " " + fmt(ev.at) : noDeadline(x)}</span></div>
      <${Collapse} open=${editing}>${editing && html`<div class="ed-edit">
        <${EditionPicker} venue=${x.venue} edition=${x.edition_from === "set" ? x.edition : ""} deadline=${x.deadline} auto=${x.edition_auto} onChange=${(p) => patch(p).then(() => !p.edition && setEditing(false))} />
        <span class="muted small">${t(x.edition_from === "set" ? "plan.target.edition_set_hint" : "plan.target.edition_guess_hint")}</span></div>`}</${Collapse}>
      <div class="stepper">${T_STEPS.map((s, k) => html`<button class=${"step" + (k < step ? " done" : k === step ? " on" : "")} title=${t("plan.target.set_status", { status: tStatus(s) })}
          onClick=${() => patch({ status: s })}><i />${tStatus(s)}</button>`)}
        ${step < 0 && html`<span class="tag warn">${tStatus(x.status)}</span>`}
        <select value=${x.status} title=${t("plan.target.all_statuses")} onChange=${(e) => patch({ status: e.target.value })}>
          ${T_STATUSES.map((k) => html`<option value=${k}>${tStatus(k)}</option>`)}</select></div>
      ${sent && html`<div class="muted small">${t(x.edition ? "plan.target.sent_note_edition" : "plan.target.sent_note", { edition: x.edition })}</div>`}
      ${x.notes && html`<div class="muted small clamp2">${x.notes}</div>`}
    </div>
  </div>`;
}

function PlanEditor({ plan, reload, toast, onOpenVenue }) {
  const [m, setM] = useState({ title: "", date: "", line: "", kind: "milestone" });
  const [tg, setTg] = useState({ title: "", venue: "", line: "", edition: "", deadline: "" });
  const [adding, setAdding] = useState({ target: false, milestone: false });
  const call = async (fn) => { try { await fn(); reload(); } catch (e) { toast(e.message); } };
  const ms = [...plan.milestones].sort((a, b) => a.date.localeCompare(b.date));
  const today = new Date().toISOString().slice(0, 10);
  const firstFuture = ms.findIndex((x) => x.date >= today);
  const done = ms.filter((x) => x.status === "done").length;
  const gates = ms.filter((x) => x.kind === "gate");
  return html`<div class="two-col">
    <section class="card">
      <div class="card-head"><h2>${t("plan.targets.title")}</h2><button class="tb-btn" onClick=${() => setAdding({ ...adding, target: !adding.target })}><${Icon} name="plus" sm />${t("common.add")}</button></div>
      ${plan.targets.length === 0 && html`<${Empty} compact icon="star" title=${t("plan.targets.empty")} hint=${t("plan.targets.empty_hint")} />`}
      ${plan.targets.map((x) => html`<${TargetCard} key=${x.id} x=${x} ev=${evOf(plan.events, x)} call=${call} onOpenVenue=${onOpenVenue} />`)}
      <${Collapse} open=${adding.target}><div class="inline-form">
        <input placeholder=${t("plan.targets.paper_hint")} value=${tg.title} onInput=${(e) => setTg({ ...tg, title: e.target.value })} />
        <input placeholder=${t("plan.targets.venue_hint")} value=${tg.venue} onInput=${(e) => setTg({ ...tg, venue: e.target.value, edition: "", deadline: "" })} style="max-width:130px" />
        <input placeholder=${t("common.term.research_line")} value=${tg.line} onInput=${(e) => setTg({ ...tg, line: e.target.value })} style="max-width:120px" />
        <${EditionPicker} venue=${tg.venue} edition=${tg.edition} deadline=${tg.deadline} onChange=${(p) => setTg({ ...tg, ...p })} />
        <button class="btn primary" disabled=${!tg.title.trim() || !tg.venue.trim()} onClick=${() => call(async () => {
          await api("/api/plan/targets", { json: { ...tg, edition: tg.edition || null, deadline: tg.deadline || null } });
          setTg({ title: "", venue: "", line: "", edition: "", deadline: "" }); })}>${t("common.add")}</button>
      </div></${Collapse}>
    </section>
    <section class="card">
      <div class="card-head"><h2>${t("common.term.milestone")} · ${t("common.term.gate")}</h2><button class="tb-btn" onClick=${() => setAdding({ ...adding, milestone: !adding.milestone })}><${Icon} name="plus" sm />${t("common.add")}</button></div>
      ${ms.length > 0 && html`<div class="ms-sum">
        <div><b>${done}</b><span class="muted small">/${ms.length} ${t("plan.ms.done_word")}</span></div><div class="grow"><${Progress} value=${done} max=${ms.length} color=${C.aqua} label=${t("plan.ms.progress", { done, total: ms.length })} /></div>
        <span class="muted small">${t("common.term.gate")} ${gates.filter((g) => g.status === "done").length}/${gates.length}</span></div>`}
      ${ms.length === 0 && html`<${Empty} compact icon="calendar" title=${t("plan.ms.empty")} hint=${t("plan.ms.empty_hint")} />`}
      <div class="mtl">
        ${ms.map((x, k) => html`${k === (firstFuture < 0 ? ms.length : firstFuture) && html`<div class="mtl-today"><span>${t("common.today")} ${md(new Date())}</span></div>`}
          <div class=${"mtl-row st-" + x.status + (x.date < today ? " past" : "")}>
            <span class="mtl-dot" />
            <div class="mtl-date"><b>${md(x.date + "T12:00:00")}</b><${Days} d=${daysTo(x.date + "T23:59:00")} /></div>
            <div class="p-main"><b>${x.title}</b> <span class="tag">${kindName(x.kind)}</span>${x.line ? html` <span class="tag">${x.line}</span>` : ""}
              ${x.notes && html`<div class="muted small clamp2">${x.notes}</div>`}</div>
            <select value=${x.status} onChange=${(e) => call(() => api(`/api/plan/milestones/${x.id}`, { method: "PATCH", json: { status: e.target.value } }))}>
              ${M_STATUSES.map((s) => html`<option value=${s}>${mStatus(s)}</option>`)}</select>
            <button class="abtn" title=${t("common.delete")} onClick=${() => confirm(t("plan.ms.confirm_delete")) && call(() => api(`/api/plan/milestones/${x.id}`, { method: "DELETE" }))}><${Icon} name="x" sm /></button>
          </div>`)}
        ${ms.length > 0 && firstFuture < 0 && html`<div class="mtl-today"><span>${t("common.today")} ${md(new Date())}</span></div>`}
      </div>
      <${Collapse} open=${adding.milestone}><div class="inline-form">
        <input placeholder=${t("common.term.milestone")} value=${m.title} onInput=${(e) => setM({ ...m, title: e.target.value })} />
        <input type="date" value=${m.date} onInput=${(e) => setM({ ...m, date: e.target.value })} style="max-width:150px" />
        <select value=${m.kind} onChange=${(e) => setM({ ...m, kind: e.target.value })}>${KINDS.map((k) => html`<option value=${k}>${kindName(k)}</option>`)}</select>
        <button class="btn primary" disabled=${!m.title.trim() || !m.date} onClick=${() => call(async () => { await api("/api/plan/milestones", { json: m }); setM({ title: "", date: "", line: "", kind: "milestone" }); })}>${t("common.add")}</button>
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
      <h2>${t("plan.capture.title")}</h2>
      ${context?.paper && html`<div class="muted small" style="margin:-8px 0 8px">${t("plan.capture.linked", { paper: context.paper })}${context.page ? " · p." + context.page : ""}</div>`}
      <textarea ref=${ref} class="ta" rows="4" placeholder=${t("plan.capture.hint")} value=${text} onInput=${(e) => setText(e.target.value)}
        onKeyDown=${(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) save(); if (e.key === "Escape") onClose(); }} />
      ${lines.length > 0 && html`<div class="seg" style="margin-top:10px"><button class=${!line ? "on" : ""} onClick=${() => setLine("")}>${t("plan.capture.ungrouped")}</button>
        ${lines.map((l) => html`<button class=${line === l ? "on" : ""} onClick=${() => setLine(l)}>${l}</button>`)}</div>`}
      <div class="row-end" style="margin-top:14px"><button class="btn ghost" onClick=${onClose}>${t("common.cancel")}</button>
        <button class="btn primary" disabled=${!text.trim()} onClick=${save}>${t("common.save")} ⌘↵</button></div>
    </div></div>`;
}

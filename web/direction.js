// Direction: roadmap phases, research-line cards (gates, kill criteria, targets), idea radar, reading courses,
// and each research line's own long-running chat (LinePanel, shown in the side panel).
// i18n: done
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { t, lang } from "/i18n.js";
import { C, Ring, Progress } from "/charts.js";
import { daysTo, Skeleton, Empty, Collapse } from "/ui.js";
import { ChatPanel } from "/chat.js";
import { label as rmapT } from "/rmap/strings.js";

const KILL = { clear: "ok", partial: "warn", triggered: "bad" }; // state -> css class
// [label, class] of a Kill criteria state; an unknown state reads as clear
const kill = (k) => { const s = KILL[k.state] ? k.state : "clear"; return [t("direction.kill." + s), KILL[s]]; }; // i18n-keys: direction.kill.{clear,partial,triggered}
const LESSON_PREFIX = /^第\s*\d+\s*课\s*-\s*/; // i18n-ignore: matches Chinese lesson note names
const CONF = { high: 4, "medium-high": 3, medium: 2, "medium-low": 1, low: 1 };
const statusText = (s) => (s === "active" || s === "paused" ? t("direction.line.status." + s) : s); // i18n-keys: direction.line.status.{active,paused}
const short = (name) => name.replace(/^Research Line - /, "").replace(/^Idea ([A-Z]) - /, "Idea $1 · ");

// Asks in a line's own chat; the session already knows which line and where its notes are.
// They are prompts for the assistant, not interface text: it answers in the answer language.
const LINE_ASKS = {
  kickoff: "先读研究线笔记的最新版本，再用 timeline 看里程碑和投稿截稿，然后告诉我：现在处于什么状态、最大的风险是什么（对照 Kill criteria）、接下来两周最该做的 3 件事。",  // i18n-ignore
  kills: "逐条对照研究线笔记里的 Kill criteria：每条现在是未触发、部分触发还是已触发？依据是什么（链接到笔记或证据）？哪条最需要尽快验证，怎么验证？",  // i18n-ignore
  plan: "根据研究线笔记、timeline 里的里程碑和投稿截稿，排接下来两周的计划：按周列可检查的交付物。需要新加的里程碑先列给我确认，再用 milestone_add。",  // i18n-ignore
};
// Calendar days since a YYYY-MM-DD date, in local time.
export const ago = (day) => {
  const [y, m, d] = day.split("-").map(Number), now = new Date();
  const n = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - new Date(y, m - 1, d)) / 86400000);
  return n <= 0 ? t("common.today") : n === 1 ? t("common.yesterday") : t("common.days_ago", { n });
};

// Days-left ring; an overdue date shows a full ring in the alert color. Without onClick it is not a button.
function MiniRing({ d, color, label, sub, onClick }) {
  const late = d != null && d < 0;
  const body = html`<${Ring} value=${d == null ? 0 : late ? 1 : Math.min(d, 180)} max=${late ? 1 : 180} size=${52} stroke=${5} color=${late ? "var(--red)" : color}>
      ${late ? html`<b class="hot late">${t("direction.ring.overdue")}</b><small>${t("common.n_days", { n: -d })}</small>` : html`<b class=${d != null && d <= 14 ? "hot" : ""}>${d == null ? "—" : d}</b><small>${d == null ? "" : t("direction.ring.days")}</small>`}</${Ring}>
    <span><b>${label}</b><span class="muted small">${sub}</span></span>`;
  const title = d == null ? t("direction.ring.tba") : late ? t("direction.ring.late", { n: -d }) : t("direction.ring.left", { n: d });
  return onClick ? html`<button class="mring" onClick=${onClick} title=${title}>${body}</button>` : html`<div class="mring static" title=${title}>${body}</div>`;
}

function LineCard({ l, onOpenNote, onLine, onOpenVenue, onMap, i, active }) {
  const [more, setMore] = useState(false);
  const ms = l.milestones.filter((m) => m.status !== "done").slice(0, 2);
  const hit = l.kills.filter((k) => k.state !== "clear").length;
  const turns = l.chat?.turns || 0;
  return html`<section class=${"card line" + (l.status === "paused" ? " paused" : "") + (active ? " talking" : "")} style=${{ "--i": i }}>
    <div class="line-head">
      <div class="lh-main"><h2><button class="lh-title" title=${t("direction.line.open_chat")} onClick=${() => onLine(l, "open")}>${short(l.name)}</button></h2>
        <div class="row-wrap tight">
          ${active && html`<span class="tag talk"><${Icon} name="chat" sm />${t("direction.line.talking")}</span>`}
          ${l.line && html`<span class="tag">${l.line}</span>`}
          ${l.status && html`<span class=${"tag st-" + l.status}>${statusText(l.status)}</span>`}
          ${l.priority != null && html`<span class="tag">P${l.priority}</span>`}
          ${l.horizon && html`<span class="muted small">${l.horizon}</span>`}
        </div></div>
      <div class="line-counts"><div><b>${l.papers}</b><span>${t(l.papers === 1 ? "direction.line.paper" : "direction.line.papers")}</span></div><div><b>${l.ideas}</b><span>${t(l.ideas === 1 ? "direction.line.idea" : "direction.line.ideas")}</span></div></div>
    </div>
    ${(l.targets.length > 0 || ms.length > 0) && html`<div class="line-rings">
      ${l.targets.map((tg) => { const ev = l.deadlines.find((e) => (e.targets || []).some((x) => x.id === tg.id)), sent = tg.status === "submitted" || tg.status === "accepted";
        return html`<${MiniRing} d=${ev && !sent ? daysTo(ev.at) : null} color=${C.orange} label=${tg.venue.toUpperCase() + (tg.edition ? " " + tg.edition : "") + ((tg.alternatives || []).length ? ` +${tg.alternatives.length}` : "")}
          sub=${sent ? (tg.status === "accepted" ? t("direction.target.accepted") : t("direction.target.submitted")) : ev ? ev.title.replace(/^\S+\s\d{4}\s/, "") : tg.state === "past" ? t("direction.target.past") : t("direction.target.tba")} onClick=${() => onOpenVenue(tg.venue)} />`; })}
      ${ms.map((m) => html`<${MiniRing} d=${daysTo(m.at)} color=${C.aqua} label=${m.title} sub=${(m.kind === "gate" ? "Gate · " : "") + m.at.slice(5, 10)} />`)}
    </div>`}
    ${l.kills.length > 0 && html`<div class="klights">
      <span class="lp-k">Kill</span>
      ${l.kills.map((k, n) => { const [label, cls] = kill(k);
        return html`<span class=${"klight " + cls} title=${t("direction.kill.title", { label, text: k.text })}><i />${k.id || "K" + (n + 1)}</span>`; })}
      <span class="muted small">${hit ? t("direction.kill.hit", { n: hit }) : t("direction.kill.none")}</span>
    </div>`}
    ${l.pitch && html`<p class=${"pitch" + (more ? "" : " clamp2")}>${l.pitch}</p>`}
    <${Collapse} open=${more}>
      ${l.latest_status && html`<div class="status-note">${l.latest_status}</div>`}
      ${l.kills.length > 0 && html`<div class="kills">${l.kills.map((k) => { const [label, cls] = kill(k);
        return html`<div class=${"kill " + cls}><span class="kdot" /><span class="klabel">${label}</span><span class="ktext">${k.text}</span></div>`; })}</div>`}
      ${l.first_experiment && html`<div class="lp"><span class="lp-k">${t("direction.line.first_experiment")}</span><span class="muted small">${l.first_experiment}</span></div>`}
    </${Collapse}>
    <div class="row-wrap line-actions">
      ${(l.pitch || l.latest_status || l.kills.length > 0) && html`<button class="chip" onClick=${() => setMore(!more)}><${Icon} name=${more ? "up" : "down"} sm />${more ? t("common.hide") : t("direction.line.details")}</button>`}
      ${l.path && html`<button class="chip" onClick=${() => onOpenNote(l.path)}><${Icon} name="note" sm />${t("common.term.note")}</button>`}
      ${onMap && (l.map || l.path) && html`<button class="chip" onClick=${() => onMap(l)}
          title=${l.map ? rmapT("rmap.line.nodes", { n: l.map.nodes, v: l.map.verified }) : rmapT("rmap.seed.title")}>
        <${Icon} name="topology" sm />${l.map ? html`${rmapT("rmap.line.open")}<b class="chip-n">${l.map.nodes}</b>` : rmapT("rmap.line.seed")}</button>`}
      <button class="chip" onClick=${() => onLine(l, "discuss")} title=${t("direction.line.chat_title")}><${Icon} name="sparkle" sm />${turns ? html`${t("direction.line.continue")}<b class="chip-n">${turns}</b>` : t("direction.line.discuss")}</button>
      <button class="chip" onClick=${() => onLine(l, "packet")} title=${t("direction.line.packet_title")}><${Icon} name="file" sm />${t("direction.line.packet")}</button>
      ${l.packet && html`<button class="chip quiet" onClick=${() => onOpenNote(l.packet.path)} title=${l.packet.path}>${t("direction.line.last_packet", { when: ago(l.packet.date) })}</button>`}
    </div>
  </section>`;
}

function Roadmap({ r, onOpenNote }) {
  const [open, setOpen] = useState(null);
  const n = r.phases.length;
  const ymd = /^(\d{4})-(\d{2})-(\d{2})/.exec(r.start || "");
  const start = ymd ? new Date(+ymd[1], +ymd[2] - 1, +ymd[3]) : null;
  const pos = start && !isNaN(start) ? Math.max(0, Math.min(1, (Date.now() - start) / (n * 365.25 * 86400000))) : null;
  const yearIdx = pos == null ? -1 : Math.min(n - 1, Math.floor(pos * n));
  const week = pos != null ? Math.floor(((Date.now() - start) / 86400000 - yearIdx * 365.25) / 7) + 1 : null;
  return html`<section class="card roadmap">
    <div class="card-head"><h2>${t("direction.roadmap.title")}</h2>
      ${pos != null && html`<span class="muted small">${t("direction.roadmap.progress", { year: yearIdx + 1, week, pct: Math.round(pos * 100) })}</span>`}
      ${r.path && html`<button class="tb-btn" onClick=${() => onOpenNote(r.path)}><${Icon} name="note" sm />${t("direction.roadmap.open")}</button>`}</div>
    ${r.positioning && html`<p class="rm-pos">${r.positioning}</p>`}
    <div class="rm-track">
      ${r.phases.map((p, k) => html`<div class=${"rm-seg" + (k < yearIdx ? " past" : k === yearIdx ? " now" : "")}>
        <i style=${{ width: (pos == null ? 0 : Math.max(0, Math.min(1, pos * n - k)) * 100) + "%" }} /></div>`)}
      ${pos != null && html`<div class="rm-here" style=${{ left: pos * 100 + "%" }}><span>${t("direction.roadmap.here")}</span></div>`}
    </div>
    <div class="phases">${r.phases.map((p, k) => html`<button class=${"phase" + (k === yearIdx ? " now" : k < yearIdx ? " past" : "")} onClick=${() => setOpen(open === k ? null : k)}>
      <div class="ph-label"><span class="ph-n">${k + 1}</span>${p.label}${k === yearIdx && html`<span class="tag st-active">${t("direction.roadmap.current")}</span>`}</div>
      <b>${p.title}</b>
      ${p.goal && html`<div class="muted small">${p.goal}</div>`}
      ${p.deliverables.length > 0 && html`<span class="ph-more">${t("direction.roadmap.deliverables", { n: p.deliverables.length })}${open === k ? "" : " ›"}</span>
        <${Collapse} open=${open === k}><ul class="rules">${p.deliverables.map((d) => html`<li>${d}</li>`)}</ul></${Collapse}>`}
    </button>`)}</div>
  </section>`;
}

export function DirectionPage({ version, onOpenNote, onLine, activeLine, onOpenVenue, onMap }) {
  const [d, setD] = useState(null);
  useEffect(() => { api("/api/direction").then(setD); }, [version, lang()]); // plan events are worded by the server
  if (!d) return html`<${Skeleton} tiles=${0} rows=${3} />`;
  const lines = d.lines.filter((l) => l.kind === "research-line");
  const ideas = d.lines.filter((l) => l.kind === "idea");
  const rank = Object.fromEntries(d.radar.map((r) => [r.name, r]));
  const lessons = d.courses.reduce((a, c) => [a[0] + c.done, a[1] + c.total], [0, 0]);
  return html`<div class="page"><div class="page-inner wide">
    <div class="page-head"><div><h1>${t("common.nav.direction")}</h1>
      <div class="sub">${t("direction.sub.lines", { n: lines.length })} · ${t("direction.sub.ideas", { n: ideas.length })} · ${t("direction.sub.course", { done: lessons[0], total: lessons[1] })}</div></div></div>
    ${d.roadmap.phases.length > 0 && html`<${Roadmap} r=${d.roadmap} onOpenNote=${onOpenNote} />`}
    <h3 class="sec">${t("direction.lines.heading")}</h3>
    ${lines.length === 0 && html`<div class="card"><${Empty} icon="compass" title=${t("direction.lines.empty")} hint=${t("direction.lines.empty_hint")} /></div>`}
    <div class="lines stagger">${lines.map((l, i) => html`<${LineCard} l=${l} i=${i} onOpenNote=${onOpenNote} onLine=${onLine} onOpenVenue=${onOpenVenue} onMap=${onMap}
      active=${activeLine === l.chat_key} />`)}</div>
    <div class="two-col">
      <section class="card">
        <div class="card-head"><h2>${t("direction.ideas.title")}</h2><span class="muted small">${t("direction.ideas.sub")}</span></div>
        ${ideas.length === 0 && html`<${Empty} compact icon="bulb" title=${t("direction.ideas.empty")} hint=${t("direction.ideas.empty_hint")} />`}
        ${[...ideas].sort((a, b) => (rank[a.name]?.rank ?? 99) - (rank[b.name]?.rank ?? 99)).map((l) => { const r = rank[l.name], c = CONF[(r?.confidence || "").toLowerCase()] || 0;
          return html`<button class="ev irow" onClick=${() => onOpenNote(l.path)} title=${r?.why || ""}>
            <span class="rank-n">${r?.rank ?? "–"}</span><span class="ev-t">${short(l.name)}</span>
            ${r && html`<span class="conf" title=${t("direction.ideas.confidence", { level: r.confidence })}>${[1, 2, 3, 4].map((k) => html`<i class=${k <= c ? "on" : ""} style=${{ height: 4 + k * 3 + "px" }} />`)}</span>
              <span class="conf-l">${r.confidence}</span>`}</button>`; })}
      </section>
      <section class="card">
        <div class="card-head"><h2>${t("direction.course.title")}</h2><span class="muted small">${t("direction.course.progress", { done: lessons[0], total: lessons[1] })}</span></div>
        ${d.courses.length === 0 && html`<${Empty} compact icon="book" title=${t("direction.course.empty")} hint=${t("direction.course.empty_hint")} />`}
        ${d.courses.map((c) => html`<div class="course">
          <button class="course-h" onClick=${() => onOpenNote(c.path)}><b>${c.title}</b>
            <span class="course-n"><b>${c.done}</b>/${c.total}${c.done >= c.total && c.total > 0 ? html` <${Icon} name="check" sm />` : ""}</span></button>
          <${Progress} value=${c.done} max=${c.total} color=${c.done >= c.total ? "var(--good)" : C.blue} label=${t("direction.course.progress", { done: c.done, total: c.total })} />
          <div class="lessons">${c.lessons.map((x, k) => html`<button class=${"lesson" + (k < c.done ? " done" : k === c.done ? " next" : "")} onClick=${() => onOpenNote(x.path)} title=${x.name}>
            <i>${k < c.done ? html`<${Icon} name="check" sm />` : k + 1}</i><span>${x.name.replace(/^\d+\s*/, "").replace(LESSON_PREFIX, "")}</span></button>`)}</div>
        </div>`)}
      </section>
    </div>
  </div></div>`;
}

// What a line's chat is pointed at; Claude reads these with tools when it needs them.
function LineContext({ ctx, onOpenNote }) {
  const items = [
    [t("direction.ctx.note"), ctx.note, ctx.note], [t("direction.ctx.roadmap"), ctx.roadmap, ctx.roadmap], ["Idea Radar", ctx.idea_radar, ctx.idea_radar],
    [t("direction.ctx.plan"), ctx.plan, null], [t("direction.ctx.evidence", { n: ctx.evidence_notes }), ctx.evidence, null],
    [t("direction.ctx.packets", { n: ctx.packet_notes }), ctx.packets, null],
  ];
  const prev = ctx.previous_packet;
  return html`<div class="line-ctx">
    ${items.map(([label, have, path]) => html`<button class=${"lc" + (have ? "" : " off")} disabled=${!path}
      title=${have ? `${have}${path ? " · " + t("direction.ctx.open") : ""}` : t("direction.ctx.missing")} onClick=${() => onOpenNote(path)}><i />${label}</button>`)}
    <span class="lc-more">${prev
      ? html`<button class="lc" onClick=${() => onOpenNote(prev.path)} title=${prev.path}>${t("direction.line.last_packet", { when: ago(prev.date) })}</button>`
      : html`<span class="muted">${t("direction.ctx.no_packet")}</span>`}
      <span class="muted" title=${ctx.template.path || t("direction.ctx.template_builtin")}>${ctx.template.source === "vault" ? t("direction.ctx.template_vault") : t("direction.ctx.template_default")}</span></span>
  </div>`;
}

// Which line panels stay mounted after opening `key`: at most `max`, unloading the least recently opened
// first, and never one that is answering or waiting on an approval (unloading closes its socket, and the
// server then denies the pending edit).
export function keepLines(lines, key, used, busy, max = 4) {
  const idle = lines.filter((x) => x.key !== key && !busy(x.key)).sort((a, b) => (used[a.key] || 0) - (used[b.key] || 0));
  const drop = new Set(idle.slice(0, Math.max(0, lines.length - max)).map((x) => x.key));
  return lines.filter((x) => !drop.has(x.key));
}

// One research line's own conversation (resumed across days and restarts) in the side panel.
// registerSend gets {send, focus, kickoff, packet} so the Direction page can hand it work.
export function LinePanel({ line, claudeOk, registerSend, onBack, onStudioChanged, onOpenNote, toast }) {
  const [att, setAtt] = useState({ selection: null, images: [] });
  const [ctx, setCtx] = useState(null);
  const name = short(line.name);
  const enc = encodeURIComponent(line.key);
  const loadCtx = () => api(`/api/lines/${enc}`).then((r) => setCtx(r.context)).catch(() => setCtx(null));
  useEffect(() => { setCtx(null); loadCtx(); }, [line.key]);
  const opts = (label) => ({ selection: null, images: [], label });
  const drafting = useRef(false); // the request is fetched first: a second click must not send twice
  const draft = async (send) => {
    if (drafting.current) return;
    drafting.current = true;
    try {
      const p = await api(`/api/lines/${enc}/packet`);
      send(p.prompt, { ...opts(t("direction.chat.packet_label", { file: p.path.split("/").pop() })), write: true });
    } catch (e) { toast?.(e.message); } finally { drafting.current = false; }
  };
  const chips = [
    { label: t("direction.chat.chip.kickoff"), prompt: LINE_ASKS.kickoff }, { label: t("direction.chat.chip.kills"), prompt: LINE_ASKS.kills },
    { label: t("direction.chat.chip.plan"), prompt: LINE_ASKS.plan }, { label: t("direction.chat.chip.packet"), run: draft, write: true },
    { label: t("direction.chat.chip.idea"), prompt: t("direction.chat.idea_prefill"), fill: true, write: true },
  ];
  const register = (a) => registerSend?.({ ...a, kickoff: () => a.send(LINE_ASKS.kickoff, opts(t("direction.chat.chip.kickoff"))), packet: () => draft(a.send) });
  return html`<${ChatPanel} stem=${line.key} paper=${null} attachments=${att} setAttachments=${setAtt} registerSend=${register}
    claudeOk=${claudeOk} title=${t("direction.chat.title", { name })} chips=${chips} onBack=${onBack} backLabel=${t("common.nav.assistant")}
    banner=${ctx && html`<${LineContext} ctx=${ctx} onOpenNote=${onOpenNote} />`}
    emptyHint=${t("direction.chat.empty_hint", { name })}
    onStudioChanged=${onStudioChanged} onNoteChanged=${loadCtx} />`;
}

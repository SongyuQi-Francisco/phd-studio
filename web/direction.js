// Direction: roadmap phases, research-line cards (gates, kill criteria, targets), idea radar, reading courses.
import { useEffect, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";

const KILL = { clear: ["未触发", "ok"], partial: ["部分触发", "warn"], triggered: ["已触发", "bad"] };
const daysTo = (iso) => Math.ceil((new Date(iso) - new Date()) / 86400000);
const short = (name) => name.replace(/^Research Line - /, "").replace(/^Idea ([A-Z]) - /, "Idea $1 · ");

function Days({ d }) {
  const cls = d < 0 ? "past" : d <= 14 ? "hot" : d <= 45 ? "warm" : "";
  return html`<span class=${"days " + cls}>${d < 0 ? `${-d} 天前` : `${d} 天`}</span>`;
}

export const discussPrompt = (l) => l.path
  ? `我们来讨论研究线「${short(l.name)}」（笔记：${l.path}）。先读这篇笔记，以及 Roadmap、Idea Radar 和相关的规划（timeline），然后告诉我：现在处于什么状态、最大的风险是什么（对照 Kill criteria）、接下来两周最该做的 3 件事。`
  : `我们来讨论研究线「${l.name}」。它没有单独的研究线笔记；先看它的投稿目标、里程碑和阅读清单里标了这条线的论文（timeline、reading_list），再找 vault 里相关的笔记，然后告诉我：现在的状态、最大的风险、接下来两周最该做的 3 件事。`;

function LineCard({ l, onOpenNote, onAsk, onOpenVenue }) {
  const ms = l.milestones.filter((m) => m.status !== "done").slice(0, 3);
  return html`<section class=${"card line" + (l.status === "paused" ? " paused" : "")}>
    <div class="line-head">
      <div><h2>${short(l.name)}</h2>
        <div class="row-wrap">
          ${l.line && html`<span class="tag">${l.line}</span>`}
          ${l.status && html`<span class=${"tag st-" + l.status}>${l.status}</span>`}
          ${l.priority != null && html`<span class="tag">P${l.priority}</span>`}
          ${l.horizon && html`<span class="muted small">${l.horizon}</span>`}
        </div></div>
      <div class="line-counts"><div><b>${l.papers}</b><span>篇论文</span></div><div><b>${l.ideas}</b><span>个想法</span></div></div>
    </div>
    ${l.pitch && html`<p class="pitch">${l.pitch}</p>`}
    ${l.latest_status && html`<div class="status-note">${l.latest_status}</div>`}
    ${(l.targets.length > 0 || ms.length > 0) && html`<div class="line-plan">
      ${l.targets.map((t) => { const ev = l.deadlines.find((e) => e.target?.id === t.id);
        return html`<div class="lp"><span class="lp-k">目标</span><button class="vlink" onClick=${() => onOpenVenue(t.venue)}>${t.venue.toUpperCase()}</button>
          ${(t.alternatives || []).map((a) => html`<button class="vlink alt" onClick=${() => onOpenVenue(a)}>${a.toUpperCase()}</button>`)}
          <span class="muted small">${t.title}</span>${ev && html`<${Days} d=${daysTo(ev.at)} />`}</div>`; })}
      ${ms.map((m) => html`<div class="lp"><span class="lp-k">${m.kind === "gate" ? "Gate" : "里程碑"}</span><span>${m.title}</span>
        <span class="muted small">${m.at.slice(0, 10)}</span><${Days} d=${daysTo(m.at)} /></div>`)}
    </div>`}
    ${l.kills.length > 0 && html`<div class="kills">
      <div class="lp-k">Kill criteria</div>
      ${l.kills.map((k) => { const [label, cls] = KILL[k.state] || KILL.clear;
        return html`<div class=${"kill " + cls}><span class="kdot" /><span class="klabel">${label}</span><span class="ktext">${k.text}</span></div>`; })}
    </div>`}
    <div class="row-wrap line-actions">
      ${l.path && html`<button class="chip" onClick=${() => onOpenNote(l.path)}><${Icon} name="note" sm />笔记</button>`}
      <button class="chip" onClick=${() => onAsk(discussPrompt(l), `讨论 ${short(l.name)}`)}><${Icon} name="sparkle" sm />和助手讨论</button>
    </div>
  </section>`;
}

export function DirectionPage({ version, onOpenNote, onAsk, onOpenVenue }) {
  const [d, setD] = useState(null);
  useEffect(() => { api("/api/direction").then(setD); }, [version]);
  if (!d) return html`<div class="page"><div class="loading">载入中…</div></div>`;
  const lines = d.lines.filter((l) => l.kind === "research-line");
  const ideas = d.lines.filter((l) => l.kind === "idea");
  const rank = Object.fromEntries(d.radar.map((r) => [r.name, r]));
  return html`<div class="page"><div class="page-inner wide">
    <div class="page-head"><h1>方向</h1>${d.roadmap.path && html`<button class="chip" onClick=${() => onOpenNote(d.roadmap.path)}><${Icon} name="note" sm />PhD Roadmap</button>`}</div>
    ${d.roadmap.phases.length > 0 && html`<section class="roadmap">
      ${d.roadmap.positioning && html`<p class="muted">${d.roadmap.positioning}</p>`}
      <div class="phases">${d.roadmap.phases.map((p, i) => html`<div class="phase">
        <div class="ph-label"><span class="ph-n">${i + 1}</span>${p.label}</div><b>${p.title}</b>
        ${p.goal && html`<div class="muted small">${p.goal}</div>`}</div>`)}</div>
    </section>`}
    <h3 class="sec">研究线</h3>
    <div class="lines">${lines.map((l) => html`<${LineCard} l=${l} onOpenNote=${onOpenNote} onAsk=${onAsk} onOpenVenue=${onOpenVenue} />`)}</div>
    <div class="two-col">
      <section class="card">
        <div class="card-head"><h2>Idea 池</h2><span class="muted small">按 Idea Radar 排序</span></div>
        ${[...ideas].sort((a, b) => (rank[a.name]?.rank ?? 99) - (rank[b.name]?.rank ?? 99)).map((l) => html`<button class="ev" onClick=${() => onOpenNote(l.path)}>
          <span class="rank-n">${rank[l.name]?.rank ?? "–"}</span><span class="ev-t">${short(l.name)}</span>
          ${rank[l.name] && html`<span class="tag">${rank[l.name].confidence}</span>`}</button>`)}
      </section>
      <section class="card">
        <div class="card-head"><h2>精读课程</h2></div>
        ${d.courses.map((c) => html`<div class="course">
          <button class="ev" onClick=${() => onOpenNote(c.path)}><span class="ev-t"><b>${c.title}</b></span><span class="muted small">${c.done}/${c.total}</span></button>
          <div class="bar"><i style=${{ width: (c.total ? (c.done / c.total) * 100 : 0) + "%" }} /></div>
          <div class="lessons">${c.lessons.map((x) => html`<button class="linkbtn" onClick=${() => onOpenNote(x.path)}>${x.name.replace(/^\d+\s*/, "")}</button>`)}</div>
        </div>`)}
      </section>
    </div>
  </div></div>`;
}

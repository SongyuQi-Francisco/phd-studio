// Direction: roadmap phases, research-line cards (gates, kill criteria, targets), idea radar, reading courses.
import { useEffect, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { C, Ring, Progress } from "/charts.js";
import { daysTo, Skeleton, Empty, Collapse } from "/ui.js";

const KILL = { clear: ["未触发", "ok"], partial: ["部分触发", "warn"], triggered: ["已触发", "bad"] };
const CONF = { high: 4, "medium-high": 3, medium: 2, "medium-low": 1, low: 1 };
const short = (name) => name.replace(/^Research Line - /, "").replace(/^Idea ([A-Z]) - /, "Idea $1 · ");

export const discussPrompt = (l) => l.path
  ? `我们来讨论研究线「${short(l.name)}」（笔记：${l.path}）。先读这篇笔记，以及 Roadmap、Idea Radar 和相关的规划（timeline），然后告诉我：现在处于什么状态、最大的风险是什么（对照 Kill criteria）、接下来两周最该做的 3 件事。`
  : `我们来讨论研究线「${l.name}」。它没有单独的研究线笔记；先看它的投稿目标、里程碑和阅读清单里标了这条线的论文（timeline、reading_list），再找 vault 里相关的笔记，然后告诉我：现在的状态、最大的风险、接下来两周最该做的 3 件事。`;

function MiniRing({ d, color, label, sub, onClick }) {
  return html`<button class="mring" onClick=${onClick} title=${d == null ? "日期未公布" : `还剩 ${d} 天`}>
    <${Ring} value=${d == null ? 0 : Math.max(0, Math.min(d, 180))} max=${180} size=${52} stroke=${5} color=${color}>
      <b class=${d != null && d <= 14 ? "hot" : ""}>${d == null ? "—" : d}</b><small>${d == null ? "" : "天"}</small></${Ring}>
    <span><b>${label}</b><span class="muted small">${sub}</span></span>
  </button>`;
}

function LineCard({ l, onOpenNote, onAsk, onOpenVenue, i }) {
  const [more, setMore] = useState(false);
  const ms = l.milestones.filter((m) => m.status !== "done").slice(0, 2);
  const hit = l.kills.filter((k) => k.state !== "clear").length;
  return html`<section class=${"card line" + (l.status === "paused" ? " paused" : "")} style=${{ "--i": i }}>
    <div class="line-head">
      <div class="lh-main"><h2>${short(l.name)}</h2>
        <div class="row-wrap tight">
          ${l.line && html`<span class="tag">${l.line}</span>`}
          ${l.status && html`<span class=${"tag st-" + l.status}>${l.status === "active" ? "进行中" : l.status === "paused" ? "暂停" : l.status}</span>`}
          ${l.priority != null && html`<span class="tag">P${l.priority}</span>`}
          ${l.horizon && html`<span class="muted small">${l.horizon}</span>`}
        </div></div>
      <div class="line-counts"><div><b>${l.papers}</b><span>篇论文</span></div><div><b>${l.ideas}</b><span>个想法</span></div></div>
    </div>
    ${(l.targets.length > 0 || ms.length > 0) && html`<div class="line-rings">
      ${l.targets.map((t) => { const ev = l.deadlines.find((e) => e.target?.id === t.id);
        return html`<${MiniRing} d=${ev ? daysTo(ev.at) : null} color=${C.orange} label=${t.venue.toUpperCase() + ((t.alternatives || []).length ? ` +${t.alternatives.length}` : "")}
          sub=${ev ? ev.title.replace(/^\S+\s\d{4}\s/, "") : "截稿未公布"} onClick=${() => onOpenVenue(t.venue)} />`; })}
      ${ms.map((m) => html`<${MiniRing} d=${daysTo(m.at)} color=${C.aqua} label=${m.title} sub=${(m.kind === "gate" ? "Gate · " : "") + m.at.slice(5, 10)} />`)}
    </div>`}
    ${l.kills.length > 0 && html`<div class="klights">
      <span class="lp-k">Kill</span>
      ${l.kills.map((k, n) => { const [label, cls] = KILL[k.state] || KILL.clear;
        return html`<span class=${"klight " + cls} title=${`${label}：${k.text}`}><i />${k.id || "K" + (n + 1)}</span>`; })}
      <span class="muted small">${hit ? `${hit} 条触发` : "全部未触发"}</span>
    </div>`}
    ${l.pitch && html`<p class=${"pitch" + (more ? "" : " clamp2")}>${l.pitch}</p>`}
    <${Collapse} open=${more}>
      ${l.latest_status && html`<div class="status-note">${l.latest_status}</div>`}
      ${l.kills.length > 0 && html`<div class="kills">${l.kills.map((k) => { const [label, cls] = KILL[k.state] || KILL.clear;
        return html`<div class=${"kill " + cls}><span class="kdot" /><span class="klabel">${label}</span><span class="ktext">${k.text}</span></div>`; })}</div>`}
      ${l.first_experiment && html`<div class="lp"><span class="lp-k">第一个实验</span><span class="muted small">${l.first_experiment}</span></div>`}
    </${Collapse}>
    <div class="row-wrap line-actions">
      ${(l.pitch || l.latest_status || l.kills.length > 0) && html`<button class="chip" onClick=${() => setMore(!more)}><${Icon} name=${more ? "up" : "down"} sm />${more ? "收起" : "详情"}</button>`}
      ${l.path && html`<button class="chip" onClick=${() => onOpenNote(l.path)}><${Icon} name="note" sm />笔记</button>`}
      <button class="chip" onClick=${() => onAsk(discussPrompt(l), `讨论 ${short(l.name)}`)}><${Icon} name="sparkle" sm />和助手讨论</button>
    </div>
  </section>`;
}

function Roadmap({ r, onOpenNote }) {
  const [open, setOpen] = useState(null);
  const n = r.phases.length;
  const start = r.start ? new Date(r.start + "T00:00:00") : null;
  const pos = start ? Math.max(0, Math.min(1, (Date.now() - start) / (n * 365.25 * 86400000))) : null;
  const yearIdx = pos == null ? -1 : Math.min(n - 1, Math.floor(pos * n));
  const week = start ? Math.floor(((Date.now() - start) / 86400000 - yearIdx * 365.25) / 7) + 1 : null;
  return html`<section class="card roadmap">
    <div class="card-head"><h2>PhD 路线图</h2>
      ${pos != null && html`<span class="muted small">第 ${yearIdx + 1} 年 · 第 ${week} 周 · 全程 ${Math.round(pos * 100)}%</span>`}
      ${r.path && html`<button class="tb-btn" onClick=${() => onOpenNote(r.path)}><${Icon} name="note" sm />Roadmap</button>`}</div>
    ${r.positioning && html`<p class="rm-pos">${r.positioning}</p>`}
    <div class="rm-track">
      ${r.phases.map((p, k) => html`<div class=${"rm-seg" + (k < yearIdx ? " past" : k === yearIdx ? " now" : "")}>
        <i style=${{ width: (pos == null ? 0 : Math.max(0, Math.min(1, pos * n - k)) * 100) + "%" }} /></div>`)}
      ${pos != null && html`<div class="rm-here" style=${{ left: pos * 100 + "%" }}><span>你在这里</span></div>`}
    </div>
    <div class="phases">${r.phases.map((p, k) => html`<button class=${"phase" + (k === yearIdx ? " now" : k < yearIdx ? " past" : "")} onClick=${() => setOpen(open === k ? null : k)}>
      <div class="ph-label"><span class="ph-n">${k + 1}</span>${p.label}${k === yearIdx && html`<span class="tag st-active">当前</span>`}</div>
      <b>${p.title}</b>
      ${p.goal && html`<div class="muted small">${p.goal}</div>`}
      ${p.deliverables.length > 0 && html`<span class="ph-more">${p.deliverables.length} 项交付${open === k ? "" : " ›"}</span>
        <${Collapse} open=${open === k}><ul class="rules">${p.deliverables.map((d) => html`<li>${d}</li>`)}</ul></${Collapse}>`}
    </button>`)}</div>
  </section>`;
}

export function DirectionPage({ version, onOpenNote, onAsk, onOpenVenue }) {
  const [d, setD] = useState(null);
  useEffect(() => { api("/api/direction").then(setD); }, [version]);
  if (!d) return html`<${Skeleton} tiles=${0} rows=${3} />`;
  const lines = d.lines.filter((l) => l.kind === "research-line");
  const ideas = d.lines.filter((l) => l.kind === "idea");
  const rank = Object.fromEntries(d.radar.map((r) => [r.name, r]));
  const lessons = d.courses.reduce((a, c) => [a[0] + c.done, a[1] + c.total], [0, 0]);
  return html`<div class="page"><div class="page-inner wide">
    <div class="page-head"><div><h1>方向</h1>
      <div class="sub">${lines.length} 条研究线 · ${ideas.length} 个 idea · 精读课程 ${lessons[0]}/${lessons[1]} 课</div></div></div>
    ${d.roadmap.phases.length > 0 && html`<${Roadmap} r=${d.roadmap} onOpenNote=${onOpenNote} />`}
    <h3 class="sec">研究线</h3>
    ${lines.length === 0 && html`<div class="card"><${Empty} icon="compass" title="还没有研究线" hint="在 vault 的 Ideas 文件夹里建 type: research-line 的笔记，这里会出现它的阶段、目标倒计时和 Kill criteria。" /></div>`}
    <div class="lines stagger">${lines.map((l, i) => html`<${LineCard} l=${l} i=${i} onOpenNote=${onOpenNote} onAsk=${onAsk} onOpenVenue=${onOpenVenue} />`)}</div>
    <div class="two-col">
      <section class="card">
        <div class="card-head"><h2>Idea 池</h2><span class="muted small">按 Idea Radar 排序 · 信心</span></div>
        ${ideas.length === 0 && html`<${Empty} compact icon="bulb" title="Idea 池是空的" hint="在 Ideas 文件夹里建 type: idea 的笔记" />`}
        ${[...ideas].sort((a, b) => (rank[a.name]?.rank ?? 99) - (rank[b.name]?.rank ?? 99)).map((l) => { const r = rank[l.name], c = CONF[(r?.confidence || "").toLowerCase()] || 0;
          return html`<button class="ev irow" onClick=${() => onOpenNote(l.path)} title=${r?.why || ""}>
            <span class="rank-n">${r?.rank ?? "–"}</span><span class="ev-t">${short(l.name)}</span>
            ${r && html`<span class="conf" title=${"信心：" + r.confidence}>${[1, 2, 3, 4].map((k) => html`<i class=${k <= c ? "on" : ""} style=${{ height: 4 + k * 3 + "px" }} />`)}</span>
              <span class="conf-l">${r.confidence}</span>`}</button>`; })}
      </section>
      <section class="card">
        <div class="card-head"><h2>精读课程</h2><span class="muted small">${lessons[0]}/${lessons[1]} 课</span></div>
        ${d.courses.length === 0 && html`<${Empty} compact icon="book" title="还没有课程" hint="Ideas 下带 00 总览笔记的文件夹会显示成课程" />`}
        ${d.courses.map((c) => html`<div class="course">
          <button class="course-h" onClick=${() => onOpenNote(c.path)}><b>${c.title}</b>
            <span class="course-n"><b>${c.done}</b>/${c.total}${c.done >= c.total && c.total > 0 ? html` <${Icon} name="check" sm />` : ""}</span></button>
          <${Progress} value=${c.done} max=${c.total} color=${c.done >= c.total ? "var(--good)" : C.blue} label=${`${c.done}/${c.total} 课`} />
          <div class="lessons">${c.lessons.map((x, k) => html`<button class=${"lesson" + (k < c.done ? " done" : k === c.done ? " next" : "")} onClick=${() => onOpenNote(x.path)} title=${x.name}>
            <i>${k < c.done ? html`<${Icon} name="check" sm />` : k + 1}</i><span>${x.name.replace(/^\d+\s*/, "").replace(/^第\s*\d+\s*课\s*-\s*/, "")}</span></button>`)}</div>
        </div>`)}
      </section>
    </div>
  </div></div>`;
}

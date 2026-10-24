// Direction: roadmap phases, research-line cards (gates, kill criteria, targets), idea radar, reading courses,
// and each research line's own long-running chat (LinePanel, shown in the side panel).
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { C, Ring, Progress } from "/charts.js";
import { daysTo, Skeleton, Empty, Collapse } from "/ui.js";
import { ChatPanel } from "/chat.js";

const KILL = { clear: ["未触发", "ok"], partial: ["部分触发", "warn"], triggered: ["已触发", "bad"] };
const CONF = { high: 4, "medium-high": 3, medium: 2, "medium-low": 1, low: 1 };
const short = (name) => name.replace(/^Research Line - /, "").replace(/^Idea ([A-Z]) - /, "Idea $1 · ");

// Asks in a line's own chat; the session already knows which line and where its notes are.
const LINE_ASKS = {
  kickoff: "先读研究线笔记的最新版本，再用 timeline 看里程碑和投稿截稿，然后告诉我：现在处于什么状态、最大的风险是什么（对照 Kill criteria）、接下来两周最该做的 3 件事。",
  kills: "逐条对照研究线笔记里的 Kill criteria：每条现在是未触发、部分触发还是已触发？依据是什么（链接到笔记或证据）？哪条最需要尽快验证，怎么验证？",
  plan: "根据研究线笔记、timeline 里的里程碑和投稿截稿，排接下来两周的计划：按周列可检查的交付物。需要新加的里程碑先列给我确认，再用 milestone_add。",
};
// Calendar days since a YYYY-MM-DD date, in local time.
export const ago = (day) => {
  const [y, m, d] = day.split("-").map(Number), now = new Date();
  const n = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - new Date(y, m - 1, d)) / 86400000);
  return n <= 0 ? "今天" : n === 1 ? "昨天" : `${n} 天前`;
};

// Days-left ring; an overdue date shows a full ring in the alert color. Without onClick it is not a button.
function MiniRing({ d, color, label, sub, onClick }) {
  const late = d != null && d < 0;
  const body = html`<${Ring} value=${d == null ? 0 : late ? 1 : Math.min(d, 180)} max=${late ? 1 : 180} size=${52} stroke=${5} color=${late ? "var(--red)" : color}>
      ${late ? html`<b class="hot late">逾期</b><small>${-d} 天</small>` : html`<b class=${d != null && d <= 14 ? "hot" : ""}>${d == null ? "—" : d}</b><small>${d == null ? "" : "天"}</small>`}</${Ring}>
    <span><b>${label}</b><span class="muted small">${sub}</span></span>`;
  const title = d == null ? "日期未公布" : late ? `已逾期 ${-d} 天` : `还剩 ${d} 天`;
  return onClick ? html`<button class="mring" onClick=${onClick} title=${title}>${body}</button>` : html`<div class="mring static" title=${title}>${body}</div>`;
}

function LineCard({ l, onOpenNote, onLine, onOpenVenue, i, active }) {
  const [more, setMore] = useState(false);
  const ms = l.milestones.filter((m) => m.status !== "done").slice(0, 2);
  const hit = l.kills.filter((k) => k.state !== "clear").length;
  const turns = l.chat?.turns || 0;
  return html`<section class=${"card line" + (l.status === "paused" ? " paused" : "") + (active ? " talking" : "")} style=${{ "--i": i }}>
    <div class="line-head">
      <div class="lh-main"><h2><button class="lh-title" title="打开这条线的专属对话" onClick=${() => onLine(l, "open")}>${short(l.name)}</button></h2>
        <div class="row-wrap tight">
          ${active && html`<span class="tag talk"><${Icon} name="chat" sm />对话中</span>`}
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
      <button class="chip" onClick=${() => onLine(l, "discuss")} title="这条线的专属对话，跨天保留上下文"><${Icon} name="sparkle" sm />${turns ? html`继续讨论<b class="chip-n">${turns}</b>` : "和助手讨论"}</button>
      <button class="chip" onClick=${() => onLine(l, "packet")} title="按模板起草一份导师汇报包，写入 Review Packets 前先给你看差异"><${Icon} name="file" sm />起草导师汇报包</button>
      ${l.packet && html`<button class="chip quiet" onClick=${() => onOpenNote(l.packet.path)} title=${l.packet.path}>上次汇报 ${ago(l.packet.date)}</button>`}
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

export function DirectionPage({ version, onOpenNote, onLine, activeLine, onOpenVenue }) {
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
    <div class="lines stagger">${lines.map((l, i) => html`<${LineCard} l=${l} i=${i} onOpenNote=${onOpenNote} onLine=${onLine} onOpenVenue=${onOpenVenue}
      active=${activeLine === l.chat_key} />`)}</div>
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

// What a line's chat is pointed at; Claude reads these with tools when it needs them.
function LineContext({ ctx, onOpenNote }) {
  const items = [
    ["研究线笔记", ctx.note, ctx.note], ["路线图", ctx.roadmap, ctx.roadmap], ["Idea Radar", ctx.idea_radar, ctx.idea_radar],
    ["规划", ctx.plan, null], [`证据 ${ctx.evidence_notes}`, ctx.evidence, null], [`评审材料 ${ctx.packet_notes}`, ctx.packets, null],
  ];
  const prev = ctx.previous_packet;
  return html`<div class="line-ctx">
    ${items.map(([label, have, path]) => html`<button class=${"lc" + (have ? "" : " off")} disabled=${!path}
      title=${have ? `${have}${path ? " · 点击查看" : ""}` : "vault 里没找到"} onClick=${() => onOpenNote(path)}><i />${label}</button>`)}
    <span class="lc-more">${prev
      ? html`<button class="lc" onClick=${() => onOpenNote(prev.path)} title=${prev.path}>上次汇报 ${ago(prev.date)}</button>`
      : html`<span class="muted">还没有汇报包</span>`}
      <span class="muted" title=${ctx.template.path || "仓库里的通用模板"}>· 模板${ctx.template.source === "vault" ? "来自 vault" : "：默认"}</span></span>
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
// registerSend gets {send, focus, kickoff, packet} so the 方向 page can hand it work.
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
      send(p.prompt, opts(`起草导师汇报包 → ${p.path.split("/").pop()}`));
    } catch (e) { toast?.(e.message); } finally { drafting.current = false; }
  };
  const chips = [
    { label: "现状与下一步", prompt: LINE_ASKS.kickoff }, { label: "Kill criteria 体检", prompt: LINE_ASKS.kills },
    { label: "两周计划", prompt: LINE_ASKS.plan }, { label: "起草导师汇报包", run: draft },
    { label: "记想法", prompt: "给这条线记一个想法：", fill: true },
  ];
  const register = (a) => registerSend?.({ ...a, kickoff: () => a.send(LINE_ASKS.kickoff, opts("现状与下一步")), packet: () => draft(a.send) });
  return html`<${ChatPanel} stem=${line.key} paper=${null} attachments=${att} setAttachments=${setAtt} registerSend=${register}
    claudeOk=${claudeOk} title=${"研究线 · " + name} chips=${chips} onBack=${onBack} backLabel="工作台助手"
    banner=${ctx && html`<${LineContext} ctx=${ctx} onOpenNote=${onOpenNote} />`}
    emptyHint=${`「${name}」的专属对话：跨天、跨重启都记得之前聊过的。问现状、风险、下一步，或起草导师汇报包。`}
    onStudioChanged=${onStudioChanged} onNoteChanged=${loadCtx} />`;
}

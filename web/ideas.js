// Ideas: capture inbox -> keep / drop -> promote into a proper idea note (drafted by the assistant).
import { useEffect, useMemo, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { C, Funnel, Sparkline, perWeek } from "/charts.js";
import { Thumb, loadThumbs, Skeleton, Empty, transition } from "/ui.js";

const STATUS = { new: "新", kept: "保留", promoted: "已整理", dropped: "丢弃" };
const ST_COLOR = { new: "var(--accent)", kept: "var(--series-3)", promoted: "var(--good)", dropped: "var(--text-3)" };

export const promotePrompt = (i) => `把想法 ${i.id} 整理成正式 idea 笔记。原文：「${i.text}」${i.line ? `（研究线：${i.line}）` : ""}${i.paper ? `（来自论文 ${i.paper}${i.page ? " p." + i.page : ""}）` : ""}。
要求：用 Idea Note Template 的结构，按 AGENTS.md 的 Idea Maintenance 写清 问题 / 为什么是现在 / 技术核心 / 第一个实验 / Kill criteria / 下一步要找的证据；先查 vault 里已有的相关 idea 和论文笔记，避免重复，并链接相关论文。
先用三四句话告诉我笔记文件名和要点，再写入 idea 笔记文件夹；写完用 idea_update 把这条想法标为 promoted，note 填笔记路径。`;

function IdeaCard({ i, lines, update, onPromote, onOpenNote, onOpenPaper, ready, n }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(i.text);
  const save = () => { setEditing(false); if (text.trim() && text !== i.text) update(i, { text: text.trim() }); };
  return html`<div class=${"idea" + (i.status === "dropped" ? " dropped" : "")} style=${{ "--st": ST_COLOR[i.status], animationDelay: Math.min(n, 10) * 30 + "ms" }}>
    <div class="idea-top">
      <span class=${"tag st-" + i.status}>${STATUS[i.status] || i.status}</span>
      <span class="muted small">${(i.created_at || "").replace("T", " ").slice(5, 16)}${i.source === "assistant" ? " · 助手" : i.source === "claude" ? " · 其他会话" : ""}</span>
      <span class="grow" />
      <select value=${i.line || ""} onChange=${(e) => update(i, { line: e.target.value || null })}>
        <option value="">不分组</option>${lines.map((l) => html`<option value=${l}>${l}</option>`)}</select>
    </div>
    ${editing
      ? html`<textarea class="ta" rows="3" value=${text} onInput=${(e) => setText(e.target.value)} onBlur=${save}
          onKeyDown=${(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save(); if (e.key === "Escape") { setText(i.text); setEditing(false); } }} autofocus />`
      : html`<div class="idea-text" title="点击编辑" onClick=${() => setEditing(true)}>${i.text}</div>`}
    ${i.paper && html`<button class="idea-paper" onClick=${() => onOpenPaper(i.paper)}>
      <${Thumb} k=${i.paper} title=${i.paper} size="sm" ready=${ready} /><span><b>${i.paper}</b>${i.page ? html`<span class="muted small">第 ${i.page} 页</span>` : ""}</span></button>`}
    <div class="idea-meta">
      ${i.note && html`<button class="chip" onClick=${() => onOpenNote(i.note)}><${Icon} name="note" sm />${i.note.split("/").pop().replace(/\.md$/, "")}</button>`}
      <span class="grow" />
      ${i.status !== "promoted" && html`<button class="chip" onClick=${() => onPromote(i)}><${Icon} name="sparkle" sm />整理成笔记</button>`}
      ${i.status === "new" && html`<button class="abtn txt" onClick=${() => update(i, { status: "kept" })}>保留</button>`}
      ${i.status !== "dropped" && i.status !== "promoted" && html`<button class="abtn" title="丢弃（保留记录）" onClick=${() => update(i, { status: "dropped" })}><${Icon} name="x" sm /></button>`}
      ${i.status === "dropped" && html`<button class="abtn txt" onClick=${() => update(i, { status: "kept" })}>恢复</button>`}
    </div>
  </div>`;
}

export function IdeasPage({ version, lines, radarPath, onAsk, onOpenNote, onOpenPaper, toast }) {
  const [ideas, setIdeas] = useState(null);
  const [filter, setFilter] = useState("active");
  const [line, setLine] = useState("");
  const [draft, setDraft] = useState("");
  const [ready, setReady] = useState(0);
  const load = () => api("/api/ideas").then((r) => {
    setIdeas(r.ideas);
    loadThumbs(r.ideas.map((i) => i.paper).filter(Boolean)).then(() => setReady((n) => n + 1));
  });
  useEffect(() => { load(); }, [version]);
  const update = async (i, patch) => { await api(`/api/ideas/${i.id}`, { method: "PATCH", json: patch }); load(); };
  const add = async () => {
    if (!draft.trim()) return;
    await api("/api/ideas", { json: { text: draft, line: line || null } });
    setDraft(""); toast("想法已记下"); load();
  };
  const all = ideas || [];
  const counts = useMemo(() => Object.fromEntries(Object.keys(STATUS).map((k) => [k, all.filter((i) => i.status === k).length])), [ideas]);
  if (!ideas) return html`<${Skeleton} tiles=${0} rows=${4} chart=${false} />`;
  const shown = all.filter((i) => (filter === "active" ? ["new", "kept"].includes(i.status) : filter === "all" || i.status === filter)
    && (!line || i.line === line));
  const weekly = perWeek(all.map((i) => i.created_at));
  const byLine = lines.map((l) => [l, all.filter((i) => i.line === l && i.status !== "dropped").length]);
  const capture = html`<div class="capture-row">
    <input placeholder="一句话记下想法，回车保存（任何页面也可以按 ⌘I）" value=${draft} onInput=${(e) => setDraft(e.target.value)}
      onKeyDown=${(e) => e.key === "Enter" && !e.isComposing && add()} />
    <button class="btn primary" disabled=${!draft.trim()} onClick=${add}>记下</button>
  </div>`;
  return html`<div class="page"><div class="page-inner">
    <div class="page-head"><div><h1>想法</h1>
      <div class="sub">${all.length ? `共 ${all.length} 条 · 近 12 周记下 ${weekly.counts.reduce((a, b) => a + b, 0)} 条` : "先记下来，之后再整理"}</div></div>
      ${radarPath && html`<button class="chip" onClick=${() => onOpenNote(radarPath)}><${Icon} name="note" sm />Idea Radar</button>`}</div>
    ${capture}
    ${all.length === 0 ? html`<div class="card"><${Empty} icon="bulb" title="还没有想法"
        hint="读论文时按 ⌘I 随手记一句，会自动附上论文和页码；也可以对右侧助手说「记一个想法：…」。攒起来的想法会在这里经过 保留 → 整理成笔记 的漏斗。" />
        <div class="es-steps">${[["⌘I", "随手记"], ["保留", "值得再想"], ["整理", "助手按 Idea 模板起草笔记"]].map(([k, l], n) => html`<div><b>${k}</b><span>${l}</span>${n < 2 && html`<${Icon} name="right" sm />`}</div>`)}</div>
      </div>` : html`
      <section class="card idea-insight">
        <div><div class="ri-head"><b>漏斗</b><span class="muted small">丢弃 ${counts.dropped} 条（保留记录）</span></div>
          <${Funnel} steps=${[
            { key: "all", label: "记下", value: all.length, color: "var(--ord-1)" },
            { key: "active", label: "留下", value: counts.kept + counts.promoted, color: "var(--ord-2)" },
            { key: "promoted", label: "成笔记", value: counts.promoted, color: "var(--ord-3)" }]} /></div>
        <div class="ii-side">
          <div class="ri-head"><b>每周记下</b><span class="muted small">近 12 周</span></div>
          <${Sparkline} values=${weekly.counts} labels=${weekly.labels.map((l) => l + " 那周")} unit=" 条" color=${C.blue} height=${44} />
          ${byLine.length > 0 && html`<div class="ii-lines">${byLine.map(([l, n]) => html`<button class=${"chip" + (line === l ? " on" : "")} onClick=${() => setLine(line === l ? "" : l)}>${l}<b>${n}</b></button>`)}</div>`}
        </div>
      </section>
      <div class="filters">
        <div class="seg">
          ${[["active", `待处理 ${counts.new + counts.kept}`], ["promoted", `已整理 ${counts.promoted}`], ["dropped", `丢弃 ${counts.dropped}`], ["all", "全部"]].map(([k, l]) => html`<button class=${filter === k ? "on" : ""} onClick=${() => transition(() => setFilter(k))}>${l}</button>`)}
        </div>
        ${lines.length > 0 && html`<div class="seg"><button class=${!line ? "on" : ""} onClick=${() => setLine("")}>全部研究线</button>
          ${lines.map((l) => html`<button class=${line === l ? "on" : ""} onClick=${() => setLine(l)}>${l}</button>`)}</div>`}
      </div>
      ${shown.length === 0 && html`<div class="card"><${Empty} compact icon=${filter === "active" ? "check" : "bulb"}
        title=${filter === "active" ? "没有待处理的想法" : "这里还是空的"} hint=${filter === "active" ? "都处理完了。读论文时按 ⌘I 记下一个新的。" : "换个筛选看看"} /></div>`}
      <div class="idea-grid">${shown.map((i, n) => html`<${IdeaCard} key=${i.id} i=${i} n=${n} lines=${lines} update=${update} ready=${ready}
        onPromote=${(x) => onAsk(promotePrompt(x), "整理想法")} onOpenNote=${onOpenNote} onOpenPaper=${onOpenPaper} />`)}</div>`}
  </div></div>`;
}

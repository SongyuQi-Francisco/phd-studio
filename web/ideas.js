// Ideas: capture inbox -> keep / drop -> promote into a proper idea note (drafted by the assistant).
import { useEffect, useMemo, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";

const STATUS = { new: "新", kept: "保留", promoted: "已整理", dropped: "丢弃" };

export const promotePrompt = (i) => `把想法 ${i.id} 整理成正式 idea 笔记。原文：「${i.text}」${i.line ? `（研究线：${i.line}）` : ""}${i.paper ? `（来自论文 ${i.paper}${i.page ? " p." + i.page : ""}）` : ""}。
要求：用 Idea Note Template 的结构，按 AGENTS.md 的 Idea Maintenance 写清 问题 / 为什么是现在 / 技术核心 / 第一个实验 / Kill criteria / 下一步要找的证据；先查 vault 里已有的相关 idea 和论文笔记，避免重复，并链接相关论文。
先用三四句话告诉我笔记文件名和要点，再写入 idea 笔记文件夹；写完用 idea_update 把这条想法标为 promoted，note 填笔记路径。`;

function IdeaCard({ i, lines, update, onPromote, onOpenNote, onOpenPaper }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(i.text);
  const save = () => { setEditing(false); if (text.trim() && text !== i.text) update(i, { text: text.trim() }); };
  return html`<div class=${"idea" + (i.status === "dropped" ? " dropped" : "")}>
    ${editing
      ? html`<textarea class="ta" rows="3" value=${text} onInput=${(e) => setText(e.target.value)} onBlur=${save}
          onKeyDown=${(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save(); if (e.key === "Escape") { setText(i.text); setEditing(false); } }} autofocus />`
      : html`<div class="idea-text" title="点击编辑" onClick=${() => setEditing(true)}>${i.text}</div>`}
    <div class="idea-meta">
      <span class=${"tag st-" + i.status}>${STATUS[i.status] || i.status}</span>
      <select value=${i.line || ""} onChange=${(e) => update(i, { line: e.target.value || null })}>
        <option value="">不分组</option>${lines.map((l) => html`<option value=${l}>${l}</option>`)}</select>
      ${i.paper && html`<button class="linkbtn" onClick=${() => onOpenPaper(i.paper)}>${i.paper}${i.page ? " · p." + i.page : ""}</button>`}
      <span class="muted small">${(i.created_at || "").replace("T", " ").slice(5, 16)}${i.source === "assistant" ? " · 助手" : i.source === "claude" ? " · 其他会话" : ""}</span>
      <span class="grow" />
      ${i.note && html`<button class="chip" onClick=${() => onOpenNote(i.note)}><${Icon} name="note" sm />${i.note.split("/").pop().replace(/\.md$/, "")}</button>`}
      ${i.status !== "promoted" && html`<button class="chip" onClick=${() => onPromote(i)}><${Icon} name="sparkle" sm />整理成笔记</button>`}
      ${i.status === "new" && html`<button class="abtn txt" onClick=${() => update(i, { status: "kept" })}>保留</button>`}
      ${i.status !== "dropped" && i.status !== "promoted" && html`<button class="abtn" title="丢弃（保留记录）" onClick=${() => update(i, { status: "dropped" })}><${Icon} name="x" sm /></button>`}
      ${i.status === "dropped" && html`<button class="abtn txt" onClick=${() => update(i, { status: "kept" })}>恢复</button>`}
    </div>
  </div>`;
}

export function IdeasPage({ version, lines, radarPath, onAsk, onOpenNote, onOpenPaper, toast }) {
  const [ideas, setIdeas] = useState([]);
  const [filter, setFilter] = useState("active");
  const [line, setLine] = useState("");
  const [draft, setDraft] = useState("");
  const load = () => api("/api/ideas").then((r) => setIdeas(r.ideas));
  useEffect(() => { load(); }, [version]);
  const update = async (i, patch) => { await api(`/api/ideas/${i.id}`, { method: "PATCH", json: patch }); load(); };
  const add = async () => {
    if (!draft.trim()) return;
    await api("/api/ideas", { json: { text: draft, line: line || null } });
    setDraft(""); toast("想法已记下"); load();
  };
  const counts = useMemo(() => Object.fromEntries(Object.keys(STATUS).map((k) => [k, ideas.filter((i) => i.status === k).length])), [ideas]);
  const shown = ideas.filter((i) => (filter === "active" ? ["new", "kept"].includes(i.status) : filter === "all" || i.status === filter)
    && (!line || i.line === line));
  return html`<div class="page"><div class="page-inner">
    <div class="page-head"><h1>想法</h1>
      ${radarPath && html`<button class="chip" onClick=${() => onOpenNote(radarPath)}><${Icon} name="note" sm />Idea Radar</button>`}</div>
    <div class="capture-row">
      <input placeholder="一句话记下想法，回车保存（任何页面也可以按 ⌘I）" value=${draft} onInput=${(e) => setDraft(e.target.value)}
        onKeyDown=${(e) => e.key === "Enter" && !e.isComposing && add()} />
      <button class="btn primary" disabled=${!draft.trim()} onClick=${add}>记下</button>
    </div>
    <div class="filters">
      <div class="seg">
        <button class=${filter === "active" ? "on" : ""} onClick=${() => setFilter("active")}>待处理 ${counts.new + counts.kept}</button>
        <button class=${filter === "promoted" ? "on" : ""} onClick=${() => setFilter("promoted")}>已整理 ${counts.promoted}</button>
        <button class=${filter === "dropped" ? "on" : ""} onClick=${() => setFilter("dropped")}>丢弃 ${counts.dropped}</button>
        <button class=${filter === "all" ? "on" : ""} onClick=${() => setFilter("all")}>全部</button>
      </div>
      ${lines.length > 0 && html`<div class="seg"><button class=${!line ? "on" : ""} onClick=${() => setLine("")}>全部研究线</button>
        ${lines.map((l) => html`<button class=${line === l ? "on" : ""} onClick=${() => setLine(l)}>${l}</button>`)}</div>`}
    </div>
    ${shown.length === 0 && html`<div class="card muted pad">${filter === "active" ? "没有待处理的想法。读论文时按 ⌘I，或对助手说“记一个想法：…”" : "这里还是空的"}</div>`}
    ${shown.map((i) => html`<${IdeaCard} key=${i.id} i=${i} lines=${lines} update=${update}
      onPromote=${(x) => onAsk(promotePrompt(x), "整理想法")} onOpenNote=${onOpenNote} onOpenPaper=${onOpenPaper} />`)}
  </div></div>`;
}

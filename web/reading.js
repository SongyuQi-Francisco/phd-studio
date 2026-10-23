// Reading list: inbox -> T1/T2/T3 -> reading -> done, with snooze / dismiss.
import { useEffect, useMemo, useState, useCallback } from "preact/hooks";
import { html, Icon, api } from "/lib.js";

const SOURCE = { tracker: "Tracker", claude: "Claude", advisor: "导师", self: "自己" };
const VERDICT = { key: "关键", useful: "有用", irrelevant: "无关" };
const DEPTH = { skim: "略读", deep: "精读" };

export function addDays(n) {
  const d = new Date(); d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

function age(days) {
  if (!days) return "今天";
  if (days < 7) return `${days} 天前`;
  if (days < 60) return `${Math.round(days / 7)} 周前`;
  return `${Math.round(days / 30)} 个月前`;
}

function shortBy(i) {
  const by = (i.pushed_by || "").replace(/^Reading Queue · /, "");
  return [SOURCE[i.source] || i.source, by].filter(Boolean).join(" · ");
}

export function DoneDialog({ item, onClose, onSaved }) {
  const [depth, setDepth] = useState(item.depth || "deep");
  const [verdict, setVerdict] = useState(item.verdict || "");
  const [takeaway, setTakeaway] = useState(item.takeaway || "");
  const [drafting, setDrafting] = useState(false);
  const [err, setErr] = useState("");
  const draft = async () => {
    setDrafting(true); setErr("");
    try { setTakeaway((await api(`/api/reading/draft/${encodeURIComponent(item.key)}`, { method: "POST" })).takeaway); }
    catch (e) { setErr(e.message); }
    setDrafting(false);
  };
  const save = async () => {
    const r = await api(`/api/reading/${item.id}`, { method: "PATCH", json: { status: "done", depth, verdict: verdict || null, takeaway: takeaway.trim() || null } });
    onSaved(r.item);
  };
  return html`<div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <div class="modal">
      <h2>读完了</h2>
      <div class="dlg-title">${item.title}</div>
      <div class="field"><label>读到什么程度</label>
        <div class="seg">${Object.entries(DEPTH).map(([k, l]) => html`<button class=${depth === k ? "on" : ""} onClick=${() => setDepth(k)}>${l}</button>`)}</div></div>
      <div class="field"><label>对我的价值</label>
        <div class="seg">${Object.entries(VERDICT).map(([k, l]) => html`<button class=${verdict === k ? "on" : ""} onClick=${() => setVerdict(verdict === k ? "" : k)}>${l}</button>`)}</div></div>
      <div class="field col">
        <div class="row-between"><label>一句话收获</label>
          <button class="tb-btn" disabled=${drafting} onClick=${draft}><${Icon} name="sparkle" sm />${drafting ? "起草中…" : "让 Claude 起草"}</button></div>
        <textarea class="ta" rows="3" placeholder="它解决了什么，我能拿走什么" value=${takeaway} onInput=${(e) => setTakeaway(e.target.value)} />
        ${err && html`<div class="err">${err}</div>`}
      </div>
      <div class="row-end" style="margin-top:14px"><button class="btn ghost" onClick=${onClose}>取消</button><button class="btn primary" onClick=${save}>保存</button></div>
    </div>
  </div>`;
}

function Row({ i, selected, onSelect, onOpen, act }) {
  const due = i.due && html`<span class=${"due" + (i.due <= addDays(2) ? " soon" : "")}>截止 ${i.due.slice(5)}</span>`;
  const left = i.status === "queued" ? html`<span class=${"tier " + i.tier}>${i.tier}</span>`
    : i.status === "reading" ? html`<span class="tier R">读</span>`
    : i.status === "done" ? html`<span class=${"tier D " + (i.verdict || "")}>${VERDICT[i.verdict] || "✓"}</span>`
    : html`<span class="dot" />`;
  const stop = (fn) => (e) => { e.stopPropagation(); fn(); };
  return html`<div class=${"rl-row" + (selected ? " sel" : "")} onMouseEnter=${onSelect} onClick=${() => onOpen(i)}>
    <div class="rl-left">${left}</div>
    <div class="rl-main">
      <div class="rl-title">${i.title}${i.paper ? "" : html` <span class="muted">arXiv</span>`}</div>
      <div class="rl-reason">${i.status === "done" && i.takeaway ? i.takeaway : i.reason}</div>
      <div class="rl-meta">
        ${i.lines.map((l) => html`<span class="tag">${l}</span>`)}
        ${due}
        <span>${shortBy(i)}</span><span>${age(i.age_days)}</span>
        ${i.status === "done" && i.depth && html`<span>${DEPTH[i.depth]} · ${i.read_at}</span>`}
      </div>
    </div>
    <div class="rl-actions">
      ${(i.status === "inbox" || i.status === "queued") && ["T1", "T2", "T3"].map((t) => html`<button class=${"tbtn" + (i.tier === t && i.status === "queued" ? " on" : "")} title=${"设为 " + t} onClick=${stop(() => act(i, { tier: t, status: "queued" }))}>${t}</button>`)}
      ${(i.status === "queued" || i.status === "reading") && html`<button class="abtn" title="读完 (D)" onClick=${stop(() => act(i, "done"))}><${Icon} name="check" sm /></button>`}
      ${(i.status === "inbox" || i.status === "queued") && html`<button class="abtn" title="稍后：一周后回到收件箱 (S)" onClick=${stop(() => act(i, { status: "snoozed", snooze_until: addDays(7) }))}><${Icon} name="clock" sm /></button>
        <button class="abtn" title="不读 (X)" onClick=${stop(() => act(i, { status: "dismissed" }))}><${Icon} name="x" sm /></button>`}
      ${(i.status === "snoozed" || i.status === "dismissed" || i.stale) && html`<button class="abtn txt" onClick=${stop(() => act(i, { status: "inbox" }))}>放回收件箱</button>`}
    </div>
  </div>`;
}

export function ReadingPage({ onOpen, toast, version }) {
  const [data, setData] = useState(null);
  const [line, setLine] = useState("");
  const [sel, setSel] = useState(0);
  const [doneItem, setDoneItem] = useState(null);
  const [open, setOpen] = useState({});

  const load = useCallback(() => api("/api/reading").then(setData), []);
  useEffect(() => { load(); }, [version]);

  const items = data?.items || [];
  const s = data?.summary;
  const groups = useMemo(() => {
    const f = items.filter((i) => !line || i.lines.includes(line));
    const by = (st) => f.filter((i) => i.status === st);
    const order = (a, b) => (a.due || "9").localeCompare(b.due || "9") || a.age_days - b.age_days;
    const queued = by("queued").sort(order);
    return [
      { id: "inbox", label: "收件箱", items: by("inbox").filter((i) => !i.stale).sort((a, b) => a.age_days - b.age_days), hint: "定优先级后进入待读" },
      { id: "reading", label: "在读", items: by("reading") },
      { id: "T1", label: "T1 · 本周", items: queued.filter((i) => i.tier === "T1") },
      { id: "T2", label: "T2", items: queued.filter((i) => i.tier === "T2") },
      { id: "T3", label: "T3", items: queued.filter((i) => i.tier === "T3") },
      { id: "done", label: "读完", items: by("done").sort((a, b) => (b.read_at || "").localeCompare(a.read_at || "")), fold: true },
      { id: "snoozed", label: "稍后", items: by("snoozed"), fold: true },
      { id: "stale", label: "未处理归档", items: by("inbox").filter((i) => i.stale), fold: true, hint: "收件箱里超过 3 周没处理的" },
      { id: "dismissed", label: "不读", items: by("dismissed"), fold: true },
    ];
  }, [items, line]);
  const flat = groups.flatMap((g) => (g.fold && !open[g.id] ? [] : g.items));
  const current = flat[Math.min(sel, flat.length - 1)];

  const act = async (i, patch) => {
    if (patch === "done") { setDoneItem(i); return; }
    if (patch.tier === "T1" && i.tier !== "T1" && s.T1 >= s.caps.T1) toast(`T1 已有 ${s.T1} 篇，建议先读完或降级一篇`);
    else if (patch.status === "queued" && i.status !== "queued" && s.queued >= s.caps.queued) toast(`待读已有 ${s.queued} 篇，清单在变长`);
    await api(`/api/reading/${i.id}`, { method: "PATCH", json: patch });
    load();
  };

  useEffect(() => {
    const k = (e) => {
      if (e.target.matches("input, textarea") || doneItem || e.metaKey || e.ctrlKey) return;
      const i = current;
      if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); setSel((n) => Math.min(n + 1, flat.length - 1)); }
      else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); setSel((n) => Math.max(n - 1, 0)); }
      else if (!i) return;
      else if (["1", "2", "3"].includes(e.key) && ["inbox", "queued"].includes(i.status)) act(i, { tier: "T" + e.key, status: "queued" });
      else if (e.key === "s" && ["inbox", "queued"].includes(i.status)) act(i, { status: "snoozed", snooze_until: addDays(7) });
      else if (e.key === "x" && ["inbox", "queued"].includes(i.status)) act(i, { status: "dismissed" });
      else if (e.key === "d" && ["queued", "reading"].includes(i.status)) act(i, "done");
      else if (e.key === "Enter") onOpen(i);
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [current, flat.length, doneItem, s]);

  if (!s) return html`<div class="rl-page"><div class="loading">载入中…</div></div>`;
  const stat = (n, label, warn) => html`<div class="stat"><b class=${warn ? "warn" : ""}>${n}</b><span>${label}</span></div>`;
  let idx = -1;
  return html`<div class="rl-page"><div class="rl-inner">
    <div class="rl-head">
      <h1>阅读清单</h1>
      <div class="stats">
        ${stat(s.inbox, "收件箱")}
        ${stat(html`${s.T1}<small>/${s.caps.T1}</small>`, "T1 本周", s.T1 > s.caps.T1)}
        ${stat(html`${s.queued}<small>/${s.caps.queued}</small>`, "待读", s.queued > s.caps.queued)}
        ${stat(s.reading, "在读")}
        ${stat(s.done_week, "本周读完")}
      </div>
      ${s.lines.length > 0 && html`<div class="seg">
        <button class=${!line ? "on" : ""} onClick=${() => setLine("")}>全部</button>
        ${s.lines.map((l) => html`<button class=${line === l ? "on" : ""} onClick=${() => setLine(l)}>${l}</button>`)}
      </div>`}
    </div>
    ${groups.map((g) => g.items.length === 0 ? null : html`<section class="rl-group">
      <div class=${"rl-gh" + (g.fold ? " fold" : "")} onClick=${() => g.fold && setOpen({ ...open, [g.id]: !open[g.id] })}>
        ${g.fold && html`<${Icon} name=${open[g.id] ? "down" : "right"} sm />`}<span>${g.label}</span><small>${g.items.length}</small>
        ${g.hint && !g.fold && html`<em>${g.hint}</em>`}
      </div>
      ${(!g.fold || open[g.id]) && g.items.map((i) => { idx += 1; const my = idx;
        return html`<${Row} i=${i} selected=${current && current.id === i.id} onSelect=${() => setSel(my)} onOpen=${onOpen} act=${act} />`; })}
    </section>`)}
    <div class="rl-keys"><span class="kbd">1</span><span class="kbd">2</span><span class="kbd">3</span> 定优先级 · <span class="kbd">S</span> 稍后一周 · <span class="kbd">X</span> 不读 · <span class="kbd">D</span> 读完 · <span class="kbd">J</span>/<span class="kbd">K</span> 上下 · <span class="kbd">↵</span> 打开</div>
  </div>
  ${doneItem && html`<${DoneDialog} item=${doneItem} onClose=${() => setDoneItem(null)} onSaved=${() => { setDoneItem(null); toast("已记为读完"); load(); }} />`}
  </div>`;
}

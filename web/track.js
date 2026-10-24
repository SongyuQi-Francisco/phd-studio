// Tracking: weekly lab radar, live follows (keywords / people via arXiv), labs, and a capture box.
import { useEffect, useState } from "preact/hooks";
import { html, Icon, api, renderMarkdown } from "/lib.js";

const LISTED = { inbox: "已在收件箱", queued: "待读", reading: "在读", done: "已读", snoozed: "稍后", dismissed: "不读" };

function PaperRow({ p, reason, onAdded, toast, onOpenPaper }) {
  const [state, setState] = useState(p.listed);
  const add = async () => {
    try { const r = await api("/api/reading", { json: { arxiv: p.arxiv, reason } }); setState(r.item.status); toast("已加入收件箱"); onAdded?.(); }
    catch (e) { toast(e.message); }
  };
  return html`<div class="prow trow">
    ${p.tier && html`<span class=${"tier " + (p.tier === "A" ? "T1" : p.tier === "B" ? "T2" : "")}>${p.tier}</span>`}
    <div class="p-main"><div class="t-title">${p.title}</div>
      <div class="muted small">${p.published || ""} · ${(p.authors || []).slice(0, 4).join(", ")}${(p.authors || []).length > 4 ? " 等" : ""}
        ${p.labs?.length ? " · " + p.labs.join(", ") : ""}</div></div>
    <a class="abtn" title="在 arXiv 打开" href=${`https://arxiv.org/abs/${p.arxiv}`} target="_blank" rel="noreferrer"><${Icon} name="external" sm /></a>
    ${p.note && html`<button class="abtn txt" onClick=${() => onOpenPaper(p.note)}>读</button>`}
    ${state ? html`<span class="tag">${LISTED[state] || state}</span>` : html`<button class="chip" onClick=${add}><${Icon} name="plus" sm />清单</button>`}
  </div>`;
}

function Capture({ onAsk, toast, lines, onChanged }) {
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const go = async () => {
    const r = await api("/api/track/capture", { json: { text, reason } }).catch((e) => ({ error: e.message }));
    if (r.error) return toast(r.error);
    if (r.unresolved) {
      onAsk(`帮我把这篇论文加入阅读清单：${text}${reason ? `，理由：${reason}` : ""}。先找到它的 arXiv 版本（找不到就告诉我，不要猜），然后用 reading_push 推进收件箱。`, "捕捉论文");
    } else toast(r.result === "added" ? "已加入收件箱" : r.result);
    setText(""); setReason(""); onChanged?.();
  };
  return html`<section class="card capture">
    <div class="card-head"><h2>捕捉</h2><span class="muted small">arXiv 链接或编号直接进收件箱；OpenReview / DOI / 标题交给助手找</span></div>
    <div class="inline-form">
      <input placeholder="粘贴论文链接、arXiv 编号或标题" value=${text} onInput=${(e) => setText(e.target.value)} />
      <input placeholder="为什么要读（一句话）" value=${reason} onInput=${(e) => setReason(e.target.value)}
        onKeyDown=${(e) => e.key === "Enter" && text.trim() && go()} />
      <button class="btn primary" disabled=${!text.trim()} onClick=${go}>进收件箱</button>
    </div>
  </section>`;
}

function Radar({ toast, onOpenPaper, onOpenNote, onChanged }) {
  const [runs, setRuns] = useState([]);
  const [cur, setCur] = useState(null);
  const [run, setRun] = useState(null);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => { api("/api/track/runs").then((r) => { setRuns(r.runs); if (r.runs[0]) setCur(r.runs[0].run); }); }, []);
  useEffect(() => { if (cur) { setRun(null); api(`/api/track/runs/${cur}`).then(setRun); } }, [cur]);
  if (!runs.length) return html`<div class="card muted pad">还没有追踪记录（每周的实验室追踪任务运行后会出现在这里）</div>`;
  const t = run?.stats?.by_tier || {};
  const papers = run ? (showAll ? run.papers : run.papers.filter((p) => p.tier !== "C" || run.papers.length <= 12)) : [];
  return html`
    <div class="runbar">${runs.map((r) => { const b = r.stats.by_tier || {};
      return html`<button class=${"runchip" + (cur === r.run ? " on" : "")} onClick=${() => setCur(r.run)}>
        <b>${r.run.slice(5)}</b><span>A${b.A || 0} · B${b.B || 0} · C${b.C || 0}</span></button>`; })}</div>
    ${run && html`
      <div class="stats five">
        <div class="stat"><b>${t.A || 0}</b><span>Tier A 必读</span></div>
        <div class="stat"><b>${t.B || 0}</b><span>Tier B 相关</span></div>
        <div class="stat"><b>${t.C || 0}</b><span>Tier C 领域内</span></div>
        <div class="stat"><b>${run.stats.input ?? "—"}</b><span>抓取总数</span></div>
        <div class="stat"><b>${run.stats.gate_fail ?? "—"}</b><span>未过主题门槛</span></div>
      </div>
      ${run.analysis && html`<section class="card"><div class="card-head"><h2>本周分析</h2>
          ${run.report_url && html`<a class="chip" href=${run.report_url}><${Icon} name="external" sm />完整周报</a>`}</div>
        <div class="md" onClick=${async (e) => { const w = e.target.closest(".wikilink"); if (!w) return;
          try { const r = await api(`/api/note/find?name=${encodeURIComponent(w.dataset.target)}`); r.paper ? onOpenPaper(r.paper) : onOpenNote(r.path); } catch (x) { toast(x.message); } }}
          dangerouslySetInnerHTML=${{ __html: renderMarkdown(run.analysis) }} /></section>`}
      <section class="card list-card">
        ${papers.map((p) => html`<${PaperRow} key=${p.arxiv} p=${p} reason=${`Lab Tracker ${run.run} · Tier ${p.tier}${p.labs?.length ? " · " + p.labs.join(", ") : ""}`}
          toast=${toast} onOpenPaper=${onOpenPaper} onAdded=${onChanged} />`)}
        ${!showAll && papers.length < run.papers.length && html`<button class="linkbtn pad" onClick=${() => setShowAll(true)}>显示全部 ${run.papers.length} 篇（含 Tier C）</button>`}
      </section>`}`;
}

function Follows({ toast, onOpenPaper, version, onChanged }) {
  const [feed, setFeed] = useState(null);
  const [kind, setKind] = useState("keyword");
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const load = async (refresh = false) => { setLoading(true); try { setFeed((await api(`/api/track/feed?refresh=${refresh}`)).feed); } finally { setLoading(false); } };
  useEffect(() => { load(); }, [version]);
  const add = async () => { await api("/api/track/follows", { json: { kind, value } }); setValue(""); toast("已关注，正在查 arXiv…"); load(); onChanged?.(); };
  const remove = async (f) => { await api(`/api/track/follows/${f.id}`, { method: "DELETE" }); load(); onChanged?.(); };
  return html`
    <section class="card">
      <div class="card-head"><h2>关注</h2><span class="muted small">实时查 arXiv 最近 30 天（缓存 6 小时）
        <button class="linkbtn" disabled=${loading} onClick=${() => load(true)}>${loading ? "查询中…" : "刷新"}</button></span></div>
      <div class="inline-form">
        <div class="seg"><button class=${kind === "keyword" ? "on" : ""} onClick=${() => setKind("keyword")}>关键词</button>
          <button class=${kind === "person" ? "on" : ""} onClick=${() => setKind("person")}>学者</button></div>
        <input placeholder=${kind === "keyword" ? "如：memory consolidation agent（多个词同时出现；\"引号\" 表示整词组）" : "如：Julian McAuley"} value=${value}
          onInput=${(e) => setValue(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && value.trim() && add()} />
        <button class="btn primary" disabled=${!value.trim()} onClick=${add}>关注</button>
      </div>
    </section>
    ${feed === null && html`<div class="muted pad">查询中…</div>`}
    ${feed && feed.length === 0 && html`<div class="card muted pad">还没有关注。加几个关键词或学者，这里会列出他们最近 30 天的 arXiv 新论文。</div>`}
    ${(feed || []).map((f) => html`<section class="card list-card">
      <div class="card-head pad-x"><h2><span class="tag">${f.kind === "person" ? "学者" : "关键词"}</span> ${f.value}</h2>
        <button class="abtn" title="取消关注" onClick=${() => remove(f)}><${Icon} name="x" sm /></button></div>
      ${f.error && html`<div class="err">${f.error}</div>`}
      ${f.papers.length === 0 && html`<div class="muted pad small">最近 30 天没有新论文</div>`}
      ${f.papers.map((p) => html`<${PaperRow} key=${p.arxiv} p=${p} reason=${`关注${f.kind === "person" ? "学者" : "关键词"}：${f.value}`} toast=${toast} onOpenPaper=${onOpenPaper} onAdded=${onChanged} />`)}
    </section>`)}`;
}

function Labs({ onOpenNote }) {
  const [labs, setLabs] = useState(null);
  useEffect(() => { api("/api/track/labs").then((r) => setLabs(r.labs)); }, []);
  if (!labs) return html`<div class="muted pad">载入中…</div>`;
  const max = Math.max(1, ...labs.map((l) => l.count));
  return html`<div class="lab-grid">${labs.map((l) => html`<button class="lab" onClick=${() => onOpenNote(l.path)}>
    <div class="lab-top"><b>${l.name}</b><span class="lab-n">${l.count}</span></div>
    <div class="muted small">${[l.institution, l.country].filter(Boolean).join(" · ")}</div>
    <div class="bar"><i style=${{ width: (l.count / max) * 100 + "%" }} /></div>
    <div class="row-wrap">${l.tracks.map((t) => html`<span class="tag">${t}</span>`)}</div>
    ${l.recent[0] && html`<div class="small lab-last">${l.recent[0].published} · ${l.recent[0].title}</div>`}
  </button>`)}</div>`;
}

export function TrackPage({ version, onAsk, toast, onOpenPaper, onOpenNote, onChanged }) {
  const [tab, setTab] = useState("radar");
  return html`<div class="page"><div class="page-inner wide">
    <div class="page-head"><h1>追踪</h1>
      <div class="seg">${[["radar", "本周雷达"], ["follows", "关注动态"], ["labs", "实验室"]].map(([k, l]) => html`<button class=${tab === k ? "on" : ""} onClick=${() => setTab(k)}>${l}</button>`)}</div>
    </div>
    <${Capture} onAsk=${onAsk} toast=${toast} onChanged=${onChanged} />
    ${tab === "radar" && html`<${Radar} toast=${toast} onOpenPaper=${onOpenPaper} onOpenNote=${onOpenNote} onChanged=${onChanged} />`}
    ${tab === "follows" && html`<${Follows} toast=${toast} onOpenPaper=${onOpenPaper} version=${version} onChanged=${onChanged} />`}
    ${tab === "labs" && html`<${Labs} onOpenNote=${onOpenNote} />`}
  </div></div>`;
}

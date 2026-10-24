// Tracking: top scholars of the field (cards, heatmap, monthly output, paper cards), the weekly lab radar,
// live follows, the 48 tracked labs, a capture box, and 「立即巡检」 (runs research_tracker in the background).
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, api, renderMarkdown, prefs } from "/lib.js";
import { Avatar, Columns, fmtN, HBars, Heatmap, Sparkline, TierStrip } from "/track-charts.js";

const LISTED = { inbox: "已在收件箱", queued: "待读", reading: "在读", done: "已读", snoozed: "稍后", dismissed: "不读" };
const WINDOWS = [[30, "30 天"], [90, "90 天"], [365, "1 年"]];
const dayISO = (n) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
const monthLabel = (m) => `${+m.slice(5)}月`;
const ago = (d) => { const n = Math.round((Date.now() - new Date(d)) / 864e5); return n <= 0 ? "今天" : n < 31 ? `${n} 天前` : d; };

// ---------------- paper card (shared by every tab) ----------------

function PaperCard({ p, reason, lines, faces, labels, toast, onOpenPaper, onAsk, onAdded, extra, thumbReady }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState(p.listed);
  const [thumb, setThumb] = useState(true);
  const date = p.date || p.published;
  const rel = (p.lines || []).length > 0;
  const add = async (e) => {
    e.stopPropagation();
    if (!p.arxiv) {
      onAsk(`帮我把这篇论文加入阅读清单：「${p.title}」${p.doi ? `（${p.doi}）` : ""}，理由：${reason}。先找到它的 arXiv 版本（找不到就告诉我，不要猜），然后用 reading_push 推进收件箱${rel ? `，lines 设为 ${p.lines.join("、")}` : ""}。`, "加入清单");
      return;
    }
    try {
      const r = await api("/api/reading", { json: { arxiv: p.arxiv, reason, lines: lines ?? p.lines ?? [] } });
      setState(r.item.status); toast(r.result === "added" ? "已加入收件箱" : r.result); onAdded?.();
    } catch (x) { toast(x.message); }
  };
  const url = p.arxiv ? `https://arxiv.org/abs/${p.arxiv}` : p.doi;
  return html`<article class=${"tk-paper" + (open ? " open" : "") + (rel ? " rel" : "")}>
    <div class="tk-thumb" onClick=${() => setOpen(!open)}>
      <div class="ph"><b>${p.venue && p.venue !== "arXiv" ? p.venue : (date || "").slice(0, 4)}</b><span>${p.venue === "arXiv" || !p.venue ? "arXiv 预印本" : p.venue_full || ""}</span></div>
      ${p.arxiv && thumbReady && thumb && html`<img src=${`/api/track/thumb/${p.arxiv}`} alt="" loading="lazy" onError=${() => setThumb(false)} />`}
      ${p.tier && html`<span class="tier-pin"><span class=${"tk-tier " + p.tier} title=${`Tier ${p.tier}（与巡检同一套打分）`}>${p.tier}</span></span>`}
    </div>
    <div class="tk-pbody">
      <div class="tk-ptitle" onClick=${() => setOpen(!open)} title=${p.title}>${p.title}</div>
      <div class="tk-pmeta">
        ${p.venue && p.venue !== "arXiv" && html`<span class="venue">${p.venue}</span>`}
        <span>${date ? (p.date_approx ? `${date.slice(0, 4)} 年` : ago(date)) : ""}</span>
        ${p.cites > 0 && html`<span>· 被引 ${p.cites}</span>`}
        ${faces?.length > 0 && html`<span class="tk-faces">${faces}</span>`}
      </div>
      <div class="tk-chips">
        ${(p.lines || []).map((l) => html`<span class="tk-badge rel" title=${`命中：${(p.line_hits?.[l] || []).join("、")}`}>${l}</span>`)}
        ${(p.topics || []).slice(0, open ? 9 : 3).map((t) => html`<span class="tk-chip">${labels?.[t] || t}</span>`)}
        ${extra}
      </div>
      ${open && html`
        ${p.abstract && html`<div class="tk-abs">${p.abstract}${p.abstract.length >= 890 ? "…" : ""}</div>`}
        ${p.authors?.length > 0 && html`<div class="tk-authors">${p.authors.slice(0, 12).join(", ")}${(p.n_authors || p.authors.length) > 12 ? " 等" : ""}</div>`}`}
      <div class="tk-pfoot">
        ${url && html`<a class="abtn" title="打开原文页" href=${url} target="_blank" rel="noreferrer" onClick=${(e) => e.stopPropagation()}><${Icon} name="external" sm /></a>`}
        ${p.note && html`<button class="abtn txt" onClick=${() => onOpenPaper(p.note)}>读</button>`}
        <span class="spacer" />
        ${state ? html`<span class="tag">${LISTED[state] || state}</span>`
          : html`<button class="tk-add" onClick=${add} title=${p.arxiv ? "加入阅读清单收件箱" : "没有 arXiv 编号：交给助手找"}><${Icon} name="plus" sm />清单</button>`}
      </div>
    </div>
  </article>`;
}

// Thumbnails render in the background; ask which are ready and look again while some are pending.
function useThumbs(ids) {
  const [ready, setReady] = useState(() => new Set());
  const key = ids.join(",");
  useEffect(() => {
    if (!ids.length) return;
    let alive = true, tries = 0, timer;
    const poll = async () => {
      try {
        const r = await api("/api/track/thumbs", { json: { ids } });
        if (!alive) return;
        setReady((old) => (r.ready.every((a) => old.has(a)) ? old : new Set([...old, ...r.ready])));
        if (r.pending.length && ++tries < 30) timer = setTimeout(poll, 4000);
      } catch {}
    };
    poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [key]);
  return ready;
}

function PaperGrid({ papers, step = 18, render }) {
  const [n, setN] = useState(step);
  useEffect(() => setN(step), [papers]);
  const shown = papers.slice(0, n);
  const ready = useThumbs(shown.map((p) => p.arxiv).filter(Boolean));
  if (!papers.length) return html`<div class="tk-empty">没有符合筛选的论文</div>`;
  return html`<div class="tk-papers">${shown.map((p) => render(p, ready.has(p.arxiv)))}</div>
    ${papers.length > n && html`<button class="btn tk-more" onClick=${() => setN(n + step * 2)}>再显示 ${Math.min(step * 2, papers.length - n)} 篇（共 ${papers.length}）</button>`}`;
}

// ---------------- 立即巡检 ----------------

const STAGES = { prepare: "准备", fetch: "抓取 arXiv", ingest: "打分归类", render: "写入 vault", audit: "检查链接", done: "完成", failed: "失败" };

function Patrol({ toast, onAsk, onDone }) {
  const [st, setSt] = useState(null);
  const [showLog, setShowLog] = useState(false);
  const timer = useRef();
  const load = async () => {
    try { const s = await api("/api/track/tracker"); setSt(s); return s; }
    catch { setSt((o) => o || { available: true, running: false, unreachable: true }); return null; }  // keep the button
  };
  useEffect(() => { load(); return () => clearTimeout(timer.current); }, []);
  useEffect(() => {
    clearTimeout(timer.current);
    if (st?.running) timer.current = setTimeout(async () => {
      const s = await load();
      if (s && !s.running && !st.external) { toast(s.ok ? `巡检完成：${s.run}（保留 ${s.stats?.kept ?? "?"} 篇）` : `巡检失败：${s.error}`); onDone?.(s); }
      else if (s && !s.running) onDone?.(s);
    }, 1500);
  }, [st]);
  const start = async () => {
    try { setSt(await api("/api/track/tracker/run", { method: "POST" })); toast("开始巡检：抓取 → 打分 → 写入 vault"); }
    catch (e) { toast(e.message); }
  };
  if (!st) return null;
  if (!st.available) return html`<span class="muted small" title="studio.toml 里没有配置 tracker 目录">未配置巡检</span>`;
  const pct = !st.running ? 100 : st.stage === "fetch" ? 5 + 75 * (st.queries ? st.queries_done / st.queries : 0)
    : { prepare: 2, ingest: 84, render: 92, audit: 97 }[st.stage] || 50;
  const finishedRecently = !st.running && st.finished && Date.now() - new Date(st.finished) < 6 * 3600e3;
  return html`<span class="tk-patrol">
    ${st.running && html`<span class="tk-prog" onClick=${() => setShowLog(!showLog)} title=${st.external ? "这次巡检是之前开始的，进度不可见" : "点击看日志"}>
      <span>${st.external ? "另一个巡检在运行" : STAGES[st.stage] || st.stage}${st.stage === "fetch" && st.queries ? ` ${st.queries_done}/${st.queries}` : ""}${st.run ? ` · ${st.run}` : ""}</span>
      <span class=${"tk-prog-bar" + (st.external ? " indet" : "")}><i style=${{ width: pct + "%" }} /></span></span>`}
    ${finishedRecently && st.ok && html`<button class="chip" onClick=${() => onAsk(`按 research_tracker/WEEKLY_RUNBOOK.md 第 3 步审阅刚跑完的巡检 ${st.run}（since ${st.since}）：检查 tier-A 的同名误匹配，写 run note 的 ANALYSIS 区块，只给真正的必读建 paper note。先读 vault 的 AGENTS.md。`, "审阅本周巡检")}>
      <${Icon} name="sparkle" sm />交给助手写分析</button>`}
    ${finishedRecently && st.ok === false && html`<button class="chip" onClick=${() => setShowLog(!showLog)} title=${st.error}><span class="tag warn">上次失败</span></button>`}
    <button class="btn primary" disabled=${st.running} onClick=${start} title=${`抓取 ${st.linkage?.count ? `48 个 lab + ${st.linkage.count} 条关注` : "48 个 lab"} 的新论文，写入 vault 的 Lab Tracker`}>
      <${Icon} name="refresh" sm />${st.running ? "巡检中…" : "立即巡检"}</button>
    ${showLog && st.log?.length > 0 && html`<div class="tk-sheet-bg" onClick=${(e) => e.target === e.currentTarget && setShowLog(false)}><div class="tk-sheet">
      <div class="head"><h2>巡检日志 · ${st.run}</h2><span class="spacer" /><button class="abtn" onClick=${() => setShowLog(false)}><${Icon} name="x" sm /></button></div>
      <div class="muted small">${STAGES[st.stage] || ""}${st.error ? ` · ${st.error}` : ""}${st.audit ? ` · audit ${st.audit}` : ""}</div>
      <pre class="tk-log">${st.log.join("\n")}</pre></div></div>`}
  </span>`;
}

// ---------------- scholars ----------------

const avatarSrcs = (s) => [s.photo ? `/api/track/avatar/${s.id}` : null, s.inst?.logo && s.inst?.id ? `/api/track/logo/${s.inst.id}` : null];

function LineKeywords({ toast, onChanged }) {
  const [lines, setLines] = useState(null);
  useEffect(() => { api("/api/track/scholars").then((r) => setLines(Object.entries(r.lines).map(([l, k]) => [l, k.join(", ")]))); }, []);
  if (!lines) return null;
  const save = async (line, text) => {
    try { await api(`/api/track/lines/${encodeURIComponent(line)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keywords: text.split(/[,，\n]/) }) }); toast(`已更新 ${line} 的关键词`); onChanged(); }
    catch (e) { toast(e.message); }
  };
  return html`<div class="label">“触及我的课题”的关键词（逗号分隔；a + b 表示同时出现，按词首匹配）</div>
    ${lines.map(([l, k], i) => html`<div class="tk-erow" style=${{ gridTemplateColumns: "90px 1fr auto" }}>
      <b>${l}</b><input class="tk-kw" value=${k} onInput=${(e) => { const n = [...lines]; n[i] = [l, e.target.value]; setLines(n); }} />
      <button class="btn" onClick=${() => save(l, lines[i][1])}>保存</button></div>`)}`;
}

// ---------------- list refresh: monthly / 立即更新 → suggestion → apply ----------------

const STAGE_TXT = { prepare: "准备", scholar: "读取 Google Scholar", openalex: "Scholar 不可用，改用 OpenAlex", check: "核对近期论文", done: "完成", failed: "失败" };
const NEED_TXT = { login: "Google Scholar 需要登录", captcha: "Google Scholar 要求人机验证" };

function useListRefresh(toast, onApplied) {
  const [rf, setRf] = useState(null);
  const timer = useRef();
  const load = async () => { try { const r = await api("/api/track/scholars/refresh"); setRf(r); return r; } catch { return null; } };
  useEffect(() => { load(); return () => clearTimeout(timer.current); }, []);
  useEffect(() => {
    clearTimeout(timer.current);
    if (rf?.job?.running) timer.current = setTimeout(async () => {
      const r = await load();
      if (r && !r.job.running) toast(r.job.error ? `名单重算没完成：${r.job.error}` : r.pending ? "名单重算完成：有更新建议" : "名单重算完成：没有变化");
    }, 2000);
  }, [rf]);
  const start = async () => { try { setRf(await api("/api/track/scholars/refresh", { method: "POST" })); toast("开始重算名单"); } catch (e) { toast(e.message); } };
  const apply = async (id) => { try { setRf(await api(`/api/track/scholars/refresh/${id}/apply`, { method: "POST" })); toast("名单已更新"); onApplied(); } catch (e) { toast(e.message); } };
  const dismiss = async (id) => { try { setRf(await api(`/api/track/scholars/refresh/${id}/dismiss`, { method: "POST" })); toast("已忽略这次建议"); } catch (e) { toast(e.message); } };
  const openScholar = async () => { try { await api("/api/track/scholars/scholar-window", { method: "POST" }); } catch (e) { toast(e.message); } };
  return { rf, load, start, apply, dismiss, openScholar };
}

function RefreshBanner({ R, onOpen }) {
  const rf = R.rf;
  if (!rf) return null;
  const j = rf.job || {};
  const p = rf.pending;
  return html`
    ${j.running && html`<div class="tk-banner"><${Icon} name="refresh" sm />
      <span>正在重算名单：${STAGE_TXT[j.stage] || j.stage}${j.current ? ` · ${j.current}` : ""}${j.total ? `（${j.done}/${j.total}）` : ""}</span>
      <span class="tk-prog" style=${{ flex: 1 }}><span class=${"tk-prog-bar" + (j.total ? "" : " indet")}><i style=${{ width: (j.total ? (j.done / j.total) * 100 : 30) + "%" }} /></span></span></div>`}
    ${j.needs_user && html`<div class="tk-banner bad"><span>${NEED_TXT[j.needs_user] || "Google Scholar 需要你处理"}${j.running ? "：已打开 Scholar 窗口，请在那里完成" : "：自动更新先用了快照或 OpenAlex"}</span>
      <span class="spacer" />${rf.capture && html`<button class="chip" onClick=${R.openScholar}>打开 Scholar 窗口</button>`}</div>`}
    ${!j.running && j.error && html`<div class="tk-banner bad"><span>${j.error}</span></div>`}
    ${p && html`<div class="tk-banner ok"><${Icon} name="sparkle" sm />
      <span>名单更新建议：进 ${p.enter.length} 人 · 出 ${p.leave.length} 人 · 名次变化 ${p.moves.filter((m) => m.from !== m.to).length} 处 <span class="muted">（${p.source}）</span></span>
      <span class="spacer" /><button class="btn primary" onClick=${onOpen}>查看并确认</button></div>`}`;
}

function ProposalSheet({ R, list, onClose, toast }) {
  const p = R.rf?.pending;
  if (!p) return null;
  const reasonOf = (c) => p.skipped.find((x) => x.name === c.name)?.reason || "被引低于新入选者";
  const ex = async (c) => {
    if (!confirm(`把 ${c.name} 加入排除名单？以后的重算不会再推荐 TA。`)) return;
    try { await api("/api/track/scholars/exclude", { json: { name: c.name, scholar_id: c.scholar_id, openalex: c.openalex, reason: "确认建议时排除" } }); toast(`已排除 ${c.name}，重新「立即更新」后生效`); R.load(); }
    catch (e) { toast(e.message); }
  };
  const move = (c) => { const m = p.moves.find((x) => x.name === c.name); return !m ? html`<span class="tk-mv new">新</span>` : m.from === m.to ? "" : m.from > m.to ? html`<span class="tk-mv up">▲${m.from - m.to}</span>` : html`<span class="tk-mv down">▼${m.to - m.from}</span>`; };
  const r = p.rules || {};
  return html`<div class="tk-sheet-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}><div class="tk-sheet">
    <div class="head"><h2>名单更新建议</h2><span class="spacer" /><button class="abtn" onClick=${onClose}><${Icon} name="x" sm /></button></div>
    <div class="muted small">${p.source} · ${p.created?.slice(0, 16).replace("T", " ")} · 规则：近 ${r.months} 个月 ≥${r.min_papers} 篇领域论文，取前 ${r.top_n}</div>
    ${p.enter.length > 0 && html`<div class="label">新进入（${p.enter.length}）</div>
      ${p.enter.map((c) => html`<div class="tk-erow" style=${{ gridTemplateColumns: "26px 1fr auto auto" }}>
        <span class="n">#${p.ranking.find((x) => x.name === c.name)?.rank ?? ""}</span>
        <div><b>${c.name}</b><div class="m">${c.affiliation || ""} · 被引 ${fmtN(c.citations)} · 近期领域论文 ${c.field_papers ?? "—"} 篇</div>
          ${(c.titles || []).slice(0, 2).map((t) => html`<div class="m">· ${t}</div>`)}</div>
        <span class="tk-mv new">新</span><button class="linkbtn" onClick=${() => ex(c)}>排除</button></div>`)}`}
    ${p.leave.length > 0 && html`<div class="label">将移出（${p.leave.length}）</div>
      ${p.leave.map((c) => html`<div class="tk-erow" style=${{ gridTemplateColumns: "26px 1fr auto" }}>
        <span class="n">#${c.rank ?? ""}</span><div><b>${c.name}</b><div class="m">${reasonOf(c)}</div></div>
        <span class="muted small">想留下：在管理名单里「固定」</span></div>`)}`}
    ${!p.enter.length && !p.leave.length && html`<div class="muted pad">没有人进出，只有名次和被引数更新。</div>`}
    <div class="label">新名单</div>
    ${p.ranking.map((c) => html`<div class="tk-erow" style=${{ gridTemplateColumns: "26px 1fr auto auto" }}>
      <span class="n">${c.rank}</span><div><b>${c.name}</b> ${c.pinned && html`<span class="tk-badge">固定</span>`}
        <div class="m">被引 ${fmtN(c.citations)} · 近期领域论文 ${c.field_papers ?? "—"} 篇</div></div>
      ${move(c)}<span /></div>`)}
    ${p.alternates.length > 0 && html`<div class="label">候补</div><div class="muted small">${p.alternates.map((c) => `${c.name}（${fmtN(c.citations)}，${c.field_papers} 篇）`).join("、")}</div>`}
    ${p.skipped.length > 0 && html`<details class="tk-skipped"><summary class="label">被跳过的候选（${p.skipped.length}）</summary>
      ${p.skipped.map((c) => html`<div class="m">${c.name} · ${fmtN(c.citations)} · ${c.reason}</div>`)}</details>`}
    <div class="tk-sheet-actions"><button class="btn" onClick=${() => { R.dismiss(p.id); onClose(); }}>忽略</button>
      <button class="btn primary" onClick=${() => { R.apply(p.id); onClose(); }}>应用到名单</button></div>
  </div></div>`;
}

function ListRules({ R, toast }) {
  const rf = R.rf;
  const [form, setForm] = useState(null);
  const [snap, setSnap] = useState("");
  useEffect(() => { if (rf && !form) setForm({ ...rf.rules, keywords: rf.rules.keywords.join(", "), labels: rf.rules.labels.join("\n") }); }, [rf]);
  if (!rf || !form) return null;
  const save = async () => {
    try {
      await api("/api/track/scholars/rules", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        ...form, keywords: form.keywords.split(/[,，]/).map((x) => x.trim()).filter(Boolean), labels: form.labels.split("\n").map((x) => x.trim()).filter(Boolean) }) });
      toast("更新规则已保存"); R.load();
    } catch (e) { toast(e.message); }
  };
  const importSnap = async () => { try { const r = await api("/api/track/scholars/snapshot", { json: { text: snap } }); toast(`已导入快照：${r.count} 人（${r.date}）`); setSnap(""); R.load(); } catch (e) { toast(e.message); } };
  const unex = async (x) => { await api(`/api/track/scholars/exclude/${x.id}`, { method: "DELETE" }); toast(`已取消排除 ${x.name}`); R.load(); };
  const num = (k, label, w = 56) => html`<label class="tk-rule">${label}<input type="number" min="1" style=${{ width: w + "px" }} value=${form[k]} onInput=${(e) => setForm({ ...form, [k]: e.target.value })} /></label>`;
  return html`
    <div class="label">自动更新</div>
    <div class="tk-rules">
      <label class="tk-rule"><input type="checkbox" checked=${form.auto} onChange=${(e) => setForm({ ...form, auto: e.target.checked })} /> 每 ${num("cadence_days", "", 48)} 天自动重算</label>
      ${num("top_n", "名单人数")}${num("min_papers", "近期论文至少")}${num("months", "近期 = 最近（月）")}
      <label class="tk-rule wide">领域关键词（OpenAlex 检索，逗号分隔）<input value=${form.keywords} onInput=${(e) => setForm({ ...form, keywords: e.target.value })} /></label>
      <label class="tk-rule wide">Scholar 标签检索（每行一个 mauthors）<textarea rows="3" value=${form.labels} onInput=${(e) => setForm({ ...form, labels: e.target.value })} /></label>
      <div class="tk-rule wide muted small">主题要求：${(rf.rules.topic_groups || []).map((g) => g.join(" 或 ")).join(" 且 ")}（每周巡检的主题模型）· 下次自动：${rf.next_auto || "—"}</div>
      <button class="btn" onClick=${save}>保存规则</button>
      ${rf.capture ? html`<button class="btn" onClick=${R.openScholar}>打开 Scholar 窗口（登录）</button>` : html`<span class="muted small">Scholar 窗口只在桌面 App 里可用</span>`}
    </div>
    <div class="label">排除名单（${rf.excluded.length}）</div>
    ${rf.excluded.length === 0 && html`<div class="muted small">没有。确认建议时可以把不想要的人排除。</div>`}
    ${rf.excluded.map((x) => html`<div class="tk-erow" style=${{ gridTemplateColumns: "1fr auto" }}><div><b>${x.name}</b><div class="m">${x.reason || ""}</div></div>
      <button class="linkbtn" onClick=${() => unex(x)}>取消排除</button></div>`)}
    <div class="label">Scholar 快照 ${rf.snapshot ? html`<span class="muted small">（最新：${rf.snapshot.date} · ${rf.snapshot.source} · ${rf.snapshot.count} 人）</span>` : ""}</div>
    <div class="tk-search"><textarea class="tk-kw" rows="3" placeholder="粘贴从 Scholar 整理的排名（每行：12. 姓名 · Scholar id · 65k · 机构 · 备注）或 JSON 快照" value=${snap} onInput=${(e) => setSnap(e.target.value)} />
      <button class="btn" disabled=${!snap.trim()} onClick=${importSnap}>导入</button></div>
    ${rf.history.length > 0 && html`<div class="label">重算记录</div>
      ${rf.history.map((h) => html`<div class="m tk-hist">${h.created?.slice(0, 10)} · ${h.source} · ${{ proposed: "待确认", applied: "已应用", dismissed: "已忽略", superseded: "被新建议替代" }[h.status] || h.status}</div>`)}`}`;
}

function ScholarEditor({ list, onClose, toast, onChanged, R }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const search = async () => { setBusy(true); try { setRes((await api(`/api/track/scholars/search?q=${encodeURIComponent(q)}`)).results); } catch (e) { toast(e.message); } finally { setBusy(false); } };
  const add = async (c) => { try { await api("/api/track/scholars", { json: { name: c.name, openalex: c.openalex, affiliation: c.affiliation } }); toast(`已加入：${c.name}（正在抓取数据）`); setRes(null); setQ(""); onChanged(); } catch (e) { toast(e.message); } };
  const remove = async (s) => { if (!confirm(`从名单移除 ${s.name}？`)) return; await api(`/api/track/scholars/${s.id}`, { method: "DELETE" }); toast(`已移除 ${s.name}`); onChanged(); };
  const pin = async (s) => { try { await api(`/api/track/scholars/${s.id}/pin`, { json: { on: !s.pinned } }); toast(s.pinned ? `取消固定 ${s.name}` : `已固定 ${s.name}：自动更新不会移除`); onChanged(); } catch (e) { toast(e.message); } };
  const have = new Set(list.map((s) => s.openalex));
  return html`<div class="tk-sheet-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}><div class="tk-sheet">
    <div class="head"><h2>学者名单</h2><span class="muted small">名单存在你的 vault 里，两台电脑同步</span><span class="spacer" />
      <button class="abtn" onClick=${onClose}><${Icon} name="x" sm /></button></div>
    <div class="tk-search">
      <input placeholder="按姓名在 OpenAlex 里找学者，如 Yongfeng Zhang" value=${q} onInput=${(e) => setQ(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && q.trim() && search()} />
      <button class="btn primary" disabled=${!q.trim() || busy} onClick=${search}>${busy ? "查询中…" : "查找"}</button></div>
    ${res && res.length === 0 && html`<div class="muted small pad">没找到</div>`}
    ${res && res.map((c) => html`<div class="tk-erow">
      <span class="n">+</span><${Avatar} name=${c.name} size=${30} />
      <div><b>${c.name}</b><div class="m">${c.affiliation || "—"} · 引用 ${fmtN(c.citations)} · h ${c.h_index ?? "—"} · ${c.topics.join(" / ")}</div></div>
      <span class="muted small">${c.openalex}</span>
      ${have.has(c.openalex) ? html`<span class="tag">已在名单</span>` : html`<button class="btn" onClick=${() => add(c)}>加入</button>`}
    </div>`)}
    <div class="label">当前名单（${list.length}）</div>
    ${list.map((s) => html`<div class="tk-erow">
      <span class="n">${s.rank ?? ""}</span><${Avatar} name=${s.name} srcs=${avatarSrcs(s)} size=${30} />
      <div><b>${s.name}</b> ${s.tracked_lab && html`<span class="tk-badge ok">48 lab 内</span>`}
        <div class="m" title=${Object.entries(s.sources || {}).map(([k, v]) => `${k}: ${v}`).join("\n")}>${[s.lab, s.affiliation].filter(Boolean).join(" · ")}</div></div>
      <span class="muted small">${s.scholar_citations ? `Scholar ${fmtN(s.scholar_citations.value)}` : s.topic_citations ? `主题引用 ${fmtN(s.topic_citations)}` : s.added === "user" ? "手动加入" : ""}</span>
      <span><button class=${"tk-pin" + (s.pinned ? " on" : "")} title="固定：自动更新永远不会移除" onClick=${() => pin(s)}>${s.pinned ? "已固定" : "固定"}</button>
        <button class="abtn" title="移除" onClick=${() => remove(s)}><${Icon} name="x" sm /></button></span>
    </div>`)}
    <${LineKeywords} toast=${toast} onChanged=${onChanged} />
    ${R && html`<${ListRules} R=${R} toast=${toast} />`}
  </div></div>`;
}

// Rank movement from the last applied recompute.
const rankMove = (s) => {
  if (!s.rank_changed) return "";
  if (s.prev_rank == null) return html` <span class="tk-mv new" title=${`${s.rank_changed} 新进入`}>新</span>`;
  const d = s.prev_rank - s.rank;
  return d === 0 ? "" : html` <span class=${"tk-mv " + (d > 0 ? "up" : "down")} title=${`${s.rank_changed} 起：#${s.prev_rank} → #${s.rank}`}>${d > 0 ? "▲" : "▼"}${Math.abs(d)}</span>`;
};

// Scholars sharing a lab (lab_id, else lab name): shown as a group, the list keeps both for now.
const labKey = (s) => s.lab_id || (s.lab || "").toLowerCase();

function ScholarCard({ s, on, onClick, mates }) {
  const m = s.metrics || {};
  const years = (s.by_year || []).slice(-8);
  const gs = s.scholar_citations?.value;
  return html`<button class=${"tk-person" + (on ? " on" : "")} onClick=${onClick}>
    <span class="tk-rank">${s.pinned && html`<span title="已固定">固定 · </span>`}#${s.rank ?? "—"}${rankMove(s)}</span>
    <div class="tk-person-top"><${Avatar} name=${s.name} srcs=${avatarSrcs(s)} size=${46} />
      <div class="tk-person-id"><b>${s.name}</b><span>${s.lab || s.inst?.name || s.affiliation || ""}</span>
        <span>${s.affiliation || s.inst?.name || ""}</span></div></div>
    <div class="tk-nums">
      <div class="tk-num" title=${gs ? `${s.scholar_citations.source}${m.citations ? `；OpenAlex ${fmtN(m.citations)}` : ""}` : "OpenAlex"}>
        <b>${fmtN(gs || m.citations)}</b><span>${gs ? "Scholar 引用" : "总引用"}</span></div>
      <div class="tk-num"><b>${m.h_index ?? "—"}</b><span>h 指数</span></div>
      <div class="tk-num"><b>${years.length ? years[years.length - 1].works : m.this_year ?? "—"}</b><span>今年论文</span></div>
    </div>
    <${Sparkline} values=${years.map((y) => y.works)} labels=${years.map((y) => `${y.year} 年`)} w=${118} unit=" 篇" />
    <div class="tk-person-foot">
      ${s.tracked_lab ? html`<span class="tk-badge ok" title=${s.tracked_lab.name}><${Icon} name="check" sm />48 lab 内</span>` : html`<span class="tk-badge">新机构</span>`}
      ${mates?.length > 0 && html`<span class="tk-badge" title=${`同一实验室：${mates.join("、")}`}>同 lab ×${mates.length + 1}</span>`}
      ${m.relevant > 0 && html`<span class="tk-badge rel">相关 ${m.relevant}</span>`}
      ${m.d30 > 0 && html`<span class="tk-badge">30 天 ${m.d30} 篇</span>`}
      ${(s.error || s.age_h > 48) && html`<span class="tk-badge warn" title=${s.error || ""}>旧数据</span>`}
    </div>
  </button>`;
}

function ScholarDetail({ s, onOpenNote, onOnly, filtered }) {
  const years = (s.by_year || []).slice(-8);
  const src = s.sources || {};
  const links = [["主页", s.homepage], ["实验室", s.lab_url], ["OpenAlex", s.openalex && `https://openalex.org/authors/${s.openalex}`],
    ["Google Scholar", s.scholar_id && `https://scholar.google.com/citations?user=${s.scholar_id}`]].filter(([, u]) => u);
  return html`<div class="tk-detail">
    <div><h4>每年发表（篇）</h4><${Columns} data=${years.map((y) => ({ label: `'${String(y.year).slice(2)}`, title: `${y.year} 年`, parts: { v: y.works } }))}
        series=${[{ key: "v", label: "发表", cls: "s1" }]} height=${120} />
      <h4 style=${{ marginTop: "12px" }} title="OpenAlex counts_by_year：各年发表的论文至今累计被引；新论文还在积累，所以近年偏低">各年论文的被引（按发表年）</h4><${Columns} data=${years.map((y) => ({ label: `'${String(y.year).slice(2)}`, title: `${y.year} 年发表`, parts: { v: y.cites } }))}
        series=${[{ key: "v", label: "被引", cls: "s3" }]} height=${120} unit="次" /></div>
    <div><h4>近一年常投</h4><${HBars} items=${s.venues} />
      ${s.recent_llm_rec != null && html`<h4 style=${{ marginTop: "12px" }}>入选依据</h4>
        <div class="tk-chips"><span class="tk-chip">2024–26 LLM/agent 推荐论文<b>${s.recent_llm_rec}</b></span>
          ${s.scholar_citations && html`<span class="tk-chip">Scholar 引用<b>${fmtN(s.scholar_citations.value)}</b></span>`}</div>`}
      <h4 style=${{ marginTop: "12px" }}>近一年主题</h4>
      <div class="tk-chips">${(s.topics || []).map(([t, n]) => html`<span class="tk-chip">${t}<b>${n}</b></span>`)}</div></div>
    <div><h4>近一年常合作</h4>
      <div class="tk-chips">${(s.coauthors || []).map(([n, c]) => html`<span class="tk-chip">${n}<b>${c}</b></span>`)}</div>
      <h4 style=${{ marginTop: "12px" }}>链接</h4>
      <div class="tk-chips">${links.map(([l, u]) => html`<a class="tk-chip" href=${u} target="_blank" rel="noreferrer">${l}</a>`)}
        ${s.tracked_lab && html`<button class="tk-chip" onClick=${() => onOpenNote(s.tracked_lab.path)}>Lab 笔记</button>`}</div></div>
    <div class="full">
      <button class=${"btn" + (filtered ? " primary" : "")} onClick=${onOnly}>${filtered ? "已只看 TA 的论文" : "只看 TA 的论文"}</button>
      <details class="src"><summary>数据：OpenAlex（${s.fetched ? new Date(s.fetched).toLocaleDateString("zh-CN") : "未抓取"}）+ arXiv 最新预印本${s.error ? ` · ${s.error}` : ""} · 每项来源</summary>
        ${Object.entries(src).map(([k, v]) => html`<div><b>${k}</b>　${v}</div>`)}</details>
    </div>
  </div>`;
}

function ScholarsTab({ toast, onOpenPaper, onOpenNote, onAsk, onChanged, version }) {
  const [d, setD] = useState(null);
  const [win, setWin] = useState(prefs.get("track.win", 365));
  const [sid, setSid] = useState(null);
  const [open, setOpen] = useState(null);
  const [topic, setTopic] = useState(null);
  const [line, setLine] = useState("");
  const [month, setMonth] = useState(null);
  const [edit, setEdit] = useState(false);
  const [proposal, setProposal] = useState(false);
  const [busy, setBusy] = useState(false);
  const timer = useRef();
  const listRef = useRef();
  const R = useListRefresh(toast, () => load());
  const load = async (refresh = false) => {
    setBusy(true);
    try { setD(await api(`/api/track/scholars/activity${refresh ? "?refresh=true" : ""}`)); } catch (e) { toast(e.message); } finally { setBusy(false); }
  };
  useEffect(() => { load(); return () => clearTimeout(timer.current); }, [version]);
  useEffect(() => { clearTimeout(timer.current); if (d?.job?.running) timer.current = setTimeout(() => load(), 2500); }, [d]);
  useEffect(() => prefs.set("track.win", win), [win]);

  const byId = useMemo(() => Object.fromEntries((d?.scholars || []).map((s) => [s.id, s])), [d]);
  const labels = d?.topic_labels || {};
  const cut = dayISO(win);
  const matchTL = (p) => (!topic || p.topics.includes(topic)) && (!line || (line === "any" ? p.lines.length : p.lines.includes(line)));
  const papers = useMemo(() => (d?.papers || []).filter((p) => (month ? p.date.startsWith(month) && !p.date_approx : p.date >= cut)
    && (!sid || p.scholars.includes(sid)) && matchTL(p)), [d, cut, sid, topic, line, month]);
  if (!d) return html`<div class="muted pad">载入中…</div>`;
  if (!d.scholars.length) return html`<div class="tk-empty">还没有学者名单。<button class="linkbtn" onClick=${() => setEdit(true)}>添加学者</button>
    ${edit && html`<${ScholarEditor} list=${[]} onClose=${() => setEdit(false)} toast=${toast} onChanged=${() => load()} />`}</div>`;

  const months = d.months.filter((m) => m >= cut.slice(0, 7));
  const heatRows = d.scholars.map((s) => ({ key: s.id, label: s.name, dim: sid && sid !== s.id,
    values: months.map((m) => d.papers.filter((p) => !p.date_approx && p.scholars.includes(s.id) && p.date.startsWith(m) && matchTL(p)).length) }));
  const inWin = (d.papers || []).filter((p) => p.date >= cut && !p.date_approx && (!sid || p.scholars.includes(sid)) && matchTL(p));
  const bars = months.map((m) => { const ps = inWin.filter((p) => p.date.startsWith(m));
    return { label: monthLabel(m), title: m, parts: { rel: ps.filter((p) => p.lines.length).length, other: ps.filter((p) => !p.lines.length).length } }; });
  const topicCounts = Object.entries((d.papers || []).filter((p) => (month ? p.date.startsWith(month) : p.date >= cut) && (!sid || p.scholars.includes(sid))
    && (!line || (line === "any" ? p.lines.length : p.lines.includes(line))))
    .reduce((acc, p) => { p.topics.forEach((t) => { acc[t] = (acc[t] || 0) + 1; }); return acc; }, {}))
    .sort((a, b) => b[1] - a[1]).slice(0, 8).map(([t, n]) => [labels[t] || t, n, t]);
  const t = d.totals;
  const job = d.job || {};
  const openS = open && byId[open];
  const pickScholar = (id) => { setOpen(open === id ? null : id); };
  const only = (id) => { setSid(sid === id ? null : id); setTimeout(() => listRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50); };
  const clear = () => { setSid(null); setTopic(null); setLine(""); setMonth(null); };
  const reason = (p) => `追踪 · 领域 Top 学者：${p.scholars.map((x) => byId[x]?.name).filter(Boolean).join("、")}${p.lines.length ? ` · 触及 ${p.lines.join("、")}` : ""}`;

  return html`
    <div class="tk-kpis">
      <button class="tk-kpi" onClick=${() => setEdit(true)}><b>${d.scholars.length}</b><span>跟踪学者</span><em>${d.scholars.filter((s) => s.tracked_lab).length} 位在 48 lab 内 · 管理名单</em></button>
      <button class=${"tk-kpi" + (win === 30 && !line ? " on" : "")} onClick=${() => { setWin(30); setMonth(null); }}><b>${t.d30}</b><span>近 30 天新论文</span><em>去重后，含 arXiv</em></button>
      <button class=${"tk-kpi" + (win === 90 && !line ? " on" : "")} onClick=${() => { setWin(90); setMonth(null); }}><b>${t.d90}</b><span>近 90 天</span><em>${d.scholars.filter((s) => s.metrics?.d90).length} 位学者有产出</em></button>
      <button class=${"tk-kpi" + (win === 365 && !line ? " on" : "")} onClick=${() => { setWin(365); setMonth(null); }}><b>${t.d365}</b><span>近一年</span><em>OpenAlex ${d.as_of}</em></button>
      <button class=${"tk-kpi" + (line === "any" ? " on" : "")} onClick=${() => setLine(line === "any" ? "" : "any")}><b>${t.relevant}<small>篇</small></b><span>触及我的课题</span><em>近 90 天 ${t.relevant90} 篇 · ${d.lines.join(" / ")}</em></button>
    </div>
    ${job.running && html`<div class="tk-banner"><${Icon} name="refresh" sm /><span>正在从 OpenAlex / arXiv 抓取 ${job.current || ""}（${job.done}/${job.total}）</span>
      <span class="tk-prog" style=${{ flex: 1 }}><span class="tk-prog-bar"><i style=${{ width: (job.total ? (job.done / job.total) * 100 : 5) + "%" }} /></span></span></div>`}
    ${job.error && !job.running && html`<div class="tk-banner bad"><span>上次抓取出错：${job.error}（显示的是缓存）</span></div>`}

    <${RefreshBanner} R=${R} onOpen=${() => setProposal(true)} />
    <div class="tk-section-title"><h2>领域 Top ${d.scholars.length} 学者</h2>
      <span class="muted">${d.scholars[0]?.rank_by ? `按 ${d.scholars[0].rank_by}` : "领域排名"} · 点卡片看详情</span><span class="spacer" />
      <button class="linkbtn" disabled=${busy || job.running} onClick=${() => load(true)}>${job.running ? "抓取中…" : "刷新数据"}</button>
      <button class="linkbtn" disabled=${R.rf?.job?.running} onClick=${R.start} title="重算名单：读 Scholar 排名、核对近期论文，结果作为建议给你确认">${R.rf?.job?.running ? "重算中…" : "立即更新"}</button>
      <button class="linkbtn" onClick=${() => setEdit(true)}>管理名单</button></div>
    <div class="tk-people">${d.scholars.map((s) => html`<${ScholarCard} key=${s.id} s=${s} on=${open === s.id} onClick=${() => pickScholar(s.id)}
      mates=${d.scholars.filter((x) => x.id !== s.id && labKey(x) && labKey(x) === labKey(s)).map((x) => x.name)} />`)}</div>
    ${openS && html`<div style=${{ marginTop: "12px" }}><${ScholarDetail} s=${openS} onOpenNote=${onOpenNote} filtered=${sid === openS.id} onOnly=${() => only(openS.id)} /></div>`}

    <div class="tk-section-title" ref=${listRef}><h2>动态</h2><span class="muted">筛选同时作用于下面所有图和卡片</span></div>
    <div class="tk-filters">
      <div class="seg">${WINDOWS.map(([k, l]) => html`<button class=${win === k && !month ? "on" : ""} onClick=${() => { setWin(k); setMonth(null); }}>${l}</button>`)}</div>
      <select value=${sid || ""} onChange=${(e) => setSid(e.target.value || null)}>
        <option value="">全部学者</option>${d.scholars.map((s) => html`<option value=${s.id}>${s.name}</option>`)}</select>
      <select value=${topic || ""} onChange=${(e) => setTopic(e.target.value || null)}>
        <option value="">全部主题</option>${Object.entries(labels).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
      <div class="seg">${[["", "全部"], ["any", "相关"], ...d.lines.map((l) => [l, l])].map(([k, l]) => html`<button class=${line === k ? "on" : ""} onClick=${() => setLine(k)}>${l}</button>`)}</div>
      ${month && html`<button class="tk-chip on" onClick=${() => setMonth(null)}>${month} ×</button>`}
      ${(sid || topic || line || month) && html`<button class="linkbtn" onClick=${clear}>清除筛选</button>`}
      <span class="count">${papers.length} 篇</span>
    </div>
    <div class="tk-grid2">
      <div class=${"tk-card" + (busy ? " loading" : "")}><h3>谁在发、什么时候发</h3><div class="sub">学者 × 月份，颜色越深论文越多；点格子看那个月</div>
        <${Heatmap} rows=${heatRows} cols=${months.map(monthLabel)} colTitles=${months} active=${sid}
          onRow=${(r) => setSid(sid === r.key ? null : r.key)} onCell=${(r, j) => { setSid(r.key); setMonth(months[j]); }} />
        <h3 style=${{ marginTop: "14px" }}>主题分布</h3><div class="sub">和每周巡检同一套主题模型；点击筛选</div>
        <${HBars} items=${topicCounts.map(([l, n]) => [l, n])} picked=${topic ? labels[topic] : null}
          onPick=${(l) => { const k = topicCounts.find((x) => x[0] === l)?.[2]; setTopic(topic === k ? null : k); }} /></div>
      <div class=${"tk-card" + (busy ? " loading" : "")}><h3>每月产出</h3><div class="sub">去重后的论文数；橙色 = 触及我的课题${t.year_only ? `；另有 ${t.year_only} 篇 OpenAlex 只给了年份，不计入月份` : ""}</div>
        <${Columns} data=${bars} series=${[{ key: "rel", label: "触及我的课题", cls: "s2" }, { key: "other", label: "其他", cls: "s1" }]}
          picked=${month ? months.indexOf(month) : null} onPick=${(i) => setMonth(month === months[i] ? null : months[i])} height=${170} />
        <h3 style=${{ marginTop: "14px" }}>最近触及我的课题</h3><div class="sub">按研究线关键词命中；点开看原因</div>
        <div class="tk-rel">${d.papers.filter((p) => p.lines.length && (!sid || p.scholars.includes(sid))).slice(0, 6).map((p) => html`
          <button class="tk-rel-row" title=${Object.entries(p.line_hits || {}).map(([l, h]) => `${l}：${h.join("、")}`).join("\n")}
            onClick=${() => { setLine("any"); setMonth(null); setTimeout(() => listRef.current?.scrollIntoView({ behavior: "smooth" }), 50); }}>
            <span class="t">${p.title}</span><span class="m">${p.date_approx ? p.date.slice(0, 4) : p.date.slice(5)} · ${p.lines.join(" / ")}</span></button>`)}
          ${!d.papers.some((p) => p.lines.length) && html`<div class="muted small">近一年没有命中的论文</div>`}</div></div>
    </div>
    <${PaperGrid} papers=${papers} render=${(p, ready) => html`<${PaperCard} thumbReady=${ready} key=${p.arxiv || p.oa || p.title} p=${p} labels=${labels} reason=${reason(p)}
      faces=${p.scholars.slice(0, 4).map((x) => byId[x] && html`<span title=${byId[x].name}><${Avatar} name=${byId[x].name} srcs=${avatarSrcs(byId[x])} size=${20} /></span>`)}
      toast=${toast} onOpenPaper=${onOpenPaper} onAsk=${onAsk} onAdded=${onChanged} />`} />
    ${edit && html`<${ScholarEditor} list=${d.scholars} onClose=${() => setEdit(false)} toast=${toast} onChanged=${() => load()} R=${R} />`}
    ${proposal && html`<${ProposalSheet} R=${R} list=${d.scholars} onClose=${() => setProposal(false)} toast=${toast} />`}`;
}

// ---------------- weekly radar ----------------

function Radar({ toast, onOpenPaper, onOpenNote, onAsk, onChanged, runsVersion }) {
  const [runs, setRuns] = useState([]);
  const [cur, setCur] = useState(null);
  const [run, setRun] = useState(null);
  const [tier, setTier] = useState(null);
  const [topic, setTopic] = useState(null);
  const [full, setFull] = useState(false);
  const [labels, setLabels] = useState({});
  useEffect(() => { api("/api/track/runs").then((r) => { setRuns(r.runs); if (r.runs[0]) setCur(r.runs[0].run); }); }, [runsVersion]);
  useEffect(() => { if (cur) { setRun(null); setTopic(null); setTier(null); api(`/api/track/runs/${cur}`).then(setRun); } }, [cur]);
  useEffect(() => { api("/api/track/scholars/activity").then((d) => setLabels(d.topic_labels || {})).catch(() => {}); }, []);
  if (!runs.length) return html`<div class="tk-empty">还没有巡检记录。点右上角「立即巡检」跑第一次。</div>`;
  const ordered = [...runs].reverse();
  const t = run?.stats?.by_tier || {};
  const papers = run ? run.papers.filter((p) => (!tier || p.tier === tier) && (!topic || p.topics.includes(topic))) : [];
  const topicCounts = run ? Object.entries(run.papers.filter((p) => !tier || p.tier === tier).reduce((a, p) => { p.topics.forEach((x) => { a[x] = (a[x] || 0) + 1; }); return a; }, {}))
    .sort((a, b) => b[1] - a[1]).slice(0, 8) : [];
  const labCounts = run ? Object.entries(run.papers.reduce((a, p) => { p.labs.forEach((x) => { a[x] = (a[x] || 0) + 1; }); return a; }, {})).sort((a, b) => b[1] - a[1]).slice(0, 8) : [];
  const tile = (k, n, label) => html`<button class=${"tk-kpi" + (tier === k ? " on" : "")} onClick=${() => setTier(tier === k ? null : k)}><b>${n}</b><span>${label}</span></button>`;
  return html`
    <div class="tk-grid2">
      <div class="tk-card"><h3>每次巡检保留的论文</h3><div class="sub">按 Tier 堆叠；点柱子切换到那一次</div>
        <${Columns} data=${ordered.map((r) => ({ label: r.run.slice(5), title: `${r.run}（since ${r.since}）`, parts: r.stats.by_tier || {} }))}
          series=${[{ key: "A", label: "A 必读", cls: "tA" }, { key: "B", label: "B 相关", cls: "tB" }, { key: "C", label: "C 领域内", cls: "tC" }]}
          picked=${ordered.findIndex((r) => r.run === cur)} onPick=${(i) => setCur(ordered[i].run)} height=${160} /></div>
      <div class="tk-card"><h3>${cur} 这一次</h3><div class="sub">${run ? `窗口 ${run.since} 起 · 抓取 ${run.stats.input ?? "—"} 篇，未过主题门槛 ${run.stats.gate_fail ?? "—"}` : "载入中…"}</div>
        ${run && html`<div class="tk-kpis" style=${{ gridTemplateColumns: "repeat(3, 1fr)", marginBottom: "10px" }}>
          ${tile("A", t.A || 0, "A 必读")}${tile("B", t.B || 0, "B 相关")}${tile("C", t.C || 0, "C 领域内")}</div>
          <h3>主题</h3><${HBars} items=${topicCounts.map(([k, n]) => [labels[k] || k, n])} picked=${topic ? labels[topic] || topic : null}
            onPick=${(l) => { const k = topicCounts.find(([x]) => (labels[x] || x) === l)?.[0]; setTopic(topic === k ? null : k); }} />`}</div>
    </div>
    ${run?.analysis && html`<div class="tk-card" style=${{ marginBottom: "14px" }}>
      <div class="card-head"><h2>本周分析</h2><span>
        <button class="linkbtn" onClick=${() => setFull(!full)}>${full ? "收起" : "展开全文"}</button>
        ${run.report_url && html` <a class="chip" href=${run.report_url}><${Icon} name="external" sm />完整周报</a>`}</span></div>
      <div class=${"md tk-analysis" + (full ? " open" : "")} onClick=${async (e) => { const w = e.target.closest(".wikilink"); if (!w) return;
        try { const r = await api(`/api/note/find?name=${encodeURIComponent(w.dataset.target)}`); r.paper ? onOpenPaper(r.paper) : onOpenNote(r.path); } catch (x) { toast(x.message); } }}
        dangerouslySetInnerHTML=${{ __html: renderMarkdown(run.analysis) }} /></div>`}
    ${run && !run.analysis.trim().replace(/_Analysis pending\._/, "") && html`<div class="tk-banner"><span>这次巡检还没有分析。</span><span class="spacer" />
      <button class="chip" onClick=${() => onAsk(`按 research_tracker/WEEKLY_RUNBOOK.md 第 3 步审阅巡检 ${run.run}（since ${run.since}），写 run note 的 ANALYSIS 区块。先读 vault 的 AGENTS.md。`, "审阅本周巡检")}><${Icon} name="sparkle" sm />交给助手写分析</button></div>`}
    ${labCounts.length > 0 && html`<div class="tk-filters"><span class="muted small">这次最活跃：</span>${labCounts.map(([l, n]) => html`<span class="tk-chip">${l}<b>${n}</b></span>`)}
      ${(tier || topic) && html`<button class="linkbtn" onClick=${() => { setTier(null); setTopic(null); }}>清除筛选</button>`}<span class="count">${papers.length} 篇</span></div>`}
    ${run && html`<${PaperGrid} papers=${papers} render=${(p, ready) => html`<${PaperCard} thumbReady=${ready} key=${p.arxiv} p=${{ ...p, lines: [] }} labels=${labels}
      reason=${`Lab Tracker ${run.run} · Tier ${p.tier}${p.labs?.length ? " · " + p.labs.join(", ") : ""}`} lines=${[]}
      extra=${[...(p.labs || []).map((l) => html`<span class="tk-chip">${l}</span>`), ...(p.follows || []).map((f) => html`<span class="tk-chip on">${f.replace(/^person:/, "学者 ").replace(/^keyword:/, "关键词 ")}</span>`)]}
      toast=${toast} onOpenPaper=${onOpenPaper} onAsk=${onAsk} onAdded=${onChanged} />`} />`}`;
}

// ---------------- follows ----------------

function Follows({ toast, onOpenPaper, onAsk, version, onChanged }) {
  const [feed, setFeed] = useState(null);
  const [link, setLink] = useState(null);
  const [kind, setKind] = useState("keyword");
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [pick, setPick] = useState(null);
  const load = async (refresh = false) => { setLoading(true); try { const r = await api(`/api/track/follows/feed?refresh=${refresh}`); setFeed(r.feed); setLink(r.linkage); } finally { setLoading(false); } };
  useEffect(() => { load(); }, [version]);
  const add = async () => { await api("/api/track/follows", { json: { kind, value } }); setValue(""); toast("已关注，正在查 arXiv…"); load(); onChanged?.(); };
  const remove = async (f, e) => { e.stopPropagation(); await api(`/api/track/follows/${f.id}`, { method: "DELETE" }); if (pick === f.id) setPick(null); load(); onChanged?.(); };
  const weeks = [...Array(5)].map((_, i) => dayISO(35 - i * 7));
  const papers = (feed || []).filter((f) => !pick || f.id === pick).flatMap((f) => f.papers.map((p) => ({ ...p, _f: f })))
    .filter((p, i, a) => a.findIndex((q) => q.arxiv === p.arxiv) === i).sort((a, b) => b.published.localeCompare(a.published));
  return html`
    ${link && html`<div class=${"tk-banner " + (link.reads_follows ? "ok" : "bad")}>
      ${link.reads_follows ? html`<${Icon} name="check" sm /><span>每周巡检会一起抓取这些关注（tracker 里有 ${link.count} 条${link.in_sync ? "" : "，和这里不一致：下次增删关注或巡检时同步"}）</span>`
        : html`<span>每周巡检还读不到这里的关注（tracker.py 未接入 follows.json）</span>`}
      <span class="spacer" /><button class="linkbtn" disabled=${loading} onClick=${() => load(true)}>${loading ? "查询中…" : "刷新 arXiv"}</button></div>`}
    <div class="tk-addfollow">
      <div class="seg"><button class=${kind === "keyword" ? "on" : ""} onClick=${() => setKind("keyword")}>关键词</button>
        <button class=${kind === "person" ? "on" : ""} onClick=${() => setKind("person")}>学者</button></div>
      <input placeholder=${kind === "keyword" ? "如：graph neural network recommendation（多个词同时出现；\"引号\" 表示整词组）" : "如：Julian McAuley"} value=${value}
        onInput=${(e) => setValue(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && value.trim() && add()} />
      <button class="btn primary" disabled=${!value.trim()} onClick=${add}>关注</button>
    </div>
    ${feed === null && html`<div class="muted pad">查询中…</div>`}
    ${feed && feed.length === 0 && html`<div class="tk-empty">还没有关注。加几个关键词或学者：这里列出它们最近 30 天的 arXiv 新论文，每周巡检也会一起抓。</div>`}
    ${feed?.length > 0 && html`<div class="tk-follows">${feed.map((f) => {
      const counts = weeks.map((w, i) => f.papers.filter((p) => p.published >= w && (i === 4 || p.published < weeks[i + 1])).length);
      return html`<button class=${"tk-follow" + (pick === f.id ? " on" : "")} onClick=${() => setPick(pick === f.id ? null : f.id)}>
        <span class="abtn x" title="取消关注" onClick=${(e) => remove(f, e)}><${Icon} name="x" sm /></span>
        <span class="tk-badge">${f.kind === "person" ? "学者" : "关键词"}</span><b class="v" title=${f.value}>${f.value}</b>
        <span class="n">${f.papers.length}<small>篇 · 30 天</small></span>
        <${Sparkline} values=${counts} labels=${weeks.map((w) => `${w.slice(5)} 起`)} w=${100} h=${24} unit=" 篇" />
        ${f.error && html`<span class="err">${f.error}</span>`}
      </button>`; })}</div>
    <${PaperGrid} papers=${papers} render=${(p, ready) => html`<${PaperCard} thumbReady=${ready} key=${p.arxiv} p=${{ ...p, date: p.published, venue: "arXiv", abstract: p.summary }}
      reason=${`关注${p._f.kind === "person" ? "学者" : "关键词"}：${p._f.value}`} lines=${[]}
      extra=${html`<span class="tk-chip on">${p._f.value}</span>`} toast=${toast} onOpenPaper=${onOpenPaper} onAsk=${onAsk} onAdded=${onChanged} />`} />`}`;
}

// ---------------- labs ----------------

function Labs({ onOpenNote, toast, onChanged }) {
  const [labs, setLabs] = useState(null);
  const [mx, setMx] = useState(null);
  const [top, setTop] = useState([]);
  const [sector, setSector] = useState("");
  const [active, setActive] = useState(true);
  const [track, setTrack] = useState("");
  const [open, setOpen] = useState(null);
  const [allRows, setAllRows] = useState(false);
  useEffect(() => {
    api("/api/track/labs").then((r) => setLabs(r.labs));
    api("/api/track/labs/matrix").then(setMx);
    api("/api/track/scholars").then((r) => setTop(r.scholars)).catch(() => {});
  }, []);
  if (!labs || !mx) return html`<div class="muted pad">载入中…</div>`;
  const runs = mx.runs.map((r) => r.run);
  const series = (l) => runs.map((r) => mx.counts[l.lab_id]?.[r] || 0);
  const topOf = (l) => top.filter((s) => s.lab_id && s.lab_id === l.lab_id);
  const tracks = [...new Set(labs.flatMap((l) => l.tracks))].sort();
  const tname = (t) => (mx.track_names?.[t] ? `${t} ${mx.track_names[t]}` : t);
  const shown = labs.filter((l) => (!sector || l.sector === sector) && (!active || l.count > 0) && (!track || l.tracks.includes(track)));
  const totalPapers = Object.values(mx.counts).reduce((s, c) => s + Object.values(c).reduce((a, b) => a + b, 0), 0);
  const last = runs[runs.length - 1];
  const o = open && labs.find((l) => l.lab_id === open);
  const sum = (ls) => ls.reduce((a, l) => a + series(l).reduce((x, y) => x + y, 0), 0);
  const trackCounts = tracks.map((t) => [tname(t), sum(labs.filter((l) => l.tracks.includes(t)))]).sort((a, b) => b[1] - a[1]);
  return html`
    <div class="tk-kpis">
      <div class="tk-kpi"><b>${labs.length}</b><span>跟踪实验室</span><em>${labs.filter((l) => l.sector === "industry").length} 个业界</em></div>
      <div class="tk-kpi"><b>${labs.filter((l) => l.count > 0).length}</b><span>有动态的实验室</span><em>累计 ${runs.length} 次巡检</em></div>
      <div class="tk-kpi"><b>${totalPapers}</b><span>累计归属论文</span><em>一篇可属多个 lab</em></div>
      <div class="tk-kpi"><b>${Object.values(mx.counts).filter((c) => c[last]).length}</b><span>最近一次活跃</span><em>${last || "—"}</em></div>
      <div class="tk-kpi"><b>${labs.filter((l) => topOf(l).length).length}</b><span>含 Top 学者</span><em>和学者名单重合</em></div>
    </div>
    <div class="tk-filters">
      <div class="seg">${[["", "全部"], ["academia", "学界"], ["industry", "业界"]].map(([k, l]) => html`<button class=${sector === k ? "on" : ""} onClick=${() => setSector(k)}>${l}</button>`)}</div>
      <select value=${track} onChange=${(e) => setTrack(e.target.value)}><option value="">全部方向</option>${tracks.map((t) => html`<option value=${t}>${tname(t)}</option>`)}</select>
      <label class="muted small"><input type="checkbox" checked=${active} onChange=${(e) => setActive(e.target.checked)} /> 只看有动态的</label>
      <span class="count">${shown.length} 个</span>
    </div>
    <div class="tk-grid2">
      <div class="tk-card"><h3>跨实验室动态</h3><div class="sub">实验室 × 每次巡检保留的论文数；点行展开那个实验室</div>
        <${Heatmap} rows=${shown.slice(0, allRows ? 99 : 15).map((l) => ({ key: l.lab_id, label: l.name, values: series(l) }))}
          cols=${runs.map((r) => r.slice(5))} colTitles=${runs} active=${open} labelW=${200}
          onRow=${(r) => setOpen(open === r.key ? null : r.key)} onCell=${(r) => setOpen(r.key)} />
        ${shown.length > 15 && html`<button class="linkbtn" onClick=${() => setAllRows(!allRows)}>${allRows ? "只看最活跃的 15 个" : `显示全部 ${shown.length} 个`}</button>`}</div>
      <div class="tk-card"><h3>各方向活跃度</h3><div class="sub">累计论文数，按实验室所属 Scholar 方向（点击筛选）</div>
        <${HBars} items=${trackCounts} picked=${track ? tname(track) : null} onPick=${(t) => { const k = t.split(" ")[0]; setTrack(track === k ? "" : k); }} />
        <h3 style=${{ marginTop: "12px" }}>学界 / 业界</h3>
        <${HBars} items=${[["学界", sum(labs.filter((l) => l.sector !== "industry"))], ["业界", sum(labs.filter((l) => l.sector === "industry"))]]}
          picked=${sector === "academia" ? "学界" : sector === "industry" ? "业界" : null}
          onPick=${(x) => { const k = x === "学界" ? "academia" : "industry"; setSector(sector === k ? "" : k); }} /></div>
    </div>
    ${o && html`<div class="tk-card" style=${{ marginBottom: "14px", borderColor: "var(--accent)" }}>
      <div class="card-head"><h2>${o.name}</h2><span><button class="chip" onClick=${() => onOpenNote(o.path)}><${Icon} name="note" sm />Lab 笔记</button>
        <button class="abtn" onClick=${() => setOpen(null)}><${Icon} name="x" sm /></button></span></div>
      <div class="muted small">${[o.institution, o.country, o.sector === "industry" ? "业界" : "学界"].filter(Boolean).join(" · ")} · 成员：${o.people.join("、")}</div>
      ${topOf(o).length > 0 && html`<div class="tk-chips" style=${{ margin: "8px 0" }}>${topOf(o).map((s) => html`<span class="tk-badge ok">Top #${s.rank} ${s.name}</span>`)}</div>`}
      ${o.recent.map((p) => html`<${LabPaperRow} p=${p} lab=${o.name} toast=${toast} onChanged=${onChanged} />`)}
      ${!o.recent.length && html`<div class="muted small pad">巡检里还没有这个实验室的论文</div>`}</div>`}
    <div class="tk-labs">${shown.map((l) => {
      const s = series(l), tt = mx.tiers[l.lab_id] || {};
      const tops = topOf(l);
      return html`<button class=${"tk-lab" + (open === l.lab_id ? " on" : "") + (l.count ? "" : " quiet")} onClick=${() => setOpen(open === l.lab_id ? null : l.lab_id)}>
        <div class="tk-lab-top"><${Avatar} name=${l.name.replace(/\(.*\)/, "").trim()} square size=${36} />
          <div class="tk-person-id"><b title=${l.name}>${l.name}</b><span>${[l.institution, l.country].filter(Boolean).join(" · ")}</span></div>
          <div class="tk-lab-n"><b>${l.count}</b><span>篇</span></div></div>
        <div class="tk-lab-mid"><${Sparkline} values=${s} labels=${runs.map((r) => r.slice(5))} w=${96} h=${24} unit=" 篇" /><${TierStrip} tiers=${tt} w=${70} /></div>
        <div class="tk-chips">${tops.map((x) => html`<span class="tk-badge ok" title=${x.name}>Top #${x.rank}</span>`)}${l.tracks.map((t) => html`<span class="tk-chip" title=${tname(t)}>${t}</span>`)}</div>
        ${l.recent[0] && html`<div class="tk-lab-last">${l.recent[0].published} · ${l.recent[0].title}</div>`}
      </button>`; })}</div>`;
}

function LabPaperRow({ p, lab, toast, onChanged }) {
  const [state, setState] = useState(null);
  const id = String(p.arxiv || "").replace(/v\d+$/, "");
  const add = async () => { try { const r = await api("/api/reading", { json: { arxiv: id, reason: `Lab Tracker ${p.run} · ${lab}` } }); setState(r.item.status); toast("已加入收件箱"); onChanged?.(); } catch (e) { toast(e.message); } };
  return html`<div class="prow trow">${p.tier && html`<span class=${"tk-tier " + p.tier}>${p.tier}</span>`}
    <div class="p-main"><div class="t-title">${p.title}</div><div class="muted small">${p.published} · ${p.run}</div></div>
    <a class="abtn" href=${`https://arxiv.org/abs/${id}`} target="_blank" rel="noreferrer"><${Icon} name="external" sm /></a>
    ${state ? html`<span class="tag">${LISTED[state] || state}</span>` : html`<button class="tk-add" onClick=${add}><${Icon} name="plus" sm />清单</button>`}</div>`;
}

// ---------------- capture + page ----------------

function Capture({ onAsk, toast, onChanged }) {
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const go = async () => {
    const r = await api("/api/track/capture", { json: { text, reason } }).catch((e) => ({ error: e.message }));
    if (r.error) return toast(r.error);
    if (r.unresolved) onAsk(`帮我把这篇论文加入阅读清单：${text}${reason ? `，理由：${reason}` : ""}。先找到它的 arXiv 版本（找不到就告诉我，不要猜），然后用 reading_push 推进收件箱。`, "捕捉论文");
    else toast(r.result === "added" ? "已加入收件箱" : r.result);
    setText(""); setReason(""); onChanged?.();
  };
  return html`<div class="tk-capture" title="arXiv 链接或编号直接进收件箱；OpenReview / DOI / 标题交给助手找">
    <${Icon} name="inbox" sm />
    <input placeholder="捕捉：粘贴论文链接、arXiv 编号或标题" value=${text} onInput=${(e) => setText(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && text.trim() && go()} />
    <input placeholder="为什么要读（一句话）" value=${reason} onInput=${(e) => setReason(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && text.trim() && go()} />
    <button class="btn primary" disabled=${!text.trim()} onClick=${go}>进收件箱</button>
  </div>`;
}

const TABS = [["scholars", "学者动态"], ["radar", "本周雷达"], ["follows", "关注"], ["labs", "实验室"]];

export function TrackPage({ version, onAsk, toast, onOpenPaper, onOpenNote, onChanged }) {
  const [tab, setTab] = useState(prefs.get("track.tab", "scholars"));
  const [runsVersion, setRunsVersion] = useState(0);
  useEffect(() => prefs.set("track.tab", tab), [tab]);
  const common = { toast, onOpenPaper, onOpenNote, onAsk, onChanged, version };
  return html`<div class="page tk"><div class="page-inner wide">
    <div class="tk-head"><h1>追踪</h1>
      <div class="seg">${TABS.map(([k, l]) => html`<button class=${tab === k ? "on" : ""} onClick=${() => setTab(k)}>${l}</button>`)}</div>
      <span class="spacer" />
      <${Patrol} toast=${toast} onAsk=${onAsk} onDone=${() => { setRunsVersion((v) => v + 1); onChanged?.(); }} />
    </div>
    <${Capture} onAsk=${onAsk} toast=${toast} onChanged=${onChanged} />
    ${tab === "scholars" && html`<${ScholarsTab} ...${common} />`}
    ${tab === "radar" && html`<${Radar} ...${common} runsVersion=${runsVersion} />`}
    ${tab === "follows" && html`<${Follows} ...${common} />`}
    ${tab === "labs" && html`<${Labs} ...${common} />`}
  </div></div>`;
}

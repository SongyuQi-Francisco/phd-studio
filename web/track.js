// Tracking: top scholars of the field (cards, heatmap, monthly output, paper cards), the weekly lab radar,
// live follows, the 48 tracked labs, a capture box, and 「立即巡检」 (runs research_tracker in the background).
// i18n: done
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, api, renderMarkdown, prefs } from "/lib.js";
import { t, tx, lang, formatDate } from "/i18n.js";
import { Avatar, Columns, fmtN, HBars, Heatmap, Sparkline, TierStrip } from "/track-charts.js";

const LISTED = ["queued", "reading", "done", "snoozed", "dismissed"];
// A paper that is already on the reading list: its status, in the reading list's own words.
const listed = (s) => (s === "inbox" ? t("track.listed.inbox") : LISTED.includes(s) ? t("common.status." + s) : s); // i18n-keys: common.status.{queued,reading,done,snoozed,dismissed}
const WINDOWS = [30, 90, 365];
const windowLabel = (k) => t("track.window." + k); // i18n-keys: track.window.{30,90,365}
const dayISO = (n) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
const monthLabel = (m) => formatDate(new Date(2000, +m.slice(5) - 1, 1), { month: "short" });
const names = (list) => list.join(t("track.sep"));
const papersOf = (n) => t("common.unit.papers", { n });
// Links that come from outside (OpenAlex, Crossref, OpenReview) only open as http(s): a javascript: URL in an href would run in the app.
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "");
const SRC = { arxiv: "arXiv", openreview: "OpenReview", acl: "ACL Anthology" };
// "ICLR 2027 Conference Submission" / "NeurIPS 2025 spotlight" -> the short name for the card's corner
const venueShort = (v) => { const s = String(v || "").replace(/\s+(?:Conference\s+|Workshop\s+)?Submission$/i, "").replace(/\s+(?:spotlight|oral|poster|regular|notable.*)$/i, ""); return s.length > 24 ? s.slice(0, 23) + "…" : s; };
const dateLabel = (p, d) => (p.month_only ? formatDate(new Date(+d.slice(0, 4), +d.slice(5, 7) - 1, 1), { year: "numeric", month: "long" }) : p.date_approx ? t("track.year", { y: d.slice(0, 4) }) : ago(d));
const ago = (d) => { const n = Math.round((Date.now() - new Date(d)) / 864e5); return n <= 0 ? t("common.today") : n < 31 ? t("common.days_ago", { n }) : d; };
const COMMAS = /[,，]/; // i18n-ignore: keywords may be typed with full-width commas
const KW_SEP = /[,，\n]/; // i18n-ignore
// What the reading list answered to a push (the same English text the command line prints), in the interface language.
const pushResult = (r) => (r === "added" ? t("track.added_inbox") : /^already listed/.test(r) ? t("track.already_listed") : /^already read/.test(r) ? t("track.already_read")
  : /^dismissed earlier/.test(r) ? t("track.dismissed_earlier") : r);

// A reason that is stored in a shared vault file (the reading list, the exclusion list) is worded by the server in the
// vault language, not in this Mac's interface language: ask for it by key (a string passes through as it is).
const vaultReason = async (reason) => (typeof reason === "string" ? reason : (await api("/api/track/reason", { json: reason })).text);

// Prompts for the assistant, not interface text: it answers in the answer language.
const PROMPT_ADD = (p, reason) => `帮我把这篇论文加入阅读清单：「${p.title}」${p.doi ? `（${p.doi}）` : ""}，理由：${reason}。先找到它的 arXiv 版本（找不到就告诉我，不要猜），然后用 reading_push 推进收件箱${(p.lines || []).length ? `，lines 设为 ${p.lines.join("、")}` : ""}。`; // i18n-ignore
const PROMPT_REVIEW_FRESH = (run, since) => `按 research_tracker/WEEKLY_RUNBOOK.md 第 3 步审阅刚跑完的巡检 ${run}（since ${since}）：检查 tier-A 的同名误匹配，写 run note 的 ANALYSIS 区块，只给真正的必读建 paper note。先读 vault 的 AGENTS.md。`; // i18n-ignore
const PROMPT_REVIEW = (run, since) => `按 research_tracker/WEEKLY_RUNBOOK.md 第 3 步审阅巡检 ${run}（since ${since}），写 run note 的 ANALYSIS 区块。先读 vault 的 AGENTS.md。`; // i18n-ignore
const PROMPT_CAPTURE = (title, reason) => `帮我把这篇论文加入阅读清单：${title}${reason ? `，理由：${reason}` : ""}。先找到它的 arXiv 版本（找不到就告诉我，不要猜），然后用 reading_push 推进收件箱。`; // i18n-ignore

// ---------------- paper card (shared by every tab) ----------------

function PaperCard({ p, reason, lines, faces, labels, toast, onOpenPaper, onAsk, onAdded, extra, thumbReady }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState(p.listed);
  const [thumb, setThumb] = useState(true);
  const date = p.date || p.published;
  const rel = (p.lines || []).length > 0;
  const add = async (e) => {
    e.stopPropagation();
    const doi = /^https?:\/\/(?:dx\.)?doi\.org\/(10\..+)$/i.exec(p.doi || "")?.[1];
    try {
      const why = await vaultReason(reason);
      if (!p.arxiv && (p.ext || doi)) {  // OpenReview / ACL Anthology / DOI paper: listed by its own key
        const r = await api("/api/track/capture/pick", { json: { candidate: { ...(p.ext ? { key: p.ext } : { doi }), title: p.title, url: p.url || p.doi, venue: p.venue_full || p.venue }, reason: why, lines: lines ?? p.lines ?? [] } });
        setState(r.item.status); toast(pushResult(r.result)); onAdded?.();
      } else if (!p.arxiv) {
        onAsk(PROMPT_ADD(p, why), t("track.ask.add"));
      } else {
        const r = await api("/api/reading", { json: { arxiv: p.arxiv, reason: why, lines: lines ?? p.lines ?? [] } });
        setState(r.item.status); toast(pushResult(r.result)); onAdded?.();
      }
    } catch (x) { toast(x.message); }
  };
  const url = p.arxiv ? `https://arxiv.org/abs/${p.arxiv}` : safeUrl(p.url) || safeUrl(p.doi);
  return html`<article class=${"tk-paper" + (open ? " open" : "") + (rel ? " rel" : "")}>
    <div class="tk-thumb" onClick=${() => setOpen(!open)}>
      <div class="ph"><b>${p.venue && p.venue !== "arXiv" ? p.venue : (date || "").slice(0, 4)}</b><span>${p.venue === "arXiv" || !p.venue ? t("track.preprint") : p.venue_full && p.venue_full !== p.venue ? p.venue_full : SRC[p.source] || ""}</span></div>
      ${p.arxiv && thumbReady && thumb && html`<img src=${`/api/track/thumb/${p.arxiv}`} alt="" loading="lazy" onError=${() => setThumb(false)} />`}
      ${p.tier && html`<span class="tier-pin"><span class=${"tk-tier " + p.tier} title=${t("track.tier_title", { tier: p.tier })}>${p.tier}</span></span>`}
    </div>
    <div class="tk-pbody">
      <div class="tk-ptitle" onClick=${() => setOpen(!open)} title=${p.title}>${p.title}</div>
      <div class="tk-pmeta">
        ${p.venue && p.venue !== "arXiv" && html`<span class="venue">${p.venue}</span>`}
        <span>${date ? dateLabel(p, date) : ""}</span>
        ${p.source && html`<span class=${"tk-src " + p.source} title=${t("track.source_title", { source: SRC[p.source] })}>${SRC[p.source]}</span>`}
        ${p.cites > 0 && html`<span>· ${t("track.cited_by", { n: p.cites })}</span>`}
        ${faces?.length > 0 && html`<span class="tk-faces">${faces}</span>`}
      </div>
      <div class="tk-chips">
        ${(p.lines || []).map((l) => html`<span class="tk-badge rel" title=${t("track.matched", { hits: names(p.line_hits?.[l] || []) })}>${l}</span>`)}
        ${(p.topics || []).slice(0, open ? 9 : 3).map((topic) => html`<span class="tk-chip">${labels?.[topic] || topic}</span>`)}
        ${(p.also || []).map((a) => html`<span class="tk-chip" title=${a.venue || ""}>${t("track.also_on", { source: SRC[a.source] || a.source })}</span>`)}
        ${extra}
      </div>
      ${open && html`
        ${p.abstract && html`<div class="tk-abs">${p.abstract}${p.abstract.length >= 890 ? "…" : ""}</div>`}
        ${p.authors?.length > 0 && html`<div class="tk-authors">${p.authors.slice(0, 12).join(", ")}${(p.n_authors || p.authors.length) > 12 ? " " + t("track.et_al") : ""}</div>`}`}
      <div class="tk-pfoot">
        ${url && html`<a class="abtn" title=${t("track.open_source")} href=${url} target="_blank" rel="noreferrer" onClick=${(e) => e.stopPropagation()}><${Icon} name="external" sm /></a>`}
        ${p.note && html`<button class="abtn txt" onClick=${() => onOpenPaper(p.note)}>${t("track.read")}</button>`}
        <span class="spacer" />
        ${state ? html`<span class="tag">${listed(state)}</span>`
          : html`<button class="tk-add" onClick=${add} title=${p.arxiv || p.ext || /doi\.org\/10\./i.test(p.doi || "") ? t("track.add_title") : t("track.add_title_ask")}><${Icon} name="plus" sm />${t("track.add_btn")}</button>`}
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
  if (!papers.length) return html`<div class="tk-empty">${t("track.no_match")}</div>`;
  return html`<div class="tk-papers">${shown.map((p) => render(p, ready.has(p.arxiv)))}</div>
    ${papers.length > n && html`<button class="btn tk-more" onClick=${() => setN(n + step * 2)}>${t("track.show_more", { n: Math.min(step * 2, papers.length - n), total: papers.length })}</button>`}`;
}

// ---------------- the run (立即巡检) ----------------

const STAGE_IDS = ["prepare", "fetch", "ingest", "render", "audit", "done", "failed"];
const stageText = (s) => (STAGE_IDS.includes(s) ? t("track.stage." + s) : s); // i18n-keys: track.stage.{prepare,fetch,ingest,render,audit,done,failed}

function Patrol({ toast, onAsk, onDone }) {
  const [st, setSt] = useState(null);
  const [showLog, setShowLog] = useState(false);
  const timer = useRef();
  const load = async () => {
    try { const s = await api("/api/track/tracker"); setSt(s); return s; }
    catch { setSt((o) => o || { available: true, running: false, unreachable: true }); return null; }  // keep the button
  };
  useEffect(() => { load(); return () => clearTimeout(timer.current); }, [lang()]);
  useEffect(() => {
    clearTimeout(timer.current);
    if (st?.running) timer.current = setTimeout(async () => {
      const s = await load();
      if (s && !s.running && !st.external) { toast(s.ok ? t("track.run.done", { run: s.run, kept: s.stats?.kept ?? "?" }) : t("track.run.failed", { error: s.error })); onDone?.(s); }
      else if (s && !s.running) onDone?.(s);
    }, 1500);
  }, [st]);
  const start = async () => {
    try { setSt(await api("/api/track/tracker/run", { method: "POST" })); toast(t("track.run.started")); }
    catch (e) { toast(e.message); }
  };
  if (!st) return null;
  if (!st.available) return html`<span class="muted small" title=${t("track.run.not_configured_hint")}>${t("track.run.not_configured")}</span>`;
  const pct = !st.running ? 100 : st.stage === "fetch" ? 5 + 75 * (st.queries ? st.queries_done / st.queries : 0)
    : { prepare: 2, ingest: 84, render: 92, audit: 97 }[st.stage] || 50;
  const finishedRecently = !st.running && st.finished && Date.now() - new Date(st.finished) < 6 * 3600e3;
  return html`<span class="tk-patrol">
    ${st.running && html`<span class="tk-prog" onClick=${() => setShowLog(!showLog)} title=${st.external ? t("track.run.external_hint") : t("track.run.log_hint")}>
      <span>${st.external ? t("track.run.external") : stageText(st.stage)}${st.stage === "fetch" && st.queries ? ` ${st.queries_done}/${st.queries}` : ""}${st.run ? ` · ${st.run}` : ""}</span>
      <span class=${"tk-prog-bar" + (st.external ? " indet" : "")}><i style=${{ width: pct + "%" }} /></span></span>`}
    ${finishedRecently && st.ok && html`<button class="chip" onClick=${() => onAsk(PROMPT_REVIEW_FRESH(st.run, st.since), t("track.ask.review_run"))}>
      <${Icon} name="sparkle" sm />${t("track.ask.write_analysis")}</button>`}
    ${finishedRecently && st.ok === false && html`<button class="chip" onClick=${() => setShowLog(!showLog)} title=${st.error}><span class="tag warn">${t("track.run.last_failed")}</span></button>`}
    <button class="btn primary" disabled=${st.running} onClick=${start} title=${st.linkage?.count ? t("track.run.title_follows", { n: st.linkage.count }) : t("track.run.title")}>
      <${Icon} name="refresh" sm />${st.running ? t("track.run.running") : t("track.run.now")}</button>
    ${showLog && st.log?.length > 0 && html`<div class="tk-sheet-bg" onClick=${(e) => e.target === e.currentTarget && setShowLog(false)}><div class="tk-sheet">
      <div class="head"><h2>${t("track.run.log_title", { run: st.run })}</h2><span class="spacer" /><button class="abtn" onClick=${() => setShowLog(false)}><${Icon} name="x" sm /></button></div>
      <div class="muted small">${st.stage ? stageText(st.stage) : ""}${st.error ? ` · ${st.error}` : ""}${st.audit ? ` · audit ${st.audit}` : ""}</div>
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
    try { await api(`/api/track/lines/${encodeURIComponent(line)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keywords: text.split(KW_SEP) }) }); toast(t("track.kw.saved", { line })); onChanged(); }
    catch (e) { toast(e.message); }
  };
  return html`<div class="label">${t("track.kw.label")}</div>
    ${lines.map(([l, k], i) => html`<div class="tk-erow" style=${{ gridTemplateColumns: "90px 1fr auto" }}>
      <b>${l}</b><input class="tk-kw" value=${k} onInput=${(e) => { const n = [...lines]; n[i] = [l, e.target.value]; setLines(n); }} />
      <button class="btn" onClick=${() => save(l, lines[i][1])}>${t("common.save")}</button></div>`)}`;
}

// ---------------- list refresh: monthly / recompute → suggestion → apply ----------------

const REFRESH_STAGES = ["prepare", "scholar", "openalex", "check", "done", "failed"];
const refreshStage = (s) => (REFRESH_STAGES.includes(s) ? t("track.refresh.stage." + s) : s); // i18n-keys: track.refresh.stage.{prepare,scholar,openalex,check,done,failed}
const needText = (n) => t("track.refresh.need." + (n === "login" || n === "captcha" ? n : "other")); // i18n-keys: track.refresh.need.{login,captcha,other}

function useListRefresh(toast, onApplied) {
  const [rf, setRf] = useState(null);
  const timer = useRef();
  const load = async () => { try { const r = await api("/api/track/scholars/refresh"); setRf(r); return r; } catch { return null; } };
  useEffect(() => { load(); return () => clearTimeout(timer.current); }, [lang()]);
  useEffect(() => {
    clearTimeout(timer.current);
    if (rf?.job?.running) timer.current = setTimeout(async () => {
      const r = await load();
      if (r && !r.job.running) toast(r.job.error ? t("track.refresh.done_error", { error: r.job.error }) : r.pending ? t("track.refresh.done_pending") : t("track.refresh.done_clean"));
    }, 2000);
  }, [rf]);
  const start = async () => { try { setRf(await api("/api/track/scholars/refresh", { method: "POST" })); toast(t("track.refresh.started")); } catch (e) { toast(e.message); } };
  const apply = async (id) => {
    try { const r = await api(`/api/track/scholars/refresh/${id}/apply`, { method: "POST" }); setRf(r); toast(r.notes?.length ? t("track.refresh.applied_notes", { notes: r.notes.join(t("track.semi")) }) : t("track.refresh.applied")); onApplied(); }
    catch (e) { toast(e.message); }
  };
  const dismiss = async (id) => { try { setRf(await api(`/api/track/scholars/refresh/${id}/dismiss`, { method: "POST" })); toast(t("track.refresh.dismissed")); } catch (e) { toast(e.message); } };
  const openScholar = async () => { try { await api("/api/track/scholars/scholar-window", { method: "POST" }); } catch (e) { toast(e.message); } };
  return { rf, load, start, apply, dismiss, openScholar };
}

function RefreshBanner({ R, onOpen }) {
  const rf = R.rf;
  if (!rf) return null;
  const j = rf.job || {};
  const p = rf.pending;
  const base = [refreshStage(j.stage), j.current].filter(Boolean).join(" · ");
  const what = needText(j.needs_user);
  return html`
    ${j.running && html`<div class="tk-banner"><${Icon} name="refresh" sm />
      <span>${t("track.refresh.running", { detail: j.total ? t("track.refresh.progress", { base, done: j.done, total: j.total }) : base })}</span>
      <span class="tk-prog" style=${{ flex: 1 }}><span class=${"tk-prog-bar" + (j.total ? "" : " indet")}><i style=${{ width: (j.total ? (j.done / j.total) * 100 : 30) + "%" }} /></span></span></div>`}
    ${j.needs_user && html`<div class="tk-banner bad"><span>${j.running ? t("track.refresh.need_running", { what }) : t("track.refresh.need_done", { what })}</span>
      <span class="spacer" />${rf.capture && html`<button class="chip" onClick=${R.openScholar}>${t("track.refresh.open_scholar")}</button>`}</div>`}
    ${!j.running && j.error && html`<div class="tk-banner bad"><span>${j.error}</span></div>`}
    ${!j.running && !j.error && rf.attempts?.failures > 0 && html`<div class="tk-banner bad"><span>${rf.attempts.error
        ? t("track.refresh.failures_error", { n: rf.attempts.failures, error: rf.attempts.error, next: rf.attempts.next_try?.replace("T", " ") })
        : t("track.refresh.failures", { n: rf.attempts.failures, next: rf.attempts.next_try?.replace("T", " ") })}</span>
      <span class="spacer" /><button class="chip" onClick=${R.start}>${t("track.refresh.retry")}</button></div>`}
    ${p && html`<div class="tk-banner ok"><${Icon} name="sparkle" sm />
      <span>${t("track.refresh.proposal", { enter: p.enter.length, leave: p.leave.length, moves: p.moves.filter((m) => m.from !== m.to).length })} <span class="muted">${t("track.refresh.source", { source: p.source })}</span></span>
      <span class="spacer" /><button class="btn primary" onClick=${onOpen}>${t("track.refresh.review")}</button></div>`}`;
}

// "Same lab as …" for a candidate the app cannot place; saved as a hint, used from the next recompute.
function LabPick({ c, list, R, toast }) {
  const labs = [...new Map(list.filter((s) => s.lab_id || s.lab).map((s) => [s.lab_id || s.lab, s])).values()];
  const save = async (e) => {
    const s = labs.find((x) => (x.lab_id || x.lab) === e.target.value);
    try {
      await api("/api/track/scholars/lab-hint", { json: { name: c.name, scholar_id: c.scholar_id, openalex: c.openalex, lab_id: s?.lab_id || null, lab_name: s?.lab || null } });
      toast(s ? t("track.labpick.saved", { name: c.name, mate: s.name }) : t("track.labpick.cleared", { name: c.name })); R.load();
    } catch (x) { toast(x.message); }
  };
  return html`<select class="tk-labpick" title=${t("track.labpick.title")} onChange=${save}>
    <option value="">${c.lab_name ? c.lab_name : t("track.labpick.unknown")}</option>
    ${labs.map((s) => html`<option value=${s.lab_id || s.lab}>${t("track.labpick.same", { name: s.name })}</option>`)}</select>`;
}

// "12 recent field papers", or a dash when the count is not known.
const fieldPapers = (n) => (n == null ? t("track.proposal.field_papers_unknown") : t("track.proposal.field_papers", { n }));

function ProposalSheet({ R, list, onClose, toast }) {
  const p = R.rf?.pending;
  if (!p) return null;
  const reasonOf = (c) => p.skipped.find((x) => x.name === c.name)?.reason || t("track.proposal.default_reason");
  const ex = async (c) => {
    if (!confirm(t("track.proposal.confirm_exclude", { name: c.name }))) return;
    try { await api("/api/track/scholars/exclude", { json: { name: c.name, scholar_id: c.scholar_id, openalex: c.openalex, reason: await vaultReason({ key: "exclude_review" }) } }); toast(t("track.proposal.excluded", { name: c.name })); R.load(); }
    catch (e) { toast(e.message); }
  };
  const move = (c) => { const m = p.moves.find((x) => x.name === c.name); return !m ? html`<span class="tk-mv new">${t("track.new")}</span>` : m.from === m.to ? "" : m.from > m.to ? html`<span class="tk-mv up">▲${m.from - m.to}</span>` : html`<span class="tk-mv down">▼${m.to - m.from}</span>`; };
  const r = p.rules || {};
  return html`<div class="tk-sheet-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}><div class="tk-sheet">
    <div class="head"><h2>${t("track.proposal.title")}</h2><span class="spacer" /><button class="abtn" onClick=${onClose}><${Icon} name="x" sm /></button></div>
    <div class="muted small">${p.source} · ${p.created?.slice(0, 16).replace("T", " ")} · ${t(r.one_per_lab ? "track.proposal.rules_one_per_lab" : "track.proposal.rules", { months: r.months, min: r.min_papers, top: r.top_n })}</div>
    ${p.enter.length > 0 && html`<div class="label">${t("track.proposal.entering", { n: p.enter.length })}</div>
      ${p.enter.map((c) => html`<div class="tk-erow" style=${{ gridTemplateColumns: "26px 1fr auto auto" }}>
        <span class="n">#${p.ranking.find((x) => x.name === c.name)?.rank ?? ""}</span>
        <div><b>${c.name}</b><div class="m">${c.lab_name || c.affiliation || ""} · ${t("track.cited_by", { n: fmtN(c.citations) })} · ${fieldPapers(c.field_papers)}</div>
          ${(c.titles || []).slice(0, 2).map((title) => html`<div class="m">· ${title}</div>`)}</div>
        <span class="tk-mv new">${t("track.new")}</span><span><${LabPick} c=${c} list=${list} R=${R} toast=${toast} /> <button class="linkbtn" onClick=${() => ex(c)}>${t("track.proposal.exclude")}</button></span></div>`)}`}
    ${p.leave.length > 0 && html`<div class="label">${t("track.proposal.leaving", { n: p.leave.length })}</div>
      ${p.leave.map((c) => html`<div class="tk-erow" style=${{ gridTemplateColumns: "26px 1fr auto" }}>
        <span class="n">#${c.rank ?? ""}</span><div><b>${c.name}</b><div class="m">${reasonOf(c)}</div></div>
        <span class="muted small">${t("track.proposal.keep_hint")}</span></div>`)}`}
    ${!p.enter.length && !p.leave.length && html`<div class="muted pad">${t("track.proposal.no_change")}</div>`}
    <div class="label">${t("track.proposal.new_list")}</div>
    ${p.ranking.map((c) => html`<div class="tk-erow" style=${{ gridTemplateColumns: "26px 1fr auto auto" }}>
      <span class="n">${c.rank}</span><div><b>${c.name}</b> ${c.pinned && html`<span class="tk-badge" title=${t("track.proposal.pinned_title", { top: r.top_n })}>${t("track.pinned")}</span>`}
        <div class="m">${c.lab_name ? `${c.lab_name} · ` : ""}${t("track.cited_by", { n: fmtN(c.citations) })} · ${fieldPapers(c.field_papers)}</div></div>
      ${move(c)}<span /></div>`)}
    ${p.alternates.length > 0 && html`<div class="label">${t("track.proposal.alternates")}</div>
      ${p.alternates.map((c) => html`<div class="tk-erow" style=${{ gridTemplateColumns: "1fr auto" }}>
        <div><b>${c.name}</b><div class="m">${c.lab_name ? `${c.lab_name} · ` : ""}${t("track.cited_by", { n: fmtN(c.citations) })} · ${fieldPapers(c.field_papers)}</div></div>
        <${LabPick} c=${c} list=${list} R=${R} toast=${toast} /></div>`)}`}
    ${p.skipped.length > 0 && html`<details class="tk-skipped"><summary class="label">${t("track.proposal.skipped", { n: p.skipped.length })}</summary>
      ${p.skipped.map((c) => html`<div class="m">${c.name} · ${fmtN(c.citations)} · ${c.reason}</div>`)}</details>`}
    <div class="tk-sheet-actions"><button class="btn" onClick=${() => { R.dismiss(p.id); onClose(); }}>${t("common.dismiss")}</button>
      <button class="btn primary" onClick=${() => { R.apply(p.id); onClose(); }}>${t("track.proposal.apply")}</button></div>
  </div></div>`;
}

const HISTORY = ["proposed", "applied", "dismissed", "superseded"];
const historyText = (s) => (HISTORY.includes(s) ? t("track.history." + s) : s); // i18n-keys: track.history.{proposed,applied,dismissed,superseded}

function ListRules({ R, toast }) {
  const rf = R.rf;
  const [form, setForm] = useState(null);
  const [snap, setSnap] = useState("");
  useEffect(() => { if (rf && !form) setForm({ ...rf.rules, keywords: rf.rules.keywords.join(", "), labels: rf.rules.labels.join("\n") }); }, [rf]);
  if (!rf || !form) return null;
  const save = async () => {
    try {
      await api("/api/track/scholars/rules", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        ...form, keywords: form.keywords.split(COMMAS).map((x) => x.trim()).filter(Boolean), labels: form.labels.split("\n").map((x) => x.trim()).filter(Boolean) }) });
      toast(t("track.rules.saved")); R.load();
    } catch (e) { toast(e.message); }
  };
  const importSnap = async () => { try { const r = await api("/api/track/scholars/snapshot", { json: { text: snap } }); toast(t("track.rules.snapshot_imported", { people: t("common.unit.people", { n: r.count }), date: r.date })); setSnap(""); R.load(); } catch (e) { toast(e.message); } };
  const unex = async (x) => { await api(`/api/track/scholars/exclude/${x.id}`, { method: "DELETE" }); toast(t("track.rules.unexcluded", { name: x.name })); R.load(); };
  const num = (k, label, w = 56) => html`<label class="tk-rule">${label}<input type="number" min="1" style=${{ width: w + "px" }} value=${form[k]} onInput=${(e) => setForm({ ...form, [k]: e.target.value })} /></label>`;
  const topicRule = (rf.rules.topic_groups || []).map((g) => g.join(t("track.rules.or"))).join(t("track.rules.and"));
  const runner = rf.rules.runner ? (rf.rules.runner === rf.device ? t("track.rules.runner_this", { device: rf.device }) : rf.rules.runner) : t("track.rules.runner_first");
  return html`
    <div class="label">${t("track.rules.auto")}</div>
    <div class="tk-rules">
      <label class="tk-rule"><input type="checkbox" checked=${form.auto} onChange=${(e) => setForm({ ...form, auto: e.target.checked })} /> ${tx("track.rules.cadence", { input: num("cadence_days", "", 48) })}</label>
      ${num("top_n", t("track.rules.top_n"))}${num("min_papers", t("track.rules.min_papers"))}${num("months", t("track.rules.months"))}
      <label class="tk-rule" title=${t("track.rules.one_per_lab_title")}><input type="checkbox" checked=${form.one_per_lab} onChange=${(e) => setForm({ ...form, one_per_lab: e.target.checked })} /> ${t("track.rules.one_per_lab")}</label>
      <label class="tk-rule wide">${t("track.rules.keywords")}<input value=${form.keywords} onInput=${(e) => setForm({ ...form, keywords: e.target.value })} /></label>
      <label class="tk-rule wide">${t("track.rules.labels")}<textarea rows="3" value=${form.labels} onInput=${(e) => setForm({ ...form, labels: e.target.value })} /></label>
      <div class="tk-rule wide muted small">${t("track.rules.topic", { rule: topicRule, next: rf.next_auto || "—" })}</div>
      <div class="tk-rule wide muted small">${t("track.rules.runner", { who: runner })}
        ${rf.rules.runner && rf.rules.runner !== rf.device && html` <button class="linkbtn" onClick=${async () => { await api("/api/track/scholars/rules", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ runner: rf.device }) }); toast(t("track.rules.runner_changed")); R.load(); }}>${t("track.rules.runner_make_this")}</button>`}</div>
      <button class="btn" onClick=${save}>${t("track.rules.save")}</button>
      ${rf.capture ? html`<button class="btn" onClick=${R.openScholar}>${t("track.rules.open_scholar_login")}</button>` : html`<span class="muted small">${t("track.rules.scholar_desktop_only")}</span>`}
    </div>
    <div class="label">${t("track.rules.excluded", { n: rf.excluded.length })}</div>
    ${rf.excluded.length === 0 && html`<div class="muted small">${t("track.rules.excluded_empty")}</div>`}
    ${rf.excluded.map((x) => html`<div class="tk-erow" style=${{ gridTemplateColumns: "1fr auto" }}><div><b>${x.name}</b><div class="m">${x.reason || ""}</div></div>
      <button class="linkbtn" onClick=${() => unex(x)}>${t("track.rules.unexclude")}</button></div>`)}
    <div class="label">${t("track.rules.snapshot")} ${rf.snapshot ? html`<span class="muted small">${t("track.rules.snapshot_latest", { date: rf.snapshot.date, source: rf.snapshot.source, people: t("common.unit.people", { n: rf.snapshot.count }) })}</span>` : ""}</div>
    <div class="tk-search"><textarea class="tk-kw" rows="3" placeholder=${t("track.rules.snapshot_placeholder")} value=${snap} onInput=${(e) => setSnap(e.target.value)} />
      <button class="btn" disabled=${!snap.trim()} onClick=${importSnap}>${t("track.rules.import")}</button></div>
    ${rf.history.length > 0 && html`<div class="label">${t("track.rules.history")}</div>
      ${rf.history.map((h) => html`<div class="m tk-hist">${h.created?.slice(0, 10)} · ${h.source} · ${historyText(h.status)}</div>`)}`}`;
}

function ScholarEditor({ list, onClose, toast, onChanged, R }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const search = async () => { setBusy(true); try { setRes((await api(`/api/track/scholars/search?q=${encodeURIComponent(q)}`)).results); } catch (e) { toast(e.message); } finally { setBusy(false); } };
  const add = async (c) => { try { await api("/api/track/scholars", { json: { name: c.name, openalex: c.openalex, affiliation: c.affiliation } }); toast(t("track.editor.added", { name: c.name })); setRes(null); setQ(""); onChanged(); } catch (e) { toast(e.message); } };
  const remove = async (s) => { if (!confirm(t("track.editor.confirm_remove", { name: s.name }))) return; await api(`/api/track/scholars/${s.id}`, { method: "DELETE" }); toast(t("track.editor.removed", { name: s.name })); onChanged(); };
  const pin = async (s) => { try { await api(`/api/track/scholars/${s.id}/pin`, { json: { on: !s.pinned } }); toast(s.pinned ? t("track.editor.unpinned", { name: s.name }) : t("track.editor.pinned_toast", { name: s.name })); onChanged(); } catch (e) { toast(e.message); } };
  const have = new Set(list.map((s) => s.openalex));
  return html`<div class="tk-sheet-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}><div class="tk-sheet">
    <div class="head"><h2>${t("track.editor.title")}</h2><span class="muted small">${t("track.editor.hint")}</span><span class="spacer" />
      <button class="abtn" onClick=${onClose}><${Icon} name="x" sm /></button></div>
    <div class="tk-search">
      <input placeholder=${t("track.editor.search_placeholder")} value=${q} onInput=${(e) => setQ(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && q.trim() && search()} />
      <button class="btn primary" disabled=${!q.trim() || busy} onClick=${search}>${busy ? t("track.searching") : t("track.editor.find")}</button></div>
    ${res && res.length === 0 && html`<div class="muted small pad">${t("track.editor.none")}</div>`}
    ${res && res.map((c) => html`<div class="tk-erow">
      <span class="n">+</span><${Avatar} name=${c.name} size=${30} />
      <div><b>${c.name}</b><div class="m">${c.affiliation || "—"} · ${t("track.cited_by", { n: fmtN(c.citations) })} · h-index ${c.h_index ?? "—"} · ${c.topics.join(" / ")}</div></div>
      <span class="muted small">${c.openalex}</span>
      ${have.has(c.openalex) ? html`<span class="tag">${t("track.editor.in_list")}</span>` : html`<button class="btn" onClick=${() => add(c)}>${t("track.editor.add")}</button>`}
    </div>`)}
    <div class="label">${t("track.editor.current", { n: list.length })}</div>
    ${list.map((s) => html`<div class="tk-erow">
      <span class="n">${s.rank ?? ""}</span><${Avatar} name=${s.name} srcs=${avatarSrcs(s)} size=${30} />
      <div><b>${s.name}</b> ${s.tracked_lab && html`<span class="tk-badge ok">${t("track.in_labs")}</span>`}
        <div class="m" title=${Object.entries(s.sources || {}).map(([k, v]) => `${k}: ${v}`).join("\n")}>${[s.lab, s.affiliation].filter(Boolean).join(" · ")}</div></div>
      <span class="muted small">${s.scholar_citations ? `Scholar ${fmtN(s.scholar_citations.value)}` : s.topic_citations ? t("track.editor.topic_cites", { n: fmtN(s.topic_citations) }) : s.added === "user" ? t("track.editor.added_manually") : ""}</span>
      <span><button class=${"tk-pin" + (s.pinned ? " on" : "")} title=${t("track.editor.pin_title")} onClick=${() => pin(s)}>${s.pinned ? t("track.pinned") : t("track.editor.pin")}</button>
        <button class="abtn" title=${t("common.remove")} onClick=${() => remove(s)}><${Icon} name="x" sm /></button></span>
    </div>`)}
    <${LineKeywords} toast=${toast} onChanged=${onChanged} />
    ${R && html`<${ListRules} R=${R} toast=${toast} />`}
  </div></div>`;
}

// Rank movement from the last applied recompute.
const rankMove = (s) => {
  if (!s.rank_changed) return "";
  if (s.prev_rank == null) return html` <span class="tk-mv new" title=${t("track.rank.new_since", { date: s.rank_changed })}>${t("track.new")}</span>`;
  const d = s.prev_rank - s.rank;
  return d === 0 ? "" : html` <span class=${"tk-mv " + (d > 0 ? "up" : "down")} title=${t("track.rank.moved_since", { date: s.rank_changed, from: s.prev_rank, to: s.rank })}>${d > 0 ? "▲" : "▼"}${Math.abs(d)}</span>`;
};

// Scholars sharing a lab (lab_id, else lab name): shown as a group, the list keeps both for now.
const labKey = (s) => s.lab_id || (s.lab || "").toLowerCase();

function ScholarCard({ s, on, onClick, mates }) {
  const m = s.metrics || {};
  const years = (s.by_year || []).slice(-8);
  const gs = s.scholar_citations?.value;
  return html`<button class=${"tk-person" + (on ? " on" : "")} onClick=${onClick}>
    <span class="tk-rank">${s.pinned && html`<span title=${t("track.pinned")}>${t("track.pinned")} · </span>`}#${s.rank ?? "—"}${rankMove(s)}</span>
    <div class="tk-person-top"><${Avatar} name=${s.name} srcs=${avatarSrcs(s)} size=${46} />
      <div class="tk-person-id"><b>${s.name}</b><span>${s.lab || s.inst?.name || s.affiliation || ""}</span>
        <span>${s.affiliation || s.inst?.name || ""}</span></div></div>
    <div class="tk-nums">
      <div class="tk-num" title=${gs ? (m.citations ? t("track.card.cites_alt", { source: s.scholar_citations.source, n: fmtN(m.citations) }) : s.scholar_citations.source) : "OpenAlex"}>
        <b>${fmtN(gs || m.citations)}</b><span>${gs ? t("track.card.scholar_cites") : t("track.card.total_cites")}</span></div>
      <div class="tk-num"><b>${m.h_index ?? "—"}</b><span>h-index</span></div>
      <div class="tk-num"><b>${years.length ? years[years.length - 1].works : m.this_year ?? "—"}</b><span>${t("track.card.papers_this_year")}</span></div>
    </div>
    <${Sparkline} values=${years.map((y) => y.works)} labels=${years.map((y) => t("track.year", { y: y.year }))} w=${118} fmt=${papersOf} />
    <div class="tk-person-foot">
      ${s.tracked_lab ? html`<span class="tk-badge ok" title=${s.tracked_lab.name}><${Icon} name="check" sm />${t("track.in_labs")}</span>` : html`<span class="tk-badge">${t("track.card.new_inst")}</span>`}
      ${mates?.length > 0 && html`<span class="tk-badge" title=${t("track.card.same_lab_title", { names: names(mates) })}>${t("track.card.same_lab", { n: mates.length + 1 })}</span>`}
      ${m.relevant > 0 && html`<span class="tk-badge rel">${t("track.card.relevant", { n: m.relevant })}</span>`}
      ${m.d30 > 0 && html`<span class="tk-badge">${t("track.card.d30", { n: m.d30 })}</span>`}
      ${(s.error || s.age_h > 48) && html`<span class="tk-badge warn" title=${s.error || ""}>${t("track.card.stale")}</span>`}
    </div>
  </button>`;
}

function ScholarDetail({ s, onOpenNote, onOnly, filtered }) {
  const years = (s.by_year || []).slice(-8);
  const src = s.sources || {};
  const links = [[t("track.detail.homepage"), s.homepage], [t("track.detail.lab_link"), s.lab_url], ["OpenAlex", s.openalex && `https://openalex.org/authors/${s.openalex}`],
    ["Google Scholar", s.scholar_id && `https://scholar.google.com/citations?user=${s.scholar_id}`]].filter(([, u]) => u);
  return html`<div class="tk-detail">
    <div><h4>${t("track.detail.per_year")}</h4><${Columns} data=${years.map((y) => ({ label: `'${String(y.year).slice(2)}`, title: t("track.year", { y: y.year }), parts: { v: y.works } }))}
        series=${[{ key: "v", label: t("track.detail.published"), cls: "s1" }]} height=${120} />
      <h4 style=${{ marginTop: "12px" }} title=${t("track.detail.cites_per_year_hint")}>${t("track.detail.cites_per_year")}</h4><${Columns} data=${years.map((y) => ({ label: `'${String(y.year).slice(2)}`, title: t("track.detail.year_published", { y: y.year }), parts: { v: y.cites } }))}
        series=${[{ key: "v", label: t("track.detail.cited"), cls: "s3" }]} height=${120} fmt=${(n) => t("track.cited_by", { n })} /></div>
    <div><h4>${t("track.detail.venues")}</h4><${HBars} items=${s.venues} />
      ${s.recent_llm_rec != null && html`<h4 style=${{ marginTop: "12px" }}>${t("track.detail.why")}</h4>
        <div class="tk-chips"><span class="tk-chip">${t("track.detail.llm_rec")}<b>${s.recent_llm_rec}</b></span>
          ${s.scholar_citations && html`<span class="tk-chip">${t("track.card.scholar_cites")}<b>${fmtN(s.scholar_citations.value)}</b></span>`}</div>`}
      <h4 style=${{ marginTop: "12px" }}>${t("track.detail.topics")}</h4>
      <div class="tk-chips">${(s.topics || []).map(([topic, n]) => html`<span class="tk-chip">${topic}<b>${n}</b></span>`)}</div></div>
    <div><h4>${t("track.detail.coauthors")}</h4>
      <div class="tk-chips">${(s.coauthors || []).map(([n, c]) => html`<span class="tk-chip">${n}<b>${c}</b></span>`)}</div>
      <h4 style=${{ marginTop: "12px" }}>${t("track.detail.links")}</h4>
      <div class="tk-chips">${links.filter(([, u]) => safeUrl(u)).map(([l, u]) => html`<a class="tk-chip" href=${u} target="_blank" rel="noreferrer">${l}</a>`)}
        ${s.tracked_lab && html`<button class="tk-chip" onClick=${() => onOpenNote(s.tracked_lab.path)}>${t("track.detail.lab_note")}</button>`}</div></div>
    <div class="full">
      <button class=${"btn" + (filtered ? " primary" : "")} onClick=${onOnly}>${filtered ? t("track.detail.only_on") : t("track.detail.only")}</button>
      <details class="src"><summary>${t("track.detail.data", { date: s.fetched ? formatDate(s.fetched) : t("track.detail.not_fetched") })}${s.error ? ` · ${s.error}` : ""}</summary>
        ${Object.entries(src).map(([k, v]) => html`<div><b>${k}</b>${"\u3000"}${v}</div>`)}</details>
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
    try { setD(await api(`/api/track/scholars/activity${refresh ? "?refresh=true" : ""}`)); R.load(); } catch (e) { toast(e.message); } finally { setBusy(false); }
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
  if (!d) return html`<div class="muted pad">${t("common.loading")}</div>`;
  if (!d.scholars.length) return html`<div class="tk-empty">${t("track.scholars.empty")} <button class="linkbtn" onClick=${() => setEdit(true)}>${t("track.scholars.add")}</button>
    ${edit && html`<${ScholarEditor} list=${[]} onClose=${() => setEdit(false)} toast=${toast} onChanged=${() => load()} />`}</div>`;

  const months = d.months.filter((m) => m >= cut.slice(0, 7));
  const heatRows = d.scholars.map((s) => ({ key: s.id, label: s.name, dim: sid && sid !== s.id,
    values: months.map((m) => d.papers.filter((p) => !p.date_approx && p.scholars.includes(s.id) && p.date.startsWith(m) && matchTL(p)).length) }));
  const inWin = (d.papers || []).filter((p) => p.date >= cut && !p.date_approx && (!sid || p.scholars.includes(sid)) && matchTL(p));
  const bars = months.map((m) => { const ps = inWin.filter((p) => p.date.startsWith(m));
    return { label: monthLabel(m), title: m, parts: { rel: ps.filter((p) => p.lines.length).length, other: ps.filter((p) => !p.lines.length).length } }; });
  const topicCounts = Object.entries((d.papers || []).filter((p) => (month ? p.date.startsWith(month) : p.date >= cut) && (!sid || p.scholars.includes(sid))
    && (!line || (line === "any" ? p.lines.length : p.lines.includes(line))))
    .reduce((acc, p) => { p.topics.forEach((tp) => { acc[tp] = (acc[tp] || 0) + 1; }); return acc; }, {}))
    .sort((a, b) => b[1] - a[1]).slice(0, 8).map(([tp, n]) => [labels[tp] || tp, n, tp]);
  const totals = d.totals;
  const job = d.job || {};
  const openS = open && byId[open];
  const pickScholar = (id) => { setOpen(open === id ? null : id); };
  const only = (id) => { setSid(sid === id ? null : id); setTimeout(() => listRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50); };
  const clear = () => { setSid(null); setTopic(null); setLine(""); setMonth(null); };
  // stored on the reading list item as its reason: worded by the server (vaultReason)
  const reason = (p) => {
    const who = p.scholars.map((x) => byId[x]?.name).filter(Boolean);
    return p.lines.length ? { key: "scholars_lines", params: { names: who, lines: p.lines } } : { key: "scholars", params: { names: who } };
  };

  return html`
    <div class="tk-kpis">
      <button class="tk-kpi" onClick=${() => setEdit(true)}><b>${d.scholars.length}</b><span>${t("track.kpi.scholars")}</span><em>${t("track.kpi.scholars_hint", { n: d.scholars.filter((s) => s.tracked_lab).length })}</em></button>
      <button class=${"tk-kpi" + (win === 30 && !line ? " on" : "")} onClick=${() => { setWin(30); setMonth(null); }}><b>${totals.d30}</b><span>${t("track.kpi.d30")}</span><em>${t("track.kpi.d30_hint")}</em></button>
      <button class=${"tk-kpi" + (win === 90 && !line ? " on" : "")} onClick=${() => { setWin(90); setMonth(null); }}><b>${totals.d90}</b><span>${t("track.kpi.d90")}</span><em>${t("track.kpi.d90_hint", { n: d.scholars.filter((s) => s.metrics?.d90).length })}</em></button>
      <button class=${"tk-kpi" + (win === 365 && !line ? " on" : "")} onClick=${() => { setWin(365); setMonth(null); }}><b>${totals.d365}</b><span>${t("track.kpi.d365")}</span><em>OpenAlex ${d.as_of}</em></button>
      <button class=${"tk-kpi" + (line === "any" ? " on" : "")} onClick=${() => setLine(line === "any" ? "" : "any")}><b>${totals.relevant}<small>${t(totals.relevant === 1 ? "track.paper_word" : "track.papers_word")}</small></b><span>${t("track.kpi.relevant")}</span><em>${t("track.kpi.relevant_hint", { n: totals.relevant90, lines: d.lines.join(" / ") })}</em></button>
    </div>
    ${job.running && html`<div class="tk-banner"><${Icon} name="refresh" sm /><span>${t("track.fetching", { current: job.current || "", done: job.done, total: job.total })}</span>
      <span class="tk-prog" style=${{ flex: 1 }}><span class="tk-prog-bar"><i style=${{ width: (job.total ? (job.done / job.total) * 100 : 5) + "%" }} /></span></span></div>`}
    ${job.error && !job.running && html`<div class="tk-banner bad"><span>${t("track.fetch_error", { error: job.error })}</span></div>`}

    <${RefreshBanner} R=${R} onOpen=${() => setProposal(true)} />
    <div class="tk-section-title"><h2>${t("track.top_title", { n: d.scholars.length })}</h2>
      <span class="muted">${d.scholars[0]?.rank_by ? t("track.ranked_by", { by: d.scholars[0].rank_by }) : t("track.ranked_default")}</span><span class="spacer" />
      <button class="linkbtn" disabled=${busy || job.running} onClick=${() => load(true)}>${job.running ? t("track.fetching_btn") : t("track.refresh_data")}</button>
      <button class="linkbtn" disabled=${R.rf?.job?.running} onClick=${R.start} title=${t("track.recompute_title")}>${R.rf?.job?.running ? t("track.recomputing") : t("track.recompute")}</button>
      <button class="linkbtn" onClick=${() => setEdit(true)}>${t("track.manage")}</button></div>
    <div class="tk-people">${d.scholars.map((s) => html`<${ScholarCard} key=${s.id} s=${s} on=${open === s.id} onClick=${() => pickScholar(s.id)}
      mates=${d.scholars.filter((x) => x.id !== s.id && labKey(x) && labKey(x) === labKey(s)).map((x) => x.name)} />`)}</div>
    ${openS && html`<div style=${{ marginTop: "12px" }}><${ScholarDetail} s=${openS} onOpenNote=${onOpenNote} filtered=${sid === openS.id} onOnly=${() => only(openS.id)} /></div>`}

    <div class="tk-section-title" ref=${listRef}><h2>${t("track.feed.title")}</h2><span class="muted">${t("track.feed.hint")}</span></div>
    <div class="tk-filters">
      <div class="seg">${WINDOWS.map((k) => html`<button class=${win === k && !month ? "on" : ""} onClick=${() => { setWin(k); setMonth(null); }}>${windowLabel(k)}</button>`)}</div>
      <select value=${sid || ""} onChange=${(e) => setSid(e.target.value || null)}>
        <option value="">${t("track.filter.all_scholars")}</option>${d.scholars.map((s) => html`<option value=${s.id}>${s.name}</option>`)}</select>
      <select value=${topic || ""} onChange=${(e) => setTopic(e.target.value || null)}>
        <option value="">${t("track.filter.all_topics")}</option>${Object.entries(labels).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
      <div class="seg">${[["", t("common.all")], ["any", t("track.filter.relevant")], ...d.lines.map((l) => [l, l])].map(([k, l]) => html`<button class=${line === k ? "on" : ""} onClick=${() => setLine(k)}>${l}</button>`)}</div>
      ${month && html`<button class="tk-chip on" onClick=${() => setMonth(null)}>${month} ×</button>`}
      ${(sid || topic || line || month) && html`<button class="linkbtn" onClick=${clear}>${t("track.filter.clear")}</button>`}
      <span class="count">${papersOf(papers.length)}</span>
    </div>
    <div class="tk-grid2">
      <div class=${"tk-card" + (busy ? " loading" : "")}><h3>${t("track.heat.title")}</h3><div class="sub">${t("track.heat.sub")}</div>
        <${Heatmap} rows=${heatRows} cols=${months.map(monthLabel)} colTitles=${months} active=${sid}
          onRow=${(r) => setSid(sid === r.key ? null : r.key)} onCell=${(r, j) => { setSid(r.key); setMonth(months[j]); }} />
        <h3 style=${{ marginTop: "14px" }}>${t("track.topics.title")}</h3><div class="sub">${t("track.topics.sub")}</div>
        <${HBars} items=${topicCounts.map(([l, n]) => [l, n])} picked=${topic ? labels[topic] : null}
          onPick=${(l) => { const k = topicCounts.find((x) => x[0] === l)?.[2]; setTopic(topic === k ? null : k); }} /></div>
      <div class=${"tk-card" + (busy ? " loading" : "")}><h3>${t("track.monthly.title")}</h3><div class="sub">${totals.year_only ? t("track.monthly.sub_year_only", { n: totals.year_only }) : t("track.monthly.sub")}</div>
        <${Columns} data=${bars} series=${[{ key: "rel", label: t("track.kpi.relevant"), cls: "s2" }, { key: "other", label: t("track.monthly.other"), cls: "s1" }]}
          picked=${month ? months.indexOf(month) : null} onPick=${(i) => setMonth(month === months[i] ? null : months[i])} height=${170} />
        <h3 style=${{ marginTop: "14px" }}>${t("track.recent.title")}</h3><div class="sub">${t("track.recent.sub")}</div>
        <div class="tk-rel">${d.papers.filter((p) => p.lines.length && (!sid || p.scholars.includes(sid))).slice(0, 6).map((p) => html`
          <button class="tk-rel-row" title=${Object.entries(p.line_hits || {}).map(([l, h]) => `${l}: ${names(h)}`).join("\n")}
            onClick=${() => { setLine("any"); setMonth(null); setTimeout(() => listRef.current?.scrollIntoView({ behavior: "smooth" }), 50); }}>
            <span class="t">${p.title}</span><span class="m">${p.date_approx ? p.date.slice(0, 4) : p.date.slice(5)} · ${p.lines.join(" / ")}</span></button>`)}
          ${!d.papers.some((p) => p.lines.length) && html`<div class="muted small">${t("track.recent.none")}</div>`}</div></div>
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
  if (!runs.length) return html`<div class="tk-empty">${t("track.radar.empty")}</div>`;
  const ordered = [...runs].reverse();
  const byTier = run?.stats?.by_tier || {};
  const papers = run ? run.papers.filter((p) => (!tier || p.tier === tier) && (!topic || p.topics.includes(topic))) : [];
  const topicCounts = run ? Object.entries(run.papers.filter((p) => !tier || p.tier === tier).reduce((a, p) => { p.topics.forEach((x) => { a[x] = (a[x] || 0) + 1; }); return a; }, {}))
    .sort((a, b) => b[1] - a[1]).slice(0, 8) : [];
  const labCounts = run ? Object.entries(run.papers.reduce((a, p) => { p.labs.forEach((x) => { a[x] = (a[x] || 0) + 1; }); return a; }, {})).sort((a, b) => b[1] - a[1]).slice(0, 8) : [];
  const tile = (k, n, label) => html`<button class=${"tk-kpi" + (tier === k ? " on" : "")} onClick=${() => setTier(tier === k ? null : k)}><b>${n}</b><span>${label}</span></button>`;
  const followLabel = (f) => (f.startsWith("person:") ? t("track.follow.person_label", { value: f.slice(7) }) : f.startsWith("keyword:") ? t("track.follow.keyword_label", { value: f.slice(8) }) : f);
  return html`
    <div class="tk-grid2">
      <div class="tk-card"><h3>${t("track.radar.kept_title")}</h3><div class="sub">${t("track.radar.kept_sub")}</div>
        <${Columns} data=${ordered.map((r) => ({ label: r.run.slice(5), title: `${r.run} (since ${r.since})`, parts: r.stats.by_tier || {} }))}
          series=${[{ key: "A", label: t("track.tier.a"), cls: "tA" }, { key: "B", label: t("track.tier.b"), cls: "tB" }, { key: "C", label: t("track.tier.c"), cls: "tC" }]}
          picked=${ordered.findIndex((r) => r.run === cur)} onPick=${(i) => setCur(ordered[i].run)} height=${160} /></div>
      <div class="tk-card"><h3>${t("track.radar.this_run", { run: cur })}</h3><div class="sub">${run ? t("track.radar.window", { since: run.since, input: run.stats.input ?? "—", fail: run.stats.gate_fail ?? "—" }) : t("common.loading")}</div>
        ${run && html`<div class="tk-kpis" style=${{ gridTemplateColumns: "repeat(3, 1fr)", marginBottom: "10px" }}>
          ${tile("A", byTier.A || 0, t("track.tier.a"))}${tile("B", byTier.B || 0, t("track.tier.b"))}${tile("C", byTier.C || 0, t("track.tier.c"))}</div>
          <h3>${t("track.radar.topics")}</h3><${HBars} items=${topicCounts.map(([k, n]) => [labels[k] || k, n])} picked=${topic ? labels[topic] || topic : null}
            onPick=${(l) => { const k = topicCounts.find(([x]) => (labels[x] || x) === l)?.[0]; setTopic(topic === k ? null : k); }} />`}</div>
    </div>
    ${run?.analysis && html`<div class="tk-card" style=${{ marginBottom: "14px" }}>
      <div class="card-head"><h2>${t("track.radar.analysis")}</h2><span>
        <button class="linkbtn" onClick=${() => setFull(!full)}>${full ? t("common.hide") : t("track.radar.expand")}</button>
        ${run.report_url && html` <a class="chip" href=${run.report_url}><${Icon} name="external" sm />${t("track.radar.full_report")}</a>`}</span></div>
      <div class=${"md tk-analysis" + (full ? " open" : "")} onClick=${async (e) => { const w = e.target.closest(".wikilink"); if (!w) return;
        try { const r = await api(`/api/note/find?name=${encodeURIComponent(w.dataset.target)}`); r.paper ? onOpenPaper(r.paper) : onOpenNote(r.path); } catch (x) { toast(x.message); } }}
        dangerouslySetInnerHTML=${{ __html: renderMarkdown(run.analysis) }} /></div>`}
    ${run && !run.analysis.trim().replace(/_Analysis pending\._/, "") && html`<div class="tk-banner"><span>${t("track.radar.no_analysis")}</span><span class="spacer" />
      <button class="chip" onClick=${() => onAsk(PROMPT_REVIEW(run.run, run.since), t("track.ask.review_run"))}><${Icon} name="sparkle" sm />${t("track.ask.write_analysis")}</button></div>`}
    ${labCounts.length > 0 && html`<div class="tk-filters"><span class="muted small">${t("track.radar.most_active")}</span>${labCounts.map(([l, n]) => html`<span class="tk-chip">${l}<b>${n}</b></span>`)}
      ${(tier || topic) && html`<button class="linkbtn" onClick=${() => { setTier(null); setTopic(null); }}>${t("track.filter.clear")}</button>`}<span class="count">${papersOf(papers.length)}</span></div>`}
    ${run && html`<${PaperGrid} papers=${papers} render=${(p, ready) => html`<${PaperCard} thumbReady=${ready} key=${p.arxiv} p=${{ ...p, lines: [] }} labels=${labels}
      reason=${`Lab Tracker ${run.run} · Tier ${p.tier}${p.labs?.length ? " · " + p.labs.join(", ") : ""}`} lines=${[]}
      extra=${[...(p.labs || []).map((l) => html`<span class="tk-chip">${l}</span>`), ...(p.follows || []).map((f) => html`<span class="tk-chip on">${followLabel(f)}</span>`)]}
      toast=${toast} onOpenPaper=${onOpenPaper} onAsk=${onAsk} onAdded=${onChanged} />`} />`}`;
}

// ---------------- follows ----------------

const kindLabel = (k) => t("track.follows.kind." + (k === "person" ? "person" : "keyword")); // i18n-keys: track.follows.kind.{keyword,person}

function Follows({ toast, onOpenPaper, onAsk, version, onChanged }) {
  const [feed, setFeed] = useState(null);
  const [link, setLink] = useState(null);
  const [kind, setKind] = useState("keyword");
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [pick, setPick] = useState(null);
  const load = async (refresh = false) => { setLoading(true); try { const r = await api(`/api/track/follows/feed?refresh=${refresh}`); setFeed(r.feed); setLink(r.linkage); } finally { setLoading(false); } };
  useEffect(() => { load(); }, [version]);
  // The Anthology volumes download in the background: look again while some are still coming. The next
  // look is scheduled only after the last one answered, so a slow source cannot pile requests up.
  const downloading = !!feed?.some((f) => f.sources?.some((s) => s.loading));
  useEffect(() => {
    if (!downloading) return;
    let alive = true, tries = 0, timer;
    const look = async () => {
      if (!alive || ++tries > 30) return;
      try { const r = await api("/api/track/follows/feed"); if (alive) setFeed(r.feed); } catch {}
      if (alive) timer = setTimeout(look, 6000);
    };
    timer = setTimeout(look, 6000);
    return () => { alive = false; clearTimeout(timer); };
  }, [downloading]);
  const add = async () => { await api("/api/track/follows", { json: { kind, value } }); setValue(""); toast(t("track.follows.followed")); load(); onChanged?.(); };
  const remove = async (f, e) => { e.stopPropagation(); await api(`/api/track/follows/${f.id}`, { method: "DELETE" }); if (pick === f.id) setPick(null); load(); onChanged?.(); };
  const weeks = [...Array(5)].map((_, i) => dayISO(35 - i * 7));
  const papers = (feed || []).filter((f) => !pick || f.id === pick).flatMap((f) => f.papers.map((p) => ({ ...p, _f: f })))
    .filter((p, i, a) => a.findIndex((q) => (q.arxiv || q.ext) === (p.arxiv || p.ext)) === i).sort((a, b) => b.published.localeCompare(a.published));
  // One line per source across all follows: what each answered, or why not.
  const states = Object.keys(SRC).map((id) => {
    const all = (feed || []).flatMap((f) => f.sources || []).filter((s) => s.id === id);
    const bad = all.find((s) => !s.ok), wait = all.find((s) => s.loading);
    return { id, n: all.reduce((a, s) => a + s.n, 0), bad, wait, show: all.length > 0 };
  }).filter((x) => x.show);
  return html`
    ${link && html`<div class=${"tk-banner " + (link.reads_follows ? "ok" : "bad")}>
      ${link.reads_follows ? html`<${Icon} name="check" sm /><span>${t(link.in_sync ? "track.follows.banner_ok" : "track.follows.banner_unsynced", { n: link.count })}</span>`
        : html`<span>${t("track.follows.banner_bad")}</span>`}
      <span class="spacer" /><button class="linkbtn" disabled=${loading} onClick=${() => load(true)}>${loading ? t("track.searching") : t("common.refresh")}</button></div>`}
    ${states.length > 0 && html`<div class="tk-srcs">${states.map((x) => html`<span class=${"tk-srcst" + (x.bad ? " bad" : x.wait ? " wait" : "")} title=${x.bad ? x.bad.error + (x.bad.stale ? t("track.follows.stale_note") : "") : x.wait ? t("track.follows.loading_hint") : t("track.follows.src_ok", { source: SRC[x.id] })}>
      <i />${SRC[x.id]}<b>${x.n}</b><small>${x.bad ? (x.bad.stale ? t("track.follows.src_failed_stale") : t("track.follows.src_failed")) : x.wait ? t("track.follows.src_downloading", { done: x.wait.progress.done, total: x.wait.progress.total }) : t(x.n === 1 ? "track.paper_word" : "track.papers_word")}</small></span>`)}</div>`}
    <div class="tk-addfollow">
      <div class="seg"><button class=${kind === "keyword" ? "on" : ""} onClick=${() => setKind("keyword")}>${kindLabel("keyword")}</button>
        <button class=${kind === "person" ? "on" : ""} onClick=${() => setKind("person")}>${kindLabel("person")}</button></div>
      <input placeholder=${kind === "keyword" ? t("track.follows.placeholder_keyword") : t("track.follows.placeholder_person")} value=${value}
        onInput=${(e) => setValue(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && value.trim() && add()} />
      <button class="btn primary" disabled=${!value.trim()} onClick=${add}>${t("track.follows.follow")}</button>
    </div>
    ${feed === null && html`<div class="muted pad">${t("track.searching")}</div>`}
    ${feed && feed.length === 0 && html`<div class="tk-empty">${t("track.follows.empty")}</div>`}
    ${feed?.length > 0 && html`<div class="tk-follows">${feed.map((f) => {
      const counts = weeks.map((w, i) => f.papers.filter((p) => !p.month_only && p.published >= w && (i === 4 || p.published < weeks[i + 1])).length);
      return html`<button class=${"tk-follow" + (pick === f.id ? " on" : "")} onClick=${() => setPick(pick === f.id ? null : f.id)}>
        <span class="abtn x" title=${t("track.follows.unfollow")} onClick=${(e) => remove(f, e)}><${Icon} name="x" sm /></span>
        <span class="tk-badge">${kindLabel(f.kind)}</span><b class="v" title=${f.value}>${f.value}</b>
        <span class="n">${f.papers.length}<small>${t("track.follows.recent")}</small></span>
        <${Sparkline} values=${counts} labels=${weeks.map((w) => t("track.follows.since", { date: w.slice(5) }))} w=${100} h=${24} />
        ${f.papers.length > 0 && html`<span class="tk-bysrc">${Object.keys(SRC).map((id) => [id, f.papers.filter((p) => p.source === id).length]).filter(([, n]) => n > 0).map(([id, n]) => `${SRC[id]} ${n}`).join(" · ")}</span>`}
        ${(f.sources || []).filter((s) => !s.ok).map((s) => html`<span class="err" title=${s.stale ? t("track.follows.stale_title") : ""}>${s.error}</span>`)}
      </button>`; })}</div>
    <${PaperGrid} papers=${papers} render=${(p, ready) => html`<${PaperCard} thumbReady=${ready} key=${p.arxiv || p.ext} p=${{ ...p, date: p.published, venue: p.source === "arxiv" ? "arXiv" : venueShort(p.venue), venue_full: p.venue, abstract: p.summary, topics: p.keywords }}
      reason=${{ key: p._f.kind === "person" ? "follow_person" : "follow_keyword", params: { value: p._f.value } }} lines=${[]}
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
  if (!labs || !mx) return html`<div class="muted pad">${t("common.loading")}</div>`;
  const runs = mx.runs.map((r) => r.run);
  const series = (l) => runs.map((r) => mx.counts[l.lab_id]?.[r] || 0);
  const topOf = (l) => top.filter((s) => s.lab_id && s.lab_id === l.lab_id);
  const tracks = [...new Set(labs.flatMap((l) => l.tracks))].sort();
  const tname = (id) => (mx.track_names?.[id] ? `${id} ${mx.track_names[id]}` : id);
  const shown = labs.filter((l) => (!sector || l.sector === sector) && (!active || l.count > 0) && (!track || l.tracks.includes(track)));
  const totalPapers = Object.values(mx.counts).reduce((s, c) => s + Object.values(c).reduce((a, b) => a + b, 0), 0);
  const last = runs[runs.length - 1];
  const o = open && labs.find((l) => l.lab_id === open);
  const sum = (ls) => ls.reduce((a, l) => a + series(l).reduce((x, y) => x + y, 0), 0);
  const trackCounts = tracks.map((id) => [tname(id), sum(labs.filter((l) => l.tracks.includes(id)))]).sort((a, b) => b[1] - a[1]);
  const sectorName = (k) => (k === "industry" ? t("track.labs.industry") : t("track.labs.academia"));
  return html`
    <div class="tk-kpis">
      <div class="tk-kpi"><b>${labs.length}</b><span>${t("track.labs.kpi.tracked")}</span><em>${t("track.labs.kpi.tracked_hint", { n: labs.filter((l) => l.sector === "industry").length })}</em></div>
      <div class="tk-kpi"><b>${labs.filter((l) => l.count > 0).length}</b><span>${t("track.labs.kpi.active")}</span><em>${t("track.labs.kpi.active_hint", { n: runs.length })}</em></div>
      <div class="tk-kpi"><b>${totalPapers}</b><span>${t("track.labs.kpi.papers")}</span><em>${t("track.labs.kpi.papers_hint")}</em></div>
      <div class="tk-kpi"><b>${Object.values(mx.counts).filter((c) => c[last]).length}</b><span>${t("track.labs.kpi.recent")}</span><em>${last || "—"}</em></div>
      <div class="tk-kpi"><b>${labs.filter((l) => topOf(l).length).length}</b><span>${t("track.labs.kpi.top")}</span><em>${t("track.labs.kpi.top_hint")}</em></div>
    </div>
    <div class="tk-filters">
      <div class="seg">${[["", t("common.all")], ["academia", t("track.labs.academia")], ["industry", t("track.labs.industry")]].map(([k, l]) => html`<button class=${sector === k ? "on" : ""} onClick=${() => setSector(k)}>${l}</button>`)}</div>
      <select value=${track} onChange=${(e) => setTrack(e.target.value)}><option value="">${t("track.labs.all_tracks")}</option>${tracks.map((id) => html`<option value=${id}>${tname(id)}</option>`)}</select>
      <label class="muted small"><input type="checkbox" checked=${active} onChange=${(e) => setActive(e.target.checked)} /> ${t("track.labs.only_active")}</label>
      <span class="count">${t("track.labs.count", { n: shown.length })}</span>
    </div>
    <div class="tk-grid2">
      <div class="tk-card"><h3>${t("track.labs.heat_title")}</h3><div class="sub">${t("track.labs.heat_sub")}</div>
        <${Heatmap} rows=${shown.slice(0, allRows ? 99 : 15).map((l) => ({ key: l.lab_id, label: l.name, values: series(l) }))}
          cols=${runs.map((r) => r.slice(5))} colTitles=${runs} active=${open} labelW=${200}
          onRow=${(r) => setOpen(open === r.key ? null : r.key)} onCell=${(r) => setOpen(r.key)} />
        ${shown.length > 15 && html`<button class="linkbtn" onClick=${() => setAllRows(!allRows)}>${allRows ? t("track.labs.top15") : t("track.labs.show_all", { n: shown.length })}</button>`}</div>
      <div class="tk-card"><h3>${t("track.labs.tracks_title")}</h3><div class="sub">${t("track.labs.tracks_sub")}</div>
        <${HBars} items=${trackCounts} picked=${track ? tname(track) : null} onPick=${(label) => { const k = label.split(" ")[0]; setTrack(track === k ? "" : k); }} />
        <h3 style=${{ marginTop: "12px" }}>${t("track.labs.sector_title")}</h3>
        <${HBars} items=${[[sectorName("academia"), sum(labs.filter((l) => l.sector !== "industry"))], [sectorName("industry"), sum(labs.filter((l) => l.sector === "industry"))]]}
          picked=${sector ? sectorName(sector) : null}
          onPick=${(x) => { const k = x === sectorName("academia") ? "academia" : "industry"; setSector(sector === k ? "" : k); }} /></div>
    </div>
    ${o && html`<div class="tk-card" style=${{ marginBottom: "14px", borderColor: "var(--accent)" }}>
      <div class="card-head"><h2>${o.name}</h2><span><button class="chip" onClick=${() => onOpenNote(o.path)}><${Icon} name="note" sm />${t("track.detail.lab_note")}</button>
        <button class="abtn" onClick=${() => setOpen(null)}><${Icon} name="x" sm /></button></span></div>
      <div class="muted small">${[o.institution, o.country, sectorName(o.sector)].filter(Boolean).join(" · ")} · ${t("track.labs.members", { names: names(o.people) })}</div>
      ${topOf(o).length > 0 && html`<div class="tk-chips" style=${{ margin: "8px 0" }}>${topOf(o).map((s) => html`<span class="tk-badge ok">Top #${s.rank} ${s.name}</span>`)}</div>`}
      ${o.recent.map((p) => html`<${LabPaperRow} p=${p} lab=${o.name} toast=${toast} onChanged=${onChanged} />`)}
      ${!o.recent.length && html`<div class="muted small pad">${t("track.labs.no_papers")}</div>`}</div>`}
    <div class="tk-labs">${shown.map((l) => {
      const s = series(l), tiers = mx.tiers[l.lab_id] || {};
      const tops = topOf(l);
      return html`<button class=${"tk-lab" + (open === l.lab_id ? " on" : "") + (l.count ? "" : " quiet")} onClick=${() => setOpen(open === l.lab_id ? null : l.lab_id)}>
        <div class="tk-lab-top"><${Avatar} name=${l.name.replace(/\(.*\)/, "").trim()} square size=${36} />
          <div class="tk-person-id"><b title=${l.name}>${l.name}</b><span>${[l.institution, l.country].filter(Boolean).join(" · ")}</span></div>
          <div class="tk-lab-n"><b>${l.count}</b><span>${t(l.count === 1 ? "track.paper_word" : "track.papers_word")}</span></div></div>
        <div class="tk-lab-mid"><${Sparkline} values=${s} labels=${runs.map((r) => r.slice(5))} w=${96} h=${24} fmt=${papersOf} /><${TierStrip} tiers=${tiers} w=${70} /></div>
        <div class="tk-chips">${tops.map((x) => html`<span class="tk-badge ok" title=${x.name}>Top #${x.rank}</span>`)}${l.tracks.map((id) => html`<span class="tk-chip" title=${tname(id)}>${id}</span>`)}</div>
        ${l.recent[0] && html`<div class="tk-lab-last">${l.recent[0].published} · ${l.recent[0].title}</div>`}
      </button>`; })}</div>`;
}

function LabPaperRow({ p, lab, toast, onChanged }) {
  const [state, setState] = useState(null);
  const id = String(p.arxiv || "").replace(/v\d+$/, "");
  const add = async () => { try { const r = await api("/api/reading", { json: { arxiv: id, reason: `Lab Tracker ${p.run} · ${lab}` } }); setState(r.item.status); toast(pushResult(r.result)); onChanged?.(); } catch (e) { toast(e.message); } };
  return html`<div class="prow trow">${p.tier && html`<span class=${"tk-tier " + p.tier}>${p.tier}</span>`}
    <div class="p-main"><div class="t-title">${p.title}</div><div class="muted small">${p.published} · ${p.run}</div></div>
    <a class="abtn" href=${`https://arxiv.org/abs/${id}`} target="_blank" rel="noreferrer"><${Icon} name="external" sm /></a>
    ${state ? html`<span class="tag">${listed(state)}</span>` : html`<button class="tk-add" onClick=${add}><${Icon} name="plus" sm />${t("track.add_btn")}</button>`}</div>`;
}

// ---------------- capture + page ----------------

function Capture({ onAsk, toast, onChanged }) {
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [found, setFound] = useState(null);  // title search: { text, candidates, sources }
  const done = () => { setText(""); setReason(""); setFound(null); onChanged?.(); };
  const ask = (title) => onAsk(PROMPT_CAPTURE(title, reason), t("track.ask.capture"));
  const go = async () => {
    setBusy(true); setFound(null);
    try {
      const r = await api("/api/track/capture", { json: { text, reason } }).catch((e) => ({ error: e.message }));
      if (r.error) return toast(r.error);
      if (r.candidates) return setFound({ text, candidates: r.candidates, sources: r.sources });
      if (r.unresolved) { ask(text); done(); return; }
      toast(r.warning ? r.warning : r.result === "added" && r.resolved?.title ? t("track.added_inbox_title", { title: r.resolved.title.slice(0, 40) }) : pushResult(r.result));
      done();
    } finally { setBusy(false); }
  };
  const pick = async (c) => {
    try {
      const r = await api("/api/track/capture/pick", { json: { candidate: c, reason } });
      toast(pushResult(r.result)); done();
    } catch (e) { toast(e.message); }
  };
  return html`<div class="tk-capture-wrap"><div class="tk-capture" title=${t("track.capture.title")}>
    <${Icon} name="inbox" sm />
    <input placeholder=${t("track.capture.placeholder")} value=${text} onInput=${(e) => { setText(e.target.value); setFound(null); }} onKeyDown=${(e) => e.key === "Enter" && text.trim() && !busy && go()} />
    <input placeholder=${t("track.capture.reason")} value=${reason} onInput=${(e) => setReason(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && text.trim() && !busy && go()} />
    <button class="btn primary" disabled=${!text.trim() || busy} onClick=${go}>${busy ? t("track.searching") : t("track.capture.go")}</button>
  </div>
  ${found && html`<div class="tk-pick">
    <div class="tk-pick-head"><b>${t("track.capture.matches", { n: found.candidates.length })}</b><span class="muted small">${reason ? t("track.capture.pick") : t("track.capture.pick_reason")}</span>
      <span class="tk-srcs">${found.sources.map((s) => html`<span class=${"tk-srcst" + (s.ok ? "" : " bad")} title=${s.error || ""}><i />${s.label}<b>${s.n}</b>${!s.ok && html`<small>${t("track.capture.failed")}</small>`}</span>`)}</span>
      <span class="spacer" /><button class="abtn" title=${t("common.close")} onClick=${() => setFound(null)}><${Icon} name="x" sm /></button></div>
    ${found.candidates.map((c) => html`<div class="tk-pick-row" key=${c.key}>
      <div class="tk-pick-main"><div class="t" title=${c.title}>${c.title}</div>
        <div class="muted small">${[c.authors.slice(0, 3).join(", ") + (c.authors.length > 3 ? " " + t("track.et_al") : ""), c.year, c.venue].filter(Boolean).join(" · ")}</div>
        <div class="tk-chips">${c.sources.map((x) => html`<span class="tk-chip">${x}</span>`)}${c.arxiv && html`<span class="tk-chip on">${t("track.capture.has_arxiv")}</span>`}</div></div>
      ${safeUrl(c.url) && html`<a class="abtn" title=${t("track.open_source")} href=${safeUrl(c.url)} target="_blank" rel="noreferrer"><${Icon} name="external" sm /></a>`}
      <button class="tk-add" onClick=${() => pick(c)}><${Icon} name="plus" sm />${t("track.add_btn")}</button></div>`)}
    <div class="tk-pick-foot"><button class="linkbtn" onClick=${() => { ask(found.text); done(); }}>${t("track.capture.none")}</button></div>
  </div>`}</div>`;
}

const TABS = ["scholars", "radar", "follows", "labs"];
const tabLabel = (k) => t("track.tab." + k); // i18n-keys: track.tab.{scholars,radar,follows,labs}

export function TrackPage({ version, onAsk, toast, onOpenPaper, onOpenNote, onChanged }) {
  const [tab, setTab] = useState(prefs.get("track.tab", "scholars"));
  const [runsVersion, setRunsVersion] = useState(0);
  useEffect(() => prefs.set("track.tab", tab), [tab]);
  const common = { toast, onOpenPaper, onOpenNote, onAsk, onChanged, version };
  return html`<div class="page tk"><div class="page-inner wide">
    <div class="tk-head"><h1>${t("common.nav.track")}</h1>
      <div class="seg">${TABS.map((k) => html`<button class=${tab === k ? "on" : ""} data-tab=${k} onClick=${() => setTab(k)}>${tabLabel(k)}</button>`)}</div>
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

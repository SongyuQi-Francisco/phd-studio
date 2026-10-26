// Reading list: inbox -> T1/T2/T3 -> reading -> done, with snooze / dismiss.
// i18n: done
import { useEffect, useMemo, useRef, useState, useCallback } from "preact/hooks";
import { html, Icon, api, prefs } from "/lib.js";
import { t, lang } from "/i18n.js";
import { C, Donut, Bars, Progress, Sparkline, perWeek, weekLabel } from "/charts.js";
import { Thumb, loadThumbs, Skeleton, Empty, Collapse, Glyph, monthDay } from "/ui.js";

const SOURCES = ["tracker", "claude", "advisor", "self"]; // i18n-keys: reading.source.{tracker,claude,advisor,self}
const VERDICTS = ["key", "useful", "irrelevant"]; // i18n-keys: reading.verdict.{key,useful,irrelevant}
const DEPTHS = ["skim", "deep"]; // i18n-keys: reading.depth.{skim,deep}
const sourceName = (s) => (SOURCES.includes(s) ? t("reading.source." + s) : s);
const verdictName = (v) => (VERDICTS.includes(v) ? t("reading.verdict." + v) : v);
const depthName = (d) => (DEPTHS.includes(d) ? t("reading.depth." + d) : d);
// Pipeline stages for the donut: inbox (neutral), tiers on one blue ramp (T1 darkest), reading, done.
const stages = () => [
  { id: "inbox", label: t("common.status.inbox"), color: "var(--series-0)" },
  { id: "T1", label: "T1", color: "var(--ord-3)" },
  { id: "T2", label: "T2", color: "var(--ord-2)" },
  { id: "T3", label: "T3", color: "var(--ord-1)" },
  { id: "reading", label: t("common.status.reading"), color: C.orange },
  { id: "done", label: t("common.status.done"), color: C.aqua },
];

export function addDays(n) {
  const d = new Date(); d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

function age(days) {
  if (!days) return t("common.today");
  if (days < 7) return t("common.days_ago", { n: days });
  if (days < 60) return t("reading.age.weeks", { n: Math.round(days / 7) });
  return t("reading.age.months", { n: Math.round(days / 30) });
}

function shortBy(i) {
  const by = (i.pushed_by || "").replace(/^Reading Queue · /, "");
  return [sourceName(i.source), by].filter(Boolean).join(" · ");
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
      <h2>${t("reading.done.title")}</h2>
      <div class="dlg-title">${item.title}</div>
      <div class="field"><label>${t("reading.done.depth")}</label>
        <div class="seg">${DEPTHS.map((k) => html`<button class=${depth === k ? "on" : ""} onClick=${() => setDepth(k)}>${depthName(k)}</button>`)}</div></div>
      <div class="field"><label>${t("reading.done.verdict")}</label>
        <div class="seg">${VERDICTS.map((k) => html`<button class=${verdict === k ? "on" : ""} onClick=${() => setVerdict(verdict === k ? "" : k)}>${verdictName(k)}</button>`)}</div></div>
      <div class="field col">
        <div class="row-between"><label>${t("common.term.takeaway")}</label>
          <button class="tb-btn" disabled=${drafting} onClick=${draft}><${Icon} name="sparkle" sm />${drafting ? t("reading.done.drafting") : t("reading.done.draft")}</button></div>
        <textarea class="ta" rows="3" placeholder=${t("reading.done.takeaway_hint")} value=${takeaway} onInput=${(e) => setTakeaway(e.target.value)} />
        ${err && html`<div class="err">${err}</div>`}
      </div>
      <div class="row-end" style="margin-top:14px"><button class="btn ghost" onClick=${onClose}>${t("common.cancel")}</button><button class="btn primary" onClick=${save}>${t("common.save")}</button></div>
    </div>
  </div>`;
}

// Hover selects a row only when the pointer really moved, not when the list scrolls under a resting pointer.
let lastPointer = "";
const pointerMoved = (e) => { const p = `${e.screenX},${e.screenY}`; if (p === lastPointer) return false; lastPointer = p; return true; };

function Row({ i, selected, onSelect, onOpen, act, ready }) {
  const due = i.due && html`<span class=${"due" + (i.due <= addDays(2) ? " soon" : "")}>${t("reading.due", { date: monthDay(i.due + "T12:00:00") })}</span>`;
  const badge = i.status === "queued" ? html`<span class=${"tier " + i.tier}>${i.tier}</span>`
    : i.status === "reading" ? html`<span class="tier R">${t("common.status.reading")}</span>`
    : i.status === "done" ? html`<span class=${"tier D " + (i.verdict || "")}>${verdictName(i.verdict) || "✓"}</span>`
    : i.status === "inbox" ? html`<span class="newdot" title=${t("common.status.inbox")} />` : null;
  const stop = (fn) => (e) => { e.stopPropagation(); fn(); };
  return html`<div class=${"rl-row" + (selected ? " sel" : "")} onMouseMove=${(e) => pointerMoved(e) && !selected && onSelect()} onClick=${() => onOpen(i)}>
    <${Thumb} k=${i.key} title=${i.title} size="sm" ready=${ready} badge=${badge && html`<span class="badge">${badge}</span>`} />
    <div class="rl-main">
      <div class="rl-title">${i.title}${i.paper ? "" : html` <span class="muted">${{ doi: "DOI", openreview: "OpenReview", acl: "ACL" }[(i.key || "").split(":")[0]] || "arXiv"}</span>`}</div>
      <div class="rl-reason">${i.status === "done" && i.takeaway ? i.takeaway : i.reason}</div>
      <div class="rl-meta">
        ${i.lines.map((l) => html`<span class="tag">${l}</span>`)}
        ${due}
        <span>${shortBy(i)}</span><span>${age(i.age_days)}</span>
        ${i.status === "done" && i.depth && html`<span>${depthName(i.depth)} · ${i.read_at}</span>`}
      </div>
    </div>
    <div class="rl-actions">
      ${(i.status === "inbox" || i.status === "queued") && ["T1", "T2", "T3"].map((tier) => html`<button class=${"tbtn" + (i.tier === tier && i.status === "queued" ? " on" : "")} title=${t("reading.set_tier", { tier })} onClick=${stop(() => act(i, { tier, status: "queued" }))}>${tier}</button>`)}
      ${(i.status === "queued" || i.status === "reading") && html`<button class="abtn" title=${t("reading.done_title")} onClick=${stop(() => act(i, "done"))}><${Icon} name="check" sm /></button>`}
      ${(i.status === "inbox" || i.status === "queued") && html`<button class="abtn" title=${t("reading.snooze_title")} onClick=${stop(() => act(i, { status: "snoozed", snooze_until: addDays(7) }))}><${Icon} name="clock" sm /></button>
        <button class="abtn" title=${t("reading.skip_title")} onClick=${stop(() => act(i, { status: "dismissed" }))}><${Icon} name="x" sm /></button>`}
      ${(i.status === "snoozed" || i.status === "dismissed" || i.stale) && html`<button class="abtn txt" onClick=${stop(() => act(i, { status: "inbox" }))}>${t("reading.back_to_inbox")}</button>`}
    </div>
  </div>`;
}

function Insight({ items }) {
  const count = (id) => items.filter((i) => id === "inbox" ? i.status === "inbox" && !i.stale
    : ["T1", "T2", "T3"].includes(id) ? i.status === "queued" && i.tier === id : i.status === id).length;
  const data = stages().map((s) => ({ ...s, value: count(s.id) }));
  const active = data.filter((d) => d.id !== "done").reduce((a, d) => a + d.value, 0);
  const ins = perWeek(items.map((i) => i.pushed_at));
  const outs = perWeek(items.filter((i) => i.status === "done").map((i) => i.read_at));
  const sum = (a) => a.reduce((x, y) => x + y, 0);
  return html`<section class="card rl-insight">
    <div class="ri-mix">
      <${Donut} data=${data} size=${124} thickness=${13}><b>${active}</b><small>${t("reading.insight.in_list")}</small></${Donut}>
      <div class="dlegend">${data.map((d) => html`<span class=${d.value ? "" : "zero"}><i class="key" style=${{ background: d.color }} />${d.label}<b>${d.value}</b></span>`)}</div>
    </div>
    <div class="ri-flow">
      <div class="ri-head"><b>${t("reading.insight.flow")}</b><span class="muted small">${t("reading.insight.flow_sum", { added: sum(ins.counts), done: sum(outs.counts) })}</span></div>
      <${Bars} labels=${ins.labels} height=${118} series=${[{ name: t("reading.insight.added"), color: C.gray, values: ins.counts }, { name: t("common.status.done"), color: C.aqua, values: outs.counts }]} />
    </div>
  </section>`;
}

export function ReadingPage({ onOpen, toast, version }) {
  const [data, setData] = useState(null);
  const [line, setLine] = useState("");
  const [sel, setSel] = useState(0);
  const [doneItem, setDoneItem] = useState(null);
  const [open, setOpen] = useState({});
  const [charts, setCharts] = useState(prefs.get("rlCharts", true));
  const [ready, setReady] = useState(0);
  const byKey = useRef(false);

  const load = useCallback(() => api("/api/reading").then((r) => {
    setData(r);
    loadThumbs(r.items.map((i) => i.key)).then(() => setReady((n) => n + 1));
  }), []);
  useEffect(() => { load(); }, [version]);
  useEffect(() => prefs.set("rlCharts", charts), [charts]);

  const items = data?.items || [];
  const s = data?.summary;
  const filtered = useMemo(() => items.filter((i) => !line || i.lines.includes(line)), [items, line]);
  const groups = useMemo(() => {
    const by = (st) => filtered.filter((i) => i.status === st);
    const order = (a, b) => (a.due || "9").localeCompare(b.due || "9") || a.age_days - b.age_days;
    const queued = by("queued").sort(order);
    return [
      { id: "inbox", label: t("common.status.inbox"), items: by("inbox").filter((i) => !i.stale).sort((a, b) => a.age_days - b.age_days), hint: t("reading.group.inbox_hint") },
      { id: "reading", label: t("common.status.reading"), items: by("reading") },
      { id: "T1", label: t("common.tier.t1_week"), items: queued.filter((i) => i.tier === "T1") },
      { id: "T2", label: "T2", items: queued.filter((i) => i.tier === "T2") },
      { id: "T3", label: "T3", items: queued.filter((i) => i.tier === "T3") },
      { id: "done", label: t("common.status.done"), items: by("done").sort((a, b) => (b.read_at || "").localeCompare(a.read_at || "")), fold: true },
      { id: "snoozed", label: t("common.status.snoozed"), items: by("snoozed"), fold: true },
      { id: "stale", label: t("reading.group.stale"), items: by("inbox").filter((i) => i.stale), fold: true, hint: t("reading.group.stale_hint") },
      { id: "dismissed", label: t("common.status.dismissed"), items: by("dismissed"), fold: true },
    ];
  }, [filtered, lang()]);
  const flat = groups.flatMap((g) => (g.fold && !open[g.id] ? [] : g.items));
  const current = flat[Math.min(sel, flat.length - 1)];

  const act = async (i, patch) => {
    if (patch === "done") { setDoneItem(i); return; }
    if (patch.tier === "T1" && i.tier !== "T1" && s.T1 >= s.caps.T1) toast(t("reading.cap.t1", { n: s.T1 }));
    else if (patch.status === "queued" && i.status !== "queued" && s.queued >= s.caps.queued) toast(t("reading.cap.queued", { n: s.queued }));
    await api(`/api/reading/${i.id}`, { method: "PATCH", json: patch });
    load();
  };

  useEffect(() => {
    const k = (e) => {
      if (e.target?.closest?.("input, textarea, select, [contenteditable]") || doneItem || e.metaKey || e.ctrlKey) return;
      const i = current;
      if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); byKey.current = true; setSel((n) => Math.min(n + 1, flat.length - 1)); }
      else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); byKey.current = true; setSel((n) => Math.max(n - 1, 0)); }
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

  // Keep the keyboard selection in view (not the mouse one: that would scroll under the pointer).
  useEffect(() => { if (byKey.current) document.querySelector(".rl-row.sel")?.scrollIntoView?.({ block: "nearest", behavior: "smooth" }); byKey.current = false; }, [sel]);

  if (!s) return html`<${Skeleton} tiles=${5} rows=${6} />`;
  const reads = perWeek(items.filter((i) => i.status === "done").map((i) => i.read_at));
  const staleN = items.filter((i) => i.stale).length;
  const jump = (id) => { if (groups.find((g) => g.id === id)?.fold && !open[id]) setOpen({ ...open, [id]: true }); document.getElementById("g-" + id)?.scrollIntoView({ behavior: "smooth", block: "start" }); };
  const kpi = (n, label, sub, viz, id, warn, i) => html`<button class="kpi click" style=${{ "--i": i }} onClick=${() => jump(id)}>
    <span class="kpi-label">${label}</span><span class=${"kpi-value" + (warn ? " warn" : "")}>${n}</span>
    ${sub && html`<span class="kpi-sub">${sub}</span>`}${viz && html`<div class="kpi-viz">${viz}</div>`}</button>`;
  const active = groups.filter((g) => !g.fold).reduce((a, g) => a + g.items.length, 0);
  let idx = -1;
  return html`<div class="rl-page"><div class="rl-inner">
    <div class="rl-head">
      <div class="page-head"><h1>${t("common.nav.reading")}</h1>
        <div class="row-wrap tight">
          ${s.lines.length > 0 && html`<div class="seg">
            <button class=${!line ? "on" : ""} onClick=${() => setLine("")}>${t("common.all")}</button>
            ${s.lines.map((l) => html`<button class=${line === l ? "on" : ""} onClick=${() => setLine(l)}>${l}</button>`)}
          </div>`}
          <button class=${"tb-btn" + (charts ? " on" : "")} title=${charts ? t("reading.charts_hide") : t("reading.charts_show")} onClick=${() => setCharts(!charts)}><${Glyph} name="chart" sm />${t("reading.charts")}</button>
        </div>
      </div>
      <div class="kpis stagger">
        ${kpi(s.inbox, t("common.status.inbox"), staleN ? t("reading.kpi.stale", { n: staleN }) : t("reading.kpi.fresh"), null, "inbox", false, 0)}
        ${kpi(html`${s.T1}<small>/${s.caps.T1}</small>`, t("common.tier.t1_week"), s.T1 > s.caps.T1 ? t("reading.kpi.over") : t("reading.kpi.room", { n: s.caps.T1 - s.T1 }),
          html`<${Progress} value=${s.T1} max=${s.caps.T1} over=${s.T1 > s.caps.T1} color="var(--ord-3)" />`, "T1", s.T1 > s.caps.T1, 1)}
        ${kpi(html`${s.queued}<small>/${s.caps.queued}</small>`, t("common.status.queued"), "T1 + T2 + T3",
          html`<${Progress} value=${s.queued} max=${s.caps.queued} over=${s.queued > s.caps.queued} color="var(--ord-2)" />`, "T1", s.queued > s.caps.queued, 2)}
        ${kpi(s.reading, t("common.status.reading"), t("reading.kpi.reading_sub"), null, "reading", false, 3)}
        ${kpi(s.done_week, t("reading.kpi.read_week"), t("reading.kpi.weeks"),
          html`<${Sparkline} values=${reads.counts} labels=${reads.labels.map(weekLabel)} unit=${(n) => t("common.unit.papers", { n })} color=${C.aqua} height=${26} />`, "done", false, 4)}
      </div>
      <${Collapse} open=${charts}><${Insight} items=${filtered} /></${Collapse}>
    </div>
    ${items.length === 0 && html`<div class="card"><${Empty} icon="inbox" title=${t("reading.empty.title")} hint=${t("reading.empty.hint")} /></div>`}
    ${items.length > 0 && active === 0 && html`<div class="card"><${Empty} icon="check" title=${line ? t("reading.clear.line", { line }) : t("reading.clear.title")}
      hint=${t("reading.clear.hint")} /></div>`}
    ${groups.map((g) => g.items.length === 0 ? null : html`<section class="rl-group" id=${"g-" + g.id}>
      <div class=${"rl-gh" + (g.fold ? " fold" : "") + (open[g.id] ? " open" : "")} onClick=${() => g.fold && setOpen({ ...open, [g.id]: !open[g.id] })}>
        ${g.fold && html`<${Icon} name="right" sm />`}<span>${g.label}</span><small>${g.items.length}</small>
        ${g.hint && !g.fold && html`<em>${g.hint}</em>`}
      </div>
      ${g.fold
        ? html`<${Collapse} open=${!!open[g.id]}>${g.items.map((i) => { const vis = !!open[g.id]; if (vis) idx += 1; const my = idx;
            return html`<${Row} key=${i.id} i=${i} ready=${ready} selected=${vis && current && current.id === i.id} onSelect=${() => vis && setSel(my)} onOpen=${onOpen} act=${act} />`; })}</${Collapse}>`
        : g.items.map((i) => { idx += 1; const my = idx;
            return html`<${Row} key=${i.id} i=${i} ready=${ready} selected=${current && current.id === i.id} onSelect=${() => setSel(my)} onOpen=${onOpen} act=${act} />`; })}
    </section>`)}
    <div class="rl-keys"><span class="kbd">1</span><span class="kbd">2</span><span class="kbd">3</span> ${t("reading.keys.tier")} · <span class="kbd">S</span> ${t("reading.keys.snooze")} · <span class="kbd">X</span> ${t("reading.keys.skip")} · <span class="kbd">D</span> ${t("common.status.done")} · <span class="kbd">J</span>/<span class="kbd">K</span> ${t("reading.keys.move")} · <span class="kbd">↵</span> ${t("common.open")}</div>
  </div>
  ${doneItem && html`<${DoneDialog} item=${doneItem} onClose=${() => setDoneItem(null)} onSaved=${() => { setDoneItem(null); toast(t("reading.done.marked")); load(); }} />`}
  </div>`;
}

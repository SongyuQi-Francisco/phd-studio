// Settings rows for the background bilingual queue: when to make bilingual PDFs ahead of time,
// what is running and waiting, pause / resume, back-fill, retry and remove, and the disk it takes.
// i18n: done
import { useEffect, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { Progress } from "/charts.js";
import { t, lang } from "/i18n.js";

const MODES = [["off", null], ["T1", "T1"], ["T1T2", "T1 + T2"]];
const mb = (b) => (b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.round(b / 1e6) + " MB");
const DONE = ["done", "error", "skipped"]; // i18n-keys: bilingual.state.{done,error,skipped}
const doneLabel = (s) => (DONE.includes(s) ? t("bilingual.state." + s) : s);
const enc = encodeURIComponent;

export function BilingualSettings({ settings, save }) {
  const [q, setQ] = useState(null);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState("");
  const [days, setDays] = useState("90");
  const [sure, setSure] = useState(false); // second click confirms the cleanup
  const load = () => api("/api/bilingual-queue").then(setQ).catch(() => {});
  // lang(): the engine name and job errors come worded by the server, in the interface language.
  useEffect(() => { load(); }, [settings.bilingual_prefetch, settings.translate_engine, lang()]);
  useEffect(() => {
    if (!q?.running && !q?.busy?.length) return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [!!q?.running, q?.busy?.length]);
  if (!q) return null;

  const act = async (path, init) => {
    setErr("");
    try { setQ(await api(path, init)); } catch (e) { setErr(e.message); }
  };
  const noKey = (q.engine_setting === "deepseek" && !q.deepseek_key) || (q.engine_setting === "glm" && !q.glm_key);
  const tiers = q.mode === "T1" ? "T1" : t("bilingual.t1_or_t2");
  const hint = q.mode === "off" ? t("bilingual.hint_off")
    : t("bilingual.hint_on", { tiers, how: noKey ? t("bilingual.no_key", { engine: q.engine_setting === "glm" ? t("models.glm") : "DeepSeek" })
      : t("bilingual.using", { engine: q.engine }) });
  // DeepSeek and GLM cost credit; Google is the free fallback when the chosen engine has no key.
  const paid = !noKey && (q.engine_setting === "deepseek" || q.engine_setting === "glm");
  const clean = q.disk.cleanable[days] || { papers: 0, bytes: 0 };
  const n = q.queued.length;
  const summary = [q.running ? t("bilingual.running") : null, n ? t("bilingual.queued", { n }) : null, q.paused ? t("bilingual.paused") : null,
    q.busy.length && !q.running ? t("bilingual.waiting_reader") : null].filter(Boolean).join(" · ") || t("bilingual.empty");
  const show = q.mode !== "off" || n > 0 || !!q.running || q.recent.length > 0;

  return html`<div class="bqset">
    <div class="field"><div><label>${t("bilingual.prefetch")}</label><small>${hint}</small>
        <small class="bq-cost">${paid ? t("bilingual.cost_paid", { engine: q.engine }) : t("bilingual.cost")}</small></div>
      <div class="seg">${MODES.map(([v, l]) => html`<button class=${settings.bilingual_prefetch === v ? "on" : ""}
        onClick=${() => save({ bilingual_prefetch: v })}>${l ?? t("common.off")}</button>`)}</div></div>
    ${show && html`<div class="field col">
      <div class="row-between"><div><label>${t("bilingual.queue")}</label><small>${summary}</small></div>
        <span class="bq-btns">
          ${(n > 0 || q.paused) && html`<button class="tb-btn" onClick=${() => act("/api/bilingual-queue/pause", { json: { paused: !q.paused } })}>${q.paused ? t("common.resume") : t("common.pause")}</button>`}
          ${(n > 0 || q.recent.length > 0) && html`<button class="tb-btn" onClick=${() => setOpen(!open)}>${open ? t("common.hide") : t("common.show")}</button>`}
        </span></div>
      ${q.running && html`<div class="bq-run">
        <span class="bq-title" title=${q.running.title}>${q.running.title}</span>
        <${Progress} value=${q.running.progress || 0} max=${100} thin label=${`${q.running.progress || 0}%`} />
        <b>${q.running.progress || 0}%</b></div>`}
      ${err && html`<div class="err">${err}</div>`}
      ${open && html`<div class="bq-list">
        ${q.queued.map((j, i) => html`<div class="bq-row">
          <span class="bq-n">${i + 1}</span><span class="bq-title" title=${j.title}>${j.title}</span><span class="bq-why">${j.reason}</span>
          <button class="abtn" title=${t("bilingual.remove")} onClick=${() => act(`/api/bilingual-queue/${enc(j.key)}`, { method: "DELETE" })}><${Icon} name="x" sm /></button>
        </div>`)}
        ${q.recent.map((j) => html`<div class=${"bq-row past " + j.state}>
          <span class="bq-n">${j.state === "done" ? html`<${Icon} name="check" sm />` : "·"}</span>
          <span class="bq-title" title=${j.error || j.title}>${j.title}</span>
          <span class="bq-why" title=${j.error || ""}>${j.state !== "done" && j.error ? t("bilingual.state_error", { state: doneLabel(j.state), error: j.error }) : doneLabel(j.state)}</span>
          ${j.state === "error" && html`<button class="abtn txt" onClick=${() => act(`/api/bilingual-queue/${enc(j.key)}/retry`, { method: "POST" })}>${t("common.retry")}</button>`}
        </div>`)}
      </div>`}
      ${q.backfill > 0 && html`<div class="row-end"><button class="tb-btn" onClick=${() => act("/api/bilingual-queue/backfill", { method: "POST" })}>
        ${t("bilingual.backfill", { n: q.backfill, tiers: q.mode === "T1" ? "T1" : "T1/T2" })}</button></div>`}
    </div>`}
    ${q.disk.papers > 0 && html`<div class="field"><div><label>${t("bilingual.disk")}</label>
        <small>${t("bilingual.disk_hint", { size: mb(q.disk.bytes), n: q.disk.papers })}</small></div>
      <span class="bq-btns">
        <select value=${days} onChange=${(e) => { setDays(e.target.value); setSure(false); }} title=${t("bilingual.age_title")}>
          ${["30", "90", "180"].map((d) => html`<option value=${d}>${t("bilingual.days_ago", { n: Number(d) })}</option>`)}
        </select>
        <button class=${"tb-btn" + (sure ? " danger" : "")} disabled=${!clean.papers}
          onClick=${() => { if (!sure) { setSure(true); return; } setSure(false); act("/api/bilingual-queue/cleanup", { json: { days: Number(days) } }); }}>
          ${!clean.papers ? t("bilingual.nothing_to_clean") : sure ? t("bilingual.confirm_clean", { n: clean.papers }) : t("bilingual.clean", { n: clean.papers, size: mb(clean.bytes) })}</button>
      </span></div>`}
  </div>`;
}

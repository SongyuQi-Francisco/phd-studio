// Settings rows for the background bilingual queue: when to make bilingual PDFs ahead of time,
// what is running and waiting, pause / resume, back-fill, retry and remove.
import { useEffect, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { Progress } from "/charts.js";

const MODES = [["off", "关（只在点「双语」时生成）"], ["T1", "进入 T1 时"], ["T1T2", "进入 T1 或 T2 时"]];
const DONE = { done: "完成", error: "失败", skipped: "跳过" };
const enc = encodeURIComponent;

export function BilingualSettings({ settings, save }) {
  const [q, setQ] = useState(null);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState("");
  const load = () => api("/api/bilingual-queue").then(setQ).catch(() => {});
  useEffect(() => { load(); }, [settings.bilingual_prefetch, settings.translate_engine]);
  useEffect(() => {
    if (!q?.running && !q?.busy?.length) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [!!q?.running, q?.busy?.length]);
  if (!q) return null;

  const act = async (path, init) => {
    setErr("");
    try { setQ(await api(path, init)); } catch (e) { setErr(e.message); }
  };
  const noKey = q.engine_setting === "deepseek" && !q.deepseek_key;
  const hint = q.mode === "off" ? "论文定为 T1/T2 时在后台先翻好，打开即读"
    : `${noKey ? "没有 DeepSeek 密钥，用 Google 翻译" : "用 " + q.engine}，一次一篇`;
  const n = q.queued.length;
  const summary = [q.running ? "1 篇生成中" : null, n ? `${n} 篇排队` : null, q.paused ? "已暂停" : null,
    q.busy.length && !q.running ? "等阅读器里的生成完成" : null].filter(Boolean).join(" · ") || "队列是空的";
  const show = q.mode !== "off" || n || q.running || q.recent.length;

  return html`<div class="bqset">
    <div class="field"><div><label>预生成双语</label><small>${hint}</small></div>
      <select value=${settings.bilingual_prefetch} onChange=${(e) => save({ bilingual_prefetch: e.target.value })}>
        ${MODES.map(([v, l]) => html`<option value=${v}>${l}</option>`)}
      </select></div>
    ${show && html`<div class="field col">
      <div class="row-between"><div><label>双语队列</label><small>${summary}</small></div>
        <span class="bq-btns">
          ${(n > 0 || q.paused) && html`<button class="tb-btn" onClick=${() => act("/api/bilingual-queue/pause", { json: { paused: !q.paused } })}>${q.paused ? "继续" : "暂停"}</button>`}
          ${(n > 0 || q.recent.length > 0) && html`<button class="tb-btn" onClick=${() => setOpen(!open)}>${open ? "收起" : "查看"}</button>`}
        </span></div>
      ${q.running && html`<div class="bq-run">
        <span class="bq-title" title=${q.running.title}>${q.running.title}</span>
        <${Progress} value=${q.running.progress || 0} max=${100} thin label=${`${q.running.progress || 0}%`} />
        <b>${q.running.progress || 0}%</b></div>`}
      ${err && html`<div class="err">${err}</div>`}
      ${open && html`<div class="bq-list">
        ${q.queued.map((j, i) => html`<div class="bq-row">
          <span class="bq-n">${i + 1}</span><span class="bq-title" title=${j.title}>${j.title}</span><span class="bq-why">${j.reason}</span>
          <button class="abtn" title="移出队列" onClick=${() => act(`/api/bilingual-queue/${enc(j.key)}`, { method: "DELETE" })}><${Icon} name="x" sm /></button>
        </div>`)}
        ${q.recent.map((j) => html`<div class=${"bq-row past " + j.state}>
          <span class="bq-n">${j.state === "done" ? html`<${Icon} name="check" sm />` : "·"}</span>
          <span class="bq-title" title=${j.error || j.title}>${j.title}</span>
          <span class="bq-why" title=${j.error || ""}>${DONE[j.state] || j.state}${j.state !== "done" && j.error ? "：" + j.error : ""}</span>
          ${j.state === "error" && html`<button class="abtn txt" onClick=${() => act(`/api/bilingual-queue/${enc(j.key)}/retry`, { method: "POST" })}>重试</button>`}
        </div>`)}
      </div>`}
      ${q.backfill > 0 && html`<div class="row-end"><button class="tb-btn" onClick=${() => act("/api/bilingual-queue/backfill", { method: "POST" })}>
        把现有 ${q.backfill} 篇 ${q.mode === "T1" ? "T1" : "T1/T2"} 也排上</button></div>`}
    </div>`}
  </div>`;
}

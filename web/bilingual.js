// Settings rows for the background bilingual queue: when to make bilingual PDFs ahead of time,
// what is running and waiting, pause / resume, back-fill, retry and remove, and the disk it takes.
import { useEffect, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { Progress } from "/charts.js";

const MODES = [["off", "关"], ["T1", "T1"], ["T1T2", "T1 + T2"]];
const mb = (b) => (b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.round(b / 1e6) + " MB");
const DONE = { done: "完成", error: "失败", skipped: "跳过" };
const enc = encodeURIComponent;

export function BilingualSettings({ settings, save }) {
  const [q, setQ] = useState(null);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState("");
  const [days, setDays] = useState("90");
  const [sure, setSure] = useState(false); // second click confirms the cleanup
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
  const noKey = (q.engine_setting === "deepseek" && !q.deepseek_key) || (q.engine_setting === "glm" && !q.glm_key);
  const hint = q.mode === "off" ? "论文定为 T1 / T2 时先在后台翻好，打开即读"
    : `进入 ${q.mode === "T1" ? "T1" : "T1 或 T2"} 时后台生成，一次一篇；${noKey ? `没有 ${q.engine_setting === "glm" ? "智谱" : "DeepSeek"} 密钥，用 Google 翻译` : "用 " + q.engine}`;
  const clean = q.disk.cleanable[days] || { papers: 0, bytes: 0 };
  const n = q.queued.length;
  const summary = [q.running ? "1 篇生成中" : null, n ? `${n} 篇排队` : null, q.paused ? "已暂停" : null,
    q.busy.length && !q.running ? "等阅读器里的生成完成" : null].filter(Boolean).join(" · ") || "队列是空的";
  const show = q.mode !== "off" || n || q.running || q.recent.length;

  return html`<div class="bqset">
    <div class="field"><div><label>预生成双语</label><small>${hint}</small>
        <small class="bq-cost">只在这台 Mac 生效：每台各自生成、各存一份${q.engine === "DeepSeek" || q.engine === "智谱 GLM" ? `，两台都开会各花一次 ${q.engine} 额度` : ""}</small></div>
      <div class="seg">${MODES.map(([v, l]) => html`<button class=${settings.bilingual_prefetch === v ? "on" : ""}
        onClick=${() => save({ bilingual_prefetch: v })}>${l}</button>`)}</div></div>
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
    ${q.disk.papers > 0 && html`<div class="field"><div><label>双语 PDF 占用</label>
        <small>${mb(q.disk.bytes)} · ${q.disk.papers} 篇。清理只删已不在待读（收件箱、T1–T3、在读）里的；需要时点「双语」会重新生成</small></div>
      <span class="bq-btns">
        <select value=${days} onChange=${(e) => { setDays(e.target.value); setSure(false); }} title="生成多久以前的">
          ${["30", "90", "180"].map((d) => html`<option value=${d}>${d} 天前</option>`)}
        </select>
        <button class=${"tb-btn" + (sure ? " danger" : "")} disabled=${!clean.papers}
          onClick=${() => { if (!sure) { setSure(true); return; } setSure(false); act("/api/bilingual-queue/cleanup", { json: { days: Number(days) } }); }}>
          ${!clean.papers ? "没有可清理的" : sure ? `确定删除 ${clean.papers} 篇？` : `清理 ${clean.papers} 篇 · ${mb(clean.bytes)}`}</button>
      </span></div>`}
  </div>`;
}

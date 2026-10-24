// Settings → 截稿提醒: macOS notifications before deadlines and milestones (app/reminders.py).
// One row in the Settings modal; its sheet holds on/off, lead times, quiet hours, the Mac that
// sends, the background check (LaunchAgent, installed only from the button here) and a test send.
import { useEffect, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { Days } from "/ui.js";

const LEAD_CHOICES = [30, 14, 7, 3, 1, 0];
const TYPES = [["target", "投稿目标"], ["milestone", "里程碑 / Gate"], ["venue", "关注的会议"]];
const SKIPPED = { off: "提醒未开启", other_device: "由另一台 Mac 发送", quiet: "免打扰时段" };
const leadLabel = (d) => (d === 0 ? "当天" : `${d} 天`);

function ago(iso) {
  if (!iso) return "";
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  if (m < 2) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  if (m < 60 * 24) return `${Math.round(m / 60)} 小时前`;
  return `${Math.round(m / 1440)} 天前`;
}

function clock(iso) {
  const d = new Date(iso);
  const day = new Date(d); day.setHours(0, 0, 0, 0);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const n = Math.round((today - day) / 86400000);
  const hm = d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  return (n === 0 ? "今天 " : n === 1 ? "昨天 " : `${d.getMonth() + 1}月${d.getDate()}日 `) + hm;
}

function Switch({ on, onChange, disabled }) {
  return html`<button class=${"rm-switch" + (on ? " on" : "")} role="switch" aria-checked=${on} disabled=${disabled}
    onClick=${(e) => { e.stopPropagation(); onChange(!on); }} />`;
}

export function ReminderRow() {
  const [st, setSt] = useState(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { api("/api/reminders").then(setSt).catch(() => {}); }, []);
  if (!st) return null;
  const s = st.settings;
  const sub = s.enabled ? `${s.leads.map(leadLabel).join(" · ")}${st.upcoming_total ? ` · 30 天内 ${st.upcoming_total} 条` : ""}`
    : "截稿、里程碑临近时发 macOS 通知";
  return html`<div class="field rm-row" onClick=${() => setOpen(true)}>
      <div><label>截稿提醒</label><small>${sub}</small></div>
      <span class="rm-row-end"><span class=${"tag" + (s.enabled ? " ok" : "")}>${s.enabled ? "已开启" : "关"}</span><${Icon} name="right" sm /></span>
    </div>
    ${open && html`<${RemindersSheet} initial=${st} onClose=${(n) => { setOpen(false); if (n) setSt(n); }} />`}`;
}

// Next 30 days, one cell per day: how many reminders go out that day.
function Strip({ upcoming }) {
  const counts = {}, titles = {};
  for (const u of upcoming) { counts[u.days] = (counts[u.days] || 0) + 1; (titles[u.days] ||= []).push(u.title); }
  const max = Math.max(1, ...Object.values(counts));
  const cells = Array.from({ length: 31 }, (_, d) => {
    const v = counts[d] || 0;
    const date = new Date(); date.setDate(date.getDate() + d);
    const label = `${date.getMonth() + 1}月${date.getDate()}日` + (v ? `：${titles[d].join("；")}` : "");
    return html`<i class=${"heat-" + (v ? Math.min(4, Math.ceil((v / max) * 4)) : 0) + (d === 0 ? " today" : "")} title=${label} />`;
  });
  return html`<div class="rm-strip">${cells}</div>
    <div class="rm-strip-axis"><span>今天</span><span>+7</span><span>+14</span><span>+21</span><span>+30</span></div>`;
}

function RemindersSheet({ initial, onClose }) {
  const [st, setSt] = useState(initial);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [sent, setSent] = useState(null);
  const [custom, setCustom] = useState("");
  useEffect(() => { api("/api/reminders").then(setSt).catch(() => {}); }, []);
  const s = st.settings, ag = st.agent;

  const save = async (patch) => {
    setErr("");
    try { setSt(await api("/api/reminders/settings", { method: "PUT", json: patch })); } catch (e) { setErr(e.message); }
  };
  const toggleLead = (d) => {
    const next = s.leads.includes(d) ? s.leads.filter((x) => x !== d) : [...s.leads, d];
    if (next.length) save({ leads: next }); else setErr("至少留一个提前量");
  };
  const addLead = () => {
    const d = parseInt(custom, 10);
    if (Number.isInteger(d) && d >= 0 && d <= 90) { save({ leads: [...s.leads, d] }); setCustom(""); } else setErr("提前量是 0–90 天");
  };
  const toggleType = (t) => {
    const next = { ...s.types, [t]: !s.types[t] };
    if (Object.values(next).some(Boolean)) save({ types: next }); else setErr("至少提醒一类");
  };
  const agent = async (install) => {
    setBusy("agent"); setErr("");
    try { setSt(await api("/api/reminders/agent", { json: { install } })); } catch (e) { setErr(e.message); }
    setBusy("");
  };
  const test = async () => {
    setBusy("test"); setSent(null);
    try { setSent(await api("/api/reminders/test", { method: "POST" })); } catch (e) { setSent({ ok: false, error: e.message }); }
    setBusy("");
  };

  const designated = st.designated || st.device;
  const devices = st.devices.some((d) => d.id === designated) ? st.devices : [...st.devices, { id: designated }];
  const other = st.devices.find((d) => d.id === designated && d.id !== st.device);
  const staleDays = other?.seen_at ? Math.floor((Date.now() - new Date(other.seen_at)) / 86400000) : null;
  const leads = [...new Set([...LEAD_CHOICES, ...s.leads])].sort((a, b) => b - a);
  const last = st.last_check;
  const close = () => onClose(st);

  return html`<div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && close()}>
    <div class="modal rm-sheet">
      <div class="rm-head">
        <div><h2>截稿提醒</h2><p class="muted-p">截稿、投稿目标和里程碑临近时发 macOS 通知；工作台关着也会提醒。</p></div>
        <${Switch} on=${s.enabled} onChange=${(v) => save({ enabled: v })} />
      </div>

      <div class="rm-kpis">
        <div><b>${s.enabled ? st.upcoming_total : "–"}</b><span>30 天内提醒</span></div>
        <div><b>${s.enabled && st.upcoming[0] ? (st.upcoming[0].days === 0 ? "今天" : `${st.upcoming[0].days} 天后`) : "–"}</b><span>下一条</span></div>
        <div><b class=${ag.installed && ag.loaded ? "ok" : ""}>${ag.installed ? (ag.loaded ? "运行中" : "已安装") : "未安装"}</b><span>后台检查</span></div>
      </div>
      ${s.enabled && html`<${Strip} upcoming=${st.upcoming} />`}

      <div class="field col"><div><label>提前量</label><small>每个截止点在这些天数各提醒一次；Mac 关机错过的只补最近的一次</small></div>
        <div class="rm-chips">
          ${leads.map((d) => html`<button class=${"chip" + (s.leads.includes(d) ? " on" : "")} onClick=${() => toggleLead(d)}>${leadLabel(d)}</button>`)}
          <input class="rm-num" type="number" min="0" max="90" placeholder="自定" value=${custom}
            onInput=${(e) => setCustom(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && addLead()} />
        </div></div>

      <div class="field col"><div><label>提醒什么</label></div>
        <div class="rm-chips">${TYPES.map(([t, label]) => html`<button class=${"chip" + (s.types[t] ? " on" : "")} onClick=${() => toggleType(t)}>${label}</button>`)}</div></div>

      <div class="field"><div><label>免打扰</label><small>${s.quiet ? "这段时间不发，之后的检查再补" : "关"}</small></div>
        <span class="rm-quiet">
          ${s.quiet && html`<input type="time" value=${s.quiet_start} onChange=${(e) => save({ quiet_start: e.target.value })} />
            <span class="muted">–</span><input type="time" value=${s.quiet_end} onChange=${(e) => save({ quiet_end: e.target.value })} />`}
          <${Switch} on=${s.quiet} onChange=${(v) => save({ quiet: v })} />
        </span></div>

      <div class="field"><div><label>提醒设备</label>
          <small>${other ? `由 ${other.id} 发送，这台不发${staleDays != null && staleDays >= 3 ? ` · 它已 ${staleDays} 天没检查` : ""}`
            : "两台 Mac 共用这些设置，只由这一台发，不会重复"}</small></div>
        <select value=${designated} onChange=${(e) => save({ device: e.target.value })}>
          ${devices.map((d) => html`<option value=${d.id}>${d.id === st.device ? `本机 · ${d.id}` : d.id + (d.seen_at ? ` · ${ago(d.seen_at)}` : "")}</option>`)}
        </select></div>

      <div class="field"><div><label>后台检查</label>
          <small>${!ag.supported ? "只支持 macOS"
            : ag.installed ? `每天 ${ag.times.join(" / ")} 和开机时检查${ag.current ? "" : " · 指向别的位置，建议重新安装"}`
            : "装到本机后，工作台关着也会按时检查"}${ag.supported && ag.runner !== "app" ? " · 没找到用本项目构建的 PhD Studio.app，将用 venv 的 Python 运行（通知显示为「脚本编辑器」）" : ""}</small></div>
        ${ag.supported && html`<span class="rm-row-end">
          ${ag.installed && !ag.current && html`<button class="btn" disabled=${busy === "agent"} onClick=${() => agent(true)}>重新安装</button>`}
          <button class=${"btn" + (ag.installed ? " ghost" : " primary")} disabled=${busy === "agent"}
            onClick=${() => agent(!ag.installed)}>${busy === "agent" ? "…" : ag.installed ? "卸载" : "安装到本机"}</button></span>`}
      </div>
      ${ag.supported && !ag.installed && html`<p class="rm-note">会在 ~/Library/LaunchAgents 写一个 local.phd-studio.reminders.plist，macOS 会提示「已添加后台项目」；随时可在这里卸载。</p>`}

      <div class="field"><div><label>测试</label>
          <small>${sent ? (sent.ok ? (sent.via === "app" ? "已发送 · 显示为 PhD Studio，点击会回到工作台" : "已发送 · 由「脚本编辑器」代发（工作台不是从 PhD Studio.app 打开的），点击不会回到工作台")
              : `没发出去：${sent.error || "未知原因"}`)
            : last?.at ? `上次检查 ${clock(last.at)}${last.skipped ? ` · ${SKIPPED[last.skipped] || last.skipped}` : ` · 发出 ${last.sent?.length || 0} 条`}${last.failed?.length ? ` · ${last.failed.length} 条失败` : ""}`
            : "还没检查过"}</small></div>
        <button class="btn" disabled=${busy === "test"} onClick=${test}>${busy === "test" ? "发送中…" : "发一条测试通知"}</button></div>

      ${s.enabled && st.upcoming.length > 0 && html`<div class="rm-list">
        ${st.upcoming.slice(0, 6).map((u) => html`<div class="rm-item">
          <${Days} d=${u.days} /><div><div class="rm-title">${u.title}</div><small>${u.subtitle}</small></div></div>`)}
        ${st.upcoming_total > 6 && html`<small class="muted">还有 ${st.upcoming_total - 6} 条</small>`}
      </div>`}

      ${err && html`<p class="rm-err">${err}</p>`}
      <div class="row-end" style="margin-top:12px"><button class="btn" onClick=${close}>完成</button></div>
    </div>
  </div>`;
}

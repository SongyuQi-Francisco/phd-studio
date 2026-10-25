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

// The background check as it really is: launchd's run count and exit code, and the checker's own last record.
function agentHealth(ag, last) {
  if (!ag.supported) return { label: "–", note: "只支持 macOS" };
  if (!ag.installed) return { label: "未安装", note: "装到本机后，工作台关着也会每小时检查一次" };
  if (!ag.loaded) return { label: "已停止", bad: true, note: "装了但没有加载：点「重新安装」" };
  const lastAt = last?.at ? new Date(last.at).getTime() : 0;
  const fresh = lastAt >= ag.installed_at * 1000;
  const why = ag.runner !== "app" ? "。项目在桌面上时，macOS 的隐私保护可能不让 venv 的 Python 读它；用 launcher/make_app.sh 构建 PhD Studio.app 后重新安装" : "";
  if (fresh && last.ok === false) return { label: "出错", bad: true, note: `${clock(last.at)} 的检查出错：${last.error || last.failed?.[0]?.error || "未知原因"}` };
  if (ag.last_exit) return { label: "出错", bad: true, note: `后台检查退出码 ${ag.last_exit}，日志在 ${ag.log}${why}` };
  if (!fresh) return ag.runs ? { label: "没有记录", bad: true, note: `launchd 已运行 ${ag.runs} 次，但检查没有留下记录${why}` }
    : { label: "等待检查", note: "装好了，还没跑过" };
  if (Date.now() - lastAt > 3 * 3600e3) return { label: "停了", bad: true, note: `上次检查是 ${clock(last.at)}，之后没再跑；日志在 ${ag.log}` };
  return { label: "正常", ok: true, note: `${ag.every}检查，开机和唤醒后补一次 · 上次 ${clock(last.at)}${last.skipped ? ` · ${SKIPPED[last.skipped] || last.skipped}` : ` · 发出 ${last.sent?.length || 0} 条`}` };
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
  const sender = st.devices.find((d) => d.id === st.designated)?.name || st.designated;
  const sub = !s.enabled ? "截稿、里程碑临近时发 macOS 通知"
    : !st.is_designated ? `由 ${sender} 发提醒，这台不发`
    : `${s.leads.map(leadLabel).join(" · ")}${st.upcoming_total ? ` · 30 天内 ${st.upcoming_total} 条` : ""}`;
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
  const openNotifSettings = () => api("/api/reminders/notification-settings", { method: "POST" }).catch(() => {});
  const test = async () => {
    setBusy("test"); setSent(null);
    try { setSent(await api("/api/reminders/test", { method: "POST" })); } catch (e) { setSent({ ok: false, error: e.message }); }
    setBusy("");
  };

  const designated = st.designated || st.device;
  const devices = st.devices.some((d) => d.id === designated) ? st.devices : [...st.devices, { id: designated, name: designated }];
  const other = designated !== st.device ? devices.find((d) => d.id === designated) : null;
  const staleDays = other?.seen_at ? Math.floor((Date.now() - new Date(other.seen_at)) / 86400000) : null;
  const health = agentHealth(ag, st.last_check);
  const leads = [...new Set([...LEAD_CHOICES, ...s.leads])].sort((a, b) => b - a);
  const close = () => onClose(st);

  return html`<div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && close()}>
    <div class="modal rm-sheet">
      <div class="rm-head">
        <div><h2>截稿提醒</h2><p class="muted-p">截稿、投稿目标和里程碑临近时发 macOS 通知；工作台关着也会提醒。</p></div>
        <${Switch} on=${s.enabled} onChange=${(v) => save({ enabled: v })} />
      </div>
      ${s.enabled && other && html`<p class="rm-warn">这台 Mac 不发提醒：由 ${other.name} 发${staleDays != null && staleDays >= 1 ? `，它已 ${staleDays} 天没检查` : ""}。要改由这台发，在下面「提醒设备」里选本机。</p>`}

      <div class="rm-kpis">
        <div><b>${s.enabled ? st.upcoming_total : "–"}</b><span>30 天内提醒</span></div>
        <div><b>${s.enabled && st.upcoming[0] ? (st.upcoming[0].days === 0 ? "今天" : `${st.upcoming[0].days} 天后`) : "–"}</b><span>下一条</span></div>
        <div><b class=${health.ok ? "ok" : health.bad ? "bad" : ""}>${health.label}</b><span>后台检查</span></div>
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

      <div class="field"><div><label>每天几点提醒</label><small>当天的提醒在截止前至少 3 小时</small></div>
        <input class="rm-time" type="time" value=${s.time} onChange=${(e) => e.target.value && save({ time: e.target.value })} /></div>

      <div class="field"><div><label>免打扰</label><small>${s.quiet ? "这段时间不发，结束后补发；截止落在其中的，提前到开始前提醒" : "关"}</small></div>
        <span class="rm-quiet">
          ${s.quiet && html`<input type="time" value=${s.quiet_start} onChange=${(e) => save({ quiet_start: e.target.value })} />
            <span class="muted">–</span><input type="time" value=${s.quiet_end} onChange=${(e) => save({ quiet_end: e.target.value })} />`}
          <${Switch} on=${s.quiet} onChange=${(v) => save({ quiet: v })} />
        </span></div>

      <div class="field"><div><label>提醒设备</label>
          <small>${other ? `由 ${other.name} 发送，这台不发` : "两台 Mac 共用这些设置，只由这一台发，不会重复"}</small></div>
        <select value=${designated} onChange=${(e) => save({ device: e.target.value })}>
          ${devices.map((d) => html`<option value=${d.id}>${d.id === st.device ? `本机 · ${st.device_name}` : d.name + (d.seen_at ? ` · ${ago(d.seen_at)}` : "")}</option>`)}
        </select></div>

      <div class="field"><div><label>后台检查</label>
          <small class=${health.bad ? "rm-bad" : ""}>${health.note}${ag.installed && !ag.current ? " · 和现在的设置不一致，建议重新安装" : ""}${ag.supported && ag.runner !== "app" ? " · 没找到用本项目构建的 PhD Studio.app，将用 venv 的 Python 运行（通知显示为「脚本编辑器」）" : ""}</small></div>
        ${ag.supported && html`<span class="rm-row-end">
          ${ag.installed && (!ag.current || !ag.loaded) && ag.can_install && html`<button class="btn" disabled=${busy === "agent"} onClick=${() => agent(true)}>重新安装</button>`}
          <button class=${"btn" + (ag.installed ? " ghost" : " primary")} disabled=${busy === "agent" || (!ag.installed && !ag.can_install)}
            onClick=${() => agent(!ag.installed)}>${busy === "agent" ? "…" : ag.installed ? "卸载" : "安装到本机"}</button></span>`}
      </div>
      ${ag.supported && !ag.installed && html`<p class="rm-note">${ag.can_install
        ? "会在 ~/Library/LaunchAgents 写一个 local.phd-studio.reminders.plist，macOS 会提示「已添加后台项目」；随时可在这里卸载。"
        : "这个工作台用的是另一份配置（STUDIO_CONFIG），不能从这里安装；请在正式的工作台里装。"}</p>`}

      <div class="field"><div><label>测试</label>
          <small>${sent ? (sent.ok ? (sent.via === "app" ? "已发送 · 显示为 PhD Studio，点击会回到工作台" : "已发送 · 由「脚本编辑器」代发（工作台不是从 PhD Studio.app 打开的），点击不会回到工作台")
              : `没发出去：${sent.error || "未知原因"}`)
            : "确认这台 Mac 能收到通知"}</small></div>
        <span class="rm-row-end">
          ${(sent?.denied || st.notify.auth === "denied") && html`<button class="btn ghost" onClick=${openNotifSettings}>打开通知设置</button>`}
          <button class="btn" disabled=${busy === "test"} onClick=${test}>${busy === "test" ? "发送中…" : "发一条测试通知"}</button></span></div>
      ${st.notify.auth === "not_determined" && !sent && html`<p class="rm-note">第一次使用：点「发一条测试通知」，在系统提示里选「允许」。提示可能直接进通知中心（开着专注模式时）；后台检查不会自己请求授权。</p>`}
      ${st.notify.auth === "denied" && !sent && html`<p class="rm-err">PhD Studio 的通知被关掉了，提醒发不出来：点「打开通知设置」，打开「允许通知」。</p>`}

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

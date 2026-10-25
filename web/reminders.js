// Settings → Reminders: macOS notifications before deadlines and milestones (app/reminders.py).
// One row in the Settings modal; its sheet holds on/off, lead times, quiet hours, the Mac that
// sends, the background check (LaunchAgent, installed only from the button here) and a test send.
// i18n: done
import { useEffect, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { Days } from "/ui.js";
import { t, intlLocale } from "/i18n.js";

const LEAD_CHOICES = [30, 14, 7, 3, 1, 0];
const TYPES = ["target", "milestone", "venue"];
const SKIPPED = ["off", "other_device", "quiet"];
const skippedLabel = (s) => (SKIPPED.includes(s) ? t("reminders.skipped." + s) : s);
const leadLabel = (d) => (d === 0 ? t("reminders.same_day") : t("common.n_days", { n: d }));
const monthDay = (d) => d.toLocaleDateString(intlLocale(), { month: "short", day: "numeric" });

function ago(iso) {
  if (!iso) return "";
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  if (m < 2) return t("common.just_now");
  if (m < 60) return t("common.minutes_ago", { n: m });
  if (m < 60 * 24) return t("common.hours_ago", { n: Math.round(m / 60) });
  return t("common.days_ago", { n: Math.round(m / 1440) });
}

function clock(iso) {
  const d = new Date(iso);
  const day = new Date(d); day.setHours(0, 0, 0, 0);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const n = Math.round((today - day) / 86400000);
  const time = d.toLocaleTimeString(intlLocale(), { hour: "2-digit", minute: "2-digit", hour12: false });
  return t("reminders.at", { day: n === 0 ? t("common.today") : n === 1 ? t("common.yesterday") : monthDay(d), time });
}

// The background check as it really is: launchd's run count and exit code, and the checker's own last record.
function agentHealth(ag, last) {
  if (!ag.supported) return { label: "–", note: t("reminders.agent.mac_only") };
  if (!ag.installed) return { label: t("reminders.agent.not_installed"), note: t("reminders.agent.not_installed_note") };
  if (!ag.loaded) return { label: t("reminders.agent.stopped"), bad: true, note: t("reminders.agent.stopped_note") };
  const lastAt = last?.at ? new Date(last.at).getTime() : 0;
  const fresh = lastAt >= ag.installed_at * 1000;
  const why = ag.runner !== "app" ? t("reminders.agent.why_venv") : "";
  const unknown = t("reminders.unknown_reason");
  if (fresh && last.ok === false) return { label: t("reminders.agent.error"), bad: true,
    note: t("reminders.agent.check_failed", { at: clock(last.at), error: last.error || last.failed?.[0]?.error || unknown }) };
  if (ag.last_exit) return { label: t("reminders.agent.error"), bad: true, note: t("reminders.agent.exit_code", { code: ag.last_exit, log: ag.log }) + why };
  if (!fresh) return ag.runs ? { label: t("reminders.agent.no_record"), bad: true, note: t("reminders.agent.no_record_note", { n: ag.runs }) + why }
    : { label: t("reminders.agent.waiting"), note: t("reminders.agent.waiting_note") };
  if (Date.now() - lastAt > 3 * 3600e3) return { label: t("reminders.agent.stalled"), bad: true, note: t("reminders.agent.stalled_note", { at: clock(last.at), log: ag.log }) };
  return { label: t("reminders.agent.ok"), ok: true, note: t("reminders.agent.ok_note", { at: clock(last.at) })
    + " · " + (last.skipped ? skippedLabel(last.skipped) : t("reminders.agent.sent", { n: last.sent?.length || 0 })) };
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
  const sub = !s.enabled ? t("reminders.row_off")
    : !st.is_designated ? t("reminders.row_other", { device: sender })
    : s.leads.map(leadLabel).join(" · ") + (st.upcoming_total ? " · " + t("reminders.row_upcoming", { n: st.upcoming_total }) : "");
  return html`<div class="field rm-row" onClick=${() => setOpen(true)}>
      <div><label>${t("reminders.title")}</label><small>${sub}</small></div>
      <span class="rm-row-end"><span class=${"tag" + (s.enabled ? " ok" : "")}>${s.enabled ? t("common.on") : t("common.off")}</span><${Icon} name="right" sm /></span>
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
    const label = v ? t("reminders.strip_day", { day: monthDay(date), titles: titles[d].join(t("reminders.strip_sep")) }) : monthDay(date);
    return html`<i class=${"heat-" + (v ? Math.min(4, Math.ceil((v / max) * 4)) : 0) + (d === 0 ? " today" : "")} title=${label} />`;
  });
  return html`<div class="rm-strip">${cells}</div>
    <div class="rm-strip-axis"><span>${t("common.today")}</span><span>+7</span><span>+14</span><span>+21</span><span>+30</span></div>`;
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
    if (next.length) save({ leads: next }); else setErr(t("reminders.err_one_lead"));
  };
  const addLead = () => {
    const d = parseInt(custom, 10);
    if (Number.isInteger(d) && d >= 0 && d <= 90) { save({ leads: [...s.leads, d] }); setCustom(""); } else setErr(t("reminders.err_lead_range"));
  };
  const toggleType = (t) => {
    const next = { ...s.types, [t]: !s.types[t] };
    if (Object.values(next).some(Boolean)) save({ types: next }); else setErr(t("reminders.err_one_type"));
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
        <div><h2>${t("reminders.title")}</h2><p class="muted-p">${t("reminders.intro")}</p></div>
        <${Switch} on=${s.enabled} onChange=${(v) => save({ enabled: v })} />
      </div>
      ${s.enabled && other && html`<p class="rm-warn">${staleDays != null && staleDays >= 1 ? t("reminders.other_sender_stale", { device: other.name, n: staleDays })
        : t("reminders.other_sender", { device: other.name })}</p>`}

      <div class="rm-kpis">
        <div><b>${s.enabled ? st.upcoming_total : "–"}</b><span>${t("reminders.kpi_upcoming")}</span></div>
        <div><b>${s.enabled && st.upcoming[0] ? (st.upcoming[0].days === 0 ? t("common.today") : t("common.in_days", { n: st.upcoming[0].days })) : "–"}</b><span>${t("reminders.kpi_next")}</span></div>
        <div><b class=${health.ok ? "ok" : health.bad ? "bad" : ""}>${health.label}</b><span>${t("reminders.kpi_agent")}</span></div>
      </div>
      ${s.enabled && html`<${Strip} upcoming=${st.upcoming} />`}

      <div class="field col"><div><label>${t("reminders.leads")}</label><small>${t("reminders.leads_hint")}</small></div>
        <div class="rm-chips">
          ${leads.map((d) => html`<button class=${"chip" + (s.leads.includes(d) ? " on" : "")} onClick=${() => toggleLead(d)}>${leadLabel(d)}</button>`)}
          <input class="rm-num" type="number" min="0" max="90" placeholder=${t("reminders.custom")} value=${custom}
            onInput=${(e) => setCustom(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && addLead()} />
        </div></div>

      <div class="field col"><div><label>${t("reminders.types")}</label></div>
        <div class="rm-chips">${TYPES.map((ty) => html`<button class=${"chip" + (s.types[ty] ? " on" : "")} onClick=${() => toggleType(ty)}>${t("reminders.type." + ty)}</button>`)}</div></div>

      <div class="field"><div><label>${t("reminders.time")}</label><small>${t("reminders.time_hint")}</small></div>
        <input class="rm-time" type="time" value=${s.time} onChange=${(e) => e.target.value && save({ time: e.target.value })} /></div>

      <div class="field"><div><label>${t("reminders.quiet")}</label><small>${s.quiet ? t("reminders.quiet_hint") : t("common.off")}</small></div>
        <span class="rm-quiet">
          ${s.quiet && html`<input type="time" value=${s.quiet_start} onChange=${(e) => save({ quiet_start: e.target.value })} />
            <span class="muted">–</span><input type="time" value=${s.quiet_end} onChange=${(e) => save({ quiet_end: e.target.value })} />`}
          <${Switch} on=${s.quiet} onChange=${(v) => save({ quiet: v })} />
        </span></div>

      <div class="field"><div><label>${t("reminders.device")}</label>
          <small>${other ? t("reminders.device_other", { device: other.name }) : t("reminders.device_hint")}</small></div>
        <select value=${designated} onChange=${(e) => save({ device: e.target.value })}>
          ${devices.map((d) => html`<option value=${d.id}>${d.id === st.device ? t("reminders.this_mac", { name: st.device_name }) : d.name + (d.seen_at ? ` · ${ago(d.seen_at)}` : "")}</option>`)}
        </select></div>

      <div class="field"><div><label>${t("reminders.kpi_agent")}</label>
          <small class=${health.bad ? "rm-bad" : ""}>${health.note}${ag.installed && !ag.current ? " · " + t("reminders.agent.outdated") : ""}${ag.supported && ag.runner !== "app" ? " · " + t("reminders.agent.no_app") : ""}</small></div>
        ${ag.supported && html`<span class="rm-row-end">
          ${ag.installed && (!ag.current || !ag.loaded) && ag.can_install && html`<button class="btn" disabled=${busy === "agent"} onClick=${() => agent(true)}>${t("reminders.agent.reinstall")}</button>`}
          <button class=${"btn" + (ag.installed ? " ghost" : " primary")} disabled=${busy === "agent" || (!ag.installed && !ag.can_install)}
            onClick=${() => agent(!ag.installed)}>${busy === "agent" ? "…" : ag.installed ? t("reminders.agent.uninstall") : t("reminders.agent.install")}</button></span>`}
      </div>
      ${ag.supported && !ag.installed && html`<p class="rm-note">${ag.can_install ? t("reminders.agent.install_note") : t("reminders.agent.other_config")}</p>`}

      <div class="field"><div><label>${t("reminders.test")}</label>
          <small>${sent ? (sent.ok ? t(sent.via === "app" ? "reminders.test_sent_app" : "reminders.test_sent_script")
              : t("reminders.test_failed", { error: sent.error || t("reminders.unknown_reason") }))
            : t("reminders.test_hint")}</small></div>
        <span class="rm-row-end">
          ${(sent?.denied || st.notify.auth === "denied") && html`<button class="btn ghost" onClick=${openNotifSettings}>${t("reminders.open_notif")}</button>`}
          <button class="btn" disabled=${busy === "test"} onClick=${test}>${busy === "test" ? t("reminders.sending") : t("reminders.send_test")}</button></span></div>
      ${st.notify.auth === "not_determined" && !sent && html`<p class="rm-note">${t("reminders.first_time")}</p>`}
      ${st.notify.auth === "denied" && !sent && html`<p class="rm-err">${t("reminders.denied")}</p>`}

      ${s.enabled && st.upcoming.length > 0 && html`<div class="rm-list">
        ${st.upcoming.slice(0, 6).map((u) => html`<div class="rm-item">
          <${Days} d=${u.days} /><div><div class="rm-title">${u.title}</div><small>${u.subtitle}</small></div></div>`)}
        ${st.upcoming_total > 6 && html`<small class="muted">${t("reminders.more", { n: st.upcoming_total - 6 })}</small>`}
      </div>`}

      ${err && html`<p class="rm-err">${err}</p>`}
      <div class="row-end" style="margin-top:12px"><button class="btn" onClick=${close}>${t("common.done")}</button></div>
    </div>
  </div>`;
}

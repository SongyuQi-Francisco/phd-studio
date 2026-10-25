// Which model answers a conversation (#15): the picker at the top of every chat panel and the
// provider rows in Settings. Switching to another provider starts a new thread; a provider that isn't
// available is shown with its fix and is never replaced by another one.
// i18n: done
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, api, copyText } from "/lib.js";
import { t } from "/i18n.js";

export const PROVIDER_LABELS = { claude: "Claude", codex: "Codex", gemini: "Gemini", glm: "GLM", deepseek: "DeepSeek" };
export const providerLabel = (id) => (id === "glm" ? t("models.glm") : PROVIDER_LABELS[id] || id);

// One shared copy of /api/providers for every panel; Settings and an opened picker refresh it.
let cache = null;
let pending = null;
const subs = new Set();

export function refreshProviders() {
  if (!pending) {
    pending = api("/api/providers")
      .then((r) => { cache = r.providers; subs.forEach((f) => f(cache)); return cache; })
      .catch(() => cache)
      .finally(() => { pending = null; });
  }
  return pending;
}

export function useProviders() {
  const [list, setList] = useState(cache);
  useEffect(() => {
    subs.add(setList);
    if (cache) setList(cache); else refreshProviders();
    return () => { subs.delete(setList); };
  }, []);
  return list;
}

// The footer of an answer. Claude answers keep showing the model id they always did.
export function answeredBy(m) {
  const other = m.provider && m.provider !== "claude";
  if (!m.model) return other ? providerLabel(m.provider) : "";
  return other ? `${providerLabel(m.provider)} · ${m.model}` : m.model;
}

const modelLabel = (p, id) => p?.models.find((x) => x.id === id)?.label || id;

export function bindingName(binding, providers) {
  const p = providers?.find((x) => x.id === binding.provider);
  if (binding.provider === "claude" && !binding.model) return "Claude";
  const id = binding.model || p?.default_model || "";
  return `${providerLabel(binding.provider)} · ${modelLabel(p, id) || t("common.default")}`;
}

function ProviderGroup({ p, binding, onPick }) {
  const current = binding.provider === p.id;
  const isOn = (m) => current && (binding.model === m.id || (!binding.model && m.id === (p.id === "claude" ? "" : p.default_model)));
  const role = p.id === "claude" ? t("models.role_claude") : t("models.role_readonly");
  return html`<div class="mpick-group">
    <div class="mpick-head"><span class=${"dot " + (p.ok ? "ok" : "bad")} /><b>${providerLabel(p.id)}</b><small>${p.ok ? role : p.detail}</small></div>
    ${p.ok
      ? p.models.map((m) => html`<button class=${"mpick-item" + (isOn(m) ? " on" : "")} onClick=${() => onPick(p.id, m.id)}>
          <span>${m.label}</span>${m.vision && p.id !== "claude" && p.kind === "api" ? html`<small>${t("models.vision")}</small>` : ""}${isOn(m) ? html`<${Icon} name="check" sm />` : ""}</button>`)
      : html`<div class="mpick-fix">${p.fix}${p.cmd && html` <${CopyCmd} cmd=${p.cmd} />`}</div>`}
  </div>`;
}

function CopyCmd({ cmd }) {
  const [done, setDone] = useState(false);
  return html`<button class="pv-cmd" title=${t("common.copy")} onClick=${async () => { setDone(await copyText(cmd)); setTimeout(() => setDone(false), 1200); }}>
    <code>${cmd}</code><${Icon} name=${done ? "check" : "copy"} sm /></button>`;
}

export function ModelPicker({ binding, busy, fallbackOk, onPick }) {
  const providers = useProviders();
  const [open, setOpen] = useState(false);
  const ref = useRef();
  useEffect(() => {
    if (!open) return;
    refreshProviders();
    const close = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  const p = providers?.find((x) => x.id === binding.provider);
  const ok = p ? p.ok : binding.provider === "claude" ? fallbackOk : undefined;
  const pick = (pid, mid) => { setOpen(false); onPick(pid, mid); };
  return html`<div class="mpick" ref=${ref}>
    <button class="mpick-btn" disabled=${busy} title=${busy ? t("models.busy") : p && !p.ok ? t("models.unavailable", { label: providerLabel(p.id), detail: p.detail }) : t("models.switch")}
      onClick=${() => setOpen(!open)}>
      <span class=${"dot " + (ok === undefined ? "" : ok ? "ok" : "bad")} /><span class="mpick-name">${bindingName(binding, providers)}</span><${Icon} name="down" sm />
    </button>
    ${open && html`<div class="pop mpick-menu">
      <div class="mpick-note">${t("models.menu_note")}</div>
      ${providers ? providers.map((pv) => html`<${ProviderGroup} p=${pv} binding=${binding} onPick=${pick} />`)
        : html`<div class="mpick-note">${t("models.checking_all")}</div>`}
    </div>`}
  </div>`;
}

// Settings: one row per provider besides Claude (Claude has its own rows above).
export function ProviderSettings() {
  const providers = useProviders();
  useEffect(() => { refreshProviders(); }, []);
  const rows = providers?.filter((p) => p.id !== "claude");
  return html`<div class="field col">
    <div><label>${t("models.other")}</label><small>${t("models.other_hint")}</small></div>
    <div class="pv-list">
      ${rows ? rows.map((p) => html`<div class="pv-row">
          <span class=${"dot " + (p.ok ? "ok" : "bad")} />
          <div class="pv-text"><div><b>${providerLabel(p.id)}</b><span class="pv-kind">${p.kind === "cli" ? t("models.kind_cli") : t("models.kind_api")}</span></div>
            <small>${!p.ok && p.fix ? t("models.detail_fix", { detail: p.detail, fix: p.fix }) : p.detail}</small>
            ${!p.ok && p.cmd && html`<${CopyCmd} cmd=${p.cmd} />`}</div>
        </div>`)
        : html`<small>${t("models.checking")}</small>`}
    </div>
  </div>`;
}

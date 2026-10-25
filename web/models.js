// Which model answers a conversation (#15): the picker at the top of every chat panel and the
// provider rows in Settings. Switching to another provider starts a new thread; a provider that isn't
// available is shown with its fix and is never replaced by another one.
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, api, copyText } from "/lib.js";

export const PROVIDER_LABELS = { claude: "Claude", codex: "Codex", gemini: "Gemini", glm: "智谱 GLM", deepseek: "DeepSeek" };
export const providerLabel = (id) => PROVIDER_LABELS[id] || id;

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
  return `${providerLabel(binding.provider)} · ${modelLabel(p, id) || "默认"}`;
}

function ProviderGroup({ p, binding, onPick }) {
  const current = binding.provider === p.id;
  const isOn = (m) => current && (binding.model === m.id || (!binding.model && m.id === (p.id === "claude" ? "" : p.default_model)));
  const role = p.id === "claude" ? "可读可写，改 vault 前先给你看差异" : "只读";
  return html`<div class="mpick-group">
    <div class="mpick-head"><span class=${"dot " + (p.ok ? "ok" : "bad")} /><b>${p.label}</b><small>${p.ok ? role : p.detail}</small></div>
    ${p.ok
      ? p.models.map((m) => html`<button class=${"mpick-item" + (isOn(m) ? " on" : "")} onClick=${() => onPick(p.id, m.id)}>
          <span>${m.label}</span>${m.vision && p.id !== "claude" && p.kind === "api" ? html`<small>看图</small>` : ""}${isOn(m) ? html`<${Icon} name="check" sm />` : ""}</button>`)
      : html`<div class="mpick-fix">${p.fix}${p.cmd && html` <${CopyCmd} cmd=${p.cmd} />`}</div>`}
  </div>`;
}

function CopyCmd({ cmd }) {
  const [done, setDone] = useState(false);
  return html`<button class="pv-cmd" title="复制" onClick=${async () => { setDone(await copyText(cmd)); setTimeout(() => setDone(false), 1200); }}>
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
    <button class="mpick-btn" disabled=${busy} title=${busy ? "回答完再换模型" : p && !p.ok ? `${p.label}：${p.detail}` : "换模型（换到别的来源会开始新线程）"}
      onClick=${() => setOpen(!open)}>
      <span class=${"dot " + (ok === undefined ? "" : ok ? "ok" : "bad")} /><span class="mpick-name">${bindingName(binding, providers)}</span><${Icon} name="down" sm />
    </button>
    ${open && html`<div class="pop mpick-menu">
      <div class="mpick-note">换到别的来源会开始新线程（之前的对话保留在记录里，新模型看不到）。不可用时直接报错，不会换成别的模型。</div>
      ${providers ? providers.map((pv) => html`<${ProviderGroup} p=${pv} binding=${binding} onPick=${pick} />`)
        : html`<div class="mpick-note">正在检查各模型的登录状态…</div>`}
    </div>`}
  </div>`;
}

// Settings: one row per provider besides Claude (Claude has its own rows above).
export function ProviderSettings() {
  const providers = useProviders();
  useEffect(() => { refreshProviders(); }, []);
  const rows = providers?.filter((p) => p.id !== "claude");
  return html`<div class="field col">
    <div><label>其他模型</label><small>在每个对话面板顶部选；只读，改 vault 仍然只有 Claude。选中的模型不可用时直接报错，不会换成别的。</small></div>
    <div class="pv-list">
      ${rows ? rows.map((p) => html`<div class="pv-row">
          <span class=${"dot " + (p.ok ? "ok" : "bad")} />
          <div class="pv-text"><div><b>${p.label}</b><span class="pv-kind">${p.kind === "cli" ? "订阅登录" : "API Key"}</span></div>
            <small>${p.detail}${!p.ok && p.fix ? `：${p.fix}` : ""}</small>
            ${!p.ok && p.cmd && html`<${CopyCmd} cmd=${p.cmd} />`}</div>
        </div>`)
        : html`<small>正在检查登录状态…</small>`}
    </div>
  </div>`;
}

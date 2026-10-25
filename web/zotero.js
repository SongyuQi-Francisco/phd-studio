// Zotero: one-click push from the reader, the item link written into the note only after you
// confirm the exact line, and the Settings rows (status, auto mode, collection, API key help).
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { t, tx, lang } from "/i18n.js";

const AUTO = ["off", "triage", "all"]; // i18n-keys: zotero.auto.{off,triage,all}
const STATE = ["added", "exists", "linked", "error"]; // i18n-keys: zotero.state.{added,exists,linked,error}
const stateLabel = (s) => (STATE.includes(s) ? t("zotero.state." + s) : s);
const enc = encodeURIComponent;

const openInZotero = (key, toast) => api("/api/zotero/open", { json: { key } }).catch((e) => toast?.(e.message));

// The change to the note's front matter, shown exactly as it will be written.
function LinkChange({ lb, busy, onWrite, onSkip, writeLabel = t("zotero.write_note") }) {
  return html`<div class="perm zperm">
    <div class="perm-head"><${Icon} name="note" sm /><b title=${lb.path}>${lb.path}</b></div>
    <div class="diff">
      <div class="h">front matter</div>
      ${lb.replaces && html`<div class="d">- ${lb.replaces}</div>`}
      <div class="a">+ ${lb.line}</div>
    </div>
    <div class="perm-foot">
      ${onSkip && html`<button class="btn ghost" onClick=${onSkip}>${t("zotero.skip")}</button>`}
      <button class="btn primary" disabled=${busy} onClick=${onWrite}>${busy ? t("zotero.writing") : writeLabel}</button>
    </div>
  </div>`;
}

const writeLink = (lb) => api(`/api/zotero/link/${enc(lb.stem)}`, { json: { key: lb.key, base: lb.base } });

export function ZoteroButton({ stem, toast, onNoteChanged }) {
  const [st, setSt] = useState(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [at, setAt] = useState(null);
  const [fresh, setFresh] = useState(false); // just added by this click
  const wrap = useRef();
  // A status error (unreadable Zotero snapshot) shows in the popover instead of a push.
  const load = () => api(`/api/zotero/paper/${enc(stem)}`).then((s) => { setSt(s); if (s.error) setErr(s.error); return s; }).catch(() => null);

  useEffect(() => { setSt(null); setOpen(false); setErr(""); setFresh(false); load(); }, [stem]);
  useEffect(() => {
    if (!open) return;
    // Fixed, placed under the button: the toolbar scrolls horizontally and would clip an absolute popover.
    const r = wrap.current.getBoundingClientRect();
    setAt({ left: Math.max(8, Math.min(r.left, window.innerWidth - 416)) + "px", top: r.bottom + 6 + "px" });
    const down = (e) => { if (!wrap.current?.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", down, true);
    return () => document.removeEventListener("mousedown", down, true);
  }, [open]);

  const push = async () => {
    setBusy("push"); setErr("");
    try {
      const r = await api(`/api/zotero/paper/${enc(stem)}`, { method: "POST" });
      const s = (await load()) || st;
      const warn = r.warnings?.length ? `（${r.warnings[0]}）` : "";
      setFresh(r.state === "added");
      if (s?.linkback) { setOpen(true); if (warn) toast(warn.slice(1, -1)); }
      else if (r.state === "pending") toast(r.warnings[0]);
      else toast((r.state === "exists" ? "Zotero 里已经有这篇" : "已加入 Zotero") + warn);
    } catch (e) { setErr(e.message); setOpen(true); }
    setBusy("");
  };
  const confirmLink = async () => {
    setBusy("link"); setErr("");
    try {
      await writeLink(st.linkback);
      toast("Zotero 链接已写进笔记");
      setOpen(false);
      onNoteChanged?.();
    } catch (e) { setErr(e.message); }
    await load();
    setBusy("");
  };

  if (!st) return null;
  const linked = st.state === "linked";
  const inLib = st.state === "in_library";
  const title = linked ? "已在 Zotero，笔记里有链接" : inLib ? (st.linkback ? "Zotero 里已有，笔记还没有链接" : "已在 Zotero") : "加入 Zotero";
  const click = () => (linked || inLib || err ? setOpen(!open) : push());

  return html`<span class="zwrap" ref=${wrap}>
    <button class=${"tb-btn" + (open ? " on" : "")} title=${title} disabled=${busy === "push"} onClick=${click}>
      ${linked || inLib ? html`<span class=${"zb" + (linked ? " on" : "")}>Z</span>` : html`<${Icon} name="plus" sm />`}
      ${busy === "push" ? "入库中…" : "Zotero"}${inLib && st.linkback ? html`<i class="zdot" />` : null}
    </button>
    ${open && at && html`<div class="pop zpop" style=${at}>
      ${err && html`<div class="err">${err}</div>`}
      ${err && !st.desktop && !st.web && html`<p class="zhint">打开 Zotero 桌面端后再点一次；或在「设置 → Zotero」里按说明建一个网络密钥。</p>`}
      ${linked && html`<p class="zline">已在 Zotero，笔记里有链接。</p>`}
      ${inLib && !st.linkback && html`<p class="zline">${st.key ? "已在 Zotero。" : "已存进 Zotero，还在等它写入数据库。"}${!st.note ? "建笔记后可以把链接写回笔记。" : ""}</p>`}
      ${inLib && st.linkback && html`<p class="zline">${fresh ? "已加入 Zotero。" : "Zotero 里有这篇。"}把条目链接写进笔记？</p>
        <${LinkChange} lb=${st.linkback} busy=${busy === "link"} onWrite=${confirmLink} onSkip=${() => setOpen(false)} />`}
      ${st.key && html`<div class="row-end"><button class="btn ghost" onClick=${() => openInZotero(st.key, toast)}>在 Zotero 中打开</button></div>`}
      ${err && !linked && !inLib && html`<div class="row-end"><button class="btn primary" onClick=${push}>再试一次</button></div>`}
    </div>`}
  </span>`;
}

// ---------- Settings ----------

export function ZoteroSettings({ settings, save, toast }) {
  const [z, setZ] = useState(null);
  const [showPending, setShowPending] = useState(false);
  const [help, setHelp] = useState(false);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const load = () => api("/api/zotero/setup").then(setZ).catch(() => {});
  useEffect(() => { load(); }, [lang()]); // the server's messages follow the interface language
  if (!z) return null;

  const ways = z.snapshot_error ? z.snapshot_error : z.desktop ? t(z.web ? "zotero.ways.desktop_web" : "zotero.ways.desktop")
    : t(z.web ? "zotero.ways.web" : "zotero.ways.none");
  const colls = [["", t("zotero.coll_root")], ...z.collections.map((c) => [c.key, c.name])];
  if (settings.zotero_collection && !z.collections.some((c) => c.key === settings.zotero_collection)) colls.push([settings.zotero_collection, t("zotero.coll_gone")]);
  const recent = z.auto.recent.slice(0, 3);

  const write = async (items) => {
    setBusy(items.length > 1 ? "all" : items[0].stem); setErr("");
    let n = 0;
    for (const lb of items) {
      try { await writeLink(lb); n++; } catch (e) { setErr(t("zotero.write_failed", { stem: lb.stem, error: e.message })); break; }
    }
    if (n) toast?.(t("zotero.written", { n }));
    await load();
    setBusy("");
  };

  return html`<div class="zset">
    <div class="field"><div><label>Zotero</label><small>${ways}</small></div>
      <span class="zset-r"><button class="tb-btn" title=${t("zotero.key_help_title")} onClick=${() => setHelp(!help)}>${t("zotero.key_help")}</button>
        <span class=${"dot " + ((z.desktop || z.web) && !z.snapshot_error ? "ok" : "bad")} /></span></div>
    ${help && html`<div class="zhelp">
      <p>${t("zotero.help.intro")}</p>
      <ol>
        <li>${tx("zotero.help.step1", { link: html`<a href="https://www.zotero.org/settings/keys" target="_blank" rel="noopener">zotero.org/settings/keys</a>` })}</li>
        <li>${t("zotero.help.step2")}</li>
        <li>${tx("zotero.help.step3", { file: html`<code>${z.secrets_file}</code>` })}<pre>ZOTERO_API_KEY=…\nZOTERO_USER_ID=…</pre></li>
      </ol>
      <p class="muted">${t("zotero.help.note")}</p>
    </div>`}
    <div class="field"><div><label>${t("zotero.auto_label")}</label>
        <small>${recent.length ? t("zotero.recent", { list: recent.map((r) => `${r.paper} ${stateLabel(r.state)}`).join(" · ") }) : t("zotero.auto_hint")}</small></div>
      <select value=${settings.zotero_auto} onChange=${(e) => save({ zotero_auto: e.target.value })}>
        ${AUTO.map((v) => html`<option value=${v}>${t("zotero.auto." + v)}</option>`)}
      </select></div>
    <div class="field"><div><label>${t("zotero.collection")}</label><small>${t("zotero.collection_hint")}</small></div>
      <select value=${settings.zotero_collection} onChange=${(e) => save({ zotero_collection: e.target.value })}>
        ${colls.map(([v, l]) => html`<option value=${v}>${l}</option>`)}
      </select></div>
    ${z.pending.length > 0 && html`<div class="field col">
      <div class="row-between"><div><label>${t("zotero.pending")}</label><small>${t("zotero.pending_hint", { n: z.pending.length })}</small></div>
        <button class="tb-btn" onClick=${() => setShowPending(!showPending)}>${showPending ? t("common.hide") : t("common.show")}</button></div>
      ${showPending && html`<div class="zpending">
        ${err && html`<div class="err">${err}</div>`}
        ${z.pending.map((lb) => html`<${LinkChange} lb=${lb} busy=${busy === lb.stem || busy === "all"} writeLabel=${t("zotero.write")} onWrite=${() => write([lb])} />`)}
        ${z.pending.length > 1 && html`<div class="row-end"><button class="btn primary" disabled=${!!busy} onClick=${() => write(z.pending)}>
          ${busy === "all" ? t("zotero.writing") : t("zotero.write_all", { n: z.pending.length })}</button></div>`}
      </div>`}
    </div>`}
  </div>`;
}

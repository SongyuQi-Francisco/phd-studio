// Zotero: one-click push from the reader, the item link written into the note only after you
// confirm the exact line, and the Settings rows (status, auto mode, collection, API key help).
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";

const AUTO = [["off", "关"], ["triage", "定了优先级或建笔记时"], ["all", "一进清单就加"]];
const STATE = { added: "已加入", exists: "已有", linked: "已链接", error: "失败" };
const enc = encodeURIComponent;

const openInZotero = (key, toast) => api("/api/zotero/open", { json: { key } }).catch((e) => toast?.(e.message));

// The change to the note's front matter, shown exactly as it will be written.
function LinkChange({ lb, busy, onWrite, onSkip, writeLabel = "写入笔记" }) {
  return html`<div class="perm zperm">
    <div class="perm-head"><${Icon} name="note" sm /><b title=${lb.path}>${lb.path}</b></div>
    <div class="diff">
      <div class="h">front matter</div>
      ${lb.replaces && html`<div class="d">- ${lb.replaces}</div>`}
      <div class="a">+ ${lb.line}</div>
    </div>
    <div class="perm-foot">
      ${onSkip && html`<button class="btn ghost" onClick=${onSkip}>不写</button>`}
      <button class="btn primary" disabled=${busy} onClick=${onWrite}>${busy ? "写入中…" : writeLabel}</button>
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
  const load = () => api(`/api/zotero/paper/${enc(stem)}`).then((s) => { setSt(s); return s; }).catch(() => null);

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
  useEffect(() => { load(); }, []);
  if (!z) return null;

  const ways = z.desktop ? "桌面端在运行" + (z.web ? "，网络密钥已配置" : "")
    : z.web ? "桌面端没开，用网络密钥" : "桌面端没开，也没有网络密钥";
  const colls = [["", "库的根目录"], ...z.collections.map((c) => [c.key, c.name])];
  if (settings.zotero_collection && !z.collections.some((c) => c.key === settings.zotero_collection)) colls.push([settings.zotero_collection, "（已不存在的分类）"]);
  const recent = z.auto.recent.slice(0, 3);

  const write = async (items) => {
    setBusy(items.length > 1 ? "all" : items[0].stem); setErr("");
    let n = 0;
    for (const lb of items) {
      try { await writeLink(lb); n++; } catch (e) { setErr(`${lb.stem}：${e.message}`); break; }
    }
    if (n) toast?.(`已写入 ${n} 篇笔记的 Zotero 链接`);
    await load();
    setBusy("");
  };

  return html`<div class="zset">
    <div class="field"><div><label>Zotero</label><small>${ways}</small></div>
      <span class="zset-r"><button class="tb-btn" title="怎么建网络密钥" onClick=${() => setHelp(!help)}>密钥说明</button>
        <span class=${"dot " + (z.desktop || z.web ? "ok" : "bad")} /></span></div>
    ${help && html`<div class="zhelp">
      <p>桌面端开着时直接存进本机 Zotero；没开时用 Zotero 网络服务，需要你自己建一个密钥：</p>
      <ol>
        <li>打开 <a href="https://www.zotero.org/settings/keys" target="_blank" rel="noopener">zotero.org/settings/keys</a>，点 Create new private key。</li>
        <li>勾选 Allow library access 和 Allow write access，保存。</li>
        <li>把显示的密钥和页面上的 user ID 写进 <code>${z.secrets_file}</code>：<pre>ZOTERO_API_KEY=…\nZOTERO_USER_ID=…</pre></li>
      </ol>
      <p class="muted">工作台只读这个文件，不会问你的 Zotero 密码，也不在界面里填密钥。</p>
    </div>`}
    <div class="field"><div><label>自动入库</label>
        <small>${recent.length ? "最近：" + recent.map((r) => `${r.paper} ${STATE[r.state] || r.state}`).join(" · ") : "论文到这一步时自动加入 Zotero；链接仍要你确认才写进笔记"}</small></div>
      <select value=${settings.zotero_auto} onChange=${(e) => save({ zotero_auto: e.target.value })}>
        ${AUTO.map(([v, l]) => html`<option value=${v}>${l}</option>`)}
      </select></div>
    <div class="field"><div><label>放进分类</label><small>新条目进 Zotero 的哪个分类</small></div>
      <select value=${settings.zotero_collection} onChange=${(e) => save({ zotero_collection: e.target.value })}>
        ${colls.map(([v, l]) => html`<option value=${v}>${l}</option>`)}
      </select></div>
    ${z.pending.length > 0 && html`<div class="field col">
      <div class="row-between"><div><label>待写回的链接</label><small>${z.pending.length} 篇已在 Zotero，笔记里还没有链接</small></div>
        <button class="tb-btn" onClick=${() => setShowPending(!showPending)}>${showPending ? "收起" : "查看"}</button></div>
      ${showPending && html`<div class="zpending">
        ${err && html`<div class="err">${err}</div>`}
        ${z.pending.map((lb) => html`<${LinkChange} lb=${lb} busy=${busy === lb.stem || busy === "all"} writeLabel="写入" onWrite=${() => write([lb])} />`)}
        ${z.pending.length > 1 && html`<div class="row-end"><button class="btn primary" disabled=${!!busy} onClick=${() => write(z.pending)}>
          ${busy === "all" ? "写入中…" : `以上 ${z.pending.length} 篇全部写入`}</button></div>`}
      </div>`}
    </div>`}
  </div>`;
}

// Read-only side sheet for any vault note; [[wikilinks]] navigate, paper notes open in the reader.
import { useEffect, useState } from "preact/hooks";
import { html, Icon, api, renderMarkdown } from "/lib.js";

export function NoteSheet({ path, onClose, onOpenPaper, actions = [] }) {
  const [stack, setStack] = useState([path]);
  const [n, setN] = useState(null);
  const [err, setErr] = useState("");
  const cur = stack[stack.length - 1];
  useEffect(() => { setStack([path]); }, [path]);
  useEffect(() => {
    setN(null); setErr("");
    api(`/api/note?path=${encodeURIComponent(cur)}`).then(setN).catch((e) => setErr(e.message));
  }, [cur]);
  const click = async (e) => {
    const w = e.target.closest(".wikilink");
    if (!w) return;
    try {
      const r = await api(`/api/note/find?name=${encodeURIComponent(w.dataset.target || w.textContent)}`);
      if (r.paper && onOpenPaper) onOpenPaper(r.paper);
      else setStack([...stack, r.path]);
    } catch (x) { setErr(x.message); }
  };
  const fm = n?.frontmatter || {};
  const tags = ["type", "status", "priority", "horizon", "created"].filter((k) => fm[k] != null).map((k) => `${k}: ${fm[k]}`);
  return html`<div class="sheet-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}><div class="sheet note-sheet">
    <div class="sheet-head">
      <div style="min-width:0">${stack.length > 1 && html`<button class="linkbtn" onClick=${() => setStack(stack.slice(0, -1))}>← 返回</button>`}
        <h2>${n?.name || "…"}</h2><div class="muted small">${cur}</div></div>
      <button class="tb-btn" onClick=${onClose}><${Icon} name="x" /></button>
    </div>
    <div class="row-wrap">
      ${n?.obsidian_url && html`<a class="chip" href=${n.obsidian_url}><${Icon} name="external" sm />在 Obsidian 打开</a>`}
      ${actions.map((a) => html`<button class="chip" onClick=${() => a.run(n)}><${Icon} name=${a.icon || "sparkle"} sm />${a.label}</button>`)}
    </div>
    ${tags.length > 0 && html`<div class="row-wrap">${tags.map((t) => html`<span class="tag">${t}</span>`)}</div>`}
    ${err && html`<div class="err">${err}</div>`}
    ${n && html`<div class="md note-body" onClick=${click} dangerouslySetInnerHTML=${{ __html: renderMarkdown(n.body) }} />`}
  </div></div>`;
}

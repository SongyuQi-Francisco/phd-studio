// Detail panel of the selected node: status, title, type, description, evidence, connections, history,
// then the sections extensions register (peers, results). Every edit is one batch (local YAML, no approval).
import { useEffect, useState } from "preact/hooks";
import { html, Icon, renderMarkdown, composing } from "/lib.js";
import { rmap } from "/rmap/registry.js";
import { t } from "/rmap/strings.js";
import { describe, titles, when } from "/rmap/describe.js";
import { NEXT_TYPES } from "/rmap/map.js";
import { useAutoFocus, safeUrl } from "/rmap/util.js";

const EV_ICON = { paper: "book", note: "note", file: "file", url: "external" };

function EvidenceAdd({ node, ctx, onDone }) {
  const kind = ctx.meta.kinds.find((k) => k.name === "evidence");
  const [f, setF] = useState({ type: "paper", target: "", title: "", stance: "supports" });
  const [papers, setPapers] = useState([]);
  const targetRef = useAutoFocus(node.id);
  useEffect(() => { ctx.api.call("/api/papers").then((r) => setPapers(r.papers.map((p) => p.stem))).catch(() => {}); }, []);
  const save = async (e) => {
    e.preventDefault();
    try {
      await ctx.api.apply([{ op: "attach.add", kind: "evidence", node: node.id, set: { ...f, title: f.title || null } }]);
      onDone();
    } catch (err) { ctx.toast(err.message); }
  };
  const ph = { paper: t("rmap.ev.ph.paper"), note: t("rmap.ev.ph.note"), file: t("rmap.ev.ph.file"), url: "https://…" }[f.type];
  return html`<form class="mp-evadd" onSubmit=${save}>
    <div class="mp-evrow">
      <select value=${f.type} onChange=${(e) => setF({ ...f, type: e.target.value })}>
        ${kind.types.map((x) => html`<option value=${x}>${t("rmap.evidence." + x)}</option>`)}</select>
      <select value=${f.stance} onChange=${(e) => setF({ ...f, stance: e.target.value })}>
        ${kind.stances.map((x) => html`<option value=${x}>${t("rmap.stance." + x)}</option>`)}</select>
    </div>
    <input ref=${targetRef} list=${f.type === "paper" ? "mp-papers" : null} placeholder=${ph} value=${f.target} onInput=${(e) => setF({ ...f, target: e.target.value })} />
    ${f.type === "paper" && html`<datalist id="mp-papers">${papers.map((p) => html`<option value=${p} />`)}</datalist>`}
    <input placeholder=${t("rmap.ev.ph.title")} value=${f.title} onInput=${(e) => setF({ ...f, title: e.target.value })} />
    <div class="row-end"><button type="button" class="btn ghost sm" onClick=${onDone}>${t("rmap.cancel")}</button>
      <button class="btn primary sm" disabled=${!f.target.trim()}>${t("rmap.add")}</button></div>
  </form>`;
}

export function Evidence({ node, ctx }) {
  const [adding, setAdding] = useState(false);
  useEffect(() => setAdding(false), [node.id]);
  const items = (ctx.map.attachments.evidence || []).filter((a) => a.node === node.id);
  const open = (a) => {
    if (a.type === "url") { const u = safeUrl(a.target); if (u) window.open(u, "_blank", "noopener"); }
    else if (a.type === "paper") ctx.onOpenPaper?.(a.target);
    else ctx.onOpenNote?.(a.target);
  };
  const remove = (a) => ctx.api.apply([{ op: "attach.delete", kind: "evidence", id: a.id }]).catch((e) => ctx.toast(e.message));
  return html`<div class="mp-sec"><h4>${t("rmap.evidence")}<small>${items.length || ""}</small>
      <button class="mp-h-btn" title=${t("rmap.ev.add")} onClick=${() => setAdding(!adding)}><${Icon} name="plus" sm /></button></h4>
    ${items.length > 0 && html`<div class="mp-chips">${items.map((a) => html`<span class=${"mp-chip " + (a.stance || "supports")} key=${a.id}
        title=${[t("rmap.stance." + (a.stance || "supports")), a.note || a.target].join(" · ")}>
      <button class="mp-chip-main" onClick=${() => open(a)}><${Icon} name=${EV_ICON[a.type] || "file"} sm />${a.title || a.target}</button>
      <button class="mp-chip-x" title=${t("rmap.ev.remove")} onClick=${() => remove(a)}><${Icon} name="x" sm /></button></span>`)}</div>`}
    ${!items.length && !adding && html`<p class="mp-quiet">${t("rmap.ev.none")}</p>`}
    ${adding && html`<${EvidenceAdd} node=${node} ctx=${ctx} onDone=${() => setAdding(false)} />`}
  </div>`;
}

function Links({ node, ctx }) {
  const name = (id) => ctx.map.nodes.find((n) => n.id === id)?.title || id;
  const rows = ctx.map.edges.filter((e) => e.from === node.id || e.to === node.id);
  if (!rows.length) return null;
  return html`<div class="mp-sec"><h4>${t("rmap.links")}<small>${rows.length}</small></h4>
    <div class="mp-links">${rows.map((e) => {
      const other = e.from === node.id ? e.to : e.from;
      const dir = ctx.meta.flow.includes(e.kind) ? (e.from === node.id ? "→" : "←") : "↔";
      return html`<div class=${"mp-link k-" + e.kind} key=${e.id}>
        <select value=${e.kind} title=${t("rmap.link.kind")} onChange=${(ev) => ctx.api.apply([{ op: "edge.update", id: e.id, set: { kind: ev.target.value } }]).catch((x) => ctx.toast(x.message))}>
          ${ctx.meta.edge_kinds.map((k) => html`<option value=${k}>${t("rmap.edge." + k)}</option>`)}</select>
        <span class="mp-dir">${dir}</span>
        <button class="mp-link-n" onClick=${() => ctx.select(other)}>${name(other)}</button>
        <button class="mp-chip-x" title=${t("rmap.menu.edge.delete")} onClick=${() => ctx.api.apply([{ op: "edge.delete", id: e.id }]).catch((x) => ctx.toast(x.message))}><${Icon} name="x" sm /></button>
      </div>`; })}</div>
  </div>`;
}

function History({ node, ctx }) {
  const [rows, setRows] = useState(null);
  useEffect(() => {
    ctx.api.call(`${ctx.api.base}/events?node=${encodeURIComponent(node.id)}&limit=12`).then((r) => setRows(r.events)).catch(() => setRows([]));
  }, [node.id, ctx.map.stamp]);
  if (!rows?.length) return null;
  const names = titles(ctx.map, rows);
  return html`<div class="mp-sec"><h4>${t("rmap.history")}</h4>
    <ul class="mp-hist">${rows.map((ev) => { const d = describe(ev, names);
      return html`<li key=${ev.id}><span class=${"mp-sign s" + d.sign}>${d.sign}</span><span class="mp-ht">${d.text}</span>
        <span class="mp-hw" title=${ev.at + " · " + (ev.device || "")}>${t("rmap.who." + (ev.who || "user"))} · ${when(ev.at)}</span></li>`; })}</ul>
  </div>`;
}

export function Detail({ ctx }) {
  const node = ctx.map.nodes.find((n) => n.id === ctx.selected);
  const [title, setTitle] = useState(null);
  const [body, setBody] = useState(null);
  const [sure, setSure] = useState(false);
  useEffect(() => { setTitle(null); setBody(null); setSure(false); }, [node?.id]);
  const titleRef = useAutoFocus(title !== null && node?.id, { select: true });
  const bodyRef = useAutoFocus(body !== null && node?.id);
  if (!node) return html`<aside class="mp-detail empty"><span class="muted">${t("rmap.select.hint")}</span>
    <span class="mp-quiet">${t("rmap.select.keys")}</span></aside>`;

  const set = (patch) => ctx.api.apply([{ op: "node.update", id: node.id, set: patch }]).catch((e) => ctx.toast(e.message));
  const st = node.status || "todo";
  const saveTitle = () => { const v = (title ?? "").trim(); setTitle(null); if (v && v !== node.title) set({ title: v }); };
  const saveBody = () => { const v = body ?? ""; setBody(null); if (v !== (node.body || "")) set({ body: v }); };
  const view = ctx.view;
  return html`<aside class="mp-detail">
    <div class="mp-dh">
      <select class="mp-type-pick" value=${node.type} onChange=${(e) => set({ type: e.target.value })}>
        ${ctx.meta.node_types.map((x) => html`<option value=${x.key}>${t("rmap.type." + x.key)}</option>`)}</select>
      <span class=${"mp-pill st-" + st}><i />${t("rmap.status." + st)}</span>
      <button class="mp-close" title=${t("rmap.close")} onClick=${() => ctx.select(null)}><${Icon} name="x" sm /></button>
    </div>
    ${title === null ? html`<h3 title=${t("rmap.dbl.edit")} onDblClick=${() => setTitle(node.title)}>${node.title}</h3>`
      : html`<input class="mp-title-edit" ref=${titleRef} value=${title} onInput=${(e) => setTitle(e.target.value)} onBlur=${saveTitle}
          onKeyDown=${(e) => { if (e.key === "Enter" && !composing(e)) e.target.blur(); if (e.key === "Escape") setTitle(null); }} />`}
    <div class="mp-stpick">${ctx.meta.statuses.map((s) => html`<button class=${"st-" + s + (s === st ? " on" : "")} title=${t("rmap.status." + s)}
      onClick=${() => s !== st && set({ status: s })}><i />${t("rmap.status." + s)}</button>`)}</div>

    ${body === null
      ? html`<div class=${"mp-desc" + (node.body ? "" : " none")} title=${t("rmap.dbl.edit")} onDblClick=${() => setBody(node.body || "")}>
          ${node.body ? html`<div class="md" dangerouslySetInnerHTML=${{ __html: renderMarkdown(node.body) }} />`
            : html`<button class="mp-quiet" onClick=${() => setBody("")}>${t("rmap.desc.add")}</button>`}</div>`
      : html`<textarea class="mp-body-edit" ref=${bodyRef} rows="5" value=${body} placeholder=${t("rmap.desc.ph")}
          onInput=${(e) => setBody(e.target.value)} onBlur=${saveBody}
          onKeyDown=${(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) e.target.blur(); if (e.key === "Escape") setBody(null); }} />`}

    <${Evidence} node=${node} ctx=${ctx} />
    ${rmap.list("section").filter((s) => !s.when || s.when(node, ctx)).map((s) => html`<div class="mp-sec mp-ext" key=${s.id}>
      ${s.label && html`<h4>${t(s.label)}</h4>`}${s.render(node, ctx)}</div>`)}
    <${Links} node=${node} ctx=${ctx} />
    <${History} node=${node} ctx=${ctx} />

    <div class="mp-actions">
      ${(NEXT_TYPES[node.type] || []).slice(0, 2).map((k) => html`<button class="btn ghost sm" onClick=${() => ctx.api.apply([{ op: "node.add", ref: "$n", parent: node.id,
          set: { type: k, title: t("rmap.new." + k) } }]).then((r) => view.setPending(r.refs.$n)).catch((e) => ctx.toast(e.message))}><${Icon} name="plus" sm />${t("rmap.type." + k)}</button>`)}
      <button class=${"btn ghost sm" + (view.focus === node.id ? " on" : "")} onClick=${() => view.setFocus(view.focus === node.id ? null : node.id)}>
        ${view.focus === node.id ? t("rmap.menu.unfocus") : t("rmap.menu.focus")}</button>
      ${node.pin && html`<button class="btn ghost sm" onClick=${() => set({ pin: null })}>${t("rmap.menu.unpin")}</button>`}
      <button class="btn ghost sm danger-text" onClick=${() => { if (!sure) { setSure(true); return; } ctx.select(null); ctx.api.apply([{ op: "node.delete", id: node.id }]).catch((e) => ctx.toast(e.message)); }}>
        ${sure ? t("rmap.menu.delete.sure") : t("rmap.menu.delete")}</button>
    </div>
  </aside>`;
}

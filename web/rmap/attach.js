// 「挂到地图」 in the reader's toolbar (#47): hang the open paper on a research map node, as evidence or as a peer.
// Registered through /reader-tools.js, which loads this file on its own (a failure here never breaks the
// reader). The likely nodes and where the paper already hangs (by any of its ids or its note, evidence from
// before #47 included) come from GET /api/rmap/peers/paper; POST /api/rmap/peers/attach adds nothing when the
// paper already hangs on that node.
// i18n: done
// i18n-keys: rmap_peers.rel.{same,competing,builds_on} rmap.stance.{supports,contradicts,context}
// i18n-keys: rmap.type.{question,hypothesis,subline,experiment,result,decision,kill,open}
import { Component } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { t } from "/i18n.js";
import { readerTool } from "/reader-tools.js";
import { RELATIONS } from "/rmap/peerlib.js";

const STANCES = ["supports", "contradicts", "context"];
const base = (slug) => `/api/rmap/projects/${encodeURIComponent(slug)}`;

function ensureCss(href) {
  if (document.querySelector(`link[href="${href}"]`)) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = href;
  document.head.appendChild(l);
}

const clip = (s, n) => (s && s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s);

// What hangs this paper on a node: evidence (the paper note, else its link) or a peer. Titles are cut to what
// the kind accepts (evidence 200 characters, peers 300).
export function attachSet(info, how, choice) {
  if (how === "evidence") {
    const set = info.note ? { type: "paper", target: info.note } : { type: "url", target: info.url };
    return { ...set, title: clip(info.title, 200), stance: choice, entity: info.entity || null };
  }
  return { type: "paper", title: clip(info.title, 300), relation: choice, url: info.url || null, paper: info.note || null,
    date: info.year ? String(info.year) : null, source: "reader", entity: info.entity || null };
}

function Picker({ onPick, picked }) {
  const [projects, setProjects] = useState(null);
  const [slug, setSlug] = useState("");
  const [nodes, setNodes] = useState([]);
  useEffect(() => { api("/api/rmap/projects").then((r) => { setProjects(r.projects); if (r.projects[0]) setSlug(r.projects[0].slug); }).catch(() => setProjects([])); }, []);
  useEffect(() => {
    if (!slug) return;
    api(base(slug)).then((m) => setNodes(m.nodes.filter((n) => !["refuted", "dropped"].includes(n.status)))).catch(() => setNodes([]));
  }, [slug]);
  if (projects === null) return html`<p class="mpa-quiet">${t("rmap_peers.loading")}</p>`;
  if (!projects.length) return html`<p class="mpa-quiet">${t("rmap_peers.an.empty")}</p>`;
  const project = projects.find((p) => p.slug === slug);
  return html`<div class="mpa-row">
    <select value=${slug} title=${t("rmap_peers.reader.project")} onChange=${(e) => { setSlug(e.target.value); onPick(null); }}>
      ${projects.map((p) => html`<option value=${p.slug}>${p.title}</option>`)}</select>
    <select value=${picked?.slug === slug ? picked.node : ""} title=${t("rmap_peers.reader.node")}
      onChange=${(e) => { const n = nodes.find((x) => x.id === e.target.value); onPick(n ? { slug, project: project?.title, node: n.id, title: n.title } : null); }}>
      <option value="">${t("rmap_peers.reader.node")}</option>
      ${nodes.map((n) => html`<option value=${n.id}>${t("rmap.type." + n.type)} · ${n.title}</option>`)}</select>
  </div>`;
}

function AttachButton({ stem, toast }) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(null);
  const [info, setInfo] = useState(null);
  const [picked, setPicked] = useState(null); // {slug, project, node, title}
  const [other, setOther] = useState(false);
  const [how, setHow] = useState("evidence");
  const [choice, setChoice] = useState({ evidence: "supports", peer: "same" });
  const [busy, setBusy] = useState(false);
  const wrap = useRef();
  const load = () => api(`/api/rmap/peers/paper?stem=${encodeURIComponent(stem)}`).then((r) => { setInfo(r); return r; })
    .catch((e) => { setInfo({ error: e.message }); return null; });

  useEffect(() => { setOpen(false); setInfo(null); setPicked(null); setOther(false); }, [stem]);
  useEffect(() => {
    if (!open) return;
    ensureCss("/rmap/attach.css");
    if (!info) load().then((r) => { if (r?.matches?.[0]) setPicked(r.matches[0]); else setOther(true); });
    // Fixed, under the button: the toolbar scrolls horizontally and would clip an absolute popover.
    const r = wrap.current.getBoundingClientRect();
    setAt({ left: Math.max(8, Math.min(r.left, window.innerWidth - 396)) + "px", top: r.bottom + 6 + "px" });
    const down = (e) => { if (!wrap.current?.contains(e.target)) setOpen(false); };
    const key = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", down, true);
    window.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", down, true); window.removeEventListener("keydown", key); };
  }, [open]);

  const canEvidence = !!(info?.note || info?.url);
  const mode = how === "evidence" && !canEvidence ? "peer" : how;
  const save = async () => {
    if (!picked) return;
    setBusy(true);
    try {
      const r = await api("/api/rmap/peers/attach", { json: { slug: picked.slug, node: picked.node, kind: mode, set: attachSet(info, mode, choice[mode]) } });
      toast(r.state === "exists" ? t("rmap_peers.attach.exists", { node: picked.title })
        : r.state === "updated" ? t("rmap_peers.attach.updated", { node: picked.title, relation: t("rmap_peers.rel." + choice.peer) })
        : t("rmap_peers.sug.attached", { node: picked.title }));
      const fresh = await load(); // the node it now hangs on drops out of the likely ones
      setPicked(fresh?.matches?.[0] || null); setOther(!fresh?.matches?.length);
      setOpen(false);
    } catch (e) { toast(e.message); } finally { setBusy(false); }
  };
  const kindName = (k) => (k === "peer" ? t("rmap_peers.kind") : t("rmap.kind.evidence"));
  return html`<span class="mpa-wrap" ref=${wrap}>
    <button class=${"tb-btn" + (open ? " on" : "")} title=${t("rmap_peers.reader.title")} onClick=${() => setOpen(!open)}>
      <${Icon} name="topology" sm />${t("rmap_peers.reader.attach")}</button>
    ${open && at && html`<div class="pop mpa-pop" style=${at}>
      <div class="mpa-h">${t("rmap_peers.reader.title")}</div>
      ${!info ? html`<p class="mpa-quiet">${t("rmap_peers.loading")}</p>` : info.error ? html`<p class="mpa-quiet">${info.error}</p>` : html`
        <div class="mpa-paper" title=${info.title}>${info.title}</div>
        ${info.attached?.length > 0 && html`<div class="mpa-sec"><div class="mpa-lbl">${t("rmap_peers.reader.hangs")}</div>
          ${info.attached.map((h) => html`<div class="mpa-hang" key=${h.slug + h.id}><span class="mpa-kind">${kindName(h.kind)}</span>${h.title}</div>`)}</div>`}
        <div class="mpa-sec"><div class="mpa-lbl">${t("rmap_peers.reader.likely")}</div>
          ${info.matches?.length ? info.matches.map((m) => html`<label class=${"mpa-opt" + (picked?.node === m.node && !other ? " on" : "")} key=${m.slug + m.node}>
              <input type="radio" name="mpa-node" checked=${picked?.node === m.node && !other} onChange=${() => { setOther(false); setPicked(m); }} />
              <span class="mpa-node">${m.title}</span><span class="mpa-proj">${m.project}</span></label>`)
            : html`<p class="mpa-quiet">${t("rmap_peers.reader.none")}</p>`}
          <label class=${"mpa-opt" + (other ? " on" : "")}><input type="radio" name="mpa-node" checked=${other} onChange=${() => { setOther(true); setPicked(null); }} />
            <span class="mpa-node">${t("rmap_peers.reader.other")}</span></label>
          ${other && html`<${Picker} picked=${picked} onPick=${setPicked} />`}
        </div>
        <div class="mpa-sec"><div class="mpa-lbl">${t("rmap_peers.reader.as")}</div>
          <div class="mpa-row">
            <div class="seg">
              <button class=${mode === "evidence" ? "on" : ""} disabled=${!canEvidence} onClick=${() => setHow("evidence")}>${t("rmap.kind.evidence")}</button>
              <button class=${mode === "peer" ? "on" : ""} onClick=${() => setHow("peer")}>${t("rmap_peers.kind")}</button>
            </div>
            <select value=${choice[mode]} onChange=${(e) => setChoice({ ...choice, [mode]: e.target.value })}>
              ${(mode === "evidence" ? STANCES : RELATIONS).map((x) => html`<option value=${x}>${mode === "evidence" ? t("rmap.stance." + x) : t("rmap_peers.rel." + x)}</option>`)}</select>
          </div>
        </div>
        <div class="row-end"><button class="btn ghost" onClick=${() => setOpen(false)}>${t("rmap_peers.cancel")}</button>
          <button class="btn primary" disabled=${!picked || busy} onClick=${save}>${t("rmap_peers.add.save")}</button></div>`}
    </div>`}
  </span>`;
}

// A failure inside the button (a render error) hides it; the reader goes on.
class Guard extends Component {
  componentDidCatch(e) { console.error("research map: the reader's add-to-map button failed", e); this.setState({ failed: true }); }
  render() { return this.state.failed ? null : this.props.children; }
}

readerTool({ id: "rmap-attach", order: 50,
  render: ({ stem, toast }) => html`<${Guard} key=${stem}><${AttachButton} stem=${stem} toast=${toast} /></${Guard}>` });

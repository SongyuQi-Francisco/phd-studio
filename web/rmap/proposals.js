// The assistant's proposals for the map (a one-sentence update split into node changes + evidence + a log
// line, or a first map drafted from a research-line note). Nothing is written until the user confirms.
// i18n: done
import { useEffect, useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { t } from "/i18n.js";
import { describe, titles, when } from "/rmap/describe.js";

const SHOW = 10;

function Proposal({ p, map, onDone, toast }) {
  const [busy, setBusy] = useState(false);
  const [all, setAll] = useState(false);
  const names = titles(p.slug ? map : null, p.events);
  const rows = p.events.filter((e) => e.kind !== "log").map((e) => ({ ev: e, ...describe(e, names) }));
  const counts = rows.reduce((c, r) => ({ ...c, [r.sign]: (c[r.sign] || 0) + 1 }), {});
  const act = async (how) => {
    setBusy(true);
    try {
      const r = how === "apply" ? await api(`/api/rmap/proposals/${p.id}/apply`, { method: "POST" })
        : await api(`/api/rmap/proposals/${p.id}`, { method: "DELETE" });
      toast(how === "apply" ? t("rmap.prop.applied") : t("rmap.prop.discarded"));
      onDone(how, r);
    } catch (e) { toast(e.message); setBusy(false); }
  };
  return html`<section class="mp-prop">
    <div class="mp-prop-h">
      <span class="mp-prop-tag"><${Icon} name="sparkle" sm />${t(p.create ? "rmap.prop.seed" : "rmap.prop.update")}</span>
      ${p.create && html`<b>${p.create.title}</b>`}
      <span class="mp-prop-n">${["+", "~", "−"].filter((s) => counts[s]).map((s) => html`<i class=${"s" + s}>${s}${counts[s]}</i>`)}</span>
      <span class="muted small">${p.source === "note" ? t("rmap.prop.from.note") : t("rmap.who.assistant")} · ${when(p.created_at)}</span>
    </div>
    <p class="mp-prop-s">${p.summary}</p>
    <ul class="mp-hist mp-prop-list">${(all ? rows : rows.slice(0, SHOW)).map((r) => html`<li key=${r.ev.id}>
      <span class=${"mp-sign s" + r.sign}>${r.sign}</span><span class="mp-ht">${r.text}</span></li>`)}</ul>
    ${rows.length > SHOW && html`<button class="mp-more" onClick=${() => setAll(!all)}>${all ? t("rmap.less") : t("rmap.more", { n: rows.length - SHOW })}</button>`}
    <div class="row-end">
      <button class="btn ghost sm" disabled=${busy} onClick=${() => act("discard")}>${t("rmap.prop.discard")}</button>
      <button class="btn primary sm" disabled=${busy} onClick=${() => act("apply")}>${t("rmap.prop.apply")}</button>
    </div>
  </section>`;
}

// Proposals for this map and the ones that would create a map. onApplied(slug) after a confirm.
export function Proposals({ slug, map, version, toast, onApplied }) {
  const [rows, setRows] = useState([]);
  const load = () => api(`/api/rmap/proposals${slug ? "?slug=" + encodeURIComponent(slug) : ""}`).then((r) => setRows(r.proposals)).catch(() => {});
  useEffect(() => { load(); }, [slug, version, map?.stamp]);
  if (!rows.length) return null;
  return html`<div class="mp-props">${rows.map((p) => html`<${Proposal} key=${p.id} p=${p} map=${map} toast=${toast}
    onDone=${(how, r) => { setRows((x) => x.filter((y) => y.id !== p.id)); if (how === "apply") onApplied?.(r.slug); }} />`)}</div>`;
}

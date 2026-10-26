// Venue details can go out of date: the "may be out of date" badge and its reasons, the assistant's re-check
// proposal with its diff and confirm, and the list of stale watched venues (timeline page, overview).
// i18n: done
import { useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";
import { t, tx } from "/i18n.js";

export const isStale = (v) => !!v?.freshness?.stale;

// Only http(s) links, and a host name that never throws on a malformed address.
export const httpUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);
export function hostOf(u) {
  try { return new URL(u).hostname || u; } catch { return String(u); }
}

// "2027 届" | "2027 edition" before a date, or nothing.
const editionOf = (year) => (year ? t("venue_check.edition", { year }) : "");
const checkedAt = (at) => (at ? t("venue_check.checked_at", { at }) : "");

export function FreshTag({ f }) {
  if (!f?.stale) return null;
  return html`<span class="tag warn fresh-tag" title=${f.reasons.map((r) => r.text).join("\n")}>${t("venue_check.stale")}</span>`;
}

function DiffView({ diff }) {
  if (!diff.length) return html`<div class="muted small">${t("venue_check.diff_same")}</div>`;
  return html`<div class="vdiff">${diff.map((d) => html`<div class="vd-field"><b>${d.label}</b>
    ${d.lines.map((l) => html`<div class=${"vd-line " + (l.op === "+" ? "add" : "del")}><i>${l.op === "+" ? "+" : "−"}</i><span>${l.text}</span></div>`)}</div>`)}</div>`;
}

function Proposal({ p, store, vkey, reload, toast, onChanged }) {
  const [busy, setBusy] = useState(false);
  const act = async (what, msg) => {
    setBusy(true);
    try { await api(`/api/venue-check/${encodeURIComponent(vkey)}/${what}`, { json: { proposal_id: p.id } }); toast(msg); await reload(); onChanged?.(); }
    catch (e) { toast(e.message); await reload(); }
    setBusy(false);
  };
  return html`<section class="v-proposal">
    <div class="vp-head"><b>${t("venue_check.proposal.title")}</b><span class="muted small">${t("venue_check.proposal.submitted", { at: p.created_at.replace("T", " ").slice(5, 16) })}</span></div>
    ${p.summary && html`<p class="vp-sum">${p.summary}</p>`}
    <${DiffView} diff=${p.diff} />
    ${p.sources.length > 0 && html`<div class="muted small srcs">${t("venue_check.sources")}${p.sources.filter(httpUrl).map((u) => html`<a href=${u} target="_blank" rel="noreferrer">${u.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}</a>`)}</div>`}
    ${p.outdated
      ? html`<div class="vp-warn">${t("venue_check.proposal.outdated")}</div>`
      : html`<div class="muted small">${tx("venue_check.proposal.confirm_note", { store: html`<code>${store}</code>` })}</div>`}
    <div class="row-wrap">
      ${!p.outdated && html`<button class="btn primary" disabled=${busy} onClick=${() => act("apply", t("venue_check.proposal.applied"))}><${Icon} name="check" sm /> ${t("venue_check.proposal.apply")}</button>`}
      <button class="btn" disabled=${busy} onClick=${() => act("dismiss", t("venue_check.proposal.discarded"))}>${t("venue_check.proposal.discard")}</button>
    </div>
  </section>`;
}

// Everything the venue sheet shows about freshness: why it may be stale, the buttons, the pending proposal.
export function CheckPanel({ vkey, v, chk, reload, onAsk, onClose, toast, onChanged }) {
  const [busy, setBusy] = useState(false);
  // A venue without details can still have a proposal waiting (the assistant may propose a first entry).
  if (!chk || (!v.detail && !chk.proposal)) return null;
  const f = chk.freshness;
  const ask = () => { onAsk(chk.prompt, t("venue_check.ask_label", { name: v.acronym })); onClose(); };
  const post = async (what, msg) => {
    setBusy(true);
    try { await api(`/api/venue-check/${encodeURIComponent(vkey)}/${what}`, { method: "POST" }); toast(msg); await reload(); onChanged?.(); }
    catch (e) { toast(e.message); }
    setBusy(false);
  };
  return html`<div class="v-check">
    ${chk.proposal && html`<${Proposal} p=${chk.proposal} store=${chk.store} vkey=${vkey} reload=${reload} toast=${toast} onChanged=${onChanged} />`}
    ${f?.stale && html`<section class="v-stale">
      <div class="vs-head"><span class="tag warn">${t("venue_check.stale")}</span><span class="muted small">${[editionOf(f.edition_year), checkedAt(f.verified_at)].filter(Boolean).join(" · ")}</span></div>
      <ul>${f.reasons.map((r) => html`<li>${r.text}</li>`)}</ul>
      <div class="row-wrap">
        <button class="btn primary" onClick=${ask}><${Icon} name="sparkle" sm /> ${t("venue_check.ask")}</button>
        <button class="btn" disabled=${busy} title=${t("venue_check.verified_title")} onClick=${() => post("verified", t("venue_check.verified_done"))}>${t("venue_check.verified")}</button>
      </div>
    </section>`}
    ${!f?.stale && !chk.proposal && html`<div class="v-fresh muted small">
      <${Icon} name="check" sm /> ${t(f?.edition_year ? "venue_check.fresh_edition" : "venue_check.fresh", { edition: editionOf(f?.edition_year), at: f?.verified_at || "?", months: t("common.unit.months", { n: f?.limit_months }) })}
      <button class="linkbtn" onClick=${ask}>${t("venue_check.ask_again")}</button></div>`}
    ${chk.override && !chk.override.outdated && html`<div class="muted small v-ov">${t("venue_check.override", { at: chk.override.updated_at.replace("T", " ").slice(0, 16) })}
      <button class="linkbtn" disabled=${busy} onClick=${() => post("reset", t("venue_check.reset_done"))}>${t("venue_check.reset")}</button></div>`}
    ${chk.override?.outdated && html`<div class="v-warn muted small">${t("venue_check.override_old", { date: chk.override.updated_at.slice(0, 10) })}
      <button class="linkbtn" disabled=${busy} onClick=${() => post("reset", t("venue_check.clear_done"))}>${t("venue_check.clear")}</button></div>`}
  </div>`;
}

// Short reasons as chips, the full text as tooltip.
const Reasons = ({ items }) => items.map((r) => html`<span class="tag warn" title=${r.text}>${r.short}</span>`);

// The timeline page's list of watched venues that need a look.
export function StaleCard({ items, months, onOpen }) {
  if (!items?.length) return null;
  return html`<section class="card stale-card">
    <div class="card-head"><h2>${t("venue_check.stale")}</h2><span class="muted small">${t("venue_check.card_count", { count: t("common.unit.venues", { n: items.length }), months })}</span></div>
    ${items.map((s) => html`<button class="srow" onClick=${() => onOpen(s.key)}>
      <b>${s.acronym}</b>
      <span class="srow-why">${s.pending && html`<span class="tag st-new">${t("venue_check.pending")}</span>`}<${Reasons} items=${s.reasons} /></span>
      <span class="muted small">${[editionOf(s.edition_year), s.verified_at && s.verified_at.slice(0, 10)].filter(Boolean).join(" · ")}</span>
      <${Icon} name="right" sm />
    </button>`)}
  </section>`;
}

// Overview: one quiet line when something needs a look.
export function StaleNote({ items, onOpen }) {
  if (!items?.length) return null;
  const waiting = items.filter((s) => s.pending).length;
  const list = items.slice(0, 6).map((s) => s.acronym).join(t("venue_check.sep")) + (items.length > 6 ? t("venue_check.note_more") : "");
  return html`<button class="stale-note" onClick=${onOpen}>
    <span class="tag warn">${t("venue_check.stale")}</span>
    <span>${tx("venue_check.note", { count: html`<b>${items.length}</b>`, list })}${waiting ? t("venue_check.note_pending", { n: waiting }) : ""}</span>
    <${Icon} name="right" sm />
  </button>`;
}

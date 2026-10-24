// Venue details can go out of date: the 「信息可能过期」 badge and its reasons, the assistant's re-check
// proposal with its diff and confirm, and the list of stale watched venues (timeline page, overview).
import { useState } from "preact/hooks";
import { html, Icon, api } from "/lib.js";

export const isStale = (v) => !!v?.freshness?.stale;

export function FreshTag({ f }) {
  if (!f?.stale) return null;
  return html`<span class="tag warn fresh-tag" title=${f.reasons.map((r) => r.text).join("\n")}>信息可能过期</span>`;
}

function DiffView({ diff }) {
  if (!diff.length) return html`<div class="muted small">和现在记录的内容一致，确认后只更新核对日期。</div>`;
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
    <div class="vp-head"><b>助手核对了官网</b><span class="muted small">${p.created_at.replace("T", " ").slice(5, 16)} 提交，待你确认</span></div>
    ${p.summary && html`<p class="vp-sum">${p.summary}</p>`}
    <${DiffView} diff=${p.diff} />
    ${p.sources.length > 0 && html`<div class="muted small srcs">来源：${p.sources.map((u) => html`<a href=${u} target="_blank" rel="noreferrer">${u.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}</a>`)}</div>`}
    ${p.outdated
      ? html`<div class="vp-warn">这个会议的记录在助手核对之后又变了（比如另一台电脑已更新），这份结果不能再直接应用。请重新让助手核对。</div>`
      : html`<div class="muted small">确认后写进 vault 的 <code>${store}</code>（盖在内置数据上，两台电脑通过 Syncthing 同步），核对日期记为今天。</div>`}
    <div class="row-wrap">
      ${!p.outdated && html`<button class="btn primary" disabled=${busy} onClick=${() => act("apply", "已写入，核对日期记为今天")}><${Icon} name="check" sm /> 确认写入</button>`}
      <button class="btn" disabled=${busy} onClick=${() => act("dismiss", "已放弃这份核对结果")}>放弃</button>
    </div>
  </section>`;
}

// Everything the venue sheet shows about freshness: why it may be stale, the buttons, the pending proposal.
export function CheckPanel({ vkey, v, chk, reload, onAsk, onClose, toast, onChanged }) {
  const [busy, setBusy] = useState(false);
  if (!chk || !v.detail) return null;
  const f = chk.freshness;
  const ask = () => { onAsk(chk.prompt, `核对 ${v.acronym}`); onClose(); };
  const post = async (what, msg) => {
    setBusy(true);
    try { await api(`/api/venue-check/${encodeURIComponent(vkey)}/${what}`, { method: "POST" }); toast(msg); await reload(); onChanged?.(); }
    catch (e) { toast(e.message); }
    setBusy(false);
  };
  return html`<div class="v-check">
    ${chk.proposal && html`<${Proposal} p=${chk.proposal} store=${chk.store} vkey=${vkey} reload=${reload} toast=${toast} onChanged=${onChanged} />`}
    ${f?.stale && html`<section class="v-stale">
      <div class="vs-head"><span class="tag warn">信息可能过期</span><span class="muted small">${f.edition_year ? f.edition_year + " 届" : ""}${f.verified_at ? " · 核对于 " + f.verified_at : ""}</span></div>
      <ul>${f.reasons.map((r) => html`<li>${r.text}</li>`)}</ul>
      <div class="row-wrap">
        <button class="btn primary" onClick=${ask}><${Icon} name="sparkle" sm /> 让助手核对</button>
        <button class="btn" disabled=${busy} title="自己看过官网、内容没变：只把核对日期记为今天" onClick=${() => post("verified", "已记为今天核对过")}>我已核对，没变化</button>
      </div>
    </section>`}
    ${!f?.stale && !chk.proposal && html`<div class="v-fresh muted small">
      <${Icon} name="check" sm /> ${f?.edition_year ? f.edition_year + " 届" : ""}信息在 ${f?.verified_at || "?"} 核对过，${f?.limit_months} 个月内不会提醒
      <button class="linkbtn" onClick=${ask}>让助手重新核对</button></div>`}
    ${chk.override && html`<div class="muted small v-ov">这个会议的详情有你确认过的更新（${chk.override.updated_at.replace("T", " ").slice(0, 16)}）。
      <button class="linkbtn" disabled=${busy} onClick=${() => post("reset", "已恢复为内置数据")}>恢复内置数据</button></div>`}
  </div>`;
}

// Short reasons as chips, the full text as tooltip.
const Reasons = ({ items }) => items.map((r) => html`<span class="tag warn" title=${r.text}>${r.short}</span>`);

// The timeline page's list of watched venues that need a look.
export function StaleCard({ items, months, onOpen }) {
  if (!items?.length) return null;
  return html`<section class="card stale-card">
    <div class="card-head"><h2>信息可能过期</h2><span class="muted small">${items.length} 个 · 超 ${months} 个月没核对、有新一届或截稿有变会提醒</span></div>
    ${items.map((s) => html`<button class="srow" onClick=${() => onOpen(s.key)}>
      <b>${s.acronym}</b>
      <span class="srow-why">${s.pending && html`<span class="tag st-new">核对结果待确认</span>`}<${Reasons} items=${s.reasons} /></span>
      <span class="muted small">${s.edition_year ? s.edition_year + " 届" : ""}${s.verified_at ? " · " + s.verified_at.slice(0, 10) : ""}</span>
      <${Icon} name="right" sm />
    </button>`)}
  </section>`;
}

// Overview: one quiet line when something needs a look.
export function StaleNote({ items, onOpen }) {
  if (!items?.length) return null;
  const waiting = items.filter((s) => s.pending).length;
  return html`<button class="stale-note" onClick=${onOpen}>
    <span class="tag warn">信息可能过期</span>
    <span>关注的会议里有 <b>${items.length}</b> 个的投稿信息可能过期：${items.slice(0, 6).map((s) => s.acronym).join("、")}${items.length > 6 ? " 等" : ""}${waiting ? `（${waiting} 个有核对结果待确认）` : ""}</span>
    <${Icon} name="right" sm />
  </button>`;
}

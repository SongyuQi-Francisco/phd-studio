// One readable line per change-log event (history list, proposals, extensions' feeds).
// i18n: done
// i18n-keys: rmap.status.{todo,active,verified,blocked,refuted,dropped}
// i18n-keys: rmap.type.{question,hypothesis,subline,experiment,result,decision,kill,open}
// i18n-keys: rmap.edge.{refines,depends,alternative,conflicts} rmap.action.{add,update,delete}
// i18n-keys: rmap.field.{type,title,status,body,pin,order,tags,entity,ext,kind,label,line,note,description,archived}
import { t } from "/i18n.js";
import { label } from "/rmap/strings.js";

// Titles of nodes by id: the live map first, then the records the events themselves carry.
export function titles(map, events = []) {
  const out = new Map((map?.nodes || []).map((n) => [n.id, n.title]));
  for (const ev of events) {
    if (ev.kind === "node" && ev.record?.title && !out.has(ev.target)) out.set(ev.target, ev.record.title);
    if (ev.kind === "node" && ev.title && !out.has(ev.target)) out.set(ev.target, ev.title);
  }
  return out;
}

const q = (s) => { const x = s || "?"; return t("rmap.quote", { text: x.length > 42 ? x.slice(0, 40).trimEnd() + "…" : x }); };
const join = (parts) => parts.reduce((a, b) => (a === null ? b : t("rmap.join.changes", { a, b })), null) ?? "";
const val = (field, v) => {
  if (v == null || v === "") return "—";
  if (field === "status") return t("rmap.status." + v);
  if (field === "type") return t("rmap.type." + v);
  if (field === "kind") return t("rmap.edge." + v);
  return String(v);
};

function changeText(field, [a, b]) {
  if (field === "status" || field === "type" || field === "kind" || field === "title" || field === "label") {
    return t("rmap.ev.field", { field: t("rmap.field." + field), from: val(field, a), to: val(field, b) });
  }
  if (field === "pin") return b ? t("rmap.ev.pinned") : t("rmap.ev.unpinned");
  return t("rmap.ev.changed", { field: t("rmap.field." + field) });
}

// {sign: "+" | "−" | "~" | "·", text, status?} for one event.
export function describe(ev, names) {
  const name = (id) => names?.get(id) || ev.title || id;
  const r = ev.record || {};
  if (ev.kind === "log") return { sign: "·", text: ev.summary || "" };
  if (ev.kind === "project") {
    return ev.action === "add" ? { sign: "+", text: t("rmap.ev.project.add", { title: q(r.title || ev.title) }) }
      : { sign: "~", text: join(Object.entries(ev.changes || {}).map(([f, c]) => t("rmap.ev.project.field", { field: t("rmap.field." + f), to: val(f, c[1]) }))) };
  }
  if (ev.kind === "node") {
    if (ev.action === "add") return { sign: "+", status: r.status, text: t("rmap.ev.node.add", { type: t("rmap.type." + r.type), title: q(r.title), status: val("status", r.status) }) };
    if (ev.action === "delete") return { sign: "−", text: t("rmap.ev.node.delete", { title: q(r.title || ev.title) }) };
    const parts = Object.entries(ev.changes || {}).map(([f, c]) => changeText(f, c));
    return { sign: "~", status: ev.changes?.status?.[1], text: t("rmap.ev.node.update", { title: q(ev.title || name(ev.target)), changes: join(parts) }) };
  }
  if (ev.kind === "edge") {
    const [from, to] = ev.nodes || [];
    const kind = t("rmap.edge." + (r.kind || "refines"));
    if (ev.action === "add") return { sign: "+", text: t("rmap.ev.edge.add", { from: q(name(from)), to: q(name(to)), kind }) };
    if (ev.action === "delete") return { sign: "−", text: t("rmap.ev.edge.delete", { from: q(name(from)), to: q(name(to)), kind }) };
    return { sign: "~", text: t("rmap.ev.edge.changes", { edge: t("rmap.ev.edge.update", { from: q(name(from)), to: q(name(to)) }),
      changes: join(Object.entries(ev.changes || {}).map(([f, c]) => changeText(f, c))) }) };
  }
  // attachments (evidence and the kinds extensions register); bulk kinds log one event per batch
  const kindLabel = ev.kind === "evidence" ? t("rmap.kind.evidence") : label("rmap.kind." + ev.kind); // extensions name their kinds
  if (ev.count) return { sign: ev.action === "add" ? "+" : ev.action === "delete" ? "−" : "~", text: t("rmap.ev.attach.bulk", { kind: kindLabel, n: ev.count, action: t("rmap.action." + ev.action) }) };
  const node = q(ev.title || name(ev.nodes?.[0]));
  const what = ev.summary || r.title || r.target || "";
  if (ev.action === "add") return { sign: "+", text: t("rmap.ev.attach.add", { kind: kindLabel, node, what }) };
  if (ev.action === "delete") return { sign: "−", text: t("rmap.ev.attach.delete", { kind: kindLabel, node, what }) };
  return { sign: "~", text: t("rmap.ev.attach.update", { kind: kindLabel, node }) };
}

// "3 minutes ago" style, for history rows.
export function when(at) {
  const d = new Date(at);
  const s = (Date.now() - d) / 1000;
  if (s < 60) return t("rmap.ago.now");
  if (s < 3600) return t("rmap.ago.min", { n: Math.floor(s / 60) });
  if (s < 86400) return t("rmap.ago.hour", { n: Math.floor(s / 3600) });
  if (s < 86400 * 7) return t("rmap.ago.day", { n: Math.floor(s / 86400) });
  return at.slice(0, 10);
}

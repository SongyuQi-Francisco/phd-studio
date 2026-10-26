// The peer layer's rules (#47), shared by its extension (web/rmap/ext/peers.js) and the tests. The same rule
// as app/rmap/peers.py; the thresholds come from /meta (the "peer" kind's extra), these are the defaults.
// No imports: runs outside the browser too (tests/js/rmap_peers.test.mjs).
// i18n: done

export const RELATIONS = ["same", "competing", "builds_on"]; // 同一做法 / 竞争做法 / 在其基础上
export const TYPES = ["paper", "scholar", "lab"];
const DEFAULT_RULE = { gap_types: ["hypothesis", "subline"], risk_relations: ["same", "competing"], dense: 3, same_dense: 2 };

// The rule in force: the server's (meta.kinds[peer]) over the defaults.
export function ruleOf(meta) {
  const kind = (meta?.kinds || []).find((k) => k.name === "peer") || {};
  return { ...DEFAULT_RULE, ...Object.fromEntries(Object.keys(DEFAULT_RULE).filter((k) => kind[k] != null).map((k) => [k, kind[k]])) };
}

// Every key a peer is known by: its entity, the ids in its link, its paper note and, for a paper, its normalised
// title (app/rmap/peers.py aliases). Records sharing any key are the same peer.
const TITLE_MIN = 12;
export function aliasesOf(p) {
  const out = new Set();
  if (p.entity) out.add(p.entity);
  for (const k of keysOf(p.url)) out.add(k);
  if (p.paper) out.add("note:" + p.paper);
  if (p.type === "paper") { const k = keyText(p.title); if (k.length >= TITLE_MIN) out.add("paper:" + k); }
  else if ((p.type === "scholar" || p.type === "lab") && keyText(p.title)) out.add(p.type + ":" + keyText(p.title));
  return out;
}

const RANK = { same: 3, competing: 2, builds_on: 1 };
const created = (r) => [Object.values(r.clock || {}).sort()[0] || r.created_at || "", r.id || ""].join("|");

// One entry per distinct peer: its first record, the closest relation among its records, every record id
// (`ids`) and alias. Two records of one peer (confirmed on both Macs, or again from the reader) count once.
export function mergePeers(list) {
  const recs = [...list].sort((a, b) => (created(a) < created(b) ? -1 : created(a) > created(b) ? 1 : 0));
  const parent = recs.map((_, i) => i);
  const root = (i) => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
  const owner = new Map();
  const keys = recs.map(aliasesOf);
  keys.forEach((ks, i) => ks.forEach((k) => {
    if (!owner.has(k)) { owner.set(k, i); return; }
    const a = root(owner.get(k)), b = root(i);
    if (a !== b) parent[Math.max(a, b)] = Math.min(a, b);
  }));
  const groups = new Map();
  recs.forEach((r, i) => { const g = root(i); if (!groups.has(g)) groups.set(g, []); groups.get(g).push(i); });
  return [...groups.values()].map((idx) => {
    const first = { ...recs[idx[0]] };
    first.relation = idx.map((i) => recs[i].relation).sort((a, b) => (RANK[b] || 0) - (RANK[a] || 0))[0];
    for (const i of idx.slice(1)) for (const f of ["url", "paper", "authors", "venue", "date", "note", "entity"]) if (!first[f] && recs[i][f]) first[f] = recs[i][f];
    first.ids = idx.map((i) => recs[i].id);
    first.aliases = [...new Set(idx.flatMap((i) => [...keys[i]]))].sort();
    return first;
  });
}

const cache = new WeakMap(); // map -> node id -> its distinct peers
export function peersByNode(map) {
  if (!map) return new Map();
  let hit = cache.get(map);
  if (!hit) {
    const raw = new Map();
    for (const p of map.attachments?.peer || []) {
      if (!raw.has(p.node)) raw.set(p.node, []);
      raw.get(p.node).push(p);
    }
    hit = new Map([...raw].map(([id, list]) => [id, mergePeers(list)]));
    cache.set(map, hit);
  }
  return hit;
}

// Counts by relation over distinct peers; a gap is an open branch with no peer, a risk a node in progress
// among dense peers.
export function nodeStats(n, list, rule = DEFAULT_RULE, closed = ["verified", "refuted", "dropped"]) {
  const peers = list.every((p) => p.ids) ? list : mergePeers(list);
  const by = Object.fromEntries(RELATIONS.map((r) => [r, peers.filter((p) => p.relation === r).length]));
  const dense = rule.risk_relations.reduce((s, r) => s + (by[r] || 0), 0);
  const status = n.status || "todo";
  return {
    n: peers.length, ...by,
    gap: rule.gap_types.includes(n.type) && !closed.includes(status) && peers.length === 0,
    risk: status === "active" && (dense >= rule.dense || by.same >= rule.same_dense),
  };
}

export function statsOf(n, map, meta) {
  return nodeStats(n, peersByNode(map).get(n.id) || [], ruleOf(meta), meta?.closed);
}

export const gaps = (map, meta) => map.nodes.filter((n) => statsOf(n, map, meta).gap);
export const risks = (map, meta) => map.nodes.filter((n) => statsOf(n, map, meta).risk);

// A name as an entity key: lower-case letters and digits (any script) joined by "-" (app/rmap/peers.py key_text).
export function keyText(text) {
  return (text || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "").slice(0, 120).replace(/-+$/, "");
}

// What a link or id names: an arXiv id (abs / pdf / html links, with a version, a query or a trailing slash),
// or an OpenReview / DOI / ACL Anthology key (the same patterns as app/sources.py).
const ACL_ID = String.raw`(?:\d{4}\.(?=[a-z0-9-]*[a-z])[a-z0-9]+(?:-[a-z0-9]+)*\.\d+|[A-Z]\d{2}-\d{4})`;
export function idsOf(text) {
  const s = (text || "").trim();
  const out = {};
  const ax = s.match(/arxiv\.org\/(?:abs|pdf|html)\/(\d{4}\.\d{4,5})/i) || s.match(/^(?:arxiv:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?$/i);
  if (ax) out.arxiv = ax[1];
  let m = s.match(/openreview\.net\/(?:forum|pdf|revisions)\?(?:[^\s#]*&)?id=([\w-]+)/i);
  if (m) return { ...out, ext: "openreview:" + m[1] };
  m = s.match(new RegExp(String.raw`aclanthology\.org\/(${ACL_ID})(?=\/|\.pdf|\.bib|\.xml|[?#\s]|$)`, "i"));
  if (m) return { ...out, ext: "acl:" + m[1] };
  m = (() => { try { return decodeURIComponent(s); } catch { return s; } })().match(/(?:^|[^\w.])(10\.\d{4,9}\/[^\s"'?#]+)/i);
  if (m) {
    let doi = m[1].toLowerCase().replace(/[.,;:>]+$/, "");
    while (doi.endsWith(")") && (doi.match(/\(/g) || []).length < (doi.match(/\)/g) || []).length) doi = doi.slice(0, -1);
    const arx = doi.match(/^10\.48550\/arxiv\.(\d{4}\.\d{4,5})/);
    if (arx) return { ...out, arxiv: arx[1] };
    return { ...out, ext: "doi:" + doi.replace(/\//g, "|") };
  }
  return out;
}

// The paper keys a link or id names (app/rmap/peers.py ids_in).
export function keysOf(text) {
  const ids = idsOf(text);
  return [ids.arxiv && "paper:" + ids.arxiv, ids.ext].filter(Boolean);
}

// The entity key of a peer, as app/rmap/peers.py entity_for makes it.
export function entityFor(type, { title, arxiv, ext, openalex, lab } = {}) {
  if (type === "paper") {
    if (arxiv) return "paper:" + arxiv.trim().replace(/v\d+$/, "");
    if (ext && /^[a-z][a-z0-9_]{1,15}:\S{1,200}$/.test(ext)) return ext;
  } else if (type === "scholar" && openalex) return "scholar:openalex:" + openalex.trim();
  else if (type === "lab" && lab && keyText(lab)) return "lab:" + keyText(lab);
  const k = keyText(title);
  return k ? `${type}:${k}` : null;
}

// Two letters for the orange ring: a person's or a lab's initials, a paper's first two words.
export function initials(title) {
  const words = (title || "").replace(/[^\p{L}\p{N}\s-]/gu, " ").split(/[\s-]+/).filter(Boolean);
  if (!words.length) return "?";
  if (/^[\p{Script=Han}]/u.test(words[0])) return words[0].slice(0, 2);
  const caps = words.filter((w) => /^\p{Lu}/u.test(w));
  const pick = (caps.length >= 2 ? caps : words).slice(0, 2);
  return pick.map((w) => w[0].toUpperCase()).join("");
}

// The heat of one cell of the 分支 × 同行 table: how much a relation presses on a branch.
export const HEAT = { same: 1, competing: 0.65, builds_on: 0.3 };

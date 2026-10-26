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

const cache = new WeakMap(); // map -> node id -> peers
export function peersByNode(map) {
  if (!map) return new Map();
  let hit = cache.get(map);
  if (!hit) {
    hit = new Map();
    for (const p of map.attachments?.peer || []) {
      if (!hit.has(p.node)) hit.set(p.node, []);
      hit.get(p.node).push(p);
    }
    cache.set(map, hit);
  }
  return hit;
}

// Counts by relation; a gap is an open branch with no peer, a risk a node in progress among dense peers.
export function nodeStats(n, peers, rule = DEFAULT_RULE, closed = ["verified", "refuted", "dropped"]) {
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

// What a link or id names: an arXiv id, or an OpenReview / DOI / ACL Anthology key.
export function idsOf(text) {
  const s = (text || "").trim();
  let m = s.match(/(?:arxiv\.org\/(?:abs|pdf)\/|arxiv:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?$/i);
  if (m && (/^\d{4}\.\d{4,5}(v\d+)?$/.test(s) || /arxiv/i.test(s))) return { arxiv: m[1] };
  m = s.match(/openreview\.net\/(?:forum|pdf)\?id=([\w-]+)/i);
  if (m) return { ext: "openreview:" + m[1] };
  m = s.match(/(?:doi\.org\/|^doi:\s*)(10\.\d{4,9}\/\S+)$/i) || s.match(/^(10\.\d{4,9}\/\S+)$/);
  if (m) return { ext: "doi:" + m[1].toLowerCase().replace(/\//g, "|") };
  m = s.match(/aclanthology\.org\/([\w.-]+?)(?:\.pdf)?\/?$/i);
  if (m) return { ext: "acl:" + m[1] };
  return {};
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

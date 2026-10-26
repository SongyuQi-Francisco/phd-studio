// web/rmap/peerlib.js without a browser: run by tests/test_rmap_peers.py with node, or with JavaScriptCore's
// jsc (WebKit's engine, the one the desktop window uses). Prints "ok" or throws.
// The test copies peerlib.js next to this file (as .mjs), with cases.mjs: what app/rmap/peers.py answers for
// the same names, so the two sides agree on entity keys.
import { nodeStats, ruleOf, statsOf, gaps, risks, entityFor, keyText, idsOf, initials, peersByNode } from "./peerlib.mjs";
import cases from "./cases.mjs";

const out = typeof print === "function" ? print : console.log.bind(console);

function ok(cond, what) {
  if (!cond) throw new Error(what);
}
function eq(actual, expected, what) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${what}: expected ${e}, got ${a}`);
}

const meta = { closed: ["verified", "refuted", "dropped"], kinds: [{ name: "evidence" }, { name: "peer", dense: 3, same_dense: 2, gap_types: ["hypothesis", "subline"], risk_relations: ["same", "competing"] }] };
const p = (node, relation, i) => ({ id: `p${node}${i}`, node, relation, title: "P" + i, type: "paper" });

// 1. the rule from /meta, with defaults for what it leaves out
eq(ruleOf({ kinds: [{ name: "peer", dense: 5 }] }).dense, 5, "rule from meta");
eq(ruleOf({}).same_dense, 2, "default rule");

// 2. gaps: open branches with no peer; risks: in progress among dense peers (builds-on does not count)
{
  const map = {
    nodes: [
      { id: "h1", type: "hypothesis", status: "active" }, { id: "h2", type: "hypothesis", status: "todo" },
      { id: "h3", type: "hypothesis", status: "verified" }, { id: "s1", type: "subline", status: "active" },
      { id: "x1", type: "experiment", status: "active" }, { id: "q", type: "question" },
    ],
    attachments: { peer: [p("h1", "same", 1), p("h1", "same", 2), p("s1", "builds_on", 1), p("s1", "builds_on", 2), p("s1", "builds_on", 3), p("x1", "competing", 1), p("x1", "competing", 2), p("x1", "same", 3)] },
  };
  eq(gaps(map, meta).map((n) => n.id), ["h2"], "gaps");
  eq(risks(map, meta).map((n) => n.id), ["h1", "x1"], "risks");
  const s = statsOf(map.nodes[3], map, meta);
  eq([s.n, s.builds_on, s.risk, s.gap], [3, 3, false, false], "builds-on peers are not pressure");
  eq(nodeStats({ type: "hypothesis", status: "todo" }, [p("a", "competing", 1), p("a", "competing", 2), p("a", "same", 3)]).risk, false, "not in progress: no risk");
  ok(peersByNode(map) === peersByNode(map), "peers by node are cached per map");
}

// 3. entity keys: the same as the server makes them
for (const [text, want] of Object.entries(cases.keys)) eq(keyText(text), want, `keyText(${text})`);
for (const [kind, kw, want] of cases.entities) eq(entityFor(kind, kw), want, `entityFor(${kind}, ${JSON.stringify(kw)})`);

// 4. links and ids typed into the add form
eq(idsOf("https://arxiv.org/abs/2310.11511v2"), { arxiv: "2310.11511" }, "arXiv link");
eq(idsOf("2310.11511"), { arxiv: "2310.11511" }, "bare arXiv id");
eq(idsOf("https://openreview.net/forum?id=abC-12"), { ext: "openreview:abC-12" }, "OpenReview link");
eq(idsOf("https://doi.org/10.1145/3626772.3657834"), { ext: "doi:10.1145|3626772.3657834" }, "DOI link");
eq(idsOf("https://aclanthology.org/2024.acl-long.12/"), { ext: "acl:2024.acl-long.12" }, "ACL Anthology link");
eq(idsOf("https://example.org/paper"), {}, "other link");

// 5. initials for the orange rings
eq(initials("Elena Marchetti"), "EM", "a person");
eq(initials("Lakeside Retrieval Lab"), "LR", "a lab");
eq(initials("Late-Interaction Rerankers Are Strong"), "LI", "a paper");
eq(initials("引用核验"), "引用", "Chinese");
eq(initials(""), "?", "empty");

out("ok");

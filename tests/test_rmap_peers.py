"""Research maps, third part (#47): peers on nodes, gaps and collision risks, the 分支 × 同行 overview,
suggestions from the tracking page / follows / reading list (never attached by themselves), the reader's
「挂到地图」 lookup, the assistant's tools, and web/rmap/peerlib.js in a JavaScript engine."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, reading, studio_tools, tracking
from app.rmap import ops, peers, project, proposals, registry

from .conftest import PROJECT


def node_id(slug: str, title: str) -> str:
    return next(n["id"] for n in project.get(slug).read()["nodes"] if n["title"] == title)


@pytest.fixture
def slug() -> str:
    s = ops.create_project("Project Atlas", line="Project A")["slug"]
    ops.apply(s, [
        {"op": "node.add", "ref": "$q", "set": {"type": "question", "title": "Can RAG citations be checked sentence by sentence?"}},
        {"op": "node.add", "ref": "$h1", "parent": "$q", "set": {"type": "hypothesis", "title": "A cheap verifier flags unsupported sentences", "status": "active"}},
        {"op": "node.add", "ref": "$s1", "parent": "$h1", "set": {"type": "subline", "title": "Late-interaction reranker as verifier", "status": "active"}},
        {"op": "node.add", "ref": "$h2", "parent": "$q", "set": {"type": "hypothesis", "title": "Errors come from generation, not retrieval", "status": "todo"}},
        {"op": "node.add", "ref": "$h3", "parent": "$q", "set": {"type": "hypothesis", "title": "Train the generator to cite", "status": "dropped"}},
        {"op": "node.add", "ref": "$x", "parent": "$s1", "set": {"type": "experiment", "title": "Annotate 300 answers", "status": "active"}},
    ])
    return s


def peer(node: str, title: str, relation: str = "same", kind: str = "paper", **extra) -> dict:
    return {"op": "attach.add", "kind": "peer", "node": node, "set": {"type": kind, "title": title, "relation": relation, **extra}}


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


# ---------- the attachment kind ----------


def test_peer_is_an_attachment_kind_with_its_rule_in_meta(client: TestClient):
    assert "peer" in registry.kinds() and registry.kinds()["peer"].filename == "peers.yaml"
    kind = next(k for k in client.get("/api/rmap/meta").json()["kinds"] if k["name"] == "peer")
    assert kind["relations"] == ["same", "competing", "builds_on"] and kind["types"] == ["paper", "scholar", "lab"]
    assert (kind["dense"], kind["same_dense"], kind["gap_types"]) == (peers.DENSE, peers.SAME_DENSE, ["hypothesis", "subline"])
    assert client.get("/api/rmap/ext").json() == {"scripts": ["/rmap/ext/peers.js"], "styles": ["/rmap/ext/peers.css"]}


def test_peers_are_validated_logged_and_go_with_their_node(slug: str):
    s1 = node_id(slug, "Late-interaction reranker as verifier")
    r = ops.apply(slug, [peer(s1, "Late-Interaction Rerankers Are Strong Attribution Judges", url="https://arxiv.org/abs/2610.90002",
                              date="2026-08", entity="paper:2610.90002", source="tracker")])
    assert [(e["kind"], e["action"], e["summary"]) for e in r["events"]] == [("peer", "add", "Late-Interaction Rerankers Are Strong Attribution Judges")]
    m = project.get(slug).read()
    assert m["attachments"]["peer"][0]["entity"] == "paper:2610.90002"
    assert (config.RMAP_DIR / slug / "peers.yaml").exists()
    for bad, word in [({"relation": "rival"}, "relation"), ({"url": "javascript:alert(1)"}, "http"), ({"date": "last week"}, "YYYY"),
                      ({"type": "person"}, "type"), ({"title": "  "}, "名字"), ({"paper": "../secret"}, "相对路径")]:
        op = peer(s1, "X")
        op["set"].update(bad)
        with pytest.raises(ValueError, match=word):
            ops.apply(slug, [op])
    with pytest.raises(ValueError, match="缺少"):
        ops.apply(slug, [{"op": "attach.add", "kind": "peer", "node": s1, "set": {"type": "lab", "title": "Lakeside"}}])
    ops.apply(slug, [{"op": "node.delete", "id": s1}])
    assert project.get(slug).read()["attachments"]["peer"] == []


def test_entities_name_the_same_peer_in_every_project():
    assert peers.entity_for("paper", arxiv="2310.11511v2", title="Self-RAG") == "paper:2310.11511"
    assert peers.entity_for("paper", ext="openreview:abc123", title="x") == "openreview:abc123"
    assert peers.entity_for("paper", doi="10.1145/3626772.3657834") == "doi:10.1145|3626772.3657834"
    assert peers.entity_for("paper", title="Citations That Hold Up: Verifying Attribution") == "paper:citations-that-hold-up-verifying-attribution"
    assert peers.entity_for("scholar", openalex="A5900000001", title="Elena Marchetti") == "scholar:openalex:A5900000001"
    assert peers.entity_for("scholar", title="Tomás Herrera") == "scholar:tomás-herrera"
    assert peers.entity_for("lab", lab="Northbridge NLP Group") == "lab:northbridge-nlp-group"
    assert peers.entity_for("lab", title="") is None
    assert peers.key_text("a_-b  C") == "a-b-c"
    for e in ("paper:2310.11511", "scholar:tomás-herrera", "lab:northbridge-nlp-group"):
        assert registry.entity(e) == e


# ---------- gaps and collision risks ----------


def test_gaps_are_open_branches_without_peers_and_risks_are_crowded_work_in_progress(slug: str):
    h1, s1, h2 = (node_id(slug, t) for t in ("A cheap verifier flags unsupported sentences", "Late-interaction reranker as verifier",
                                              "Errors come from generation, not retrieval"))
    a = peers.analyze(slug)
    assert {g["title"] for g in a["gaps"]} == {"A cheap verifier flags unsupported sentences", "Late-interaction reranker as verifier",
                                               "Errors come from generation, not retrieval"}  # dropped / questions / experiments are not gaps
    assert a["risks"] == []
    # two doing the same approach on an active node: a risk; builds-on peers never count
    ops.apply(slug, [peer(s1, "P1"), peer(s1, "P2"), peer(h1, "P3", "builds_on"), peer(h1, "P4", "builds_on"), peer(h1, "P5", "builds_on"),
                     peer(h2, "Lakeside Retrieval Lab", "competing", "lab")])
    a = peers.analyze(slug)
    rows = {r["title"]: r for r in a["nodes"]}
    assert rows["Late-interaction reranker as verifier"]["risk"] and rows["Late-interaction reranker as verifier"]["same"] == 2
    assert not rows["A cheap verifier flags unsupported sentences"]["risk"] and rows["A cheap verifier flags unsupported sentences"]["n"] == 3
    assert [g["title"] for g in a["gaps"]] == []
    # one same + two competing = dense; but only while the node is in progress
    ops.apply(slug, [peer(h1, "C1", "competing"), peer(h1, "C2", "competing"), peer(h1, "S1", "same")])
    assert {r["title"] for r in peers.analyze(slug)["risks"]} == {"Late-interaction reranker as verifier", "A cheap verifier flags unsupported sentences"}
    ops.apply(slug, [{"op": "node.update", "id": h1, "set": {"status": "verified"}}])
    assert {r["title"] for r in peers.analyze(slug)["risks"]} == {"Late-interaction reranker as verifier"}
    # rows follow the map: depth first along refines
    assert [r["title"] for r in peers.analyze(slug)["nodes"]][:3] == ["Can RAG citations be checked sentence by sentence?",
                                                                      "A cheap verifier flags unsupported sentences",
                                                                      "Late-interaction reranker as verifier"]


def test_overview_merges_a_shared_peer_across_projects(slug: str, client: TestClient):
    other = ops.create_project("Project Beacon")["slug"]
    ops.apply(other, [{"op": "node.add", "ref": "$h", "set": {"type": "hypothesis", "title": "Block-sparse + learned global tokens", "status": "active"}},
                      peer("$h", "Context Compression for RAG", "competing", entity="paper:2610.90009")])
    ops.apply(slug, [peer(node_id(slug, "Errors come from generation, not retrieval"), "Context Compression for RAG", "builds_on", entity="paper:2610.90009")])
    o = client.get("/api/rmap/peers/overview").json()
    col = o["columns"][0]
    assert col["key"] == "paper:2610.90009" and col["projects"] == sorted([slug, other]) and col["nodes"] == 2
    atlas = next(m for m in o["maps"] if m["slug"] == slug)
    row = next(r for r in atlas["rows"] if r["title"] == "Errors come from generation, not retrieval")
    assert row["cells"] == {"paper:2610.90009": "builds_on"}
    assert "Train the generator to cite" not in {r["title"] for r in atlas["rows"]}  # dropped, no peers: not a row
    assert {g["title"] for g in o["gaps"]} == {"A cheap verifier flags unsupported sentences", "Late-interaction reranker as verifier"}
    assert o["stamp"] and o["rule"]["dense"] == peers.DENSE
    a = client.get(f"/api/rmap/projects/{slug}/peers/analysis").json()
    assert len(a["gaps"]) == 2
    assert client.get("/api/rmap/projects/nope-1234/peers/analysis").status_code == 404
    ops.apply(other, [{"op": "project.update", "set": {"archived": True}}])
    assert [m["slug"] for m in client.get("/api/rmap/peers/overview").json()["maps"]] == [slug]


# ---------- suggestions ----------


@pytest.fixture
def feed(monkeypatch, tmp_path) -> dict:
    """A keyword follow with a cached arXiv answer, a weekly tracker run and a reading-list item: all on disk."""
    f = tracking.add_follow("keyword", "late interaction")
    tracking.FEED_CACHE.mkdir(parents=True, exist_ok=True)
    from datetime import date, timedelta
    recent = (date.today() - timedelta(days=5)).isoformat()
    (tracking.FEED_CACHE / f"{f['id']}.json").write_text(json.dumps([
        {"arxiv": "2610.90002", "title": "Late-Interaction Rerankers Are Strong Attribution Judges", "authors": ["Kenji Arai"],
         "published": recent, "summary": "Late interaction rerankers judge whether a passage supports a sentence of the answer."},
        {"arxiv": "2610.90003", "title": "Old News About Rerankers", "authors": ["A B"], "published": "2020-01-01", "summary": ""},
        {"arxiv": "2610.90004", "title": "Region-Grounded Captions with Pointing Supervision", "authors": ["Iris Vance"],
         "published": recent, "summary": "Vision language captions."},
    ]), encoding="utf-8")
    runs = tmp_path / "tracker" / "runs" / recent
    runs.mkdir(parents=True)
    (runs / "candidates.json").write_text(json.dumps({"run": recent, "papers": [
        {"id": "2610.90005v1", "title": "Self-Checking Retrieval: Training Generators to Flag Unsupported Claims", "authors": ["Priya Raman"],
         "published": recent, "tier": "A", "abstract": "A cheap verifier that flags unsupported sentences in generated answers."}]}), encoding="utf-8")
    monkeypatch.setattr(tracking, "TRACKER_DIR", tmp_path / "tracker")
    with reading.editing() as items:
        items.append({"id": "r1", "title": "Sentence-Level Citation Checks with a Cheap Verifier", "status": "inbox",
                      "ext": "openreview:demo1", "reason": "verifier for unsupported sentences", "pushed_at": recent})
    return f


def test_suggestions_match_new_papers_to_nodes_and_attach_nothing(slug: str, feed: dict, client: TestClient):
    before = (config.RMAP_DIR / slug / "map.yaml").read_bytes()
    out = client.get("/api/rmap/peers/suggestions").json()["suggestions"]
    by = {s["title"]: s for s in out}
    late = by["Late-Interaction Rerankers Are Strong Attribution Judges"]
    assert late["key"] == "paper:2610.90002" and late["sources"] == ["follow"] and late["via"] == ["late interaction"]
    assert late["matches"][0]["title"] == "Late-interaction reranker as verifier" and "interaction" in late["matches"][0]["terms"]
    assert late["url"] == "https://arxiv.org/abs/2610.90002"
    check = by["Self-Checking Retrieval: Training Generators to Flag Unsupported Claims"]
    assert check["key"] == "paper:2610.90005" and check["sources"] == ["tracker"]
    assert check["matches"][0]["title"] == "A cheap verifier flags unsupported sentences"
    assert by["Sentence-Level Citation Checks with a Cheap Verifier"]["key"] == "openreview:demo1"
    assert "Old News About Rerankers" not in by and "Region-Grounded Captions with Pointing Supervision" not in by
    assert (config.RMAP_DIR / slug / "map.yaml").read_bytes() == before and project.get(slug).read()["attachments"]["peer"] == []
    # narrowed to one node
    s1 = node_id(slug, "Late-interaction reranker as verifier")
    only = client.get(f"/api/rmap/peers/suggestions?slug={slug}&node={s1}").json()["suggestions"]
    assert [s["key"] for s in only] == ["paper:2610.90002"]
    # once hung there (as a peer or as evidence, by entity), it is no longer suggested there
    ops.apply(slug, [peer(s1, late["title"], entity=late["key"])])
    assert all(m["node"] != s1 for s in peers.suggestions() if s["key"] == late["key"] for m in s["matches"])
    # dismissed for this map: gone, on both Macs (a file in the project folder)
    r = client.post("/api/rmap/peers/dismiss", json={"key": check["key"], "slugs": [slug]})
    assert r.status_code == 200 and r.json()["slugs"] == [slug]
    assert check["key"] not in {s["key"] for s in peers.suggestions()}
    assert check["key"] in peers.dismissed(slug)
    assert (config.RMAP_DIR / slug / "peer_suggestions.yaml").exists()
    client.post("/api/rmap/peers/dismiss", json={"key": check["key"], "slugs": [slug]})  # twice: one record
    assert len([r for r in peers._store(slug).load()["dismissed"] if r["key"] == check["key"]]) == 1
    assert client.post("/api/rmap/peers/dismiss", json={"key": "not a key", "slugs": [slug]}).status_code == 400
    assert client.post("/api/rmap/peers/dismiss", json={"key": check["key"], "slugs": ["nope-1234"]}).status_code == 404
    assert client.get("/api/rmap/peers/suggestions?slug=nope-1234").status_code == 404


def test_suggestions_skip_faded_nodes_and_need_more_than_one_shared_word(slug: str):
    cands = [peers._cand("follow", "x", title="Train the Generator to Cite with RL", abstract="train generator cite reward"),
             peers._cand("follow", "x", title="Errors in Vision Models", abstract="")]
    out = peers.match(cands)
    assert all(m["title"] != "Train the generator to cite" for s in out for m in s["matches"])  # dropped
    assert "Errors in Vision Models" not in {s["title"] for s in out}  # one shared word is not a match


def test_the_readers_paper_finds_its_likely_nodes_and_where_it_hangs(slug: str, make_note, client: TestClient):
    make_note("Rerankers 2026", arxiv="2610.90002", title="Late-Interaction Rerankers Are Strong Attribution Judges",
              tags=["reranker", "attribution"], body="## TL;DR\n\nA late interaction reranker as an attribution verifier.\n")
    r = client.get("/api/rmap/peers/paper", params={"stem": "Rerankers 2026"}).json()
    assert r["entity"] == "paper:2610.90002" and r["note"] == "Rerankers 2026" and r["url"] == "https://arxiv.org/abs/2610.90002"
    assert r["matches"][0]["title"] == "Late-interaction reranker as verifier" and r["attached"] == []
    s1 = node_id(slug, "Late-interaction reranker as verifier")
    ops.apply(slug, [{"op": "attach.add", "kind": "evidence", "node": s1,
                      "set": {"type": "paper", "target": "Rerankers 2026", "entity": "paper:2610.90002"}}])
    r = client.get("/api/rmap/peers/paper", params={"stem": "Rerankers 2026"}).json()
    assert [(h["kind"], h["node"]) for h in r["attached"]] == [("evidence", s1)]
    assert all(m["node"] != s1 for m in r["matches"])
    assert client.get("/api/rmap/peers/paper", params={"stem": "Nope"}).status_code in (400, 404)


# ---------- the assistant ----------


@pytest.fixture
def call(monkeypatch):
    tools: dict = {}
    events: list[dict] = []

    async def emit(e):
        events.append(e)

    real = studio_tools.create_sdk_mcp_server
    monkeypatch.setattr(studio_tools, "create_sdk_mcp_server",
                        lambda name, tools=None, **kw: (found.update({t.name: t for t in tools}), real(name, tools=tools, **kw))[1])
    found = tools
    studio_tools.build(emit)

    def run(name: str, args: dict):
        events.clear()
        res = asyncio.run(tools[name].handler(args))
        return json.loads(res["content"][0]["text"]) if not res.get("is_error") else res["content"][0]["text"], list(events)
    return run


def test_assistant_lists_gaps_and_suggestions_read_only(call, slug: str, feed: dict):
    assert {"map_gaps", "map_peer_suggest"} <= set(studio_tools.READ_ONLY) and "map_peer_propose" not in studio_tools.READ_ONLY
    out, ev = call("map_gaps", {"project": "Project A"})
    assert out["slug"] == slug and {g["title"] for g in out["gaps"]} >= {"Errors come from generation, not retrieval"} and ev == []
    allmaps, _ = call("map_gaps", {})
    assert {g["slug"] for g in allmaps["gaps"]} == {slug}
    sug, _ = call("map_peer_suggest", {"project": slug})
    assert any(s["key"] == "paper:2610.90002" and s["matches"][0]["node"] == node_id(slug, "Late-interaction reranker as verifier") for s in sug)
    err, _ = call("map_gaps", {"project": "nope"})
    assert "没有这张地图" in err


def test_assistant_proposes_peers_and_the_user_confirms(call, slug: str):
    s1 = node_id(slug, "Late-interaction reranker as verifier")
    before = (config.RMAP_DIR / slug / "peers.yaml").exists()
    out, ev = call("map_peer_propose", {"project": slug, "summary": "两篇同行", "peers": [
        {"node": s1, "type": "paper", "title": "Late-Interaction Rerankers Are Strong Attribution Judges", "relation": "same", "arxiv": "2610.90002",
         "note": "same idea, whole answers"},
        {"node": s1, "type": "lab", "title": "Lakeside Retrieval Lab", "relation": "competing"}]})
    assert out["proposal"].startswith("rp") and ev[0]["area"] == "rmap"
    assert (config.RMAP_DIR / slug / "peers.yaml").exists() == before  # nothing written yet
    proposals.apply(out["proposal"])
    got = {p["title"]: p for p in project.get(slug).read()["attachments"]["peer"]}
    assert got["Late-Interaction Rerankers Are Strong Attribution Judges"]["entity"] == "paper:2610.90002"
    assert got["Late-Interaction Rerankers Are Strong Attribution Judges"]["url"] == "https://arxiv.org/abs/2610.90002"
    assert got["Lakeside Retrieval Lab"]["entity"] == "lab:lakeside-retrieval-lab" and got["Lakeside Retrieval Lab"]["source"] == "assistant"
    m, _ = call("map_read", {"project": slug})
    assert next(n for n in m["nodes"] if n["id"] == s1)["peers"] == ["Late-Interaction Rerankers Are Strong Attribution Judges (same)",
                                                                     "Lakeside Retrieval Lab (competing)"]
    err, _ = call("map_peer_propose", {"project": slug, "summary": "x", "peers": [{"node": "nfake", "title": "X", "relation": "same"}]})
    assert "没有这个节点" in err


# ---------- web/rmap/peerlib.js in a JavaScript engine ----------

JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")


@pytest.mark.parametrize("engine", ["node", "jsc"])
def test_peer_rules_in_a_javascript_engine(engine, tmp_path):
    exe = shutil.which("node") if engine == "node" else (str(JSC) if JSC.exists() else None)
    if not exe:
        pytest.skip(f"{engine} is not installed")
    shutil.copy(PROJECT / "web" / "rmap" / "peerlib.js", tmp_path / "peerlib.mjs")
    shutil.copy(PROJECT / "tests" / "js" / "rmap_peers.test.mjs", tmp_path / "test.mjs")
    cases = {"keys": {k: peers.key_text(k) for k in ("Citations That Hold Up: Verifying", "Tomás  Herrera", "a_-b  C", "引用 核验")},
             "entities": [[k, kw, peers.entity_for(k, **kw)] for k, kw in [
                 ("paper", {"arxiv": "2310.11511v2", "title": "x"}), ("paper", {"ext": "openreview:abc", "title": "x"}),
                 ("paper", {"title": "Self-RAG: Learning to Retrieve"}), ("lab", {"lab": "Lakeside Retrieval Lab"}),
                 ("scholar", {"openalex": "A59", "title": "x"}), ("scholar", {"title": "Elena Marchetti"})]]}
    (tmp_path / "cases.mjs").write_text("export default " + json.dumps(cases, ensure_ascii=False) + ";\n", encoding="utf-8")
    args = [exe, "-m", "test.mjs"] if engine == "jsc" else [exe, "test.mjs"]
    r = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr


@pytest.mark.parametrize("engine", ["node", "jsc"])
def test_reader_tool_registry_in_a_javascript_engine(engine, tmp_path):
    exe = shutil.which("node") if engine == "node" else (str(JSC) if JSC.exists() else None)
    if not exe:
        pytest.skip(f"{engine} is not installed")
    shutil.copy(PROJECT / "web" / "reader-tools.js", tmp_path / "reader-tools.mjs")
    shutil.copy(PROJECT / "tests" / "js" / "reader_tools.test.mjs", tmp_path / "test.mjs")
    args = [exe, "-m", "test.mjs"] if engine == "jsc" else [exe, "test.mjs"]
    r = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr


def test_the_hooks_stay_wired():
    """The reader renders the registered tools and loads 挂到地图; the map page hands views ctx.open (documented)."""
    main = (PROJECT / "web" / "main.js").read_text(encoding="utf-8")
    assert 'import { readerTools } from "/reader-tools.js";' in main and 'import "/rmap/attach.js";' in main
    assert "readerTools().map((tool) =>" in main and "tool.render({ stem, paper, toast })" in main
    attach = (PROJECT / "web" / "rmap" / "attach.js").read_text(encoding="utf-8")
    assert 'readerTool({ id: "rmap-attach"' in attach
    page = (PROJECT / "web" / "rmap" / "page.js").read_text(encoding="utf-8")
    assert "open: openNode" in page and "wantNode.current" in page
    assert "| `open(slug, node?)` |" in (PROJECT / "docs" / "research-map.md").read_text(encoding="utf-8")


def test_a_paper_without_a_note_takes_its_title_from_the_reading_list(slug: str, client: TestClient):
    with reading.editing() as items:
        items.append({"id": "r9", "arxiv": "2610.90002", "title": "Late-Interaction Rerankers Are Strong Attribution Judges",
                      "status": "reading", "reason": "a reranker as verifier"})
    r = client.get("/api/rmap/peers/paper", params={"stem": "arxiv:2610.90002"}).json()
    assert r["title"] == "Late-Interaction Rerankers Are Strong Attribution Judges" and r["note"] is None
    assert r["entity"] == "paper:2610.90002" and r["url"] == "https://arxiv.org/abs/2610.90002"
    assert r["matches"][0]["title"] == "Late-interaction reranker as verifier"

"""Research maps, second part: the assistant's proposals, first maps drafted from research-line notes,
the Obsidian Canvas view, the assistant tools, the 方向 link and the UI strings."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, direction, line_chat, studio_tools
from app.rmap import canvas, lines, ops, project, proposals

from .conftest import PROJECT

LINE_NOTE = """---
type: research-line
status: active
created: 2026-01-01
tags: [project-a]
---
# Research Line - Project A

## One-line Pitch

Agents that cite what they use. Every sentence points at a passage.

## Working Title

Citations That Hold Up

## Hypotheses

- A cheap verifier flags unsupported sentences
- Errors come from generation, not retrieval

## First Experiment

Annotate 300 answers for sentence-level support. Then compare a reranker with NLI.

## Kill Criteria

- K1 If the reranker is below NLI after two weeks, drop the verifier.
- K2 If more than 40% of misses come from retrieval, pivot.

## Related

- [[Paper One 2024]] · [[Some Note]] · [[Missing Note]]

## 2026-10-20

K2 partially triggered: 31% of misses come from retrieval.
"""


@pytest.fixture
def line_note(write_vault_file, make_note):
    line_chat._cache.clear()
    make_note("Paper One 2024", title="Paper One")
    write_vault_file("Notes/Some Note.md", "# Some note\n")
    write_vault_file("Ideas/Research Line - Project A.md", LINE_NOTE)
    return "Ideas/Research Line - Project A.md"


@pytest.fixture
def slug() -> str:
    s = ops.create_project("Project Atlas", line="Project A")["slug"]
    ops.apply(s, [{"op": "node.add", "ref": "$q", "set": {"type": "question", "title": "Q"}},
                  {"op": "node.add", "ref": "$h", "parent": "$q", "set": {"type": "hypothesis", "title": "H", "status": "active"}}])
    return s


def node_id(slug: str, title: str) -> str:
    return next(n["id"] for n in project.get(slug).read()["nodes"] if n["title"] == title)


# ---------- dry run ----------


def test_simulate_validates_like_apply_and_writes_nothing(slug: str):
    folder = config.RMAP_DIR / slug
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    sim = ops.simulate(slug, [{"op": "node.update", "id": node_id(slug, "H"), "set": {"status": "verified"}},
                              {"op": "node.add", "ref": "$r", "parent": node_id(slug, "H"), "set": {"type": "result", "title": "R"}}],
                       who="assistant", summary="done")
    assert [(e["kind"], e["action"]) for e in sim["events"]] == [("node", "update"), ("node", "add"), ("edge", "add"), ("log", "note")]
    assert {e["who"] for e in sim["events"]} == {"assistant"} and "$r" in sim["refs"]
    assert {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before
    with pytest.raises(ValueError):
        ops.simulate(slug, [{"op": "node.update", "id": node_id(slug, "H"), "set": {"status": "nope"}}])


def test_simulate_a_new_map_starts_from_nothing():
    sim = ops.simulate(None, [{"op": "node.add", "ref": "$q", "set": {"title": "Q"}},
                              {"op": "attach.add", "kind": "evidence", "node": "$q", "set": {"type": "url", "target": "https://x.org"}}])
    assert [e["kind"] for e in sim["events"]] == ["node", "evidence"]
    assert not (config.RMAP_DIR / "draft").exists() and project.slugs() == []


# ---------- proposals ----------


def test_a_proposal_is_written_only_when_confirmed(slug: str):
    h = node_id(slug, "H")
    prop = proposals.propose(ops_=[{"op": "node.update", "id": h, "set": {"status": "verified"}}], summary="ablation done", slug=slug)
    assert project.get(slug).read()["nodes"][1]["status"] == "active"
    assert [p["id"] for p in proposals.pending(slug)] == [prop["id"]]
    assert proposals.PROPOSALS_FILE.is_relative_to(config.STATE_DIR)
    r = proposals.apply(prop["id"])
    assert r["slug"] == slug
    n = next(n for n in project.get(slug).read()["nodes"] if n["id"] == h)
    assert n["status"] == "verified"
    evs = project.get(slug).events(limit=3)["events"]
    assert {e["who"] for e in evs[:2]} == {"assistant"} and evs[0]["summary"] == "ablation done"
    assert proposals.pending() == []


def test_a_proposal_that_no_longer_fits_stays_and_says_why(slug: str):
    h = node_id(slug, "H")
    prop = proposals.propose(ops_=[{"op": "node.update", "id": h, "set": {"status": "verified"}}], summary="x", slug=slug)
    ops.apply(slug, [{"op": "node.delete", "id": h}])
    with pytest.raises(ValueError, match="地图在提议之后改过了.*已被删除"):
        proposals.apply(prop["id"])
    assert [p["id"] for p in proposals.pending()] == [prop["id"]]
    proposals.discard(prop["id"])
    assert proposals.pending() == []
    with pytest.raises(KeyError):
        proposals.discard(prop["id"])


def test_confirming_refuses_when_a_field_it_changes_was_changed_meanwhile(slug: str):
    h = node_id(slug, "H")
    prop = proposals.propose(ops_=[{"op": "node.update", "id": h, "set": {"status": "verified"}}], summary="x", slug=slug)
    ops.apply(slug, [{"op": "node.update", "id": h, "set": {"status": "blocked"}}])  # e.g. synced from the other Mac
    with pytest.raises(ValueError, match="status"):
        proposals.apply(prop["id"])
    assert next(n for n in project.get(slug).read()["nodes"] if n["id"] == h)["status"] == "blocked"
    assert [p["id"] for p in proposals.pending()] == [prop["id"]]


def test_confirming_goes_ahead_when_only_other_fields_changed(slug: str):
    h = node_id(slug, "H")
    prop = proposals.propose(ops_=[{"op": "node.update", "id": h, "set": {"status": "verified"}}], summary="x", slug=slug)
    ops.apply(slug, [{"op": "node.update", "id": h, "set": {"title": "H renamed"}}])
    proposals.apply(prop["id"])
    n = next(n for n in project.get(slug).read()["nodes"] if n["id"] == h)
    assert (n["title"], n["status"]) == ("H renamed", "verified")


def test_confirming_refuses_a_delete_of_something_edited_meanwhile(slug: str):
    h = node_id(slug, "H")
    prop = proposals.propose(ops_=[{"op": "node.delete", "id": h}], summary="drop H", slug=slug)
    ops.apply(slug, [{"op": "node.update", "id": h, "set": {"body": "new notes"}}])
    with pytest.raises(ValueError, match="H"):
        proposals.apply(prop["id"])
    assert h in {n["id"] for n in project.get(slug).read()["nodes"]}


def test_confirming_refuses_when_the_node_it_builds_on_is_gone(slug: str):
    h = node_id(slug, "H")
    prop = proposals.propose(ops_=[{"op": "node.add", "parent": h, "set": {"type": "experiment", "title": "X"}}],
                             summary="add X", slug=slug)
    assert prop["base"] == [{"kind": "node", "id": h, "title": "H", "fields": {}}]
    ops.apply(slug, [{"op": "node.delete", "id": h}])
    with pytest.raises(ValueError, match="已被删除"):
        proposals.apply(prop["id"])


def test_invalid_proposals_are_refused_up_front(slug: str):
    with pytest.raises(KeyError):
        proposals.propose(ops_=[{"op": "node.update", "id": "nmissing", "set": {"status": "verified"}}], summary="x", slug=slug)
    with pytest.raises(ValueError, match="summary"):
        proposals.propose(ops_=[{"op": "log", "text": "x"}], summary="", slug=slug)
    with pytest.raises(ValueError):
        proposals.propose(ops_=[{"op": "log", "text": "x"}], summary="x", slug=slug, create={"title": "B"})
    with pytest.raises(ValueError):
        proposals.propose(ops_=[{"op": "log", "text": "x"}], summary="x")
    assert proposals.pending() == []


def test_a_new_map_proposal_creates_the_project_on_confirm():
    prop = proposals.propose(ops_=[{"op": "node.add", "ref": "$q", "set": {"type": "question", "title": "Q"}}], summary="first map",
                             create={"title": "Project Beacon", "line": "Project B"}, kind="seed")
    assert project.slugs() == []
    r = proposals.apply(prop["id"])
    assert r["slug"].startswith("project-beacon-")
    m = project.get(r["slug"]).read()
    assert m["project"]["line"] == "Project B" and [n["title"] for n in m["nodes"]] == ["Q"]
    assert proposals.describe(prop)[0].startswith("新增节点")


# ---------- drafting a first map from a research-line note ----------


def test_draft_reads_the_note_into_a_first_map(line_note):
    d = lines.draft("Project A")
    assert d["create"] == {"title": "Project A", "line": "Project A", "note": line_note}
    added = [op["set"] for op in d["ops"] if op["op"] == "node.add"]
    assert [(n["type"], n["title"]) for n in added] == [
        ("question", "Citations That Hold Up"),
        ("hypothesis", "A cheap verifier flags unsupported sentences"),
        ("hypothesis", "Errors come from generation, not retrieval"),
        ("experiment", "Annotate 300 answers for sentence-level support."),
        ("kill", "K1 If the reranker is below NLI after two weeks, drop the verifier."),
        ("kill", "K2 If more than 40% of misses come from retrieval, pivot."),
    ]
    assert added[0]["body"].startswith("Agents that cite")
    assert [n["status"] for n in added if n["type"] == "kill"] == ["todo", "active"]  # K2 partially triggered
    ev = [op["set"] for op in d["ops"] if op["op"] == "attach.add"]
    assert [(e["type"], e["target"]) for e in ev] == [("paper", "Paper One 2024"), ("note", "Notes/Some Note.md"), ("note", line_note)]
    assert "最新状态" in d["summary"]
    ops.simulate(None, d["ops"], who="assistant", summary=d["summary"])  # a valid batch


def test_draft_by_note_name_or_path_and_unknown_lines(line_note):
    assert lines.draft(line_note)["create"]["note"] == line_note
    assert lines.draft("Research Line - Project A")["create"]["note"] == line_note
    with pytest.raises(KeyError):
        lines.draft("Project Z")


def test_research_lines_know_their_map(line_note):
    def noted() -> list[dict]:
        return [x for x in direction.lines() if x["kind"] == "research-line" and x.get("path")]
    assert [x.get("map") for x in noted()] == [None]
    s = ops.create_project("Atlas", line="Project A")["slug"]
    line_chat._cache.clear()
    [ln] = noted()
    assert ln["map"]["slug"] == s and ln["map"]["nodes"] == 0
    assert lines.line_notes()[0]["map"]["slug"] == s


# ---------- canvas ----------


def test_canvas_is_written_after_changes_and_only_when_it_changes(slug: str):
    path = config.RMAP_DIR / slug / "map.canvas"
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = {n["id"] for n in data["nodes"]}
    assert {node_id(slug, "Q"), node_id(slug, "H")} <= ids and "rmap-header" in ids
    [edge] = data["edges"]
    assert (edge["fromNode"], edge["toNode"]) == (node_id(slug, "Q"), node_id(slug, "H"))
    q, h = (next(n for n in data["nodes"] if n["id"] == node_id(slug, t_)) for t_ in ("Q", "H"))
    assert h["x"] > q["x"] and h["color"] == canvas.COLOR["active"] and "假设 · 进行中" in h["text"]
    mtime = path.stat().st_mtime_ns
    assert canvas.write(project.get(slug)) is False and path.stat().st_mtime_ns == mtime
    ops.apply(slug, [{"op": "attach.add", "kind": "evidence", "node": node_id(slug, "H"), "set": {"type": "paper", "target": "P 2024"}}])
    assert "[[P 2024]]" in path.read_text(encoding="utf-8")


def test_reading_or_syncing_never_rewrites_the_canvas(slug: str):
    from app.main import app
    from app.rmap import watch
    path = config.RMAP_DIR / slug / "map.canvas"
    path.write_text("{}", encoding="utf-8")  # say, the other Mac's copy mid-sync
    TestClient(app).get(f"/api/rmap/projects/{slug}")
    assert path.read_text(encoding="utf-8") == "{}"
    p = project.get(slug)
    with p.event_store("2026-02").editing() as data:  # events synced in from the other Mac
        data["events"].append({"id": "evremote", "at": "2026-02-01T09:00:00", "hlc": "9999999999999.0000.othermac",
                               "who": "user", "device": "othermac", "kind": "log", "action": "note", "summary": "remote"})
    watch.once({})
    p.read()
    assert path.read_text(encoding="utf-8") == "{}"
    ops.apply(slug, [{"op": "log", "text": "local"}])  # a write here brings it up to date
    assert json.loads(path.read_text(encoding="utf-8"))["nodes"]


def test_canvas_conflict_copies_are_removed_and_temp_files_stay_out_of_the_vault(slug: str):
    folder = config.RMAP_DIR / slug
    copy = folder / "map.sync-conflict-20261026-120000-ABCDEFG.canvas"
    copy.write_text("{}", encoding="utf-8")
    ops.apply(slug, [{"op": "node.update", "id": node_id(slug, "H"), "set": {"status": "verified"}}])
    assert not copy.exists()
    assert not [f for f in folder.rglob("*.tmp")] and not list((config.DATA_DIR / "rmap").glob("*.canvas*.tmp"))


def test_canvas_labels_follow_the_vault_language(slug: str, monkeypatch: pytest.MonkeyPatch):
    from app import i18n
    monkeypatch.setattr(i18n, "vault_lang", lambda: "en")
    text = canvas.render(project.get(slug).read())
    assert "Hypothesis · In progress" in text and "Generated by PhD Studio" in text


def boxes_overlap(a: dict, b: dict) -> bool:
    return a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"] and a["y"] < b["y"] + b["height"] and b["y"] < a["y"] + a["height"]


def test_canvas_nodes_never_overlap_with_evidence_depends_and_pins(slug: str):
    q, h = node_id(slug, "Q"), node_id(slug, "H")
    r = ops.apply(slug, [
        {"op": "node.add", "ref": "$h2", "parent": q, "set": {"type": "hypothesis", "title": "H2"}},
        {"op": "node.add", "ref": "$x1", "parent": h, "set": {"type": "experiment", "title": "X1"}},
        {"op": "node.add", "ref": "$x2", "parent": "$h2", "set": {"type": "experiment", "title": "X2"}},
        {"op": "edge.add", "from": "$x1", "to": "$x2", "kind": "depends"},  # a cross-link between branches
        *[{"op": "attach.add", "kind": "evidence", "node": h, "set": {"type": "paper", "target": f"P{i}"}} for i in range(5)],
    ])
    x1 = r["refs"]["$x1"]
    # pin X1 where the page would put the first row of the third column
    ops.apply(slug, [{"op": "node.update", "id": x1, "set": {"pin": {"x": 22 + 2 * 212, "y": 44}}}])
    data = json.loads((config.RMAP_DIR / slug / "map.canvas").read_text(encoding="utf-8"))
    cards = [n for n in data["nodes"] if n["id"] != "rmap-header"]
    assert len({n["height"] for n in cards}) == 1
    assert not [(a["id"], b["id"]) for i, a in enumerate(cards) for b in cards[i + 1:] if boxes_overlap(a, b)]
    pinned = next(n for n in cards if n["id"] == x1)
    assert (pinned["x"], pinned["y"]) == (2 * (canvas.W + canvas.GX), 0)  # the page's padding is taken off
    assert "+" not in pinned["text"] and "还有 3 条证据" in next(n for n in cards if n["id"] == h)["text"]


def test_canvas_is_deterministic(slug: str):
    m = project.get(slug).read()
    assert canvas.render(m) == canvas.render(project.get(slug).read())


def test_lateral_edges_and_cycles_do_not_break_the_canvas(slug: str):
    q, h = node_id(slug, "Q"), node_id(slug, "H")
    ops.apply(slug, [{"op": "edge.add", "from": h, "to": q, "kind": "alternative"}])
    m = project.get(slug).read()
    m["edges"].append({"id": "ecycle", "from": h, "to": q, "kind": "refines"})  # two Macs closed a cycle
    data = json.loads(canvas.render(m))
    assert len(data["edges"]) == 3 and {n["id"] for n in data["nodes"]} >= {q, h}


# ---------- assistant tools ----------


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


def test_map_read_lists_and_reads_maps(call, slug: str):
    listing, _ = call("map_read", {})
    assert listing[0]["slug"] == slug
    m, _ = call("map_read", {"project": "Project A"})  # by research line
    assert m["project"]["slug"] == slug
    h = next(n for n in m["nodes"] if n["title"] == "H")
    assert h["parents"] == [node_id(slug, "Q")] and h["status"] == "active"
    err, _ = call("map_read", {"project": "nope"})
    assert "没有这张地图" in err
    assert "map_read" in studio_tools.READ_ONLY


def test_map_propose_records_a_proposal_and_tells_the_page(call, slug: str):
    h = node_id(slug, "H")
    out, ev = call("map_propose", {"project": "Project Atlas", "summary": "消融做完了",
                                   "ops": [{"op": "node.update", "id": h, "set": {"status": "verified"}},
                                           {"op": "attach.add", "kind": "evidence", "node": h, "set": {"type": "paper", "target": "P 2024"}}]})
    assert out["proposal"].startswith("rp") and "不能应用" in out["note"]
    assert ev[0]["type"] == "studio_changed" and ev[0]["area"] == "rmap"
    assert project.get(slug).read()["attachments"]["evidence"] == []
    err, ev = call("map_propose", {"project": slug, "summary": "x", "ops": [{"op": "node.update", "id": "nfake", "set": {"status": "verified"}}]})
    assert "没有这个节点" in err and ev == []


def test_map_seed_draft_then_propose_a_new_map(call, line_note):
    d, _ = call("map_seed_draft", {"line": "Project A"})
    out, ev = call("map_propose", {"new_project": d["create"], "summary": d["summary"], "ops": d["ops"]})
    assert "第一版草稿" in ev[0]["summary"]
    assert proposals.pending()[0]["create"]["note"] == line_note and project.slugs() == []


# ---------- routes ----------


def test_proposal_and_seed_routes(line_note):
    from app.main import app
    c = TestClient(app)
    assert c.get("/api/rmap/lines").json()["lines"][0]["path"] == line_note
    p = c.post("/api/rmap/seed", json={"line": "Project A"}).json()
    assert p["kind"] == "seed" and p["source"] == "note"
    assert [x["id"] for x in c.get("/api/rmap/proposals").json()["proposals"]] == [p["id"]]
    r = c.post(f"/api/rmap/proposals/{p['id']}/apply").json()
    assert r["slug"].startswith("project-a-") and c.get("/api/rmap/proposals").json()["proposals"] == []
    assert c.post("/api/rmap/proposals/rpnope/apply").status_code == 404
    assert c.delete("/api/rmap/proposals/rpnope").status_code == 404
    assert c.post("/api/rmap/seed", json={"line": "nope"}).status_code == 404


# ---------- UI strings ----------


def _keys(lang: str, tree: str = "web") -> set[str]:
    """The research map's keys in one language (web/locales/<lang>/rmap.json, or app/locales for the server)."""
    data = json.loads((PROJECT / tree / "locales" / lang / "rmap.json").read_text(encoding="utf-8"))
    return {f"rmap.{k}" for k in data}


def test_the_map_text_lives_in_its_locale_module_in_both_languages():
    assert _keys("zh") and _keys("zh") == _keys("en")
    assert _keys("zh", "app") == _keys("en", "app")
    shim = (PROJECT / "web" / "rmap" / "strings.js").read_text(encoding="utf-8")
    assert '"rmap.' not in shim  # no text of its own any more: only the extensions' label() shim
    import sys
    sys.path.insert(0, str(PROJECT / "scripts"))
    import i18n_scan
    report = i18n_scan.scan([str(PROJECT / "web" / "rmap")])
    assert all(r["done"] and not r["lines"] and not r["errors"] for r in report.values()), report
    assert len(report) == len(list((PROJECT / "web" / "rmap").glob("*.js")))


def test_strings_cover_the_whole_vocabulary():
    from app.rmap import evidence, model
    zh = _keys("zh")
    want = ({f"rmap.status.{s}" for s in model.STATUSES} | {f"rmap.type.{k}" for k in model.NODE_TYPES}
            | {f"rmap.new.{k}" for k in model.NODE_TYPES} | {f"rmap.edge.{k}" for k in model.EDGE_KINDS}
            | {f"rmap.who.{w}" for w in model.WHO} | {f"rmap.evidence.{x}" for x in evidence.TYPES}
            | {f"rmap.stance.{x}" for x in evidence.STANCES}
            | {f"rmap.field.{f}" for f in (*model.NODE_FIELDS, *model.EDGE_FIELDS, *model.PROJECT_FIELDS)}
            | {f"rmap.col.{k}" for k, v in model.NODE_TYPES.items() if v["col"] is not None} | {"rmap.kind.evidence"})
    assert want - zh == set()


# ---------- web/rmap/layout.js in a JavaScript engine ----------

JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")


@pytest.mark.parametrize("engine", ["node", "jsc"])
def test_map_layout_in_a_javascript_engine(engine, tmp_path):
    exe = shutil.which("node") if engine == "node" else (str(JSC) if JSC.exists() else None)
    if not exe:
        pytest.skip(f"{engine} is not installed")
    src = (PROJECT / "web" / "rmap" / "layout.js").read_text(encoding="utf-8").replace('from "./util.js"', 'from "./util.mjs"')
    (tmp_path / "layout.mjs").write_text(src, encoding="utf-8")
    shutil.copy(PROJECT / "web" / "rmap" / "util.js", tmp_path / "util.mjs")
    shutil.copy(PROJECT / "tests" / "js" / "rmap_layout.test.mjs", tmp_path / "test.mjs")
    args = [exe, "-m", "test.mjs"] if engine == "jsc" else [exe, "test.mjs"]
    r = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr

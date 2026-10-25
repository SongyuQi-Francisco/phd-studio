"""Research maps (app/rmap): project files, batched writes with events, sync merge, extension points, routes."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import config
from app.rmap import model, ops, project, registry
from app.rmap.registry import AttachmentKind

from .conftest import ROOT


@pytest.fixture
def slug() -> str:
    return ops.create_project("Project Atlas", line="Project A")["slug"]


def nodes(slug: str) -> dict[str, dict]:
    return {n["title"]: n for n in project.get(slug).read()["nodes"]}


def raw(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def all_events(slug: str) -> list[dict]:
    return project.get(slug).events(limit=1000)


def conflict(path: Path, name: str = "20261026-120000-ABCDEFG") -> Path:
    return path.parent / f"{path.stem}.sync-conflict-{name}{path.suffix}"


def seed(slug: str) -> dict[str, str]:
    r = ops.apply(slug, [
        {"op": "node.add", "ref": "$q", "set": {"type": "question", "title": "Can agents recommend?"}},
        {"op": "node.add", "ref": "$h", "set": {"type": "hypothesis", "title": "Tool use helps", "status": "active"},
         "parent": "$q"},
        {"op": "node.add", "ref": "$x", "set": {"type": "experiment", "title": "Ablation A"}, "parent": "$h"},
        {"op": "attach.add", "ref": "$v", "kind": "evidence", "node": "$h",
         "set": {"type": "paper", "target": "Attention 2017", "stance": "supports"}},
    ], summary="first map")
    return r["refs"]


# ---------- projects ----------


def test_project_folder_lives_in_the_configured_vault_folder(slug: str):
    assert slug == "project-atlas"
    folder = config.RMAP_DIR / slug
    assert folder.parent == config.VAULT_DIR / "Research Maps"
    data = raw(folder / "map.yaml")
    assert data["project"][0]["title"] == "Project Atlas" and data["project"][0]["line"] == "Project A"
    assert (folder / "map.yaml").read_text(encoding="utf-8").startswith("# Research map")
    assert project.slugs() == [slug]
    [ev] = all_events(slug)
    assert ev["kind"] == "project" and ev["action"] == "add" and ev["who"] == "user" and ev["device"] == "test"


def test_project_names_get_unique_readable_folders():
    a = ops.create_project("Project Atlas")["slug"]
    b = ops.create_project("Project Atlas")["slug"]
    c = ops.create_project("课题 B：检索")["slug"]
    assert (a, b) == ("project-atlas", "project-atlas-2")
    assert c == "课题-b-检索"
    with pytest.raises(ValueError):
        ops.create_project("x", slug="project-atlas")
    with pytest.raises(ValueError):
        ops.create_project("x", slug="../escape")
    with pytest.raises(ValueError):
        ops.create_project("   ")


def test_unknown_or_hostile_project_names_are_not_found():
    for s in ("nope", "..", "../vault", ".hidden", "a/b"):
        with pytest.raises(KeyError):
            project.get(s)


def test_lock_and_temp_files_stay_out_of_the_vault(slug: str):
    seed(slug)
    p = project.get(slug)
    assert p.map._lock.parent == config.DATA_DIR / "rmap" and p.map._tmp.parent == config.DATA_DIR / "rmap"
    vault_files = {f.relative_to(config.RMAP_DIR).as_posix() for f in config.RMAP_DIR.rglob("*") if f.is_file()}
    month = all_events(slug)[0]["at"][:7]
    assert vault_files == {f"{slug}/map.yaml", f"{slug}/evidence.yaml", f"{slug}/events/{month}.yaml"}


# ---------- nodes, edges, attachments ----------


def test_node_add_with_parent_adds_the_edge_and_logs_one_group(slug: str):
    refs = seed(slug)
    m = project.get(slug).read()
    assert {n["title"]: n["status"] for n in m["nodes"]} == {"Can agents recommend?": "todo", "Tool use helps": "active",
                                                             "Ablation A": "todo"}
    assert {(e["from"], e["to"], e["kind"]) for e in m["edges"]} == {(refs["$q"], refs["$h"], "refines"),
                                                                     (refs["$h"], refs["$x"], "refines")}
    assert [a["target"] for a in m["attachments"]["evidence"]] == ["Attention 2017"]
    evs = [e for e in all_events(slug) if e["kind"] != "project"]
    assert len({e["group"] for e in evs}) == 1
    assert sorted((e["kind"], e["action"]) for e in evs) == sorted(
        [("node", "add")] * 3 + [("edge", "add")] * 2 + [("evidence", "add"), ("log", "note")])
    assert next(e for e in evs if e["kind"] == "log")["summary"] == "first map"
    assert all(r.startswith(("n", "e", "v")) for r in refs.values())


def test_update_logs_only_real_changes_and_skips_the_write_when_nothing_changed(slug: str):
    refs = seed(slug)
    path = config.RMAP_DIR / slug / "map.yaml"
    before = len(all_events(slug))
    r = ops.apply(slug, [{"op": "node.update", "id": refs["$h"], "set": {"status": "verified", "title": "Tool use helps"}}])
    [ev] = r["events"]
    assert ev["changes"] == {"status": ["active", "verified"]} and ev["nodes"] == [refs["$h"]]
    mtime = path.stat().st_mtime_ns
    r = ops.apply(slug, [{"op": "node.update", "id": refs["$h"], "set": {"status": "verified"}}])
    assert r["events"] == [] and path.stat().st_mtime_ns == mtime
    assert len(all_events(slug)) == before + 1


def test_clearing_a_field_removes_it(slug: str):
    refs = seed(slug)
    ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"body": "notes", "pin": {"x": 10, "y": 20.04}}}])
    assert nodes(slug)["Ablation A"]["pin"] == {"x": 10.0, "y": 20.0}
    ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"body": "", "pin": None}}])
    n = nodes(slug)["Ablation A"]
    assert "body" not in n and "pin" not in n


def test_ext_data_is_merged_per_extension_key(slug: str):
    refs = seed(slug)
    ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"ext": {"results": {"metric": "Recall@10"}}}}])
    ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"ext": {"peers": {"seen": 3}}}}])
    assert nodes(slug)["Ablation A"]["ext"] == {"results": {"metric": "Recall@10"}, "peers": {"seen": 3}}
    ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"ext": {"results": None}}}])
    assert nodes(slug)["Ablation A"]["ext"] == {"peers": {"seen": 3}}
    with pytest.raises(ValueError):
        ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"ext": {"Bad Key": 1}}}])


@pytest.mark.parametrize("patch", [{"status": "done"}, {"type": "paper"}, {"title": ""}, {"title": "x" * 201},
                                   {"pin": {"x": 1}}, {"pin": {"x": float("nan"), "y": 1}}, {"color": "red"}])
def test_invalid_node_fields_are_refused(slug: str, patch: dict):
    refs = seed(slug)
    with pytest.raises(ValueError):
        ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": patch}])


def test_titles_are_single_line(slug: str):
    refs = seed(slug)
    ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"title": "  two\nlines  "}}])
    assert "two lines" in nodes(slug)


def test_delete_tombstones_the_node_its_edges_and_its_attachments(slug: str):
    refs = seed(slug)
    r = ops.apply(slug, [{"op": "node.delete", "id": refs["$h"]}])
    assert sorted((e["kind"], e["action"]) for e in r["events"]) == [("edge", "delete"), ("edge", "delete"),
                                                                     ("evidence", "delete"), ("node", "delete")]
    m = project.get(slug).read()
    assert set(nodes(slug)) == {"Can agents recommend?", "Ablation A"} and m["edges"] == [] and m["attachments"]["evidence"] == []
    full = project.get(slug).read(include_deleted=True)
    assert all(e.get("deleted") for e in full["edges"])
    assert next(n for n in full["nodes"] if n["id"] == refs["$h"])["deleted"] is True
    with pytest.raises(KeyError):
        ops.apply(slug, [{"op": "node.update", "id": refs["$h"], "set": {"title": "zombie"}}])


def test_edge_rules(slug: str):
    refs = seed(slug)
    q, h, x = refs["$q"], refs["$h"], refs["$x"]
    with pytest.raises(ValueError, match="自己"):
        ops.apply(slug, [{"op": "edge.add", "from": q, "to": q}])
    with pytest.raises(ValueError, match="已经有"):
        ops.apply(slug, [{"op": "edge.add", "from": q, "to": h}])
    with pytest.raises(ValueError, match="环"):
        ops.apply(slug, [{"op": "edge.add", "from": x, "to": q, "kind": "depends"}])
    with pytest.raises(ValueError):
        ops.apply(slug, [{"op": "edge.add", "from": q, "to": x, "kind": "cites"}])
    with pytest.raises(KeyError):
        ops.apply(slug, [{"op": "edge.add", "from": q, "to": "n-missing"}])
    # lateral kinds may point backwards, but only once per pair whatever the direction
    eid = ops.apply(slug, [{"op": "edge.add", "ref": "$a", "from": x, "to": q, "kind": "alternative"}])["refs"]["$a"]
    with pytest.raises(ValueError, match="已经有"):
        ops.apply(slug, [{"op": "edge.add", "from": q, "to": x, "kind": "alternative"}])
    with pytest.raises(ValueError, match="环"):  # and cannot be turned into a backward flow edge
        ops.apply(slug, [{"op": "edge.update", "id": eid, "set": {"kind": "depends"}}])
    ops.apply(slug, [{"op": "edge.update", "id": eid, "set": {"label": "either way", "kind": "conflicts"}}])
    e = next(e for e in project.get(slug).read()["edges"] if e["id"] == eid)
    assert e["kind"] == "conflicts" and e["label"] == "either way"


def test_attachment_rules(slug: str):
    refs = seed(slug)
    for bad in ({"type": "paper"}, {"type": "web", "target": "x"}, {"type": "note", "target": "../outside.md"},
                {"type": "file", "target": "/etc/passwd"}, {"type": "paper", "target": "x", "stance": "maybe"},
                {"type": "paper", "target": "x", "weight": 3}):
        with pytest.raises(ValueError):
            ops.apply(slug, [{"op": "attach.add", "kind": "evidence", "node": refs["$x"], "set": bad}])
    with pytest.raises(ValueError, match="没有这种挂载"):
        ops.apply(slug, [{"op": "attach.add", "kind": "peer", "node": refs["$x"], "set": {}}])
    r = ops.apply(slug, [{"op": "attach.add", "ref": "$u", "kind": "evidence", "node": refs["$x"],
                          "set": {"type": "url", "target": "https://example.org/a?b=1", "title": "Blog"}}])
    vid = r["refs"]["$u"]
    ops.apply(slug, [{"op": "attach.update", "kind": "evidence", "id": vid, "set": {"title": None, "note": "why"}}])
    rec = next(a for a in project.get(slug).read()["attachments"]["evidence"] if a["id"] == vid)
    assert "title" not in rec and rec["note"] == "why"
    with pytest.raises(ValueError):  # required fields cannot be cleared
        ops.apply(slug, [{"op": "attach.update", "kind": "evidence", "id": vid, "set": {"target": None}}])


# ---------- batches ----------


def test_batch_is_all_or_nothing(slug: str):
    refs = seed(slug)
    folder = config.RMAP_DIR / slug
    before = {p: p.read_bytes() for p in folder.rglob("*.yaml")}
    with pytest.raises(ValueError):
        ops.apply(slug, [
            {"op": "node.add", "ref": "$n", "set": {"title": "new one"}, "parent": refs["$h"]},
            {"op": "attach.add", "kind": "evidence", "node": "$n", "set": {"type": "paper", "target": "P"}},
            {"op": "node.update", "id": refs["$x"], "set": {"status": "nonsense"}},
        ])
    assert {p: p.read_bytes() for p in folder.rglob("*.yaml")} == before


def test_batch_refs_must_be_defined_first_and_unique(slug: str):
    with pytest.raises(ValueError, match="还没定义"):
        ops.apply(slug, [{"op": "node.update", "id": "$later", "set": {"title": "x"}},
                         {"op": "node.add", "ref": "$later", "set": {"title": "x"}}])
    with pytest.raises(ValueError, match="ref"):
        ops.apply(slug, [{"op": "node.add", "ref": "$a", "set": {"title": "x"}}, {"op": "node.add", "ref": "$a", "set": {"title": "y"}}])
    with pytest.raises(ValueError):
        ops.apply(slug, [{"op": "node.explode"}])
    with pytest.raises(ValueError):
        ops.apply(slug, [])
    with pytest.raises(ValueError):
        ops.apply(slug, [{"op": "log", "text": "x"}], who="someone")


def test_a_log_entry_alone_is_an_event_without_touching_the_map(slug: str):
    refs = seed(slug)
    path = config.RMAP_DIR / slug / "map.yaml"
    mtime = path.stat().st_mtime_ns
    r = ops.apply(slug, [{"op": "log", "text": "Talked to advisor", "nodes": [refs["$h"]]}], who="assistant")
    [ev] = r["events"]
    assert ev["summary"] == "Talked to advisor" and ev["nodes"] == [refs["$h"]] and ev["who"] == "assistant"
    assert "group" not in ev and path.stat().st_mtime_ns == mtime


# ---------- events ----------


def test_events_are_newest_first_and_filterable(slug: str):
    refs = seed(slug)
    p = project.get(slug)
    month = p.event_months()[0]
    # an older month on disk: the feed walks back through the files
    old = p.event_store("2026-01")
    with old.editing() as data:
        data["events"].append({"id": "evold", "at": "2026-01-05T10:00:00", "who": "user", "kind": "node",
                               "action": "update", "target": refs["$x"], "nodes": [refs["$x"]]})
    assert p.event_months() == sorted({month, "2026-01"}, reverse=True)
    evs = p.events(limit=1000)
    assert evs[-1]["id"] == "evold"
    assert [e["at"] for e in evs] == sorted((e["at"] for e in evs), reverse=True)
    assert [e["id"] for e in p.events(before="2026-02-01")] == ["evold"]
    assert "evold" not in {e["id"] for e in p.events(since="2026-01-05T10:00:00")}
    assert {e["kind"] for e in p.events(node=refs["$x"], limit=1000)} == {"node", "edge"}
    assert len(p.events(limit=2)) == 2
    assert all(e["kind"] == "evidence" for e in p.events(kind="evidence"))


def test_event_log_conflict_copies_are_unioned(slug: str):
    seed(slug)
    p = project.get(slug)
    month = p.event_months()[0]
    path = p.dir / "events" / f"{month}.yaml"
    other = raw(path)
    other["events"] = [{"id": "evtheirs", "at": f"{month}-02T09:00:00", "who": "user", "device": "other-mac",
                        "kind": "log", "action": "note", "summary": "from the other Mac"}]
    conflict(path).write_text(yaml.safe_dump(other, allow_unicode=True), encoding="utf-8")
    ids = {e["id"] for e in p.events(limit=1000)}
    assert "evtheirs" in ids and len(ids) == 9
    assert not conflict(path).exists()


# ---------- sync between two Macs ----------


def test_map_conflict_copy_merges_nodes_and_newest_edit_wins(slug: str):
    refs = seed(slug)
    path = config.RMAP_DIR / slug / "map.yaml"
    mine = raw(path)
    theirs = raw(path)
    h = next(n for n in theirs["nodes"] if n["id"] == refs["$h"])
    h.update(status="blocked", updated_at="2099-01-01T00:00:00")
    theirs["nodes"].append({"id": "nother", "type": "subline", "title": "Their sub-line", "status": "todo",
                            "created_at": "2099-01-01T00:00:00", "updated_at": "2099-01-01T00:00:00"})
    theirs["edges"].append({"id": "eother", "from": refs["$h"], "to": "nother", "kind": "refines",
                            "updated_at": "2099-01-01T00:00:00"})
    x = next(n for n in theirs["nodes"] if n["id"] == refs["$x"])
    x.update(title="their older title", updated_at="2000-01-01T00:00:00")
    path.write_text(yaml.safe_dump(mine, allow_unicode=True), encoding="utf-8")
    conflict(path).write_text(yaml.safe_dump(theirs, allow_unicode=True), encoding="utf-8")
    got = nodes(slug)
    assert got["Tool use helps"]["status"] == "blocked"
    assert "Their sub-line" in got and "Ablation A" in got
    assert any(e["id"] == "eother" for e in project.get(slug).read()["edges"])
    assert not conflict(path).exists()


def test_a_node_deleted_on_the_other_mac_hides_its_edges_and_evidence(slug: str):
    refs = seed(slug)
    path = config.RMAP_DIR / slug / "map.yaml"
    theirs = raw(path)
    for n in theirs["nodes"]:
        if n["id"] == refs["$h"]:
            n.update(deleted=True, updated_at="2099-01-01T00:00:00")  # their Mac deleted only the node record
    conflict(path).write_text(yaml.safe_dump(theirs, allow_unicode=True), encoding="utf-8")
    m = project.get(slug).read()
    assert refs["$h"] not in {n["id"] for n in m["nodes"]}
    assert m["edges"] == [] and m["attachments"]["evidence"] == []


def test_edits_after_a_merge_work_on_records_from_the_other_mac(slug: str):
    seed(slug)
    path = config.RMAP_DIR / slug / "map.yaml"
    theirs = raw(path)
    theirs["nodes"].append({"id": "nother", "type": "subline", "title": "Theirs", "status": "todo",
                            "updated_at": "2026-10-26T10:00:00"})
    conflict(path).write_text(yaml.safe_dump(theirs, allow_unicode=True), encoding="utf-8")
    ops.apply(slug, [{"op": "node.update", "id": "nother", "set": {"status": "active"}}])
    assert nodes(slug)["Theirs"]["status"] == "active"


# ---------- change stamp ----------


def test_stamp_moves_on_writes_and_sync_copies_but_not_on_reads(slug: str):
    refs = seed(slug)
    p = project.get(slug)
    s0 = p.stamp()
    p.read()
    p.events()
    assert p.stamp() == s0
    ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"status": "active"}}])
    s1 = p.stamp()
    assert s1 != s0
    path = p.dir / "map.yaml"
    conflict(path).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    assert p.stamp() != s1
    (p.dir / "map.canvas").write_text("{}", encoding="utf-8")  # generated views do not count
    s2 = p.stamp()
    os.utime(p.dir / "map.canvas")
    assert p.stamp() == s2


# ---------- extension points ----------


@pytest.fixture
def peer_kind():
    kind = AttachmentKind(name="peer_test", prefix="p", fields={"who": str, "relation": str}, required=("who",),
                          node_types=("hypothesis",), file="peers_test.yaml")
    registry.attachment_kind(kind)
    yield kind
    registry.KINDS.pop(kind.name, None)


def test_a_registered_attachment_kind_gets_its_own_file_and_cascade(slug: str, peer_kind):
    refs = seed(slug)
    with pytest.raises(ValueError, match="只能挂在"):
        ops.apply(slug, [{"op": "attach.add", "kind": "peer_test", "node": refs["$x"], "set": {"who": "A. Lab"}}])
    ops.apply(slug, [{"op": "attach.add", "kind": "peer_test", "node": refs["$h"], "set": {"who": "A. Lab"}}])
    assert raw(config.RMAP_DIR / slug / "peers_test.yaml")["peer_test"][0]["who"] == "A. Lab"
    assert project.get(slug).read()["attachments"]["peer_test"][0]["node"] == refs["$h"]
    r = ops.apply(slug, [{"op": "node.delete", "id": refs["$h"]}])
    assert ("peer_test", "delete") in {(e["kind"], e["action"]) for e in r["events"]}


def test_attachment_kinds_cannot_clash():
    with pytest.raises(ValueError):
        registry.attachment_kind(AttachmentKind(name="node", prefix="x", fields={}))
    with pytest.raises(ValueError):
        registry.attachment_kind(AttachmentKind(name="other", prefix="x", fields={}, file="evidence.yaml"))
    with pytest.raises(ValueError):
        registry.attachment_kind(AttachmentKind(name="evidence", prefix="x", fields={}))


def test_event_subscribers_see_every_write_and_cannot_break_it(slug: str, monkeypatch: pytest.MonkeyPatch):
    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(registry, "_SUBSCRIBERS", [])
    registry.on_event(lambda s, evs: seen.append((s, len(evs))))

    @registry.on_event
    def broken(s, evs):
        raise RuntimeError("extension bug")

    refs = seed(slug)
    ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"status": "active"}}])
    assert seen == [(slug, 7), (slug, 1)]
    assert nodes(slug)["Ablation A"]["status"] == "active"


def test_plugins_package_loads_quietly():
    mods = registry.load_plugins()
    assert all(m.__name__.startswith("app.rmap.plugins.") for m in mods)


# ---------- routes ----------


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


def test_routes_roundtrip(client: TestClient):
    r = client.post("/api/rmap/projects", json={"title": "Project Beacon", "line": "Project B"})
    assert r.status_code == 200
    s = r.json()["slug"]
    q = client.post(f"/api/rmap/projects/{s}/nodes", json={"type": "question", "title": "Q"}).json()["id"]
    h = client.post(f"/api/rmap/projects/{s}/nodes", json={"title": "H", "parent": q}).json()["id"]
    assert client.patch(f"/api/rmap/projects/{s}/nodes/{h}", json={"status": "blocked"}).status_code == 200
    v = client.post(f"/api/rmap/projects/{s}/attachments/evidence",
                    json={"node": h, "type": "note", "target": "Ideas/Beacon.md"}).json()["id"]
    assert client.get(f"/api/rmap/projects/{s}/attachments/evidence", params={"node": h}).json()["evidence"][0]["id"] == v
    m = client.get(f"/api/rmap/projects/{s}").json()
    assert {n["title"] for n in m["nodes"]} == {"Q", "H"} and len(m["edges"]) == 1 and m["stamp"]
    e = m["edges"][0]["id"]
    assert client.patch(f"/api/rmap/projects/{s}/edges/{e}", json={"label": "via tools"}).status_code == 200
    assert client.get(f"/api/rmap/projects/{s}/stamp").json()["stamp"] != m["stamp"]
    assert client.patch(f"/api/rmap/projects/{s}", json={"description": "About beacons"}).status_code == 200
    listing = client.get("/api/rmap/projects").json()["projects"]
    assert listing[0]["slug"] == s and listing[0]["status"]["blocked"] == 1 and listing[0]["nodes"] == 2
    evs = client.get(f"/api/rmap/projects/{s}/events", params={"node": h}).json()["events"]
    assert evs[0]["kind"] == "edge" and evs[0]["action"] == "update"
    assert client.delete(f"/api/rmap/projects/{s}/attachments/evidence/{v}").status_code == 200
    assert client.delete(f"/api/rmap/projects/{s}/edges/{e}").status_code == 200
    assert client.delete(f"/api/rmap/projects/{s}/nodes/{h}").status_code == 200
    r = client.post(f"/api/rmap/projects/{s}/batch", json={"ops": [{"op": "node.add", "ref": "$a", "set": {"title": "A"}}],
                                                           "summary": "added A"})
    assert r.status_code == 200 and "$a" in r.json()["refs"]
    assert client.patch(f"/api/rmap/projects/{s}", json={"archived": True}).status_code == 200
    assert client.get("/api/rmap/projects").json()["projects"] == []
    assert client.get("/api/rmap/projects", params={"archived": True}).json()["projects"][0]["archived"] is True


def test_route_errors(client: TestClient, slug: str):
    assert client.get("/api/rmap/projects/nope").status_code == 404
    assert client.get("/api/rmap/projects/nope/stamp").status_code == 404
    assert client.patch(f"/api/rmap/projects/{slug}/nodes/nmissing", json={"title": "x"}).status_code == 404
    assert client.post(f"/api/rmap/projects/{slug}/nodes", json={"title": ""}).status_code == 400
    assert client.post(f"/api/rmap/projects/{slug}/attachments/nokind", json={}).status_code == 404
    assert client.post(f"/api/rmap/projects/{slug}/batch", json={"ops": []}).status_code == 400
    assert client.post("/api/rmap/projects", json={"title": ""}).status_code == 400


def test_meta_and_extension_listing(client: TestClient):
    meta = client.get("/api/rmap/meta").json()
    assert [t["key"] for t in meta["node_types"]][:5] == ["question", "hypothesis", "subline", "experiment", "result"]
    assert meta["statuses"] == list(model.STATUSES) and meta["edge_kinds"] == list(model.EDGE_KINDS)
    assert any(k["name"] == "evidence" and "paper" in k["types"] for k in meta["kinds"])
    ext = client.get("/api/rmap/ext").json()
    assert set(ext) == {"scripts", "styles"}
    assert all(u.startswith("/rmap/ext/") for u in ext["scripts"] + ext["styles"])
    assert client.get("/api/rmap/stamp").json()["stamp"]


def test_research_maps_live_in_the_sandbox():
    assert config.RMAP_DIR.is_relative_to(ROOT)
    assert project.LOCKS.is_relative_to(ROOT)

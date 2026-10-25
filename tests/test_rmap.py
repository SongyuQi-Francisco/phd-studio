"""Research maps (app/rmap): project files, batched writes with events, sync merge, extension points, routes."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import config
from app.rmap import clock, model, ops, project, registry
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
    return project.get(slug).events(limit=1000)["events"]


def later(field_stamp: str | None = None, device: str = "othermac") -> str:
    """A clock stamp from the other Mac, after everything written so far."""
    p = clock.parse(field_stamp or "") if field_stamp else None
    ms = max(p[0] if p else 0, clock.wall_ms()) + 60_000
    return clock.make(ms, 0, device)


def as_other_mac(rec: dict, stamp: str | None = None, **fields) -> dict:
    """Change fields of a raw record the way the other Mac's app would: values plus their clock stamps."""
    stamp = stamp or later()
    rec.setdefault("clock", {})
    for k, v in fields.items():
        if v is None:
            rec.pop(k, None)
        else:
            rec[k] = v
        rec["clock"][k] = stamp
    return rec


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
    assert re.fullmatch(r"project-atlas-[0-9a-f]{4}", slug)
    folder = config.RMAP_DIR / slug
    assert folder.parent == config.VAULT_DIR / "Research Maps"
    data = raw(folder / "map.yaml")
    assert data["project"][0]["title"] == "Project Atlas" and data["project"][0]["line"] == "Project A"
    assert (folder / "map.yaml").read_text(encoding="utf-8").startswith("# Research map")
    assert project.slugs() == [slug]
    [ev] = all_events(slug)
    assert ev["kind"] == "project" and ev["action"] == "add" and ev["who"] == "user" and ev["device"] == "test"


def test_project_folders_are_lower_case_and_never_shared():
    a = ops.create_project("Project Atlas")["slug"]
    b = ops.create_project("Project Beacon")["slug"]
    c = ops.create_project("课题 B：检索")["slug"]
    assert re.fullmatch(r"project-atlas-[0-9a-f]{4}", a) and re.fullmatch(r"project-beacon-[0-9a-f]{4}", b)
    assert re.fullmatch(r"课题-b-检索-[0-9a-f]{4}", c)
    with pytest.raises(ValueError, match="已经有"):  # the same title again, in any case
        ops.create_project("project ATLAS")
    assert ops.create_project("Fixed", slug="fixed-name")["slug"] == "fixed-name"
    with pytest.raises(ValueError):  # the Mac's disk ignores case: FIXED-NAME is fixed-name
        ops.create_project("Other", slug="FIXED-NAME")
    with pytest.raises(ValueError):
        ops.create_project("Other 2", slug="fixed-name")
    for bad in ("../escape", "Upper", "with_underscore", ""):
        with pytest.raises(ValueError):
            ops.create_project("x " + bad, slug=bad or "-")
    with pytest.raises(ValueError):
        ops.create_project("   ")


def test_two_macs_that_create_the_same_title_get_two_maps_and_a_warning():
    a = ops.create_project("Project Atlas")["slug"]
    # the other Mac, offline, made its own "Project Atlas": its folder syncs in next to ours
    other = config.RMAP_DIR / "project-atlas-ffff"
    other.mkdir()
    (other / "map.yaml").write_text(yaml.safe_dump({"project": [{"id": "project", "title": "Project Atlas", "uid": "ffff0000",
                                                                   "updated_at": "2026-10-26T10:00:00"}]}), encoding="utf-8")
    listing = {p["slug"]: p for p in project.listing()}
    assert set(listing) == {a, "project-atlas-ffff"}
    flagged = [p for p in listing.values() if any(w["kind"] == "same_title" for w in p["warnings"])]
    assert len(flagged) == 1


def test_unknown_or_hostile_project_names_are_not_found():
    for s in ("nope", "..", "../vault", ".hidden", "a/b", "Project-Atlas"):
        with pytest.raises(KeyError):
            project.get(s)


def test_lock_and_temp_files_stay_out_of_the_vault(slug: str):
    seed(slug)
    p = project.get(slug)
    assert p.map._lock.parent == config.DATA_DIR / "rmap" and p.map._tmp.parent == config.DATA_DIR / "rmap"
    vault_files = {f.relative_to(config.RMAP_DIR).as_posix() for f in config.RMAP_DIR.rglob("*") if f.is_file()}
    month = all_events(slug)[0]["at"][:7]
    assert vault_files == {f"{slug}/map.yaml", f"{slug}/evidence.yaml", f"{slug}/events/{month}.yaml", f"{slug}/map.canvas"}


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


# ---------- events: order, cursors, this Mac's ingest sequence ----------


def test_events_are_newest_first_and_filterable(slug: str):
    refs = seed(slug)
    p = project.get(slug)
    month = p.event_months()[0]
    old = p.event_store("2026-01")  # an older month on disk, logged before events had clock stamps
    with old.editing() as data:
        data["events"].append({"id": "evold", "at": "2026-01-05T10:00:00", "who": "user", "kind": "node",
                               "action": "update", "target": refs["$x"], "nodes": [refs["$x"]]})
    assert p.event_months() == sorted({month, "2026-01"}, reverse=True)
    evs = all_events(slug)
    assert evs[-1]["id"] == "evold"
    keys = [(e["hlc"], e["id"]) for e in evs]
    assert keys == sorted(keys, reverse=True) and all(e["cursor"] == f"{e['hlc']}|{e['id']}" for e in evs)
    assert [e["id"] for e in p.events(before="2026-02-01")["events"]] == ["evold"]
    assert "evold" not in {e["id"] for e in p.events(since="2026-01-05T10:00:00")["events"]}
    assert {e["kind"] for e in p.events(node=refs["$x"], limit=1000)["events"]} == {"node", "edge"}
    assert all(e["kind"] == "evidence" for e in p.events(kind="evidence")["events"])
    with pytest.raises(ValueError):
        p.events(before="next tuesday")


def test_paging_by_cursor_loses_nothing_within_one_second(slug: str):
    ops.apply(slug, [{"op": "node.add", "set": {"title": f"n{i}"}} for i in range(30)], summary="one batch")
    p = project.get(slug)
    seen, page = [], p.events(limit=7)
    while True:
        seen += [e["id"] for e in page["events"]]
        if not page["next"]:
            break
        page = p.events(limit=7, before=page["next"])
    every = [e["id"] for e in p.events(limit=1000)["events"]]
    assert seen == every and len(every) == 32  # project + 30 nodes + the log line
    batch = [e for e in p.events(limit=1000)["events"] if e.get("group")]
    assert len({e["hlc"] for e in batch}) == len(batch)  # one stamp per event, in write order


def test_since_a_cursor_sees_writes_in_the_same_second(slug: str):
    p = project.get(slug)
    mark = p.events(limit=1)["events"][0]["cursor"]
    ops.apply(slug, [{"op": "log", "text": "a"}])
    ops.apply(slug, [{"op": "log", "text": "b"}])
    assert [e["summary"] for e in p.events(since=mark)["events"]] == ["b", "a"]


def test_the_ingest_sequence_includes_events_synced_late_from_an_offline_mac(slug: str):
    p = project.get(slug)
    seq = p.events(limit=1)["seq"]
    ops.apply(slug, [{"op": "log", "text": "here, now"}])
    # the other Mac was offline last month; its log for that month arrives only now
    stale = clock.make(clock.wall_ms() - 40 * 86400_000, 0, "othermac")
    with p.event_store("2026-01").editing() as data:
        data["events"].append({"id": "evlate", "at": "2026-01-03T09:00:00", "hlc": stale, "who": "user",
                               "device": "othermac", "kind": "log", "action": "note", "summary": "written offline"})
    got = p.events(after=seq)
    assert [e["summary"] for e in got["events"]] == ["here, now", "written offline"]
    assert got["seq"] == seq + 2 and all(e["seq"] > seq for e in got["events"])
    assert p.events(after=got["seq"])["events"] == []


def test_event_log_conflict_copies_are_unioned(slug: str):
    seed(slug)
    p = project.get(slug)
    month = p.event_months()[0]
    path = p.dir / "events" / f"{month}.yaml"
    other = raw(path)
    other["events"] = [{"id": "evtheirs", "at": f"{month}-02T09:00:00", "hlc": later(), "who": "user",
                        "device": "othermac", "kind": "log", "action": "note", "summary": "from the other Mac"}]
    conflict(path).write_text(yaml.safe_dump(other, allow_unicode=True), encoding="utf-8")
    ids = {e["id"] for e in all_events(slug)}
    assert "evtheirs" in ids and len(ids) == 9
    assert not conflict(path).exists()


def test_subscribers_hear_of_events_from_the_other_mac_once(slug: str, monkeypatch: pytest.MonkeyPatch):
    seen: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(registry, "_SUBSCRIBERS", [])
    registry.on_event(lambda s, evs: seen.append((s, [e.get("summary") or e["kind"] for e in evs])))
    ops.apply(slug, [{"op": "log", "text": "local"}])
    assert seen == [(slug, ["local"])]
    p = project.get(slug)
    with p.event_store("2026-02").editing() as data:  # synced in from the other Mac
        data["events"].append({"id": "evsync", "at": "2026-02-01T09:00:00", "hlc": later(), "who": "user",
                               "device": "othermac", "kind": "log", "action": "note", "summary": "remote"})
    from app.rmap import watch
    watch.once({})  # the background round (also what reading the map does)
    project.get(slug).read()
    assert seen == [(slug, ["local"]), (slug, ["remote"])]


def test_a_device_seeing_a_map_for_the_first_time_does_not_replay_its_history(slug: str, monkeypatch):
    seed(slug)
    for f in project.INGEST.glob("*.json"):  # e.g. the data dir was cleared
        f.unlink()
    seen: list = []
    monkeypatch.setattr(registry, "_SUBSCRIBERS", [])
    registry.on_event(lambda s, evs: seen.append(len(evs)))
    project.get(slug).read()
    assert seen == []
    ops.apply(slug, [{"op": "log", "text": "x"}])
    assert seen == [1]


# ---------- sync between two Macs: field by field ----------


def node_rec(data: dict, nid: str) -> dict:
    return next(n for n in data["nodes"] if n["id"] == nid)


def test_two_macs_changing_different_fields_of_a_node_keep_both(slug: str):
    refs = seed(slug)
    path = config.RMAP_DIR / slug / "map.yaml"
    theirs = raw(path)
    as_other_mac(node_rec(theirs, refs["$h"]), status="blocked")  # the other Mac changed the status
    ops.apply(slug, [{"op": "node.update", "id": refs["$h"], "set": {"title": "Tool use helps (rev)"}}])  # we changed the title
    conflict(path).write_text(yaml.safe_dump(theirs, allow_unicode=True), encoding="utf-8")
    n = nodes(slug)["Tool use helps (rev)"]
    assert n["status"] == "blocked"


def test_the_same_field_goes_to_the_later_stamp_even_in_the_same_second(slug: str):
    refs = seed(slug)
    path = config.RMAP_DIR / slug / "map.yaml"
    ours = node_rec(raw(path), refs["$h"])
    mine_stamp = ours["clock"]["status"]
    ms, c, _ = clock.parse(mine_stamp)
    theirs = raw(path)
    as_other_mac(node_rec(theirs, refs["$h"]), clock.make(ms, c + 1, "othermac"), status="verified")  # same millisecond, one tick on
    conflict(path).write_text(yaml.safe_dump(theirs, allow_unicode=True), encoding="utf-8")
    assert nodes(slug)["Tool use helps"]["status"] == "verified"
    before = raw(path)
    as_other_mac(node_rec(before, refs["$h"]), clock.make(ms, c, "aaa-older-device"), status="todo")  # an earlier stamp loses
    conflict(path, "20261026-130000-BBBBBBB").write_text(yaml.safe_dump(before, allow_unicode=True), encoding="utf-8")
    assert nodes(slug)["Tool use helps"]["status"] == "verified"


def test_a_cleared_field_stays_cleared_against_an_older_copy(slug: str):
    refs = seed(slug)
    path = config.RMAP_DIR / slug / "map.yaml"
    ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"body": "notes"}}])
    old = raw(path)  # the other Mac still has the body
    ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"body": ""}}])
    conflict(path).write_text(yaml.safe_dump(old, allow_unicode=True), encoding="utf-8")
    assert "body" not in nodes(slug)["Ablation A"]


def test_an_edit_after_a_delete_brings_the_node_back_with_what_the_delete_took(slug: str):
    refs = seed(slug)
    path = config.RMAP_DIR / slug / "map.yaml"
    theirs = raw(path)  # the other Mac, not having seen the delete, renames the node a minute later
    ops.apply(slug, [{"op": "node.delete", "id": refs["$h"]}])
    assert "Tool use helps" not in nodes(slug)
    as_other_mac(node_rec(theirs, refs["$h"]), title="Tool use helps, revised")
    conflict(path).write_text(yaml.safe_dump(theirs, allow_unicode=True), encoding="utf-8")
    m = project.get(slug).read()
    assert "Tool use helps, revised" in {n["title"] for n in m["nodes"]}
    assert {(e["from"], e["to"]) for e in m["edges"]} == {(refs["$q"], refs["$h"]), (refs["$h"], refs["$x"])}
    assert [a["target"] for a in m["attachments"]["evidence"]] == ["Attention 2017"]  # consistent: evidence back too
    # and it can be worked on (and deleted again) normally
    ops.apply(slug, [{"op": "node.update", "id": refs["$h"], "set": {"status": "verified"}}])
    ops.apply(slug, [{"op": "node.delete", "id": refs["$h"]}])
    m = project.get(slug).read()
    assert "Tool use helps, revised" not in {n["title"] for n in m["nodes"]} and m["attachments"]["evidence"] == []


def test_a_delete_after_the_edit_wins(slug: str):
    refs = seed(slug)
    path = config.RMAP_DIR / slug / "map.yaml"
    theirs = raw(path)
    ours = node_rec(theirs, refs["$h"])
    as_other_mac(ours, clock.make(clock.parse(ours["clock"]["title"])[0] + 1, 0, "othermac"), title="older edit")
    ops.apply(slug, [{"op": "node.delete", "id": refs["$h"]}])  # after that edit, by stamp
    # a stamp before our delete: written while the other Mac's clock was behind ours
    stamp = clock.make(clock.parse(node_rec(raw(path), refs["$h"])["clock"]["deleted"])[0] - 1, 0, "othermac")
    as_other_mac(node_rec(theirs, refs["$h"]), stamp, title="older edit")
    conflict(path).write_text(yaml.safe_dump(theirs, allow_unicode=True), encoding="utf-8")
    m = project.get(slug).read()
    assert refs["$h"] not in {n["id"] for n in m["nodes"]} and m["attachments"]["evidence"] == []


def test_an_edge_deleted_on_its_own_stays_deleted_when_its_node_comes_back(slug: str):
    refs = seed(slug)
    path = config.RMAP_DIR / slug / "map.yaml"
    edge = next(e for e in project.get(slug).read()["edges"] if e["to"] == refs["$x"])
    ops.apply(slug, [{"op": "edge.delete", "id": edge["id"]}])
    theirs = raw(path)
    ops.apply(slug, [{"op": "node.delete", "id": refs["$x"]}])
    as_other_mac(node_rec(theirs, refs["$x"]), title="Ablation A2")
    conflict(path).write_text(yaml.safe_dump(theirs, allow_unicode=True), encoding="utf-8")
    m = project.get(slug).read()
    assert "Ablation A2" in {n["title"] for n in m["nodes"]} and edge["id"] not in {e["id"] for e in m["edges"]}


def test_records_from_before_clocks_merge_whole_by_updated_at(slug: str):
    path = config.RMAP_DIR / slug / "map.yaml"
    data = raw(path)
    data["nodes"] = [{"id": "nlegacy", "type": "subline", "title": "mine", "status": "todo", "updated_at": "2026-10-24T10:00:00"}]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    data["nodes"] = [{"id": "nlegacy", "type": "subline", "title": "theirs", "status": "active", "updated_at": "2026-10-24T11:00:00"}]
    conflict(path).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    n = nodes(slug)["theirs"]
    assert n["status"] == "active"


def test_a_node_deleted_on_the_other_mac_hides_its_edges_and_evidence(slug: str):
    refs = seed(slug)
    path = config.RMAP_DIR / slug / "map.yaml"
    theirs = raw(path)
    as_other_mac(node_rec(theirs, refs["$h"]), deleted=True)  # their Mac deleted only the node record
    conflict(path).write_text(yaml.safe_dump(theirs, allow_unicode=True), encoding="utf-8")
    m = project.get(slug).read()
    assert refs["$h"] not in {n["id"] for n in m["nodes"]}
    assert m["edges"] == [] and m["attachments"]["evidence"] == []


def test_records_only_the_other_mac_has_are_added_and_editable(slug: str):
    seed(slug)
    path = config.RMAP_DIR / slug / "map.yaml"
    theirs = raw(path)
    theirs["nodes"].append(as_other_mac({"id": "nother", "created_at": "2026-10-26T10:00:00"}, type="subline",
                                        title="Theirs", status="todo"))
    conflict(path).write_text(yaml.safe_dump(theirs, allow_unicode=True), encoding="utf-8")
    ops.apply(slug, [{"op": "node.update", "id": "nother", "set": {"status": "active"}}])
    assert nodes(slug)["Theirs"]["status"] == "active"
    assert not conflict(path).exists()


def test_a_cycle_made_by_two_macs_is_marked_not_walked(slug: str):
    refs = seed(slug)
    q, h = refs["$q"], refs["$h"]
    path = config.RMAP_DIR / slug / "map.yaml"
    theirs = raw(path)
    ops.apply(slug, [{"op": "edge.add", "from": refs["$x"], "to": q, "kind": "alternative"}])  # lateral: no cycle
    theirs["edges"].append(as_other_mac({"id": "eback", "created_at": "2026-10-26T10:00:00"}, **{"from": h, "to": q, "kind": "depends"}))
    conflict(path).write_text(yaml.safe_dump(theirs, allow_unicode=True), encoding="utf-8")
    m = project.get(slug).read()
    marked = {e["id"] for e in m["edges"] if e.get("cycle")}
    assert marked == {"eback"} and m["warnings"] == [{"kind": "cycle", "edges": ["eback"]}]
    assert "eback" not in {e["id"] for e in project.dag_edges(m)}
    assert all(e["kind"] in model.FLOW for e in project.dag_edges(m))
    with pytest.raises(ValueError, match="环"):  # and no new edge may close another one
        ops.apply(slug, [{"op": "edge.add", "from": refs["$x"], "to": h, "kind": "refines"}])


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


# ---------- plugins are part of every code path, not only the HTTP routes ----------


PLUGIN = """
from app.rmap.registry import AttachmentKind, attachment_kind, ext_field

def _value(v):
    if not isinstance(v, (int, float)):
        raise ValueError("value must be a number")
    return v

attachment_kind(AttachmentKind(name="result_t", prefix="r", fields={"metric": str, "value": _value},
                               required=("metric", "value"), node_types=("experiment", "result"), log="batch"))

def _results(v):
    if not isinstance(v, dict) or set(v) - {"metric"}:
        raise ValueError("results: only metric")
    return v

ext_field("results_t", _results)
"""


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    """A plugin module in app/rmap/plugins, as a later phase would add it (the registry not yet loaded)."""
    import sys
    from app.rmap import plugins
    (tmp_path / "zz_results_t.py").write_text(PLUGIN, encoding="utf-8")
    monkeypatch.setattr(plugins, "__path__", [str(tmp_path)])
    monkeypatch.setattr(registry, "_loaded", None)
    yield
    registry.KINDS.pop("result_t", None)
    registry.EXT.pop("results_t", None)
    sys.modules.pop("app.rmap.plugins.zz_results_t", None)


def test_a_plugin_kind_works_without_the_http_routes_being_imported(slug: str, plugin):
    refs = seed(slug)
    # the CLI and the assistant call ops / project directly
    ops.apply(slug, [{"op": "attach.add", "kind": "result_t", "node": refs["$x"], "set": {"metric": "Recall@10", "value": 0.41}}],
              who="cli")
    m = project.get(slug).read()
    assert m["attachments"]["result_t"][0]["value"] == 0.41
    r = ops.apply(slug, [{"op": "node.delete", "id": refs["$x"]}])
    assert ("result_t", "delete") in {(e["kind"], e["action"]) for e in r["events"]}
    assert project.get(slug).read()["attachments"]["result_t"] == []


def test_plugins_validate_their_node_ext_keys(slug: str, plugin):
    refs = seed(slug)
    ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"ext": {"results_t": {"metric": "MRR"}}}}])
    with pytest.raises(ValueError, match="only metric"):
        ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"ext": {"results_t": {"oops": 1}}}}])
    with pytest.raises(ValueError, match="太大"):  # unregistered keys only hold small JSON
        ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"ext": {"free": "x" * 5000}}}])


def test_a_large_import_is_one_atomic_batch_and_one_log_line_per_kind(slug: str, plugin):
    refs = seed(slug)
    rows = [{"op": "attach.add", "kind": "result_t", "node": refs["$x"], "set": {"metric": f"m{i}", "value": i}}
            for i in range(ops.MAX_OPS + 100)]
    with pytest.raises(ValueError, match="最多"):
        ops.apply(slug, rows)  # a person's batch is capped
    before = raw(config.RMAP_DIR / slug / "map.yaml")
    with pytest.raises(ValueError):  # one bad row: nothing of the import is written
        ops.apply(slug, [*rows, {"op": "attach.add", "kind": "result_t", "node": refs["$x"], "set": {"metric": "x", "value": "bad"}}],
                  who="import")
    assert raw(config.RMAP_DIR / slug / "map.yaml") == before and not (config.RMAP_DIR / slug / "result_t.yaml").exists()
    r = ops.apply(slug, rows, who="import", summary="imported results.csv")
    assert len(project.get(slug).read()["attachments"]["result_t"]) == ops.MAX_OPS + 100
    bulk = [e for e in r["events"] if e["kind"] == "result_t"]
    assert len(bulk) == 1 and bulk[0]["count"] == ops.MAX_OPS + 100 and len(bulk[0]["targets"]) == ops.MAX_OPS + 100


# ---------- unsafe links, entities ----------


@pytest.mark.parametrize("target", ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "vbscript:x", "ftp://x"])
def test_links_only_open_safe_schemes(slug: str, target: str):
    refs = seed(slug)
    with pytest.raises(ValueError, match="链接只能是"):
        ops.apply(slug, [{"op": "attach.add", "kind": "evidence", "node": refs["$x"], "set": {"type": "url", "target": target}}])


def test_safe_links_and_no_schemes_in_vault_paths(slug: str):
    refs = seed(slug)
    for t in ("https://example.org", "obsidian://open?vault=V&file=Note", "zotero://select/library/items/ABC"):
        ops.apply(slug, [{"op": "attach.add", "kind": "evidence", "node": refs["$x"], "set": {"type": "url", "target": t}}])
    for t in ("javascript:alert(1)", "~/secret.md", "C:\\x.md", "a/../../b.md"):
        with pytest.raises(ValueError):
            ops.apply(slug, [{"op": "attach.add", "kind": "evidence", "node": refs["$x"], "set": {"type": "note", "target": t}}])
    vid = next(a["id"] for a in project.get(slug).read()["attachments"]["evidence"] if a["target"] == "https://example.org")
    with pytest.raises(ValueError):  # a change is checked as a whole record too
        ops.apply(slug, [{"op": "attach.update", "kind": "evidence", "id": vid, "set": {"target": "javascript:alert(1)"}}])


def test_entities_tie_nodes_and_attachments_across_projects(slug: str):
    refs = seed(slug)
    other = ops.create_project("Project Beacon")["slug"]
    ops.apply(other, [{"op": "node.add", "ref": "$d", "set": {"title": "MS MARCO", "type": "experiment", "entity": "dataset:msmarco"}}])
    ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"entity": "dataset:msmarco"}},
                     {"op": "attach.add", "kind": "evidence", "node": refs["$h"],
                      "set": {"type": "paper", "target": "ColBERT 2020", "entity": "paper:2004.12832"}}])
    idx = project.entities()
    assert {(h["slug"], h["kind"]) for h in idx["dataset:msmarco"]} == {(slug, "node"), (other, "node")}
    assert idx["paper:2004.12832"][0]["node"] == refs["$h"]
    for bad in ("msmarco", "Dataset:x", "dataset: x"):
        with pytest.raises(ValueError, match="entity"):
            ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"entity": bad}}])


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


# ---------- the clock ----------


def test_stamps_keep_order_when_the_wall_clock_goes_back(slug: str, monkeypatch: pytest.MonkeyPatch):
    refs = seed(slug)
    first = ops.apply(slug, [{"op": "node.update", "id": refs["$x"], "set": {"status": "active"}}])["events"][0]["hlc"]
    real = clock.wall_ms()
    monkeypatch.setattr(clock, "wall_ms", lambda: real - 3_600_000)  # NTP set the clock an hour back
    second = ops.apply(slug, [{"op": "log", "text": "after"}])["events"][0]["hlc"]
    assert second > first
    assert clock.parse(second)[2] == config.DEVICE


def test_stamp_format_sorts_as_text():
    a, b = clock.make(1793001234567, 15, "zz"), clock.make(1793001234567, 16, "aa")
    assert a < b and clock.parse(b) == (1793001234567, 16, "aa")
    assert clock.make(999, 0, "x") < clock.make(1000, 0, "a")
    assert clock.parse("nonsense") is None and clock.from_iso("2026-10-26T10:00:00")


def test_events_route_pages_and_follows(client: TestClient, slug: str):
    ops.apply(slug, [{"op": "node.add", "set": {"title": f"n{i}"}} for i in range(5)])
    r = client.get(f"/api/rmap/projects/{slug}/events", params={"limit": 3}).json()
    assert len(r["events"]) == 3 and r["next"] and r["seq"] >= 6
    r2 = client.get(f"/api/rmap/projects/{slug}/events", params={"limit": 3, "before": r["next"]}).json()
    assert not {e["id"] for e in r["events"]} & {e["id"] for e in r2["events"]}
    m = client.get(f"/api/rmap/projects/{slug}").json()
    assert m["seq"] == r["seq"] and m["warnings"] == []
    assert client.get(f"/api/rmap/projects/{slug}/events", params={"after": m["seq"]}).json()["events"] == []
    assert client.get(f"/api/rmap/projects/{slug}/events", params={"before": "garbage"}).status_code == 400
    assert client.get("/api/rmap/entities").json() == {"entities": {}}

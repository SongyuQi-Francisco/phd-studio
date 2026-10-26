"""Research map time travel (#46, app/rmap/history.py): replay from the change log, weekly frames, diffs,
the all-projects map and the changes since the last advisor packet."""
from __future__ import annotations

import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import config, line_chat
from app.rmap import clock, history, ops, project

TODAY = date.today()


def day(off: int) -> str:
    return (TODAY + timedelta(days=off)).isoformat()


@pytest.fixture
def backdate(monkeypatch):
    """Write as if it were `off` days ago at 10:00: the log's times and its clock stamps (forward in time only)."""
    def at(off: int, hour: int = 10) -> None:
        when = datetime.combine(TODAY + timedelta(days=off), datetime.min.time()).replace(hour=hour)
        monkeypatch.setattr(ops, "now", lambda: when.isoformat())
        monkeypatch.setattr(clock, "wall_ms", lambda: int(when.timestamp() * 1000))
    return at


@pytest.fixture
def atlas(backdate) -> dict[str, str]:
    """A map grown over three weeks: a question and two paths, an experiment validated, a path dropped and
    then deleted, a node renamed."""
    backdate(-21, 9)
    slug = ops.create_project("Project Atlas", line="Project A")["slug"]
    backdate(-21)
    r = ops.apply(slug, [
        {"op": "node.add", "ref": "$q", "set": {"type": "question", "title": "Can citations be checked?", "status": "active"}},
        {"op": "node.add", "ref": "$h1", "parent": "$q", "set": {"type": "hypothesis", "title": "Cheap verifier", "status": "active"}},
        {"op": "node.add", "ref": "$h2", "parent": "$q", "set": {"type": "hypothesis", "title": "RL reward", "status": "todo"}},
    ], summary="kick-off")
    refs = dict(r["refs"])
    backdate(-14)
    r = ops.apply(slug, [
        {"op": "node.add", "ref": "$x", "parent": refs["$h1"], "set": {"type": "experiment", "title": "Annotate 300", "status": "active"}},
        {"op": "node.update", "id": refs["$h2"], "set": {"status": "dropped"}},
        {"op": "attach.add", "kind": "evidence", "node": refs["$h1"], "set": {"type": "paper", "target": "Self-RAG 2023"}},
    ], summary="drop RL")
    refs.update(r["refs"])
    backdate(-7)
    r = ops.apply(slug, [
        {"op": "node.update", "id": refs["$x"], "set": {"status": "verified", "title": "Annotate 300 answers"}},
        {"op": "node.add", "ref": "$d", "parent": refs["$h1"], "set": {"type": "decision", "title": "Keep the verifier", "status": "verified"}},
    ], summary="annotation done")
    refs.update(r["refs"])
    backdate(-2)
    ops.apply(slug, [{"op": "node.delete", "id": refs["$h2"]}])
    return {"slug": slug, **refs}


def titles(m: dict) -> dict[str, dict]:
    return {n["title"]: n for n in m["nodes"]}


# ---------- replay ----------


def test_the_map_at_a_date_has_the_nodes_statuses_and_titles_of_then(atlas):
    slug = atlas["slug"]
    assert history.map_at(slug, day(-22))["nodes"] == []
    w1 = titles(history.map_at(slug, day(-21)))
    assert set(w1) == {"Can citations be checked?", "Cheap verifier", "RL reward"}
    assert w1["RL reward"]["status"] == "todo"
    m2 = history.map_at(slug, day(-10))
    w2 = titles(m2)
    assert w2["RL reward"]["status"] == "dropped" and w2["Annotate 300"]["status"] == "active"
    assert [a["target"] for a in m2["attachments"]["evidence"]] == ["Self-RAG 2023"]
    w3 = titles(history.map_at(slug, day(-5)))
    assert "Annotate 300" not in w3 and w3["Annotate 300 answers"]["status"] == "verified"
    assert "RL reward" in w3 and "Keep the verifier" in w3
    now = history.map_at(slug)
    assert "RL reward" not in titles(now)  # deleted two days ago, with its edge
    assert {(e["from"], e["to"]) for e in now["edges"]} == {(atlas["$q"], atlas["$h1"]), (atlas["$h1"], atlas["$x"]), (atlas["$h1"], atlas["$d"])}
    live = project.get(slug).read()
    assert {n["id"]: n["status"] for n in now["nodes"]} == {n["id"]: n["status"] for n in live["nodes"]}
    assert now["project"]["title"] == "Project Atlas"


def test_a_date_counts_to_its_end_and_a_cursor_to_its_event(atlas):
    slug = atlas["slug"]
    assert len(history.map_at(slug, day(-21))["nodes"]) == 3  # the day's changes are in
    evs = project.get(slug).events(limit=1000)["events"]
    first_node = next(e for e in reversed(evs) if e["kind"] == "node")
    assert len(history.map_at(slug, first_node["cursor"])["nodes"]) == 1
    assert len(history.map_at(slug, f"{day(-21)}T09:30")["nodes"]) == 0
    with pytest.raises(ValueError):
        history.map_at(slug, "last tuesday")


def test_an_edit_from_the_other_mac_after_a_delete_brings_the_node_and_its_edges_back(atlas):
    slug = atlas["slug"]
    p = project.get(slug)
    [delete] = [e for e in p.events(limit=1000)["events"] if e["kind"] == "node" and e["action"] == "delete"]
    later = clock.make(clock.parse(delete["hlc"])[0] + 60_000, 0, "othermac")
    with p.event_store(delete["at"][:7]).editing() as data:  # the other Mac renamed it, not having seen the delete
        data["events"].append({"id": "evother000001", "hlc": later, "at": delete["at"], "who": "user", "device": "othermac",
                               "kind": "node", "action": "update", "target": atlas["$h2"], "nodes": [atlas["$h2"]],
                               "changes": {"title": ["RL reward", "RL reward, again"]}})
    now = titles(history.map_at(slug))
    assert now["RL reward, again"]["status"] == "dropped"
    assert any(e["to"] == atlas["$h2"] for e in history.map_at(slug)["edges"])  # the cascaded edge followed it back
    before = clock.make(clock.parse(delete["hlc"])[0] - 1, 0, "othermac")  # an edit stamped before the delete loses
    with p.event_store(delete["at"][:7]).editing() as data:
        data["events"][-1]["hlc"] = before
    assert "RL reward, again" not in titles(history.map_at(slug))


def test_records_the_log_does_not_know_appear_at_their_creation(atlas):
    slug = atlas["slug"]
    path = config.RMAP_DIR / slug / "map.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    created = f"{day(-30)}T08:00:00"
    data["nodes"].append({"id": "nhand0001", "type": "open", "title": "Written by hand", "status": "todo",
                          "created_at": created, "updated_at": created})
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert "Written by hand" not in titles(history.map_at(slug, day(-31)))
    assert "Written by hand" in titles(history.map_at(slug, day(-30)))
    assert "Written by hand" in titles(history.map_at(slug))


# ---------- the time scrubber ----------


def materialize(fr: dict, wi: int) -> dict:
    """What web/rmap/ext/history.js does: each record's last state up to the week."""
    def last(states):
        hit = [s for w, s in states if w <= wi]
        return hit[-1] if hit else None
    return {"nodes": [r for r in (last(s) for s in fr["nodes"].values()) if r],
            "edges": [r for r in (last(s) for s in fr["edges"].values()) if r]}


def test_frames_rebuild_each_week(atlas):
    fr = history.frames(atlas["slug"])
    assert [w["label"] for w in fr["weeks"]][0] == "W1" and len(fr["weeks"]) in (4, 5)
    assert fr["weeks"][-1]["end"] >= TODAY.isoformat() and fr["weeks"][0]["start"] <= day(-21)
    counts = [len(materialize(fr, w["i"])["nodes"]) for w in fr["weeks"]]
    assert counts[-1] == len(project.get(atlas["slug"]).read()["nodes"]) == 4
    assert counts[0] == 3
    week = lambda d: next(w["i"] for w in fr["weeks"] if w["start"] <= d <= w["end"])  # noqa: E731
    then = {n["title"]: n for n in materialize(fr, week(day(-14)))["nodes"]}
    assert then["RL reward"]["status"] == "dropped" and then["Annotate 300"]["status"] == "active"
    assert "Keep the verifier" in {n["title"] for n in materialize(fr, week(day(-7)))["nodes"]}
    assert "RL reward" not in {n["title"] for n in materialize(fr, fr["weeks"][-1]["i"])["nodes"]}
    assert sum(w["events"] for w in fr["weeks"]) == len(project.get(atlas["slug"]).events(limit=1000)["events"])
    assert any("kick-off" in w["notes"] for w in fr["weeks"])
    assert fr["weeks"][-1]["nodes"] == 4


def test_weeks_are_capped_and_start_on_monday():
    first = int(datetime(2020, 1, 1).timestamp() * 1000)
    ws = history.weeks(first, date(2026, 10, 28))
    assert len(ws) == history.MAX_WEEKS and ws[-1]["start"] == "2026-10-26" and ws[-1]["end"] == "2026-11-01"
    assert all(date.fromisoformat(w["start"]).weekday() == 0 for w in ws)
    assert history.weeks(0, date(2026, 10, 28))[0]["label"] == "W1"


# ---------- change view ----------


def test_diff_marks_added_dropped_removed_and_status_changes(atlas):
    d = history.diff(atlas["slug"], day(-14), day(-7))
    c = d["changes"]
    assert {r["title"] for r in c["added"]} == {"Annotate 300 answers", "Keep the verifier"}
    assert [(r["title"], r["from"], r["to"]) for r in c["dropped"]] == [("RL reward", "todo", "dropped")]
    assert c["removed"] == [] and c["status"] == []
    assert c["attachments"]["evidence"]["added"][0]["what"] == "Self-RAG 2023"
    assert d["marks"][atlas["$x"]] == {"mark": "added"} and d["marks"][atlas["$h2"]]["mark"] == "dropped"
    assert [n["text"] for n in c["notes"]] == ["drop RL", "annotation done"]

    d = history.diff(atlas["slug"], day(-7), None)  # from the start of that day to now
    c = d["changes"]
    assert [r["title"] for r in c["removed"]] == ["RL reward"]
    assert [(r["title"], r["from"], r["to"]) for r in c["status"]] == [("Annotate 300 answers", "active", "verified")]
    assert c["retitled"][0]["from"] == "Annotate 300"
    ghost = next(n for n in d["map"]["nodes"] if n["id"] == atlas["$h2"])
    assert ghost["gone"] is True and d["marks"][atlas["$h2"]] == {"mark": "removed"}
    assert any(e.get("gone") and e["to"] == atlas["$h2"] for e in d["map"]["edges"])
    assert d["total"] == len(d["events"]) and all(e["at"] >= day(-7) for e in d["events"])
    assert d["events"][0]["kind"] == "node" and d["events"][0]["action"] == "delete"  # newest first


def test_diff_with_dates_in_either_order_and_from_the_beginning(atlas):
    a = history.diff(atlas["slug"], day(-7), day(-14))
    b = history.diff(atlas["slug"], day(-14), day(-7))
    assert a["changes"] == b["changes"]
    whole = history.diff(atlas["slug"])
    assert {r["title"] for r in whole["changes"]["added"]} == {n["title"] for n in project.get(atlas["slug"]).read()["nodes"]}


# ---------- all projects ----------


def test_overview_has_a_line_per_project_with_stations_and_interchanges(atlas, backdate):
    slug = atlas["slug"]
    backdate(-1)
    ops.apply(slug, [{"op": "node.update", "id": atlas["$x"], "set": {"entity": "dataset:msmarco"}}])
    other = ops.create_project("Project Beacon")["slug"]
    ops.apply(other, [{"op": "node.add", "ref": "$q", "set": {"type": "question", "title": "Long context"}},
                      {"op": "node.add", "parent": "$q", "set": {"type": "experiment", "title": "Needle test", "entity": "dataset:msmarco",
                                                                  "status": "verified"}}])
    ops.create_project("Empty one")
    ov = history.overview()
    lines = {ln["slug"]: ln for ln in ov["lines"]}
    assert set(lines) >= {slug, other}
    st = lines[slug]["stations"]
    assert [s["day"] for s in st] == [day(-14), day(-7)]
    assert st[0]["kind"] == "decision" and st[0]["items"][0]["what"] == "dropped"
    assert st[1]["kind"] == "decision" and {x["what"] for x in st[1]["items"]} == {"verified", "decision"}
    assert lines[slug]["start"] < lines[other]["start"] <= ov["now"]
    [ic] = ov["interchanges"]
    assert ic["entity"] == "dataset:msmarco" and {p["slug"] for p in ic["projects"]} == {slug, other}
    mine = next(p for p in ic["projects"] if p["slug"] == slug)
    assert mine["node"] == atlas["$x"] and mine["title"] == "Annotate 300 answers"
    assert ic["ms"] == max(p["ms"] for p in ic["projects"])
    assert ov["weeks"][0]["start"] <= day(-21)


# ---------- advisor packets ----------


def test_the_advisor_packet_prompt_lists_map_changes_since_the_last_packet(atlas, write_vault_file):
    write_vault_file(f"08 Review Packets/Advisor Packet - Project A {day(-7)}.md",
                     f"---\ntype: review-packet\npacket: advisor\nline: Project A\ncreated: {day(-7)}\n---\n")
    out = line_chat.packet_prompt({"name": "Project A", "line": "Project A"})
    assert out["map"]["slug"] == atlas["slug"] and out["map"]["since"] == day(-7)
    prompt = out["prompt"]
    assert "研究地图「Project Atlas」自上次汇报" in prompt
    assert "「Annotate 300 answers」进行中 → 已验证" in prompt and "删除「RL reward」" in prompt
    assert "drop RL" not in prompt and "annotation done" in prompt  # only what came after the packet's day began
    assert history.last_packet(atlas["slug"])["date"] == day(-7)


def test_no_map_leaves_the_packet_prompt_alone():
    out = line_chat.packet_prompt({"name": "Project Zed", "line": "Project Zed"})
    assert out["map"] is None and "研究地图" not in out["prompt"]


# ---------- routes ----------


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


def test_routes(atlas, client):
    base = f"/api/rmap/projects/{atlas['slug']}"
    r = client.get(f"{base}/history")
    assert r.status_code == 200 and r.json()["weeks"][0]["label"] == "W1" and atlas["$q"] in r.json()["nodes"]
    r = client.get(f"{base}/history/at", params={"at": day(-21)})
    assert r.status_code == 200 and len(r.json()["nodes"]) == 3
    r = client.get(f"{base}/history/diff", params={"since": day(-14), "until": day(-7)})
    assert r.status_code == 200 and len(r.json()["changes"]["added"]) == 2
    assert client.get(f"{base}/history/diff", params={"since": "soon"}).status_code == 400
    assert client.get("/api/rmap/projects/nope-0000/history").status_code == 404
    assert client.get(f"{base}/history/packet").json() == {"packet": None}
    r = client.get("/api/rmap/overview")
    assert r.status_code == 200 and r.json()["lines"][0]["slug"] == atlas["slug"]


# ---------- web/rmap/timeline.js in a JavaScript engine ----------

JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")
PROJECT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("engine", ["node", "jsc"])
def test_timeline_helpers_in_a_javascript_engine(engine, tmp_path):
    exe = shutil.which("node") if engine == "node" else (str(JSC) if JSC.exists() else None)
    if not exe:
        pytest.skip(f"{engine} is not installed")
    shutil.copy(PROJECT / "web" / "rmap" / "timeline.js", tmp_path / "timeline.mjs")
    shutil.copy(PROJECT / "tests" / "js" / "rmap_history.test.mjs", tmp_path / "test.mjs")
    args = [exe, "-m", "test.mjs"] if engine == "jsc" else [exe, "test.mjs"]
    r = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr


@pytest.mark.parametrize("engine", ["node", "jsc"])
def test_project_views_in_the_registry_in_a_javascript_engine(engine, tmp_path):
    exe = shutil.which("node") if engine == "node" else (str(JSC) if JSC.exists() else None)
    if not exe:
        pytest.skip(f"{engine} is not installed")
    web = PROJECT / "web"
    (tmp_path / "registry.mjs").write_text((web / "rmap" / "registry.js").read_text(encoding="utf-8")
                                           .replace('from "/rmap/strings.js"', 'from "./strings.mjs"'), encoding="utf-8")
    (tmp_path / "strings.mjs").write_text((web / "rmap" / "strings.js").read_text(encoding="utf-8")
                                          .replace('from "/i18n.js"', 'from "./i18n.mjs"'), encoding="utf-8")
    shutil.copy(web / "i18n.js", tmp_path / "i18n.mjs")
    shutil.copy(PROJECT / "tests" / "js" / "rmap_registry.test.mjs", tmp_path / "test.mjs")
    args = [exe, "-m", "test.mjs"] if engine == "jsc" else [exe, "test.mjs"]
    r = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr

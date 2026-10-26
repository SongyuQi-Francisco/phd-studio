"""app/plan.py: watched venues, targets, milestones, ideas and the combined timeline()."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml

from app import config, plan, venues


def in_days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def plan_file() -> dict:
    return yaml.safe_load(config.PLAN_FILE.read_text(encoding="utf-8"))


# ---------- milestones ----------


def test_add_milestone_stores_a_record_in_the_vault_plan_file():
    m = plan.add_milestone("Draft experiments", "2026-11-15", line="Project A", kind="gate", notes="n")
    assert m["id"].startswith("m")
    assert (m["title"], m["date"], m["line"], m["kind"], m["status"], m["notes"]) == \
        ("Draft experiments", "2026-11-15", "Project A", "gate", "todo", "n")
    assert plan_file()["milestones"][0]["id"] == m["id"]
    assert config.PLAN_FILE.read_text(encoding="utf-8").startswith("# Planning data, managed by PhD Studio")


def test_add_milestone_defaults_and_optional_fields_are_dropped():
    m = plan.add_milestone("Plain", "2026-11-15")
    assert m["kind"] == "milestone" and m["status"] == "todo"
    assert "line" not in m and "notes" not in m


@pytest.mark.parametrize("kwargs", [
    {"date_": "2026-13-01"}, {"date_": "tomorrow"}, {"date_": "15/11/2026"}, {"date_": "2026-11-15", "kind": "party"},
    {"date_": "2026-11-15", "status": "finished"},
])
def test_add_milestone_validates_input_and_stores_nothing(kwargs):
    with pytest.raises(ValueError):
        plan.add_milestone("Bad", **kwargs)
    assert plan.plan_store.load()["milestones"] == []


def test_kinds_and_statuses_are_the_documented_ones():
    assert plan.KINDS == ("milestone", "gate", "kill", "deadline")
    assert plan.M_STATUS == ("todo", "doing", "done", "warn")


def test_update_milestone_validates_and_bumps_updated_at():
    m = plan.add_milestone("M", "2026-11-15")
    out = plan.update("milestones", m["id"], {"status": "done", "date": "2026-11-20"})
    assert out["status"] == "done" and out["date"] == "2026-11-20"
    for bad in ({"status": "nope"}, {"kind": "nope"}, {"date": "soon"}):
        with pytest.raises(ValueError):
            plan.update("milestones", m["id"], bad)
    assert plan.plan_store.load()["milestones"][0]["status"] == "done"


def test_update_unknown_record_is_a_keyerror():
    with pytest.raises(KeyError):
        plan.update("milestones", "m000000", {"status": "done"})


def test_delete_milestone_is_a_tombstone_and_leaves_the_timeline():
    m = plan.add_milestone("Gone", in_days(5))
    keep = plan.add_milestone("Stay", in_days(6))
    plan.delete("milestones", m["id"])
    assert [x["id"] for x in plan.plan_store.load()["milestones"]] == [keep["id"]]
    assert [e["title"] for e in plan.timeline()["events"] if e["type"] == "milestone"] == ["Stay"]
    assert any(r.get("deleted") for r in plan_file()["milestones"])


# ---------- watched venues ----------


def test_first_use_watches_the_profile_venues_that_exist_in_the_catalog(venue_data):
    assert sorted(plan.watched_keys()) == ["acl", "kdd"]  # NoSuchVenue is skipped
    assert sorted(plan.watched_keys()) == ["acl", "kdd"]  # and not added twice
    assert len(plan_file()["watched"]) == 2


def test_watch_and_unwatch(venue_data):
    plan.watched_keys()
    assert plan.watch("CIKM") == {"venue": "cikm", "watched": True}
    plan.watch("cikm")
    assert sorted(plan.watched_keys()) == ["acl", "cikm", "kdd"]
    assert plan.watch("CIKM", on=False) == {"venue": "cikm", "watched": False}
    assert sorted(plan.watched_keys()) == ["acl", "kdd"]
    plan.watch("CIKM", on=False)  # already off: no error
    plan.watch("CIKM")            # back on
    assert "cikm" in plan.watched_keys()


def test_watch_unknown_venue_is_an_error(venue_data):
    with pytest.raises(ValueError, match="unknown venue"):
        plan.watch("Nope")


def test_unwatching_every_venue_sticks(venue_data):
    for k in plan.watched_keys():
        plan.watch(k, on=False)
    assert plan.watched_keys() == []  # must not fall back to the profile list again


# ---------- targets ----------


def test_add_target_normalizes_venues_and_watches_them(venue_data):
    t = plan.add_target("Right Rec, Wrong Memory", "KDD", line="Project A", alternatives=["CIKM", "Unknown Conf"], notes="n")
    assert t["id"].startswith("t")
    assert (t["venue"], t["status"], t["line"]) == ("kdd", "planning", "Project A")
    assert t["alternatives"] == ["cikm", "unknownconf"]
    watched = set(plan.watched_keys())
    assert {"kdd", "cikm"} <= watched and "unknownconf" not in watched


def test_add_target_rejects_unknown_venue_and_bad_status(venue_data):
    with pytest.raises(ValueError, match="unknown venue"):
        plan.add_target("T", "Nope")
    with pytest.raises(ValueError, match="status"):
        plan.add_target("T", "KDD", status="winning")
    assert plan.plan_store.load()["targets"] == []


def test_update_target_normalizes_the_venue_and_checks_status(venue_data):
    t = plan.add_target("T", "KDD")
    out = plan.update("targets", t["id"], {"venue": "CIKM", "status": "writing"})
    assert (out["venue"], out["status"]) == ("cikm", "writing")
    with pytest.raises(ValueError):
        plan.update("targets", t["id"], {"status": "winning"})


# ---------- timeline ----------


def events(tl: dict, type_: str) -> list[dict]:
    return [e for e in tl["events"] if e["type"] == type_]


def test_timeline_lists_watched_venue_deadlines_as_utc_events(venue_data):
    plan.watched_keys()
    plan.watch("CIKM")
    tl = plan.timeline()
    assert sorted(tl["watched"]) == ["acl", "cikm", "kdd"]
    by_title = {e["title"]: e for e in tl["events"] if e["type"] != "milestone"}
    year = venue_data.kdd_paper.year + 1
    kdd_abs = by_title[f"KDD {year} Abstract deadline"]
    assert kdd_abs["at"] == venue_data.kdd_abstract.isoformat()
    assert kdd_abs["venue"] == "kdd" and kdd_abs["acronym"] == "KDD"
    assert (kdd_abs["core"], kdd_abs["ccf"]) == ("A*", "A")
    assert kdd_abs["days"] in (19, 20)
    assert kdd_abs["link"] == "https://kdd.example/next" and kdd_abs["comment"] == "Research Track"
    assert by_title[f"KDD {year} Full paper deadline"]["at"] == venue_data.kdd_paper.isoformat()
    assert by_title[f"CIKM {venue_data.cikm_paper.year} Full paper deadline"]["days"] in (89, 90)


def test_timeline_skips_tbd_and_past_beyond_the_window(venue_data):
    plan.watched_keys()
    tl = plan.timeline()
    assert all(e["venue"] != "acl" for e in tl["events"] if e["type"] != "milestone")
    assert all(e["days"] >= -14 for e in tl["events"])
    wide = plan.timeline(days_back=300)
    assert any(e["venue"] == "acl" and e["days"] < -150 for e in wide["events"] if e["type"] != "milestone")
    assert any(e["venue"] == "kdd" and e["days"] < -150 for e in wide["events"] if e["type"] != "milestone")


def test_timeline_days_ahead_cuts_off_far_deadlines(venue_data):
    plan.watched_keys()
    plan.watch("CIKM")
    near = plan.timeline(days_ahead=30)
    assert [e["venue"] for e in near["events"]] == ["kdd", "kdd"]
    assert {e["venue"] for e in plan.timeline(days_ahead=400)["events"]} == {"kdd", "cikm"}


def test_timeline_unwatched_venues_are_left_out(venue_data):
    plan.watched_keys()
    plan.watch("KDD", on=False)
    assert not [e for e in plan.timeline()["events"] if e.get("venue") == "kdd"]


def test_timeline_marks_events_of_a_target_venue(venue_data):
    t = plan.add_target("Right Rec", "KDD", line="Project A", alternatives=["CIKM"])
    tl = plan.timeline()
    kdd = [e for e in tl["events"] if e.get("venue") == "kdd"]
    assert kdd and all(e["type"] == "target" for e in kdd)
    assert kdd[0]["target"] == {"id": t["id"], "title": "Right Rec", "line": "Project A"}
    cikm = [e for e in tl["events"] if e.get("venue") == "cikm"]
    assert cikm and all(e["type"] == "venue" and e["target"] is None for e in cikm)  # alternatives are only watched
    assert [x["id"] for x in tl["targets"]] == [t["id"]]


@pytest.mark.parametrize("status", ["dropped", "rejected"])
def test_dropped_or_rejected_targets_stop_marking_their_venue(venue_data, status):
    t = plan.add_target("Right Rec", "KDD")
    plan.update("targets", t["id"], {"status": status})
    tl = plan.timeline()
    assert tl["targets"] == []
    assert {e["type"] for e in tl["events"] if e.get("venue") == "kdd"} == {"venue"}


@pytest.mark.parametrize("status", ["planning", "writing", "submitted", "accepted"])
def test_live_targets_keep_marking_their_venue(venue_data, status):
    t = plan.add_target("Right Rec", "KDD", status=status)
    assert plan.timeline()["targets"][0]["id"] == t["id"]


def test_timeline_milestones_become_end_of_day_events(venue_data):
    m = plan.add_milestone("Draft experiments", in_days(10), line="Project A", kind="gate", notes="n")
    [e] = events(plan.timeline(), "milestone")
    assert (e["id"], e["title"], e["kind"], e["status"], e["line"], e["notes"]) == \
        (m["id"], "Draft experiments", "gate", "todo", "Project A", "n")
    assert abs(e["days"] - 10) <= 1
    local_end = datetime.fromisoformat(in_days(10) + "T23:59:00").astimezone(timezone.utc)
    assert datetime.fromisoformat(e["at"]) == local_end


def test_timeline_keeps_open_milestones_however_far_and_hides_old_done_ones(venue_data):
    plan.add_milestone("overdue", "2020-01-01")
    plan.add_milestone("far away", "2099-01-01", status="doing")
    plan.add_milestone("old and done", "2020-01-01", status="done")
    plan.add_milestone("far and done", "2099-01-01", status="done")
    plan.add_milestone("recent and done", in_days(-3), status="done")
    plan.add_milestone("soon", in_days(3))
    titles = [e["title"] for e in events(plan.timeline(), "milestone")]
    assert titles == ["overdue", "recent and done", "soon", "far away"]  # sorted by date


def test_timeline_events_are_sorted_by_time_across_types(venue_data):
    plan.add_target("T", "KDD")
    plan.add_milestone("between", (venue_data.kdd_abstract + timedelta(days=3)).astimezone().date().isoformat())
    plan.add_milestone("first", in_days(2))
    ats = [e["at"] for e in plan.timeline()["events"]]
    assert ats == sorted(ats)
    assert [e["title"] for e in plan.timeline()["events"]][0] == "first"


def test_timeline_returns_all_live_milestones_even_when_not_listed_as_events(venue_data):
    plan.add_milestone("old and done", "2020-01-01", status="done")
    tl = plan.timeline()
    assert [m["title"] for m in tl["milestones"]] == ["old and done"]
    assert events(tl, "milestone") == []


def test_timeline_on_an_empty_plan_is_empty(venue_data):
    tl = plan.timeline()
    assert tl["targets"] == [] and tl["milestones"] == []
    assert sorted(tl["watched"]) == ["acl", "kdd"]  # seeded from the profile


def test_official_deadline_appears_in_the_timeline_with_a_marked_title(venue_data, tmp_path, monkeypatch):
    details = tmp_path / "d.json"
    far = (date.today() + timedelta(days=60)).isoformat()
    details.write_text(json.dumps({"venues": [{
        "key": "ACL", "status": "confirmed", "edition": "ACL 2099", "cfp_url": "https://cfp.example",
        "deadlines": [{"track": "Main", "abstract": None, "paper": f"{far} AoE"}]}]}), encoding="utf-8")
    monkeypatch.setattr(venues, "DETAILS_FILE", details)
    venues._catalog.cache_clear()
    plan.watched_keys()
    [e] = [e for e in plan.timeline()["events"] if e.get("venue") == "acl"]
    assert e["title"].startswith("ACL ") and "Main" in e["title"] and e["title"].endswith("Full paper deadline")
    assert "官网" not in e["title"]
    assert e["comment"] == "Main（官网）"


# ---------- ideas ----------


def test_add_idea_trims_and_defaults():
    i = plan.add_idea("  use X as a verifier for Y  ", line="Project A", source="claude")
    assert i["id"].startswith("i")
    assert (i["text"], i["line"], i["status"], i["source"]) == ("use X as a verifier for Y", "Project A", "new", "claude")
    assert "tags" not in i and "paper" not in i
    assert yaml.safe_load(config.IDEAS_FILE.read_text(encoding="utf-8"))["ideas"][0]["id"] == i["id"]
    assert config.IDEAS_FILE.read_text(encoding="utf-8").startswith("# Idea inbox")


def test_add_idea_keeps_paper_page_and_tags():
    i = plan.add_idea("check eq. 3", paper="Alpha", page=4, tags=["metric", "bug"])
    assert (i["paper"], i["page"], i["tags"]) == ("Alpha", 4, ["metric", "bug"])


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_add_idea_refuses_empty_text(text):
    with pytest.raises(ValueError, match="empty"):
        plan.add_idea(text)
    assert plan.ideas() == []


def test_ideas_are_listed_newest_first_and_skip_deleted():
    a = plan.add_idea("first")
    b = plan.add_idea("second")
    c = plan.add_idea("third")
    with plan.idea_store.editing() as data:
        for rec, ts in ((a, "2026-10-01T10:00:00"), (b, "2026-10-02T10:00:00"), (c, "2026-10-03T10:00:00")):
            next(r for r in data["ideas"] if r["id"] == rec["id"])["created_at"] = ts
    plan.idea_store.delete("ideas", b["id"])
    assert [i["text"] for i in plan.ideas()] == ["third", "first"]


def test_idea_status_update_goes_through_the_store():
    i = plan.add_idea("keep me")
    out = plan.idea_store.update("ideas", i["id"], {"status": "kept"})
    assert out["status"] == "kept"
    assert plan.ideas()[0]["status"] == "kept"
    assert plan.I_STATUS == ("new", "kept", "promoted", "dropped")

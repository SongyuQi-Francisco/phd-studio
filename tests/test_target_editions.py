"""Submission targets aim at an edition (year) and optionally one round of it: several targets per venue,
the timeline's events, reminders muted per paper and edition, and older targets (saved without an edition)
read as the nearest upcoming one or the one their status implies, without being rewritten."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import yaml

from app import config, editions, plan, reminders, studio_tools, venues

NOW = datetime.now(timezone.utc)
FAR = (NOW + timedelta(days=300)).date().isoformat()  # near no round of the venues below


def iso(days: float) -> str:
    return (NOW + timedelta(days=days)).isoformat()


def wall(days: float) -> str:
    return (NOW + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def local_stamp(days: float) -> str:
    """A saved timestamp the way Store writes it: naive local time."""
    return (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")


def rnd(paper: float | None, abstract: float | None = None, comment: str | None = None) -> dict[str, Any]:
    return {"abstract": iso(abstract) if abstract is not None else None, "deadline": iso(paper) if paper is not None else None,
            "comment": comment}


def edition(year: int, *rounds: dict[str, Any]) -> dict[str, Any]:
    return {"year": year, "link": f"https://zz.example/{year}", "date": "Aug", "place": "Somewhere", "timezone": "UTC",
            "timeline": list(rounds)}


def venue_entry(*eds: dict[str, Any]) -> dict[str, Any]:
    return {"key": "zz", "acronym": "ZZ", "editions": list(eds)}


def add_venue(title: str, *confs: tuple[int, list[tuple[float | None, float | None, str | None]]]) -> None:
    """A ccfddl venue on disk (UTC deadlines, `days` from now); each conf is (year, [(abstract, paper, comment)])."""
    folder = venues.CCFDDL_DIR / "conference" / "AI"
    folder.mkdir(parents=True, exist_ok=True)
    body = [{"title": title, "description": f"{title} test conference", "sub": "AI", "rank": {"ccf": "A", "core": "A"},
             "confs": [{"year": year, "link": f"https://{title.lower()}.example/{year}", "timezone": "UTC", "date": "Aug", "place": "X",
                        "timeline": [{**({"abstract_deadline": wall(a)} if a is not None else {}),
                                      **({"deadline": wall(p)} if p is not None else {}),
                                      **({"comment": c} if c else {})} for a, p, c in rounds]}
                       for year, rounds in confs]}]
    (folder / f"{title.lower()}.yml").write_text(yaml.safe_dump(body), encoding="utf-8")
    venues._catalog.cache_clear()


def legacy(title: str, venue: str, status: str = "planning", updated_days_ago: float = 0, **extra: Any) -> dict[str, Any]:
    """A target saved before editions existed: no `edition`, and the last change `updated_days_ago` days ago."""
    rec = plan.plan_store.add("targets", {"title": title, "venue": venue, "status": status, **extra}, "t")
    with plan.plan_store.editing() as data:
        next(r for r in data["targets"] if r["id"] == rec["id"])["updated_at"] = local_stamp(-updated_days_ago)
    return rec


def stored(rid: str) -> dict[str, Any]:
    return next(t for t in plan.plan_store.load()["targets"] if t["id"] == rid)


def events_of(venue: str, tl: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [e for e in (tl or plan.timeline())["events"] if e.get("venue") == venue]


def titles(rows: list[dict[str, Any]]) -> list[str]:
    return [r["title"] for r in rows]


# ---------- editions.py: working out an edition ----------


def test_a_target_being_written_aims_at_the_nearest_upcoming_edition() -> None:
    e = venue_entry(edition(2026, rnd(-100)), edition(2027, rnd(40)), edition(2028, rnd(400)))
    assert editions.resolve({"status": "planning"}, e) == (2027, "nearest")
    assert editions.resolve({"status": "writing"}, venue_entry(edition(2026, rnd(-100)))) == (None, None)  # nothing announced yet
    assert editions.resolve({"status": "writing"}, None) == (None, None)


def test_a_stored_edition_wins_and_a_hand_edited_one_reads_as_not_set() -> None:
    e = venue_entry(edition(2027, rnd(40)), edition(2028, rnd(400)))
    assert editions.resolve({"status": "planning", "edition": 2028}, e) == (2028, "set")
    assert editions.resolve({"status": "planning", "edition": "2028"}, e) == (2028, "set")
    assert editions.resolve({"status": "planning", "edition": "soon"}, e) == (2027, "nearest")
    assert editions.resolve({"status": "planning", "edition": 3000}, e) == (2027, "nearest")
    assert editions.resolve({"status": "planning", "edition": 2028}, None) == (2028, "set")  # a venue the catalog lost


def test_a_sent_target_aims_at_the_edition_it_was_sent_to() -> None:
    e = venue_entry(edition(2026, rnd(-100)), edition(2027, rnd(40)), edition(2028, rnd(400)))
    sent = lambda days_ago: editions.resolve({"status": "submitted", "updated_at": local_stamp(-days_ago)}, e)  # noqa: E731
    assert sent(90) == (2026, "status")   # marked long after the 2026 deadline, long before the 2027 one
    assert sent(2) == (2027, "status")    # marked days before the 2027 deadline: an early submission
    assert editions.resolve({"status": "accepted", "created_at": local_stamp(-60)}, e) == (2026, "status")
    only_ahead = venue_entry(edition(2027, rnd(40)))
    assert editions.resolve({"status": "submitted", "updated_at": local_stamp(-90)}, only_ahead) == (2027, "status")


def test_a_deadline_names_the_round_by_date_and_follows_a_small_move() -> None:
    tl = [rnd(30, 23), rnd(120, 110)]
    key = lambda days: (NOW + timedelta(days=days)).date().isoformat()  # noqa: E731
    assert editions.match_round(tl, key(30)) == 0 and editions.match_round(tl, key(120)) == 1
    assert editions.match_round(tl, key(23)) == 0 and editions.match_round(tl, key(110)) == 1   # by its abstract date too
    assert editions.match_round(tl, key(128)) == 1     # extended by a week: still that round
    assert editions.match_round(tl, key(70)) is None   # near none
    assert editions.match_round(tl, None) is None and editions.match_round(tl, "soon") is None
    assert editions.match_round([rnd(None)], key(30)) is None  # a TBD round has no date to match


def test_covered_rounds_state_and_options() -> None:
    e = venue_entry(edition(2026, rnd(-100)), edition(2027, rnd(30, 23, "Cycle 1"), rnd(120, 110, "Cycle 2")), edition(2028, rnd(None)))
    key2 = (NOW + timedelta(days=120)).date().isoformat()
    assert editions.covered(e, 2027, None) == {(1, 0), (1, 1)}
    assert editions.covered(e, 2027, key2) == {(1, 1)}
    assert editions.covered(e, 2027, "2099-01-01") == {(1, 0), (1, 1)}  # names no round any more: the whole edition
    assert editions.covered(e, None, None) == set() and editions.covered(e, 1999, None) == set()
    assert editions.state(e, editions.covered(e, 2027, key2), NOW)["state"] == "upcoming"
    assert editions.state(e, editions.covered(e, 2026, None), NOW) == {"state": "past"}
    assert editions.state(e, editions.covered(e, 2028, None), NOW) == {"state": "tbd"}
    assert editions.state(e, set(), NOW) == {"state": "tbd"}
    opts = editions.options(e, NOW)
    assert [y["year"] for y in opts["editions"]] == [2026, 2027, 2028] and opts["default"] == 2027
    y27 = opts["editions"][1]
    assert [r["comment"] for r in y27["rounds"]] == ["Cycle 1", "Cycle 2"] and y27["past"] is False
    assert opts["editions"][0]["past"] is True and opts["editions"][2]["rounds"] == []  # announced, dates TBD


# ---------- adding and editing a target ----------


def test_a_new_target_is_saved_with_the_nearest_upcoming_edition(venue_data) -> None:
    t = plan.add_target("Right Rec", "KDD")
    assert t["edition"] == venue_data.kdd_paper.year + 1 and "deadline" not in t
    assert stored(t["id"])["edition"] == t["edition"]
    assert yaml.safe_load(config.PLAN_FILE.read_text(encoding="utf-8"))["targets"][0]["edition"] == t["edition"]
    assert "edition" not in plan.add_target("Not announced", "ACL")  # nothing upcoming: it follows the calendar until there is
    sent = plan.add_target("Already out", "KDD", status="submitted")
    assert sent["edition"] == venue_data.kdd_paper.year + 1  # sent now: the edition whose deadline is about to pass


def test_a_target_can_name_its_edition_and_round(venue_data) -> None:
    add_venue("ZZ", (2027, [(20, 30, "Cycle 1"), (110, 120, "Cycle 2")]), (2028, [(None, None, None)]))
    day = lambda n: (NOW + timedelta(days=n)).date().isoformat()  # noqa: E731
    t = plan.add_target("Paper", "ZZ", edition=2027, deadline=day(120))
    assert (t["edition"], t["deadline"]) == (2027, day(120))
    assert plan.add_target("Later", "ZZ", edition=2028)["edition"] == 2028         # announced without dates
    assert plan.add_target("Future", "ZZ", edition=2031, deadline="2031-02-08")["deadline"] == "2031-02-08"  # not in the catalog yet
    assert plan.add_target("By date", "ZZ", deadline=day(30))["edition"] == 2027  # the edition defaults first
    assert plan.add_target("Time too", "ZZ", edition=2027, deadline=day(30) + "T23:59:00+00:00")["deadline"] == day(30)


@pytest.mark.parametrize("kwargs, message", [
    ({"edition": 27}, "year"), ({"edition": "next"}, "year"), ({"edition": 2027, "deadline": "soon"}, "date"),
    ({"edition": 2027, "deadline": FAR}, f"no deadline near {FAR}"),
])
def test_a_bad_edition_or_round_is_refused_and_nothing_is_saved(venue_data, kwargs, message) -> None:
    add_venue("ZZ", (2027, [(20, 30, None)]))
    with pytest.raises(ValueError, match=message):
        plan.add_target("Paper", "ZZ", **kwargs)
    assert plan.plan_store.load()["targets"] == []


def test_the_error_for_a_missing_round_lists_the_rounds(venue_data) -> None:
    add_venue("ZZ", (2027, [(20, 30, None), (110, 120, None)]))
    with pytest.raises(ValueError) as err:
        plan.add_target("Paper", "ZZ", edition=2027, deadline=FAR)
    assert (NOW + timedelta(days=30)).date().isoformat() in str(err.value)
    with pytest.raises(ValueError, match="needs an edition"):
        plan.add_target("Paper", "ACL", deadline=FAR)  # ACL has no edition to default to


def test_changing_the_edition_drops_the_old_round_and_changing_the_venue_starts_over(venue_data) -> None:
    add_venue("ZZ", (2027, [(20, 30, None), (110, 120, None)]), (2028, [(380, 390, None)]))
    key = (NOW + timedelta(days=120)).date().isoformat()
    t = plan.add_target("Paper", "ZZ", edition=2027, deadline=key)
    out = plan.update("targets", t["id"], {"edition": 2028})
    assert out["edition"] == 2028 and "deadline" not in stored(t["id"])
    out = plan.update("targets", t["id"], {"edition": 2027, "deadline": key})
    assert (out["edition"], out["deadline"]) == (2027, key)
    out = plan.update("targets", t["id"], {"venue": "CIKM"})
    assert out["venue"] == "cikm" and out["edition"] == venue_data.cikm_paper.year  # the new venue's nearest
    assert "deadline" not in stored(t["id"])
    with pytest.raises(ValueError, match="unknown venue"):
        plan.update("targets", t["id"], {"venue": "Nope"})
    with pytest.raises(ValueError, match="year"):
        plan.update("targets", t["id"], {"edition": "2027x"})
    with pytest.raises(KeyError):
        plan.update("targets", "tnope", {"edition": 2027})


def test_edition_zero_hands_the_target_back_to_the_calendar(venue_data) -> None:
    t = plan.add_target("Paper", "KDD")
    plan.update("targets", t["id"], {"edition": 0})
    assert "edition" not in stored(t["id"])
    [shown] = plan.targets()
    assert shown["edition"] == venue_data.kdd_paper.year + 1 and shown["edition_from"] == "nearest"


# ---------- migration: older targets are read, not rewritten ----------


def test_older_targets_are_read_with_an_edition_and_nothing_is_written(venue_data) -> None:
    plan.watched_keys()  # seeds the watched list, which is the only write we allow before the checks
    open_ = legacy("Being written", "kdd", "writing")
    old_sent = legacy("Sent long ago", "kdd", "accepted", updated_days_ago=150)
    before = config.PLAN_FILE.read_bytes()
    mtime = config.PLAN_FILE.stat().st_mtime_ns

    tl = plan.timeline()
    by_id = {t["id"]: t for t in tl["targets"]}
    nxt = venue_data.kdd_paper.year + 1
    assert (by_id[open_["id"]]["edition"], by_id[open_["id"]]["edition_from"]) == (nxt, "nearest")
    assert (by_id[old_sent["id"]]["edition"], by_id[old_sent["id"]]["edition_from"]) == (venue_data.past.year, "status")
    assert [t["edition"] for t in plan.targets()] == [nxt, venue_data.past.year]
    assert reminders.events()
    assert config.PLAN_FILE.read_bytes() == before and config.PLAN_FILE.stat().st_mtime_ns == mtime
    assert "edition" not in stored(open_["id"]) and "edition" not in stored(old_sent["id"])


def test_an_older_sent_target_does_not_quiet_the_next_edition(venue_data) -> None:
    legacy("Accepted last year", "kdd", "accepted", updated_days_ago=150)
    evs = events_of("kdd")
    assert evs and all(e["type"] == "venue" and not e["muted"] and e["closed"] == [] for e in evs)
    assert {e["venue"] for e in reminders.events()} == {"kdd"}  # still reminding for the venue


def test_an_older_target_just_marked_sent_quiets_the_edition_it_was_sent_to(venue_data) -> None:
    legacy("Sent early", "kdd", "submitted", updated_days_ago=1)  # the 2nd-next KDD deadlines are 20 and 27 days away
    evs = events_of("kdd")
    assert evs and all(e["muted"] and titles(e["closed"]) == ["Sent early"] for e in evs)
    assert reminders.events() == []


def test_the_first_change_to_an_older_target_saves_the_edition_it_meant(venue_data) -> None:
    open_ = legacy("Being written", "kdd", "writing")
    plan.update("targets", open_["id"], {"notes": "just a note"})
    assert "edition" not in stored(open_["id"])  # a note does not change what the target aims at
    out = plan.update("targets", open_["id"], {"status": "submitted"})
    assert out["edition"] == venue_data.kdd_paper.year + 1 and stored(open_["id"])["edition"] == out["edition"]

    sent = legacy("Sent long ago", "kdd", "submitted", updated_days_ago=150)
    out = plan.update("targets", sent["id"], {"status": "accepted"})  # sent -> accepted keeps the edition it already read as
    assert out["edition"] == venue_data.past.year

    again = legacy("Back to work", "kdd", "rejected", updated_days_ago=150)
    out = plan.update("targets", again["id"], {"status": "writing"})
    assert out["edition"] == venue_data.kdd_paper.year + 1  # a new attempt aims at the next one


def test_marking_a_paper_sent_keeps_the_edition_the_card_showed_even_if_it_is_far_off(venue_data) -> None:
    add_venue("ZZ", (2026, [(-250, -240, None)]), (2027, [(80, 90, None)]))
    t = legacy("Written early", "zz", "planning")  # the card showed 2027, three months ahead
    assert plan.targets()[0]["edition"] == 2027
    out = plan.update("targets", t["id"], {"status": "submitted"})
    assert out["edition"] == 2027  # not 2026, the last edition whose deadline passed
    assert all(e["muted"] for e in events_of("zz") if e["edition"] == 2027)


def test_dropping_an_older_target_does_not_pin_anything(venue_data) -> None:
    t = legacy("Given up", "kdd", "writing")
    plan.update("targets", t["id"], {"status": "dropped"})
    assert "edition" not in stored(t["id"])


# ---------- the timeline: several targets, editions and rounds ----------


def test_several_papers_aimed_at_one_edition_all_own_its_deadlines(venue_data) -> None:
    a = plan.add_target("Paper A", "KDD", line="Project A")
    b = plan.add_target("Paper B", "KDD", line="Project B")
    tl = plan.timeline()
    kdd = events_of("kdd", tl)
    assert kdd and all(e["type"] == "target" for e in kdd)
    assert all(e["targets"] == [{"id": a["id"], "title": "Paper A", "line": "Project A"},
                                {"id": b["id"], "title": "Paper B", "line": "Project B"}] for e in kdd)
    assert kdd[0]["target"] == kdd[0]["targets"][0] and kdd[0]["edition"] == venue_data.kdd_paper.year + 1
    assert [t["id"] for t in tl["targets"]] == [a["id"], b["id"]]
    assert all(t["state"] == "upcoming" and t["next_at"] for t in tl["targets"])


def test_a_target_owns_only_the_deadlines_of_its_edition(venue_data) -> None:
    add_venue("ZZ", (2027, [(20, 30, None)]), (2028, [(380, 390, None)]))
    p27 = plan.add_target("For 2027", "ZZ", edition=2027)
    p28 = plan.add_target("For 2028", "ZZ", edition=2028)
    evs = events_of("zz")
    assert {e["edition"]: [x["id"] for x in e["targets"]] for e in evs} == {2027: [p27["id"]], 2028: [p28["id"]]}
    assert all(e["type"] == "target" for e in evs)
    plan.update("targets", p28["id"], {"edition": 0, "status": "dropped"})
    by_year = {e["edition"]: e for e in events_of("zz")}
    assert by_year[2028]["type"] == "venue" and by_year[2028]["target"] is None and by_year[2027]["type"] == "target"


def test_a_target_naming_a_round_owns_only_that_round(venue_data) -> None:
    add_venue("ZZ", (2027, [(20, 30, "Cycle 1"), (110, 120, "Cycle 2")]))
    t = plan.add_target("Paper", "ZZ", edition=2027, deadline=(NOW + timedelta(days=120)).date().isoformat())
    evs = events_of("zz")
    assert {e["comment"]: e["type"] for e in evs} == {"Cycle 1": "venue", "Cycle 2": "target"}
    assert all(e["targets"][0]["id"] == t["id"] for e in evs if e["type"] == "target")
    # moved by a week: the same round
    key = (NOW + timedelta(days=127)).date().isoformat()
    plan.update("targets", t["id"], {"deadline": key})
    assert {e["comment"]: e["type"] for e in events_of("zz")}["Cycle 2"] == "target"


def test_a_sent_paper_quiets_every_round_of_its_edition_but_stays_on_the_calendar(venue_data) -> None:
    add_venue("ZZ", (2027, [(20, 30, "Cycle 1"), (110, 120, "Cycle 2")]), (2028, [(380, 390, None)]))
    t = plan.add_target("Paper", "ZZ", edition=2027, deadline=(NOW + timedelta(days=30)).date().isoformat())
    plan.update("targets", t["id"], {"status": "submitted"})
    tl = plan.timeline()
    by_ed = {}
    for e in events_of("zz", tl):
        by_ed.setdefault(e["edition"], []).append(e)
    assert len(by_ed[2027]) == 4 and all(e["muted"] and e["type"] == "venue" and e["target"] is None for e in by_ed[2027])
    assert all(e["closed"] == [{"id": t["id"], "title": "Paper", "line": None, "status": "submitted"}] for e in by_ed[2027])
    assert all(not e["muted"] and e["closed"] == [] for e in by_ed[2028])  # next year's edition is not touched
    assert [x["id"] for x in tl["targets"]] == [t["id"]]  # still listed as a target, with its status
    assert plan.timeline()["targets"][0]["edition"] == 2027


def test_another_paper_still_being_written_keeps_its_deadlines(venue_data) -> None:
    add_venue("ZZ", (2027, [(20, 30, None)]))
    sent = plan.add_target("Sent", "ZZ", edition=2027, status="submitted")
    plan.add_target("Planned elsewhere", "KDD")
    mine = plan.add_target("Mine", "ZZ", edition=2027)
    evs = events_of("zz")
    assert all(e["type"] == "target" and titles(e["targets"]) == ["Mine"] and titles(e["closed"]) == ["Sent"] and not e["muted"]
               for e in evs)
    plan.update("targets", mine["id"], {"status": "accepted"})
    assert all(e["muted"] and titles(e["closed"]) == ["Sent", "Mine"] for e in events_of("zz"))
    assert sent["id"] != mine["id"]


def test_rejected_and_dropped_targets_leave_the_calendar_alone(venue_data) -> None:
    add_venue("ZZ", (2027, [(20, 30, None)]))
    for status in ("rejected", "dropped"):
        plan.add_target(status, "ZZ", edition=2027, status=status)
    assert all(e["type"] == "venue" and not e["muted"] and e["closed"] == [] for e in events_of("zz"))
    assert plan.timeline()["targets"] == [] and len(plan.targets(include_ended=True)) == 2


def test_a_target_whose_edition_is_past_or_unannounced_says_so(venue_data) -> None:
    add_venue("ZZ", (2027, [(-20, -10, None)]), (2028, [(None, None, None)]))
    past = plan.add_target("Missed", "ZZ", edition=2027)
    tbd = plan.add_target("Waiting", "ZZ", edition=2028)
    by_id = {t["id"]: t for t in plan.timeline()["targets"]}
    assert by_id[past["id"]]["state"] == "past" and by_id[tbd["id"]]["state"] == "tbd"
    assert "next_at" not in by_id[past["id"]]


def test_the_overview_countdown_is_the_next_deadline_of_a_paper_still_being_written(venue_data) -> None:
    add_venue("ZZ", (2027, [(15, 25, None)]))
    sent = plan.add_target("Sent", "ZZ", edition=2027, status="submitted")
    tl = plan.timeline()
    assert next((e for e in tl["events"] if e["type"] == "target"), None) is None   # nothing left to prepare for
    mine = plan.add_target("Mine", "KDD")
    first = next(e for e in plan.timeline()["events"] if e["type"] == "target")
    assert first["venue"] == "kdd" and first["targets"][0]["id"] == mine["id"] and sent["id"] != mine["id"]


# ---------- reminders ----------


def reminding(now: datetime | None = None) -> list[dict[str, Any]]:
    cfg = {**reminders.DEFAULTS, "enabled": True, "types": dict(reminders.DEFAULTS["types"])}
    return reminders.due(reminders.events(), cfg, now or datetime.now(timezone.utc), set())


def test_submitting_quiets_only_that_edition_for_that_paper(venue_data) -> None:
    add_venue("ZZ", (2027, [(1, 2, None)]), (2028, [(380, 390, None)]))
    plan.watch("ZZ")
    paper = plan.add_target("Paper", "ZZ", edition=2027)
    evs = {e["key"]: e for e in reminders.events() if e["venue"] == "zz"}
    assert sorted(e["type"] for e in evs.values()) == ["target", "target", "venue", "venue"]
    assert {e["key"] for e in reminders.events() if e["venue"] == "zz" and e["type"] == "target"} == \
        {k for k, e in evs.items() if e["at"] < NOW + timedelta(days=10)}

    plan.update("targets", paper["id"], {"status": "submitted"})
    left = [e for e in reminders.events() if e["venue"] == "zz"]
    assert [e["type"] for e in left] == ["venue", "venue"]  # 2028's abstract and paper deadlines, as a watched venue
    assert all("2028" in e["title"] for e in left)
    assert {r["venue"] for r in reminding() if r["venue"] == "zz"} == set()  # 2027's deadline is days away, but quiet


def test_another_paper_for_the_same_edition_keeps_reminding(venue_data) -> None:
    add_venue("ZZ", (2027, [(1, 2, None)]))
    sent = plan.add_target("Sent", "ZZ", edition=2027, status="submitted")
    assert [e for e in reminders.events() if e["venue"] == "zz"] == []
    mine = plan.add_target("Mine", "ZZ", edition=2027)
    mine_events = [e for e in reminders.events() if e["venue"] == "zz"]
    assert mine_events and all(e["type"] == "target" and titles(e["targets"]) == ["Mine"] for e in mine_events)
    assert reminders.message({**mine_events[0], "lead": 1, "days_left": 1})["subtitle"] == "投稿目标 · Mine"
    assert sent["id"] != mine["id"]


def test_a_paper_accepted_at_an_earlier_edition_does_not_quiet_the_next(venue_data) -> None:
    add_venue("ZZ", (2026, [(-300, -290, None)]), (2027, [(1, 2, None)]))
    old = plan.add_target("Old", "ZZ", edition=2026, status="accepted")
    assert titles(plan.targets()) == ["Old"] and old["edition"] == 2026
    now_events = [e for e in reminders.events() if e["venue"] == "zz"]
    assert now_events and all(e["type"] == "venue" and "2027" in e["title"] for e in now_events)


def test_papers_aimed_at_different_rounds_are_named_on_their_own_deadlines(venue_data) -> None:
    add_venue("ZZ", (2027, [(1, 2, "Cycle 1"), (3, 4, "Cycle 2")]))
    day = lambda n: (NOW + timedelta(days=n)).date().isoformat()  # noqa: E731
    plan.add_target("First", "ZZ", edition=2027, deadline=day(2))
    plan.add_target("Second", "ZZ", edition=2027, deadline=day(4))
    got = [titles(e["targets"]) for e in reminders.events() if e["venue"] == "zz"]
    assert sorted(got) == [["First"], ["First"], ["Second"], ["Second"]]


def test_reminder_keys_do_not_change_with_editions(venue_data) -> None:
    """The sent-log keys were venue + stage + date before editions existed, and still are: no repeats after the update."""
    add_venue("ZZ", (2027, [(1, 2, None)]))
    day = (NOW + timedelta(days=2)).date().isoformat()
    plan.watch("ZZ")
    assert {e["key"] for e in reminders.events() if e["venue"] == "zz"} == {f"v:zz:deadline:{day}", f"v:zz:abstract:{(NOW + timedelta(days=1)).date().isoformat()}"}
    plan.add_target("Paper", "ZZ", edition=2027)
    assert {e["key"] for e in reminders.events() if e["venue"] == "zz"} == {f"v:zz:deadline:{day}", f"v:zz:abstract:{(NOW + timedelta(days=1)).date().isoformat()}"}


# ---------- assistant tools, options and the CLI ----------


@pytest.fixture
def call(monkeypatch):
    """call(tool, args) -> (result, emitted events): runs a studio tool handler the way the SDK would."""
    tools: dict = {}
    events: list[dict] = []

    async def emit(e):
        events.append(e)

    real = studio_tools.create_sdk_mcp_server
    monkeypatch.setattr(studio_tools, "create_sdk_mcp_server",
                        lambda name, tools=None, **kw: (by_name.update({t.name: t for t in tools}), real(name, tools=tools, **kw))[1])
    by_name = tools
    studio_tools.build(emit)

    def run_tool(name: str, args: dict):
        events.clear()
        return asyncio.run(tools[name].handler(args)), list(events)

    return run_tool


def payload(res: dict) -> Any:
    return json.loads(res["content"][0]["text"])


def test_tools_set_and_change_the_edition(venue_data, call) -> None:
    add_venue("ZZ", (2027, [(20, 30, None), (110, 120, None)]), (2028, [(380, 390, None)]))
    key = (NOW + timedelta(days=120)).date().isoformat()
    res, ev = call("target_add", {"title": "Paper", "venue": "ZZ", "edition": 2027, "deadline": key})
    rec = payload(res)
    assert (rec["edition"], rec["deadline"]) == (2027, key) and "ZZ 2027" in ev[0]["summary"]
    res, _ = call("target_update", {"id": rec["id"], "edition": 2028})
    assert payload(res)["edition"] == 2028 and "deadline" not in stored(rec["id"])
    res, _ = call("target_update", {"id": rec["id"], "edition": 2027, "deadline": key, "status": "writing"})
    assert payload(res)["deadline"] == key
    res, _ = call("target_update", {"id": rec["id"], "deadline": ""})
    assert "deadline" not in stored(rec["id"])
    res, _ = call("target_update", {"id": rec["id"], "edition": 1})
    assert res.get("is_error") and "year" in res["content"][0]["text"]
    res, _ = call("target_add", {"title": "Other", "venue": "ZZ", "edition": 2027, "deadline": FAR})
    assert res.get("is_error") and "no deadline near" in res["content"][0]["text"]
    res, _ = call("timeline", {"days": 500})
    assert payload(res)["targets"][0]["edition"] == 2027


def test_edition_options_list_the_rounds_to_pick_from(venue_data) -> None:
    add_venue("ZZ", (2027, [(20, 30, "Cycle 1"), (110, 120, "Cycle 2")]))
    opts = plan.edition_options("zz")
    assert opts["default"] == 2027 and opts["acronym"] == "ZZ"
    [y] = opts["editions"]
    assert [r["key"] for r in y["rounds"]] == [(NOW + timedelta(days=d)).date().isoformat() for d in (30, 120)]
    assert y["rounds"][0]["deadline"] and y["rounds"][0]["abstract"] and y["rounds"][1]["comment"] == "Cycle 2"
    with pytest.raises(ValueError, match="unknown venue"):
        plan.edition_options("nope")


def test_official_editions_can_be_picked_too(venue_data, tmp_path, monkeypatch) -> None:
    """A venue ccfddl has no upcoming deadline for takes its edition from the verified CFP details."""
    details = tmp_path / "d.json"
    far = (NOW + timedelta(days=60)).date().isoformat()
    details.write_text(json.dumps({"venues": [{
        "key": "ACL", "status": "confirmed", "edition": "ACL 2099", "cfp_url": "https://cfp.example",
        "deadlines": [{"track": "Main", "abstract": None, "paper": f"{far} AoE"}]}]}), encoding="utf-8")
    monkeypatch.setattr(venues, "DETAILS_FILE", details)
    venues._catalog.cache_clear()
    assert plan.edition_options("ACL")["default"] == 2099
    t = plan.add_target("Paper", "ACL")
    assert t["edition"] == 2099
    assert any(e["edition"] == 2099 and e["type"] == "target" for e in events_of("acl"))


def test_cli_adds_lists_and_updates_targets(venue_data, capsys) -> None:
    from app import cli

    def run(*argv: str) -> tuple[int, str, str]:
        code = cli.main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    add_venue("ZZ", (2027, [(20, 30, None)]), (2028, [(380, 390, None)]))
    code, out, _ = run("target", "Paper A", "ZZ", "--edition", "2027", "--line", "Project A")
    assert code == 0 and out.startswith("added: t") and "ZZ 2027 Paper A" in out
    code, out, _ = run("target", "add", "Paper B", "KDD")
    assert code == 0 and f"KDD {venue_data.kdd_paper.year + 1}" in out
    code, out, _ = run("target", "list")
    rows = out.splitlines()
    assert code == 0 and len(rows) == 2 and "ZZ" in rows[0] and "2027 " in rows[0] and "Project A" in rows[0]
    tid = rows[0].split()[0]
    code, out, _ = run("target", "update", tid, "--status", "submitted", "--edition", "2028")
    assert code == 0 and "[submitted]" in out and "2028" in out
    code, out, _ = run("target", "list", "--status", "submitted", "--json")
    assert [t["edition"] for t in json.loads(out)] == [2028]
    code, out, _ = run("deadlines", "--days", "400")
    assert "(sent: Paper A)" in out.split("ZZ 2028")[1].splitlines()[0] and "-> Paper B" in out
    code, out, err = run("target", "update", tid, "--edition", "auto")
    assert code == 0 and "edition" not in stored(tid)
    code, _, err = run("target", "update", tid)
    assert code == 2 and "nothing to change" in err
    code, _, err = run("target", "update", "tnope", "--status", "writing")
    assert code == 2 and "no target tnope" in err
    code, _, err = run("target", "Paper C", "ZZ", "--edition", "2027", "--deadline", FAR)
    assert code == 2 and "no deadline near" in err


# ---------- the web API ----------


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_api_lists_editions_and_takes_an_edition_with_a_target(venue_data, client) -> None:
    add_venue("ZZ", (2027, [(20, 30, "Cycle 1"), (110, 120, "Cycle 2")]))
    r = client.get("/api/venues/zz/editions")
    assert r.status_code == 200 and r.json()["default"] == 2027 and len(r.json()["editions"][0]["rounds"]) == 2
    assert client.get("/api/venues/nope/editions").status_code == 404
    key = r.json()["editions"][0]["rounds"][1]["key"]
    r = client.post("/api/plan/targets", json={"title": "Paper", "venue": "ZZ", "edition": 2027, "deadline": key})
    assert r.status_code == 200 and (r.json()["edition"], r.json()["deadline"]) == (2027, key)
    tid = r.json()["id"]
    assert client.post("/api/plan/targets", json={"title": "Bad", "venue": "ZZ", "edition": 2027, "deadline": FAR}).status_code == 400
    r = client.patch(f"/api/plan/targets/{tid}", json={"edition": "", "deadline": ""})  # back to automatic
    assert r.status_code == 200 and "edition" not in stored(tid)
    assert client.patch(f"/api/plan/targets/{tid}", json={"edition": 12}).status_code == 400
    assert client.patch("/api/plan/targets/tnope", json={"edition": 2027}).status_code == 400


def test_api_overview_counts_down_to_the_paper_still_being_written(venue_data, client) -> None:
    add_venue("ZZ", (2027, [(5, 6, None)]))
    plan.add_target("Sent", "ZZ", edition=2027, status="submitted")
    assert client.get("/api/overview").json()["next_target"] is None  # the only deadline soon is a sent paper's
    mine = plan.add_target("Mine", "KDD")
    o = client.get("/api/overview").json()
    assert o["next_target"]["venue"] == "kdd" and o["next_target"]["targets"][0]["id"] == mine["id"]
    assert {t["title"]: t["edition"] for t in o["targets"]} == {"Sent": 2027, "Mine": venue_data.kdd_paper.year + 1}

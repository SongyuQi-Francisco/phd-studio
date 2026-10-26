"""Deadline reminders: lead times, local time, quiet hours, dedupe (restarts, two Macs), the check,
the LaunchAgent definition and the CLI. Every clock here is fake; nothing is posted or installed."""
from __future__ import annotations

import json
import plistlib
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml

from app import cli, config, notify, plan, remind_agent, reminders

MEL = ZoneInfo("Pacific/Noumea")  # UTC+11 all year
LA = ZoneInfo("America/Los_Angeles")
LEADS = [14, 7, 3, 1, 0]
REAL_SEND = notify.send  # the autouse fixture below replaces notify.send in every test
NOON = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)  # checks run "today at noon"


@pytest.fixture(autouse=True)
def no_system(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[tuple[str, ...]]:
    """No real LaunchAgents dir, launchctl, app lookup or notification in any test here."""
    calls: list[tuple[str, ...]] = []

    def launchctl(*args: str) -> Any:
        calls.append(args)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(remind_agent, "AGENTS_DIR", tmp_path / "LaunchAgents")
    monkeypatch.setattr(remind_agent, "LOG", tmp_path / "Logs" / "reminders.log")
    monkeypatch.setattr(remind_agent, "_launchctl", launchctl)
    monkeypatch.setattr(remind_agent, "find_app", lambda: None)
    monkeypatch.setattr(notify, "send", lambda *a, **k: pytest.fail("notify.send called in a test"))
    return calls


class Sender:
    """Records notifications instead of posting them."""

    def __init__(self, ok: bool = True, denied: bool = False):
        self.ok, self.denied, self.sent = ok, denied, []

    def __call__(self, title: str, body: str = "", subtitle: str = "", open_: Any = None, ident: Any = None,
                 ask: bool = False) -> dict:
        self.sent.append({"title": title, "body": body, "subtitle": subtitle, "open": open_, "ident": ident, "ask": ask})
        out = {"ok": self.ok, "via": "fake"}
        if not self.ok:
            out["error"] = "nope"
        if self.denied:
            out["denied"] = True
        return out


def ev(at: datetime, key: str = "v:kdd:KDD 2027 全文截止", type_: str = "venue", **kw: Any) -> dict[str, Any]:
    return {"key": key, "type": type_, "title": kw.pop("title", "KDD 2027 全文截止"), "at": at,
            "open": {"view": "timeline", "venue": "kdd"}, **kw}


def cfg(**kw: Any) -> dict[str, Any]:
    c = {**reminders.DEFAULTS, "enabled": True, "types": dict(reminders.DEFAULTS["types"])}
    c.update(kw)
    return c


def walk(event: dict[str, Any], start: datetime, end: datetime, every: timedelta, conf: dict[str, Any],
         tz: Any = MEL, skip: Any = None) -> list[tuple[str, int, int]]:
    """Run due() on a fake clock from start to end; returns (local day, lead, days_left) per send."""
    sent: set[str] = set()
    out = []
    now = start
    while now <= end:
        if not (skip and skip(now)):
            for r in reminders.due([event], conf, now, sent, tz):
                sent.add(r["id"])
                out.append((now.astimezone(tz).date().isoformat(), r["lead"], r["days_left"]))
        now += every
    return out


def hourly(event: dict[str, Any], start: datetime, end: datetime, conf: dict[str, Any], tz: Any = MEL,
           off: Any = None) -> list[tuple[str, int, int]]:
    """Like the LaunchAgent: a check every hour on the hour, none in quiet hours (or while `off`).
    Returns (local time sent, lead, days_left) per send."""
    sent: set[str] = set()
    out = []
    now = start.replace(minute=0, second=0, microsecond=0)
    while now <= end:
        if not reminders.in_quiet(conf, now, tz) and not (off and off(now)):
            for r in reminders.due([event], conf, now, sent, tz):
                sent.add(r["id"])
                out.append((now.astimezone(tz).strftime("%Y-%m-%d %H:%M"), r["lead"], r["days_left"]))
        now += timedelta(hours=1)
    return out


# ---------- lead times on a fake clock ----------


def test_each_lead_time_fires_once_in_order() -> None:
    at = datetime(2027, 1, 22, 22, 59, tzinfo=MEL)
    got = walk(ev(at), datetime(2026, 12, 20, 9, tzinfo=MEL), at + timedelta(days=2), timedelta(hours=4), cfg())
    assert [g[1] for g in got] == LEADS
    assert got[0] == ("2027-01-08", 14, 14)
    assert got[-1] == ("2027-01-22", 0, 0)


def test_checks_three_times_a_day_still_send_each_lead_once() -> None:
    at = datetime(2027, 3, 1, 12, 0, tzinfo=MEL)
    times = {9, 13, 18}
    got = walk(ev(at), datetime(2027, 2, 1, 0, tzinfo=MEL), at, timedelta(hours=1), cfg(),
               skip=lambda n: n.astimezone(MEL).hour not in times or n.minute)
    assert [g[1] for g in got] == LEADS


def test_a_mac_that_was_off_sends_only_the_nearest_lead() -> None:
    at = datetime(2027, 1, 22, 22, 59, tzinfo=MEL)
    off = (datetime(2027, 1, 10, tzinfo=MEL), datetime(2027, 1, 20, 8, tzinfo=MEL))
    got = walk(ev(at), datetime(2027, 1, 1, 9, tzinfo=MEL), at, timedelta(hours=6), cfg(),
               skip=lambda n: off[0] <= n < off[1])
    assert [(g[1], g[2]) for g in got] == [(14, 14), (3, 2), (1, 1), (0, 0)]


def test_a_new_event_inside_the_window_gets_one_catch_up_reminder() -> None:
    at = datetime(2027, 1, 22, 22, 59, tzinfo=MEL)
    r = reminders.due([ev(at)], cfg(), datetime(2027, 1, 12, 9, tzinfo=MEL), set(), MEL)
    assert [(x["lead"], x["days_left"]) for x in r] == [(14, 10)]


def test_nothing_after_the_deadline_or_beyond_the_longest_lead() -> None:
    at = datetime(2027, 1, 22, 22, 59, tzinfo=MEL)
    assert reminders.due([ev(at)], cfg(), at + timedelta(minutes=1), set(), MEL) == []
    assert reminders.due([ev(at)], cfg(), at - timedelta(days=20), set(), MEL) == []


def test_custom_leads() -> None:
    at = datetime(2027, 5, 30, 23, 59, tzinfo=MEL)
    got = walk(ev(at), datetime(2027, 4, 1, 9, tzinfo=MEL), at, timedelta(days=1), cfg(leads=[30, 2]))
    assert [(g[1], g[2]) for g in got] == [(30, 30), (2, 2)]


def test_days_are_counted_in_local_time() -> None:
    # 23:59 AoE on Jan 22 is 11:59 UTC on Jan 23: Jan 23 22:59 at UTC+11, Jan 23 03:59 in Los Angeles.
    at = datetime(2027, 1, 23, 11, 59, tzinfo=timezone.utc)
    mel = reminders.due([ev(at)], cfg(leads=[1, 0]), datetime(2027, 1, 22, 20, tzinfo=MEL), set(), MEL)
    assert [(x["lead"], x["days_left"]) for x in mel] == [(1, 1)]
    assert reminders.message(mel[0], MEL)["body"] == "1月23日 周六 22:59 截止（本地时间）"
    la = reminders.due([ev(at)], cfg(leads=[1, 0]), datetime(2027, 1, 23, 1, tzinfo=LA), set(), LA)
    assert [(x["lead"], x["days_left"]) for x in la] == [(0, 0)]
    assert reminders.message(la[0], LA)["body"].endswith("03:59 截止（本地时间）")


def test_a_moved_deadline_starts_its_reminders_again() -> None:
    at = datetime(2027, 1, 22, 22, 59, tzinfo=MEL)
    now = datetime(2027, 1, 21, 9, tzinfo=MEL)
    first = reminders.due([ev(at, key=f"v:kdd:t:{at.isoformat()}")], cfg(), now, set(), MEL)
    sent = {first[0]["id"]}
    later = at + timedelta(days=7)  # extension
    again = reminders.due([ev(later, key=f"v:kdd:t:{later.isoformat()}")], cfg(), now, sent, MEL)
    assert [(x["lead"], x["days_left"]) for x in again] == [(14, 8)]


def test_event_types_can_be_switched_off() -> None:
    at = datetime(2027, 1, 22, 22, 59, tzinfo=MEL)
    evs = [ev(at), ev(at, key="m:1", type_="milestone", title="Gate", kind="gate")]
    now = datetime(2027, 1, 21, 9, tzinfo=MEL)
    conf = cfg(types={"target": True, "milestone": True, "venue": False})
    assert [r["type"] for r in reminders.due(evs, conf, now, set(), MEL)] == ["milestone"]


# ---------- when in the day (hourly checks) ----------


def test_reminders_come_due_at_the_reminder_time_not_at_midnight() -> None:
    at = datetime(2027, 1, 22, 22, 59, tzinfo=MEL)
    got = hourly(ev(at), datetime(2027, 1, 1, tzinfo=MEL), at, cfg())
    assert [(t[11:], lead) for t, lead, _ in got] == [("09:00", 14), ("09:00", 7), ("09:00", 3), ("09:00", 1),
                                                     ("09:00", 0)]
    got = hourly(ev(at), datetime(2027, 1, 1, tzinfo=MEL), at, cfg(time="18:00"))
    assert {t[11:] for t, _, _ in got} == {"18:00"}


def test_the_day_of_reminder_comes_before_an_early_deadline() -> None:
    at = datetime(2027, 1, 22, 7, 0, tzinfo=MEL)  # before the 09:00 reminder time
    got = hourly(ev(at), datetime(2027, 1, 1, tzinfo=MEL), at + timedelta(days=1), cfg())
    assert got[-1] == ("2027-01-22 04:00", 0, 0)  # three hours ahead
    at = datetime(2027, 1, 22, 1, 30, tzinfo=MEL)  # just after midnight: as early as that day allows
    assert hourly(ev(at), datetime(2027, 1, 20, tzinfo=MEL), at, cfg())[-1] == ("2027-01-22 00:00", 0, 0)


def test_long_quiet_hours_only_delay_reminders() -> None:
    at = datetime(2027, 1, 22, 22, 59, tzinfo=MEL)
    conf = cfg(quiet=True, quiet_start="08:30", quiet_end="18:30")  # covers 09:00, 13:00 and 18:00
    got = hourly(ev(at), datetime(2027, 1, 1, tzinfo=MEL), at, conf)
    assert [lead for _, lead, _ in got] == LEADS and {t[11:] for t, _, _ in got} == {"19:00"}


def test_a_deadline_inside_quiet_hours_is_announced_before_they_start() -> None:
    at = datetime(2027, 1, 22, 3, 0, tzinfo=MEL)
    conf = cfg(quiet=True, quiet_start="22:00", quiet_end="08:00")
    got = hourly(ev(at), datetime(2027, 1, 1, tzinfo=MEL), at, conf)
    assert got[-2:] == [("2027-01-21 09:00", 1, 1), ("2027-01-21 21:00", 0, 1)]  # last one before 22:00


def test_a_reminder_held_by_quiet_hours_goes_out_when_they_end() -> None:
    at = datetime(2027, 1, 22, 22, 59, tzinfo=MEL)
    conf = cfg(quiet=True, quiet_start="07:00", quiet_end="10:30", leads=[3])
    assert hourly(ev(at), datetime(2027, 1, 1, tzinfo=MEL), at, conf) == [("2027-01-19 11:00", 3, 3)]


def test_a_mac_asleep_through_a_lead_time_catches_up_once_awake() -> None:
    at = datetime(2027, 1, 22, 22, 59, tzinfo=MEL)
    asleep = (datetime(2027, 1, 15, 8, tzinfo=MEL), datetime(2027, 1, 16, 14, tzinfo=MEL))
    got = hourly(ev(at), datetime(2027, 1, 1, tzinfo=MEL), at, cfg(), off=lambda n: asleep[0] <= n < asleep[1])
    assert [(t, lead) for t, lead, _ in got][:2] == [("2027-01-08 09:00", 14), ("2027-01-16 14:00", 7)]


# ---------- quiet hours ----------


@pytest.mark.parametrize("hhmm,quiet", [("21:59", False), ("22:00", True), ("23:30", True), ("03:00", True),
                                        ("07:59", True), ("08:00", False), ("13:00", False)])
def test_quiet_hours_across_midnight(hhmm: str, quiet: bool) -> None:
    h, m = map(int, hhmm.split(":"))
    now = datetime(2027, 1, 10, h, m, tzinfo=MEL)
    assert reminders.in_quiet(cfg(quiet=True, quiet_start="22:00", quiet_end="08:00"), now, MEL) is quiet


def test_quiet_hours_within_a_day_and_off() -> None:
    c = cfg(quiet=True, quiet_start="12:00", quiet_end="14:00")
    assert reminders.in_quiet(c, datetime(2027, 1, 10, 12, 30, tzinfo=MEL), MEL)
    assert not reminders.in_quiet(c, datetime(2027, 1, 10, 14, 0, tzinfo=MEL), MEL)
    assert not reminders.in_quiet({**c, "quiet": False}, datetime(2027, 1, 10, 12, 30, tzinfo=MEL), MEL)
    assert not reminders.in_quiet({**c, "quiet_end": "12:00"}, datetime(2027, 1, 10, 12, 30, tzinfo=MEL), MEL)


# ---------- text ----------


def test_message_text() -> None:
    at = datetime(2027, 1, 22, 22, 59, tzinfo=MEL)
    base = {**ev(at, ccf="A", core="A*"), "lead": 3}
    assert reminders.message({**base, "days_left": 3}, MEL) == {
        "title": "KDD 2027 全文截止 · 还有 3 天", "subtitle": "关注的会议 · CCF A / CORE A*",
        "body": "1月22日 周五 22:59 截止（本地时间）"}
    assert reminders.message({**base, "days_left": 1}, MEL)["title"].endswith("明天截止")
    assert reminders.message({**base, "days_left": 0}, MEL)["title"].endswith("今天截止")
    tgt = {**base, "type": "target", "targets": [{"id": "t1", "title": "Paper A"}, {"id": "t2", "title": "Paper B"}],
           "days_left": 7}
    assert reminders.message(tgt, MEL)["subtitle"] == "投稿目标 · Paper A、Paper B"
    ms = {**ev(at, key="m:1", type_="milestone", title="Gate 2", kind="gate", line="Project A"), "days_left": 0}
    assert reminders.message(ms, MEL) == {"title": "Gate 2 · 今天截止", "subtitle": "Gate · Project A",
                                          "body": "1月22日 周五 截止"}


# ---------- settings ----------


def test_settings_defaults_and_validation() -> None:
    s = reminders.settings()
    assert s["enabled"] is False and s["leads"] == LEADS and all(s["types"].values()) and s["device"] == ""
    assert s["time"] == "09:00"
    assert not reminders.FILE.exists()  # reading never creates the shared file
    for bad in ({"leads": []}, {"leads": [-1]}, {"leads": [91]}, {"types": {t: False for t in reminders.TYPES}},
                {"quiet_start": "25:00"}, {"time": "noon"}, {"enabled": "yes"}, {"nope": 1}):
        with pytest.raises(ValueError):
            reminders.save_settings(bad)


def test_turning_on_designates_this_mac_and_is_stored_in_the_vault() -> None:
    s = reminders.save_settings({"enabled": True, "leads": [7, 1, 7], "quiet": True, "quiet_start": "23:00",
                                 "time": "08:30"})
    mid = reminders.machine_id()
    assert s["device"] == mid and len(mid) == 12 and mid == reminders.machine_id()  # stable
    assert reminders.MACHINE_FILE.is_relative_to(config.DATA_DIR)  # per machine, not synced
    assert s["leads"] == [7, 1] and s["quiet_start"] == "23:00" and s["time"] == "08:30"
    assert reminders.FILE.is_relative_to(config.VAULT_DIR) and reminders.FILE.parent == config.PLAN_FILE.parent
    rec = yaml.safe_load(reminders.FILE.read_text(encoding="utf-8"))["settings"][0]
    assert rec["enabled"] is True and rec["device"] == mid
    reminders.save_settings({"device": "0123456789ab"})
    assert reminders.save_settings({"enabled": False})["device"] == "0123456789ab"  # switching off keeps the choice


def test_unchecking_one_type_survives_the_round_trip() -> None:
    s = reminders.save_settings({"types": {"target": True, "milestone": False, "venue": False}})
    assert s["types"] == {"target": True, "milestone": False, "venue": False}


# ---------- events from the plan ----------


def _milestone(days: int, **kw: Any) -> dict[str, Any]:
    return plan.add_milestone(kw.pop("title", "Draft"), (date.today() + timedelta(days=days)).isoformat(), **kw)


def test_events_from_the_plan(venue_data: Any) -> None:
    m = _milestone(5, kind="gate", line="Project A")
    _milestone(3, title="Finished", status="done")
    t = plan.add_target("Paper A", "KDD", line="Project A")
    plan.add_target("Paper C", "CIKM", status="submitted")
    plan.add_target("Paper D", "KDD", status="accepted")
    evs = reminders.events()
    by_title = {e["title"]: e for e in evs}
    assert "Finished" not in by_title
    assert by_title["Draft"]["key"] == f"m:{m['id']}:{m['date']}" and by_title["Draft"]["open"] == {
        "view": "plan", "milestone": m["id"]}
    kdd = [e for e in evs if e.get("venue") == "kdd"]
    assert kdd and all(e["type"] == "target" and [x["id"] for x in e["targets"]] == [t["id"]] for e in kdd)
    assert kdd[0]["open"] == {"view": "timeline", "venue": "kdd"}
    assert sorted(e["key"].split(":")[2] for e in kdd) == ["abstract", "deadline"]
    assert all("KDD" not in e["key"] for e in kdd)  # the display title is not part of the key
    assert [e for e in evs if e.get("venue") == "cikm"] == []  # the only paper aimed at that edition is sent
    assert all(e["at"].tzinfo is not None for e in evs)


def _timeline(events: list[dict[str, Any]], targets: list[dict[str, Any]]) -> dict[str, Any]:
    return {"events": events, "targets": targets, "milestones": [], "watched": []}


def _venue_event(title: str, at: str, stage: str = "deadline", venue: str = "acl", **kw: Any) -> dict[str, Any]:
    return {"type": "venue", "venue": venue, "title": title, "at": at, "stage": stage, "ccf": "A", "core": "A*", **kw}


def test_the_same_deadline_keeps_its_key_when_its_title_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The official-site fallback says 「主会」; ccfddl's edition, published later, does not."""
    at = "2027-02-16T11:59:59+00:00"
    before = _timeline([_venue_event("ACL 2027 主会 全文截止", at)], [])
    after = _timeline([_venue_event("ACL 2027 全文截止", at)], [])
    monkeypatch.setattr(plan, "timeline", lambda **kw: before)
    old = reminders.events()[0]
    monkeypatch.setattr(plan, "timeline", lambda **kw: after)
    new = reminders.events()[0]
    assert old["key"] == new["key"] == "v:acl:deadline:2027-02-16"
    now = datetime(2027, 2, 14, 12, tzinfo=MEL)
    sent = {r["id"] for r in reminders.due([old], cfg(), now, set(), MEL)}
    assert sent and reminders.due([new], cfg(), now, sent, MEL) == []
    # abstract and paper deadline on the same day stay two reminders; two tracks at one deadline are one
    both = _timeline([_venue_event("ACL 2027 摘要截止", at, "abstract"), _venue_event("ACL 2027 全文截止", at),
                      _venue_event("ACL 2027 Industry 全文截止", at)], [])
    monkeypatch.setattr(plan, "timeline", lambda **kw: both)
    assert len(reminders.due(reminders.events(), cfg(), now, set(), MEL)) == 2


def test_events_follow_the_papers_the_timeline_attaches_to_a_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Which papers a deadline belongs to is the timeline's call (edition, round); reminders only read it."""
    at = "2027-02-16T11:59:59+00:00"
    paper_b = {"id": "t3", "title": "Paper B", "line": None}
    tl = _timeline([
        _venue_event("KDD 2027 全文截止", at, venue="kdd", targets=[], closed=[], muted=False),  # nobody's: the venue's
        _venue_event("ACL 2027 全文截止", at, targets=[paper_b], closed=[{**paper_b, "id": "t2", "status": "submitted"}], muted=False),
        _venue_event("CIKM 2027 全文截止", at, venue="cikm", targets=[], closed=[{**paper_b, "id": "t4", "status": "accepted"}], muted=True),
    ], [])
    monkeypatch.setattr(plan, "timeline", lambda **kw: tl)
    by_venue = {e["venue"]: e for e in reminders.events()}
    assert by_venue["kdd"]["type"] == "venue" and by_venue["kdd"]["targets"] == []
    assert by_venue["acl"]["type"] == "target" and [t["title"] for t in by_venue["acl"]["targets"]] == ["Paper B"]
    assert "cikm" not in by_venue  # only sent papers aimed at it: quiet
    now = datetime(2027, 2, 14, 12, tzinfo=MEL)
    assert {r["venue"] for r in reminders.due(reminders.events(), cfg(), now, set(), MEL)} == {"kdd", "acl"}


# ---------- the check ----------


def _now(days_before: float, at: datetime) -> datetime:
    return at - timedelta(days=days_before)


def test_check_does_nothing_while_off() -> None:
    _milestone(1)
    send = Sender()
    out = reminders.check(now=NOON, send=send)
    assert out["skipped"] == "off" and send.sent == [] and not reminders.FILE.exists()


def test_check_sends_once_and_remembers_across_restarts() -> None:
    reminders.save_settings({"enabled": True})
    m = _milestone(3, title="Gate 2", kind="gate")
    send = Sender()
    first = reminders.check(now=NOON, send=send)
    assert first["sent"] == ["Gate 2 · 还有 3 天"]
    assert send.sent[0]["open"] == {"view": "plan", "milestone": m["id"]}
    assert send.sent[0]["subtitle"] == "Gate" and send.sent[0]["ident"].startswith("s")
    assert send.sent[0]["ask"] is False  # a background check never shows the permission prompt
    assert reminders.check(now=NOON, send=send)["sent"] == [] and len(send.sent) == 1
    log = yaml.safe_load(reminders.FILE.read_text(encoding="utf-8"))["sent"]
    assert [(r["lead"], r["device"]) for r in log] == [(3, "test")]
    assert reminders.LOCAL_FILE.is_relative_to(config.DATA_DIR)  # this Mac's own record: not synced
    last = reminders.status(now=NOON)["last_check"]
    assert last["ok"] is True and "sent" not in last  # nothing sent the second time


def test_failed_send_is_retried_next_time() -> None:
    reminders.save_settings({"enabled": True})
    _milestone(2)
    out = reminders.check(now=NOON, send=Sender(ok=False))
    assert out["sent"] == [] and out["failed"][0]["error"] == "nope"
    assert reminders.check(now=NOON, send=Sender())["sent"] == ["Draft · 还有 2 天"]


def test_denied_notifications_stop_the_run() -> None:
    reminders.save_settings({"enabled": True})
    _milestone(1, title="A")
    _milestone(2, title="B")
    send = Sender(ok=False, denied=True)
    reminders.check(now=NOON, send=send)
    assert len(send.sent) == 1


def test_only_the_designated_mac_sends() -> None:
    reminders.save_settings({"enabled": True, "device": "other-mac"})
    _milestone(1)
    send = Sender()
    out = reminders.check(now=NOON, send=send)
    assert out["skipped"] == "other_device" and out["designated"] == "other-mac" and send.sent == []


def test_switching_macs_does_not_repeat_what_the_other_one_sent() -> None:
    reminders.save_settings({"enabled": True})
    _milestone(3)
    first = reminders.check(now=NOON, send=Sender(), dry_run=True)["due"][0]
    with reminders.store.editing() as data:  # what the other Mac wrote, synced over
        data["sent"].append({"id": first["id"], "event": "x", "lead": 3, "title": "Draft",
                             "at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(), "device": "other-mac"})
    send = Sender()
    assert reminders.check(now=NOON, send=send)["sent"] == [] and send.sent == []


def test_a_sync_conflict_copy_of_the_log_still_counts() -> None:
    reminders.save_settings({"enabled": True})
    _milestone(3)
    sid = reminders.check(now=NOON, send=Sender(), dry_run=True)["due"][0]["id"]
    at = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    conflict = reminders.FILE.with_name("reminders.sync-conflict-20261025-120000-ABCDEFG.yaml")
    conflict.write_text(yaml.safe_dump({"sent": [{"id": sid, "lead": 3, "at": at, "device": "other-mac",
                                                  "updated_at": "2026-10-25T12:00:00"}]}), encoding="utf-8")
    assert reminders.check(now=NOON, send=Sender())["sent"] == []
    assert not conflict.exists()


def test_this_macs_own_record_guards_against_a_lost_vault_log() -> None:
    reminders.save_settings({"enabled": True})
    _milestone(3)
    assert len(reminders.check(now=NOON, send=Sender())["sent"]) == 1
    with reminders.store.editing() as data:
        data["sent"].clear()  # e.g. the other Mac's older copy won a sync conflict
    assert reminders.check(now=NOON, send=Sender())["sent"] == []


def test_a_failed_run_leaves_its_error_for_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    reminders.save_settings({"enabled": True})

    def unreadable() -> list:
        raise PermissionError("Operation not permitted: plan.yaml")

    readable = reminders.events
    monkeypatch.setattr(reminders, "events", unreadable)
    with pytest.raises(PermissionError):
        reminders.check(now=NOON, send=Sender())
    monkeypatch.setattr(reminders, "events", readable)  # not undo(): that would lift the sandbox's guards too
    last = json.loads(reminders.LOCAL_FILE.read_text(encoding="utf-8"))["last"]
    assert last["ok"] is False and "Operation not permitted" in last["error"]
    reminders.check(now=NOON, send=Sender())
    assert json.loads(reminders.LOCAL_FILE.read_text(encoding="utf-8"))["last"]["ok"] is True


def test_the_sending_mac_is_known_by_its_id_not_its_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    reminders.save_settings({"enabled": True})
    _milestone(2)
    monkeypatch.setattr(reminders, "DEVICE", "macbook-air-2")  # the hostname changed with the network
    assert len(reminders.check(now=NOON, send=Sender())["sent"]) == 1
    names = {d["id"]: d["name"] for d in reminders.store.load()["devices"]}
    assert names == {reminders.machine_id(): "macbook-air-2"}
    # another Mac that happens to have this hostname is still not the sending one
    reminders.save_settings({"device": "0123456789ab"})
    _milestone(1, title="Other")
    out = reminders.check(now=NOON, send=Sender())
    assert out["skipped"] == "other_device" and reminders.status(now=NOON)["is_designated"] is False


def test_quiet_hours_hold_reminders_until_later() -> None:
    reminders.save_settings({"enabled": True, "quiet": True, "quiet_start": "11:00", "quiet_end": "13:00"})
    _milestone(2)
    noon = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    send = Sender()
    assert reminders.check(now=noon, send=send)["skipped"] == "quiet" and send.sent == []
    assert len(reminders.check(now=noon.replace(hour=13), send=send)["sent"]) == 1


def test_many_at_once_become_a_few_and_one_summary() -> None:
    reminders.save_settings({"enabled": True})
    for i in range(6):
        _milestone(1 + i % 3, title=f"M{i}")
    send = Sender()
    out = reminders.check(now=NOON, send=send)
    assert len(send.sent) == reminders.MAX_PER_RUN
    assert send.sent[-1]["title"] == "另有 3 项临近" and send.sent[-1]["open"] == {"view": "timeline"}
    assert len(out["sent"]) == 6
    assert reminders.check(now=NOON, send=send)["sent"] == []


def test_dry_run_sends_and_writes_nothing() -> None:
    _milestone(2)
    out = reminders.check(now=NOON, send=Sender(), dry_run=True)
    assert out["skipped"] == "off" and [d["title"] for d in out["due"]] == ["Draft · 还有 2 天"]
    assert not reminders.FILE.exists() and not reminders.LOCAL_FILE.exists()


def test_device_heartbeat_is_rate_limited() -> None:
    reminders.save_settings({"enabled": True})
    now = datetime.now(timezone.utc)
    reminders.mark_device(now=now)
    stamp = reminders.FILE.stat().st_mtime_ns
    reminders.mark_device(now=now + timedelta(hours=1))
    assert reminders.FILE.stat().st_mtime_ns == stamp
    reminders.mark_device(now=now + timedelta(hours=7))
    devs = reminders.store.load()["devices"]
    assert [(d["id"], d["name"]) for d in devs] == [(reminders.machine_id(), "test")]


def test_old_sent_entries_are_pruned() -> None:
    reminders.save_settings({"enabled": True})
    with reminders.store.editing() as data:
        data["sent"].append({"id": "sold", "lead": 1, "at": "2025-01-01T00:00:00+00:00", "device": "test"})
    _milestone(1)
    reminders.check(now=NOON, send=Sender())
    assert "sold" not in {r["id"] for r in reminders.store.load()["sent"]}


def test_schedule_lists_upcoming_reminders_by_day() -> None:
    at = datetime(2027, 1, 22, 22, 59, tzinfo=MEL)
    now = datetime(2027, 1, 12, 9, tzinfo=MEL)
    rows = reminders.schedule([ev(at)], cfg(), now, set(), MEL)
    assert [(r["due"].astimezone(MEL).strftime("%m-%d %H:%M"), r["lead"]) for r in rows] == [
        ("01-12 09:00", 14), ("01-15 09:00", 7), ("01-19 09:00", 3), ("01-21 09:00", 1), ("01-22 09:00", 0)]
    sent = {rows[0]["id"]}
    assert [r["lead"] for r in reminders.schedule([ev(at)], cfg(), now, sent, MEL)] == [7, 3, 1, 0]


def test_status_shape() -> None:
    _milestone(4)
    reminders.save_settings({"enabled": True})
    st = reminders.status(now=NOON)
    mid = reminders.machine_id()
    assert st["device"] == mid and st["device_name"] == "test" and st["is_designated"]
    assert st["devices"][0] == {"id": mid, "name": "test", "seen_at": None, "agent": None}
    assert st["agent"]["installed"] is False and st["upcoming"][0]["title"] == "Draft · 还有 4 天"
    assert st["upcoming"][0]["days"] == 0


def test_test_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    send = Sender()
    monkeypatch.setattr(notify, "send", send)
    assert reminders.test_notification()["ok"]
    assert send.sent[0]["title"] == "PhD Studio Deadline 提醒" and send.sent[0]["ask"] is True  # only it may prompt
    assert reminders.status(now=NOON)["last_test"]["ok"] is True


# ---------- LaunchAgent ----------


def test_agent_runs_the_venv_python_not_sys_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "executable", "/Applications/PhD Studio.app/Contents/MacOS/PhDStudio")
    d = remind_agent.definition()
    assert d["ProgramArguments"] == [str(config.STUDIO_DIR / ".venv" / "bin" / "python"), "-m", "app.cli",
                                     "remind", "--check"]
    assert d["WorkingDirectory"] == str(config.STUDIO_DIR) and d["RunAtLoad"] is True
    assert d["Label"] == "local.phd-studio.reminders"
    assert d["StartCalendarInterval"] == [{"Minute": 0}]  # every hour
    assert set(d["EnvironmentVariables"]) == {"PATH", "PYTHONUNBUFFERED"}  # nothing from this process's env


def _fake_app(root: Path, studio: Path, bundle: str = "local.phd-studio") -> Path:
    app = root / "PhD Studio.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": bundle,
                                                                    "CFBundleExecutable": "PhDStudio"}))
    (app / "Contents" / "MacOS" / "PhDStudio").write_bytes(
        b"\x00stub\x00import site; sys.path.insert(0, '" + str(studio).encode() + b"'); runpy\x00")
    return app


def test_agent_prefers_the_app_built_from_this_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mine = _fake_app(tmp_path / "a", config.STUDIO_DIR)
    other = _fake_app(tmp_path / "b", Path("/elsewhere/phd_studio"))
    assert remind_agent._built_here(mine) and remind_agent._built_here(other) is None
    assert remind_agent._built_here(_fake_app(tmp_path / "c", config.STUDIO_DIR, "com.example.app")) is None
    exe = mine / "Contents" / "MacOS" / "PhDStudio"
    d = remind_agent.definition((mine, exe))
    assert d["ProgramArguments"] == [str(exe)] and "WorkingDirectory" not in d
    assert d["EnvironmentVariables"]["PHD_STUDIO_TASK"] == "remind --check"


def test_agent_install_and_uninstall(no_system: list, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(remind_agent, "official_config", lambda: True)
    py = tmp_path / "python"
    py.write_text("")
    monkeypatch.setattr(remind_agent, "venv_python", lambda: py)
    st = remind_agent.install()
    plist = remind_agent.plist_path()
    assert plist.is_relative_to(tmp_path) and st["installed"] and st["loaded"] and st["current"]
    assert plistlib.loads(plist.read_bytes())["ProgramArguments"][0] == str(py)
    assert [c[0] for c in no_system] == ["bootout", "bootstrap", "kickstart", "print"]
    # installed by something else (an older version, another config): not current
    d = plistlib.loads(plist.read_bytes())
    d["EnvironmentVariables"]["STUDIO_CONFIG"] = "/tmp/sandbox/studio.toml"
    plist.write_bytes(plistlib.dumps(d))
    assert remind_agent.status()["current"] is False
    st = remind_agent.uninstall()
    assert not plist.exists() and not st["installed"]
    assert no_system[-1][0] == "bootout"


def test_agent_install_needs_the_venv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(remind_agent, "official_config", lambda: True)
    monkeypatch.setattr(remind_agent, "venv_python", lambda: tmp_path / "missing")
    with pytest.raises(RuntimeError, match="uv sync"):
        remind_agent.install()
    assert not remind_agent.plist_path().exists()


def test_agent_install_refused_from_a_sandboxed_workbench(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    py = tmp_path / "python"
    py.write_text("")
    monkeypatch.setattr(remind_agent, "venv_python", lambda: py)
    assert config.CONFIG_FILE != config.STUDIO_DIR / "studio.toml"  # the tests run on STUDIO_CONFIG
    assert remind_agent.official_config() is False and remind_agent.status()["can_install"] is False
    with pytest.raises(RuntimeError, match="另一份配置"):
        remind_agent.install()
    assert not remind_agent.plist_path().exists()


def test_agent_status_reports_launchd_runs_and_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out = "gui/501/local.phd-studio.reminders = {\n\truns = 3\n\tlast exit code = 78: EX_CONFIG\n}\n"
    monkeypatch.setattr(remind_agent, "_launchctl",
                        lambda *a: type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})())
    remind_agent.AGENTS_DIR.mkdir(parents=True)
    remind_agent.plist_path().write_bytes(plistlib.dumps(remind_agent.definition()))
    st = remind_agent.status()
    assert st["loaded"] and st["runs"] == 3 and st["last_exit"] == 78 and st["installed_at"] > 0
    monkeypatch.setattr(remind_agent, "_launchctl",
                        lambda *a: type("R", (), {"returncode": 0, "stdout": "\truns = 0\n\tlast exit code = (never exited)\n",
                                                  "stderr": ""})())
    st = remind_agent.status()
    assert st["runs"] == 0 and st["last_exit"] is None


def test_agent_install_refused_off_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError):
        remind_agent.install()


# ---------- notify, desktop task, API, CLI ----------


def test_osascript_gets_the_text_as_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def run(args: list[str], **kw: Any) -> Any:
        seen["args"] = args
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(notify.subprocess, "run", run)
    out = notify._send_osascript('KDD "2027"', "body\\n", "sub")
    assert out == {"ok": True, "via": "osascript"}
    assert seen["args"][0] == "osascript" and seen["args"][-3:] == ['KDD "2027"', "body\\n", "sub"]


def test_notify_never_raises_and_skips_the_center_outside_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/osascript")
    monkeypatch.setattr(notify, "bundle_id", lambda: "")  # a bare Python: the center would abort it
    monkeypatch.setattr(notify, "_send_un", lambda *a: pytest.fail("UNUserNotificationCenter outside the app"))
    monkeypatch.setattr(notify, "_send_osascript", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    assert notify.method() == "osascript"
    assert REAL_SEND("t") == {"ok": False, "via": "osascript", "error": "OSError: boom"}
    monkeypatch.setattr(notify, "bundle_id", lambda: "local.phd-studio")
    monkeypatch.setattr(notify, "_send_un", lambda *a: {"ok": True, "via": "app"})
    assert notify.method() == "app" and REAL_SEND("t") == {"ok": True, "via": "app"}
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(notify, "bundle_id", lambda: "")
    assert REAL_SEND("t")["via"] == "none"


def test_only_an_asking_send_may_prompt_for_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify, "_load", lambda: pytest.fail("must not touch the center before the check"))
    monkeypatch.setattr(notify, "authorization", lambda wait=5.0: "not_determined")
    out = notify._send_un("t", "b", "", None, "id", 1.0, False)
    assert out["denied"] and out["auth"] == "not_determined" and "发一条测试通知" in out["error"]
    monkeypatch.setattr(notify, "authorization", lambda wait=5.0: "denied")
    assert notify._send_un("t", "b", "", None, "id", 1.0, True)["denied"]


def test_desktop_task_only_runs_remind(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import desktop

    seen = []
    monkeypatch.setattr(cli, "main", lambda args: seen.append(args) or 0)
    assert desktop._task("remind --check") == 0 and seen == [["remind", "--check"]]
    assert desktop._task("push --paper x") == 2 and len(seen) == 1


def test_api_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.reminders_api import router

    api = FastAPI()
    api.include_router(router)
    c = TestClient(api)
    assert c.get("/api/reminders").json()["settings"]["enabled"] is False
    r = c.put("/api/reminders/settings", json={"enabled": True, "leads": [7, 1]})
    assert r.status_code == 200 and r.json()["settings"]["leads"] == [7, 1]
    assert r.json()["designated"] == reminders.machine_id() and r.json()["devices"][0]["name"] == "test"
    assert c.put("/api/reminders/settings", json={"leads": []}).status_code == 400
    monkeypatch.setattr(sys, "platform", "linux")
    assert c.post("/api/reminders/agent", json={"install": True}).status_code == 400
    send = Sender()
    monkeypatch.setattr(notify, "send", send)
    assert c.post("/api/reminders/test").json()["ok"] and len(send.sent) == 1


def test_cli_remind(capsys: pytest.CaptureFixture[str]) -> None:
    _milestone(2, title="Gate 2")
    assert cli.main(["remind", "--check", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "skipped: off" in out and "would send  Gate 2 · 还有 2 天" in out
    later = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
    assert cli.main(["remind", "--check", "--dry-run", "--now", later]) == 0
    assert "Gate 2 · 明天截止" in capsys.readouterr().out
    assert cli.main(["remind", "--check", "--now", later]) == 2  # a fake clock only for a dry run
    assert cli.main(["remind"]) == 0
    assert "reminders off" in capsys.readouterr().out
    assert not reminders.FILE.exists()

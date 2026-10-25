"""Which edition (year) and which deadline round a submission target aims at.

A target stores `edition` (the conference year) and, optionally, `deadline`: one round of that edition,
named by the UTC date of its full-paper deadline (its abstract deadline while the paper one is TBD). Both
are chosen from the venue's ccfddl / official editions (`options`). Without `deadline` a target covers
every round of its edition; a `deadline` that no round is near any more (moved by more than TOLERANCE)
falls back to that.

A target saved before editions existed has no `edition`. `resolve` works one out each time it is read and
nothing is written back; plan.update pins it into the record only when the user changes the target:
- planning / writing: the edition with the nearest upcoming deadline;
- submitted / accepted: the edition it was sent to, the one with the latest deadline up to EARLY after the
  target's last change (a paper is marked submitted a little before the deadline at the earliest).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

OPEN = ("planning", "writing")  # still to be sent: its deadlines are the target's
CLOSED = ("submitted", "accepted")  # sent: the rest of its edition no longer needs reminding for this paper
EARLY = timedelta(days=45)
TOLERANCE = timedelta(days=30)
MIN_YEAR, MAX_YEAR = 2000, 2100


def year_of(value: Any) -> int | None:
    """An edition year from a stored or typed value; None for 'not set' (None, '', 0)."""
    if value in (None, "", 0, False):
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        raise ValueError("edition must be a year like 2027") from None
    if isinstance(value, bool) or not MIN_YEAR <= year <= MAX_YEAR:
        raise ValueError("edition must be a year like 2027")
    return year


def stored_year(t: dict[str, Any]) -> int | None:
    """A target's stored edition; a hand-edited value that is no year reads as not set."""
    try:
        return year_of(t.get("edition"))
    except ValueError:
        return None


def round_key(t: dict[str, Any]) -> str | None:
    """The identity of a round (a ccfddl timeline entry): its paper deadline's UTC date, else its abstract's."""
    at = t.get("deadline") or t.get("abstract")
    return str(at)[:10] if at else None


def stamp(value: Any) -> datetime | None:
    """A stored timestamp (Store writes naive local time) as an aware datetime."""
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


def _deadlines(e: dict[str, Any]) -> list[tuple[datetime, int]]:
    out = []
    for ed in e["editions"]:
        if isinstance(ed.get("year"), int):
            for t in ed["timeline"]:
                out += [(datetime.fromisoformat(t[k]), ed["year"]) for k in ("abstract", "deadline") if t.get(k)]
    return out


def infer(e: dict[str, Any], closed: bool, anchor: datetime, now: datetime) -> int | None:
    """The edition for a target with none: the one sent to (closed, by `anchor`), else the nearest upcoming."""
    dls = _deadlines(e)
    if closed:
        sent = [d for d in dls if d[0] <= anchor + EARLY]
        if sent:
            return max(sent)[1]
    ahead = [d for d in dls if d[0] >= now]
    return min(ahead)[1] if ahead else None


def resolve(t: dict[str, Any], e: dict[str, Any] | None, now: datetime | None = None) -> tuple[int | None, str | None]:
    """(edition, how) of a target: how is 'set' (stored), 'nearest' or 'status' (worked out), None (unknown)."""
    year = stored_year(t)
    if year:
        return year, "set"
    if not e:
        return None, None
    now = now or datetime.now(timezone.utc)
    closed = t.get("status") in CLOSED
    year = infer(e, closed, stamp(t.get("updated_at") or t.get("created_at")) or now, now)
    return (year, "status" if closed else "nearest") if year else (None, None)


def match_round(timeline: list[dict[str, Any]], key: str | None) -> int | None:
    """The index of the round a stored `deadline` names: the one with a deadline on that date, else the
    nearest within TOLERANCE (a moved deadline); None when there is none."""
    try:
        want = date.fromisoformat(key or "")
    except ValueError:
        return None
    best = None
    for i, t in enumerate(timeline):
        gaps = [abs((datetime.fromisoformat(t[k]).date() - want).days) for k in ("deadline", "abstract") if t.get(k)]
        if gaps and (best is None or min(gaps) < best[0]):
            best = (min(gaps), i)
    return best[1] if best and best[0] <= TOLERANCE.days else None


def covered(e: dict[str, Any], year: int | None, key: str | None) -> set[tuple[int, int]]:
    """(edition index, round index) pairs of `e` that a target for `year` aims at: every round of the
    edition, or just the round `key` names (every round when it names none)."""
    out: set[tuple[int, int]] = set()
    for ei, ed in enumerate(e["editions"]):
        if year is None or ed.get("year") != year:
            continue
        only = match_round(ed["timeline"], key)
        out |= {(ei, ti) for ti in range(len(ed["timeline"])) if only is None or only == ti}
    return out


def state(e: dict[str, Any], cov: set[tuple[int, int]], now: datetime) -> dict[str, Any]:
    """Where the covered deadlines stand: 'upcoming' (with the next one's `next_at`), 'past' or 'tbd'."""
    ats = sorted(datetime.fromisoformat(t[k]) for ei, ti in cov for t in [e["editions"][ei]["timeline"][ti]]
                 for k in ("abstract", "deadline") if t.get(k))
    ahead = [a for a in ats if a >= now]
    if ahead:
        return {"state": "upcoming", "next_at": ahead[0].isoformat()}
    return {"state": "past" if ats else "tbd"}


def options(e: dict[str, Any], now: datetime | None = None, keep: int = 5) -> dict[str, Any]:
    """What a target of this venue can aim at: the last `keep` editions, each with its dated rounds."""
    now = now or datetime.now(timezone.utc)
    years: dict[int, dict[str, Any]] = {}
    for ed in e["editions"]:
        if not isinstance(ed.get("year"), int):
            continue
        cur = years.setdefault(ed["year"], {"year": ed["year"], "link": ed.get("link"), "date": ed.get("date"),
                                            "place": ed.get("place"), "timezone": ed.get("timezone"), "rounds": []})
        for t in ed["timeline"]:
            key = round_key(t)
            if key and all(r["key"] != key for r in cur["rounds"]):
                ats = [datetime.fromisoformat(t[k]) for k in ("abstract", "deadline") if t.get(k)]
                cur["rounds"].append({"key": key, "abstract": t.get("abstract"), "deadline": t.get("deadline"),
                                      "comment": t.get("comment"), "past": all(a < now for a in ats)})
    out = sorted(years.values(), key=lambda y: y["year"])[-keep:]
    for y in out:
        y["rounds"].sort(key=lambda r: r["key"])
        y["past"] = bool(y["rounds"]) and all(r["past"] for r in y["rounds"])
    return {"venue": e["key"], "acronym": e["acronym"], "default": infer(e, False, now, now), "editions": out}

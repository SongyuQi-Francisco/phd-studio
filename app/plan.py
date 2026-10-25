"""Planning data: watched venues, submission targets, milestones; plus the idea inbox.

plan.yaml (vault):  watched   {id, venue}
                    targets   {id, title, line, venue, edition, deadline, alternatives[], status, notes}
                              (edition = the conference year, deadline = one round of it; see editions.py;
                              several targets per venue, and per edition, are fine)
                    milestones{id, title, date, line, kind, status, notes}
                    follows   {id, kind: keyword|person, value, note}
ideas.yaml (vault): ideas     {id, text, line, paper, page, tags[], status, source, note,
                               merged_from[], merged_sources[], merged_into, premerge_status}
                    (merge / restore / tag operations live in idea_ops.py)
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from . import editions, venues
from .config import IDEAS_FILE, PLAN_FILE, PROFILE
from .store import Store, now as stamp_now

KINDS = ("milestone", "gate", "kill", "deadline")
M_STATUS = ("todo", "doing", "done", "warn")
T_STATUS = ("planning", "writing", "submitted", "accepted", "rejected", "dropped")
OPEN_STATUS = editions.OPEN      # planning / writing: the target's deadlines are reminders
CLOSED_STATUS = editions.CLOSED  # submitted / accepted: the rest of that edition is quiet for this paper
I_STATUS = ("new", "kept", "promoted", "dropped")  # what a user or the assistant can set
MERGED = "merged"                                  # only idea_ops.merge sets it (and restore clears it)
I_STATUS_ALL = (*I_STATUS, MERGED)
TAG_MAX = 30

plan_store = Store(PLAN_FILE, ("watched", "targets", "milestones", "follows"),
                   "# Planning data, managed by PhD Studio (studio milestone / studio target / the app).\n")
idea_store = Store(IDEAS_FILE, ("ideas",),
                   "# Idea inbox, managed by PhD Studio (studio idea \"...\" / ⌘I in the app).\n")


def _check(value: str | None, allowed: tuple[str, ...], name: str) -> None:
    if value is not None and value not in allowed:
        raise ValueError(f"{name} must be one of {allowed}")


def _date(value: str | None) -> None:
    if value:
        date.fromisoformat(value)


# ---------- watched venues ----------

def watched_keys() -> list[str]:
    keys = [w["venue"] for w in plan_store.load()["watched"]]
    if not keys and not plan_store.load(include_deleted=True)["watched"]:  # first run only: start from the profile's list
        for v in PROFILE.get("venues", []):
            if venues.catalog().get(venues._key(v)):
                plan_store.add("watched", {"venue": venues._key(v)}, "w")
        keys = [w["venue"] for w in plan_store.load()["watched"]]
    return keys


def watch(venue: str, on: bool = True) -> dict[str, Any]:
    k = venues._key(venue)
    if not venues.catalog().get(k):
        raise ValueError(f"unknown venue {venue}")
    current = {w["venue"]: w for w in plan_store.load()["watched"]}
    if on and k not in current:
        plan_store.add("watched", {"venue": k}, "w")
    elif not on and k in current:
        plan_store.delete("watched", current[k]["id"])
    return {"venue": k, "watched": on}


# ---------- targets & milestones ----------

def _round(value: Any) -> str | None:
    """A `deadline` value as a YYYY-MM-DD date (an ISO time is cut to its date); None clears it."""
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        raise ValueError("deadline must be a date like 2027-02-08") from None


def _check_round(e: dict[str, Any], year: int | None, key: str) -> None:
    """A round must exist in the edition when the catalog has that edition; an edition it does not know
    yet (not announced) takes any date."""
    if year is None:
        raise ValueError("deadline needs an edition: give edition too")
    eds = [ed for ed in e["editions"] if ed.get("year") == year]
    if eds and all(editions.match_round(ed["timeline"], key) is None for ed in eds):
        rounds = next((y["rounds"] for y in editions.options(e, keep=99)["editions"] if y["year"] == year), [])
        raise ValueError(f"{e['acronym']} {year} has no deadline near {key}"
                         + (" (rounds: " + ", ".join(r["key"] for r in rounds) + ")" if rounds else ""))


def add_target(title: str, venue: str, line: str | None = None, alternatives: list[str] | None = None,
               status: str = "planning", notes: str | None = None, edition: int | None = None,
               deadline: str | None = None) -> dict[str, Any]:
    """A submission target. Without an edition it aims at the one the status implies (the nearest upcoming
    one for a paper still being written), and that is saved with it."""
    _check(status, T_STATUS, "status")
    k = venues._key(venue)
    e = venues.catalog().get(k)
    if not e:
        raise ValueError(f"unknown venue {venue}")
    year, key = editions.year_of(edition), _round(deadline)
    if year is None and status in (*OPEN_STATUS, *CLOSED_STATUS):
        year = editions.resolve({"status": status, "status_at": stamp_now()}, e)[0]
    if key:
        _check_round(e, year, key)
    alts = [venues._key(a) for a in alternatives or []]
    rec = plan_store.add("targets", {"title": title, "line": line, "venue": k, "edition": year, "deadline": key,
                                     "alternatives": alts, "status": status, "status_at": stamp_now(), "notes": notes}, "t")
    watch(k)
    for a in alts:
        if venues.catalog().get(a):
            watch(a)
    return rec


def edition_options(venue: str) -> dict[str, Any]:
    """The editions of a venue (last five, with their dated rounds) and the one a new target would get."""
    e = venues.catalog().get(venues._key(venue))
    if not e:
        raise ValueError(f"unknown venue {venue}")
    return editions.options(e)


def add_milestone(title: str, date_: str, line: str | None = None, kind: str = "milestone",
                  status: str = "todo", notes: str | None = None) -> dict[str, Any]:
    _check(kind, KINDS, "kind")
    _check(status, M_STATUS, "status")
    _date(date_)
    return plan_store.add("milestones", {"title": title, "date": date_, "line": line, "kind": kind,
                                         "status": status, "notes": notes}, "m")


def milestones() -> list[dict[str, Any]]:
    """Live milestones, soonest first."""
    return sorted(plan_store.load()["milestones"], key=lambda m: (m.get("date", ""), m.get("title", "")))


def find_milestone(key: str, prefer_open: bool = False) -> dict[str, Any]:
    """A milestone by id, title (case-insensitive) or a part of its title; it must match exactly one.

    With prefer_open, finished milestones only count when no open one matches at that step.
    """
    key = key.strip()
    if not key:
        raise ValueError("milestone title or id is required")
    folded = key.casefold()
    ms = milestones()
    for matches in (lambda m: m["id"] == key, lambda m: (m.get("title") or "").casefold() == folded,
                    lambda m: folded in (m.get("title") or "").casefold()):
        hits = [m for m in ms if matches(m)]
        if prefer_open and any(m.get("status") != "done" for m in hits):
            hits = [m for m in hits if m.get("status") != "done"]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise ValueError(f"'{key}' matches {len(hits)} milestones, use the id: "
                             + "; ".join(f"{m['id']} {m.get('date', '')} {m.get('title', '')}" for m in hits))
    raise ValueError(f"no milestone matches '{key}'")


def update(collection: str, rid: str, patch: dict[str, Any]) -> dict[str, Any]:
    if collection == "milestones":
        _check(patch.get("kind"), KINDS, "kind")
        _check(patch.get("status"), M_STATUS, "status")
        _date(patch.get("date"))
    if collection == "targets":
        _check(patch.get("status"), T_STATUS, "status")
        patch = _target_patch(rid, patch)
    return plan_store.update(collection, rid, patch)


def _target_patch(rid: str, patch: dict[str, Any]) -> dict[str, Any]:
    """A target edit with its edition kept consistent.

    - A changed venue or edition drops the old round; a rejected target taken up again starts a new
      attempt, so it forgets its edition and round and follows the calendar.
    - A target that has no edition yet gets the one it showed before this edit, whatever the edit is (an
      edition worked out from the calendar moves with it, so it is saved the first time the user touches
      the target). A paper marked submitted just after a deadline went by was sent to that edition.
    - The status change time (`status_at`) is kept, so what a sent paper's edition is read from does not
      move when something else about it is edited.
    - A round is checked only when this edit sets it: a stored one that has since moved or gone must not
      stop the target from being edited (reads fall back to the whole edition).
    """
    patch = dict(patch)
    cur = next((t for t in plan_store.load()["targets"] if t["id"] == rid), None)
    if cur is None:
        raise KeyError(rid)
    venue = patch.get("venue", cur["venue"])
    moved = "venue" in patch and venues._key(venue) != cur["venue"]
    if "venue" in patch:
        venue = patch["venue"] = venues._key(venue)
        if moved and not venues.catalog().get(venue):
            raise ValueError(f"unknown venue {venue}")
    auto = "edition" in patch  # an explicit edition, or an explicit 'automatic' (0 / empty): not ours to fill in
    if auto:
        patch["edition"] = editions.year_of(patch["edition"])
    elif moved:
        patch["edition"] = None
    if "deadline" in patch:
        patch["deadline"] = _round(patch["deadline"])
    elif moved or ("edition" in patch and patch["edition"] != editions.stored_year(cur)):
        patch["deadline"] = None
    was, status = cur.get("status", "planning"), patch.get("status", cur.get("status", "planning"))
    if status != was:
        patch["status_at"] = stamp_now()
    elif status in CLOSED_STATUS and not cur.get("status_at"):  # an older record: keep the moment it was read from
        patch["status_at"] = cur.get("updated_at") or cur.get("created_at")
    revived = was == "rejected" and status in OPEN_STATUS and "edition" not in patch
    if revived:
        patch["edition"] = patch["deadline"] = None
    new = {**cur, **patch}
    e = venues.catalog().get(venue)
    if e and status in (*OPEN_STATUS, *CLOSED_STATUS) and not new.get("edition") and not (auto and not revived):
        now = datetime.now(timezone.utc)
        year = editions.just_passed(e, now) if was in OPEN_STATUS and status in CLOSED_STATUS else None
        if not year:
            year = editions.resolve({**new, "status_at": stamp_now()} if moved or revived else cur, e, now)[0]
        if year:
            patch["edition"] = new["edition"] = year
    if patch.get("deadline"):
        _check_round(e or {"editions": [], "acronym": venue}, editions.stored_year(new), patch["deadline"])
    return patch


def delete(collection: str, rid: str) -> None:
    plan_store.delete(collection, rid)


# ---------- ideas ----------

def norm_tags(tags: Any, strict: bool = True) -> list[str]:
    """Clean free-form tags: split on commas, drop a leading #, collapse spaces, de-duplicate ignoring case.

    strict (for what a user or the assistant types) refuses a tag over TAG_MAX characters; reading tags that
    are already stored never fails on one, since older ideas were saved as given.
    """
    if isinstance(tags, str):
        tags = [tags]
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        for part in re.split(r"[,，、;；]", str(raw)):
            t = " ".join(part.split()).lstrip("#＃").strip()
            if not t or t.casefold() in seen:
                continue
            if strict and len(t) > TAG_MAX:
                raise ValueError(f"标签太长（最多 {TAG_MAX} 个字符）：{t[:12]}…")
            seen.add(t.casefold())
            out.append(t)
    return out


def add_idea(text: str, line: str | None = None, paper: str | None = None, page: int | None = None,
             tags: list[str] | None = None, source: str = "self") -> dict[str, Any]:
    if not text.strip():
        raise ValueError("idea text is empty")
    return idea_store.add("ideas", {"text": text.strip(), "line": line, "paper": paper, "page": page,
                                    "tags": norm_tags(tags), "status": "new", "source": source}, "i")


def ideas(include_merged: bool = False) -> list[dict[str, Any]]:
    """Live ideas, newest first; the ones merged into another idea only when asked (see idea_ops.listing)."""
    return sorted((i for i in idea_store.load()["ideas"] if include_merged or i.get("status") != MERGED),
                  key=lambda i: i.get("created_at", ""), reverse=True)


# ---------- the combined timeline ----------

def _aims(t: dict[str, Any], cat: dict[str, Any], now: datetime) -> tuple[int | None, set[tuple[int, int]], dict[str, Any]]:
    """(edition, the venue rounds it covers, the target as shown): the shown copy has `edition` and
    `edition_from` (set / nearest / status, see editions.py), `edition_auto` (the edition the automatic
    reading gives, whatever is stored) and where its deadlines stand (`state`, with `next_at`)."""
    e = cat.get(t["venue"])
    year, how = editions.resolve(t, e, now)
    cov = editions.covered(e, year, t.get("deadline")) if e else set()
    auto = editions.resolve({k: v for k, v in t.items() if k != "edition"}, e, now)[0]  # what 'automatic' would give
    return year, cov, {**t, "edition": year, "edition_from": how, "edition_auto": auto,
                       **(editions.state(e, cov, now) if e else {"state": "tbd"})}


def targets(include_ended: bool = False) -> list[dict[str, Any]]:
    """The targets as shown in the app, in the order saved; rejected and dropped ones only when asked."""
    now, cat = datetime.now(timezone.utc), venues.catalog()
    return [_aims(t, cat, now)[2] for t in plan_store.load()["targets"]
            if include_ended or t.get("status", "planning") not in ("dropped", "rejected")]


def timeline(days_back: int = 14, days_ahead: int = 400) -> dict[str, Any]:
    """Every dated event: watched-venue deadlines, target deadlines, milestones.

    A deadline belongs to a target when it is in the edition the target aims at (and in its round, if it
    names one): such an event is type "target", with `targets` (all of those still being prepared) and
    `target` (the first). The deadlines of an edition that only has sent papers (submitted / accepted) stay
    on the calendar as "venue" events with `closed` listing those papers and `muted` set: they need no
    reminder. A target's `edition` is the stored one or, for a target saved without one, the one worked
    out now (`edition_from` says which); nothing is written.
    """
    now = datetime.now(timezone.utc)
    data = plan_store.load()
    targets = [t for t in data["targets"] if t.get("status", "planning") not in ("dropped", "rejected")]
    cat = venues.catalog()
    aim: dict[str, tuple[int | None, set[tuple[int, int]]]] = {}
    shown = []
    for t in targets:
        year, cov, view = _aims(t, cat, now)
        aim[t["id"]] = (year, cov)
        shown.append(view)
    events: list[dict[str, Any]] = []
    for k in watched_keys():
        e = cat.get(k)
        if not e:
            continue
        mine = [t for t in targets if t["venue"] == k]
        for ei, ed in enumerate(e["editions"]):
            for ti, t in enumerate(ed["timeline"]):
                opened = [x for x in mine if x.get("status", "planning") in OPEN_STATUS and (ei, ti) in aim[x["id"]][1]]
                closed = [x for x in mine if x.get("status") in CLOSED_STATUS and aim[x["id"]][0] == ed.get("year")]
                for kind in ("abstract", "deadline"):
                    if not t.get(kind):
                        continue
                    at = datetime.fromisoformat(t[kind])
                    delta = (at - now).days
                    if -days_back <= delta <= days_ahead:
                        compact = [{"id": x["id"], "title": x["title"], "line": x.get("line")} for x in opened]
                        events.append({
                            "type": "target" if opened else "venue", "venue": k, "acronym": e["acronym"],
                            "edition": ed.get("year"),
                            "title": f"{e['acronym']} {ed['year']} "
                                     + (f"{(t.get('comment') or '').replace('（官网）', '')} " if "官网" in (t.get("comment") or "") else "")
                                     + f"{'摘要' if kind == 'abstract' else '全文'}截止",
                            "at": t[kind], "days": delta, "core": e["core"], "ccf": e["ccf"],
                            "target": compact[0] if compact else None, "targets": compact,
                            "closed": [{"id": x["id"], "title": x["title"], "line": x.get("line"), "status": x["status"]}
                                       for x in closed],
                            "muted": bool(closed) and not opened,
                            "link": ed.get("link"), "comment": t.get("comment"), "stage": kind,
                        })
    for m in data["milestones"]:
        at = datetime.fromisoformat(m["date"] + "T23:59:00").astimezone(timezone.utc)
        delta = (at - now).days
        if -days_back <= delta <= days_ahead or m.get("status") not in ("done",):
            events.append({"type": "milestone", "id": m["id"], "title": m["title"], "at": at.isoformat(),
                           "days": delta, "kind": m.get("kind"), "status": m.get("status"),
                           "line": m.get("line"), "notes": m.get("notes")})
    events.sort(key=lambda e: e["at"])
    return {"events": events, "targets": shown, "milestones": data["milestones"],
            "watched": watched_keys()}

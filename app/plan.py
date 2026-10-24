"""Planning data: watched venues, submission targets, milestones; plus the idea inbox.

plan.yaml (vault):  watched   {id, venue}
                    targets   {id, title, line, venue, alternatives[], status, notes}
                    milestones{id, title, date, line, kind, status, notes}
                    follows   {id, kind: keyword|person, value, note}
ideas.yaml (vault): ideas     {id, text, line, paper, page, tags[], status}
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from . import venues
from .config import IDEAS_FILE, PLAN_FILE, PROFILE
from .store import Store

KINDS = ("milestone", "gate", "kill", "deadline")
M_STATUS = ("todo", "doing", "done", "warn")
T_STATUS = ("planning", "writing", "submitted", "accepted", "rejected", "dropped")
I_STATUS = ("new", "kept", "promoted", "dropped")

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

def add_target(title: str, venue: str, line: str | None = None, alternatives: list[str] | None = None,
               status: str = "planning", notes: str | None = None) -> dict[str, Any]:
    _check(status, T_STATUS, "status")
    k = venues._key(venue)
    if not venues.catalog().get(k):
        raise ValueError(f"unknown venue {venue}")
    alts = [venues._key(a) for a in alternatives or []]
    rec = plan_store.add("targets", {"title": title, "line": line, "venue": k, "alternatives": alts,
                                     "status": status, "notes": notes}, "t")
    watch(k)
    for a in alts:
        if venues.catalog().get(a):
            watch(a)
    return rec


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
        if "venue" in patch:
            patch["venue"] = venues._key(patch["venue"])
    return plan_store.update(collection, rid, patch)


def delete(collection: str, rid: str) -> None:
    plan_store.delete(collection, rid)


# ---------- ideas ----------

def add_idea(text: str, line: str | None = None, paper: str | None = None, page: int | None = None,
             tags: list[str] | None = None, source: str = "self") -> dict[str, Any]:
    if not text.strip():
        raise ValueError("idea text is empty")
    return idea_store.add("ideas", {"text": text.strip(), "line": line, "paper": paper, "page": page,
                                    "tags": tags or [], "status": "new", "source": source}, "i")


def ideas() -> list[dict[str, Any]]:
    return sorted(idea_store.load()["ideas"], key=lambda i: i.get("created_at", ""), reverse=True)


# ---------- the combined timeline ----------

def timeline(days_back: int = 14, days_ahead: int = 400) -> dict[str, Any]:
    """Every dated event: watched-venue deadlines, target deadlines, milestones."""
    now = datetime.now(timezone.utc)
    data = plan_store.load()
    targets = [t for t in data["targets"] if t.get("status") not in ("dropped", "rejected")]
    target_venues = {t["venue"]: t for t in targets}
    events: list[dict[str, Any]] = []
    cat = venues.catalog()
    for k in watched_keys():
        e = cat.get(k)
        if not e:
            continue
        for ed in e["editions"]:
            for t in ed["timeline"]:
                for kind in ("abstract", "deadline"):
                    if not t.get(kind):
                        continue
                    at = datetime.fromisoformat(t[kind])
                    delta = (at - now).days
                    if -days_back <= delta <= days_ahead:
                        tgt = target_venues.get(k)
                        events.append({
                            "type": "target" if tgt else "venue", "venue": k, "acronym": e["acronym"],
                            "title": f"{e['acronym']} {ed['year']} "
                                     + (f"{(t.get('comment') or '').replace('（官网）', '')} " if "官网" in (t.get("comment") or "") else "")
                                     + f"{'摘要' if kind == 'abstract' else '全文'}截止",
                            "at": t[kind], "days": delta, "core": e["core"], "ccf": e["ccf"],
                            "target": tgt and {"id": tgt["id"], "title": tgt["title"], "line": tgt.get("line")},
                            "link": ed.get("link"), "comment": t.get("comment"),
                        })
    for m in data["milestones"]:
        at = datetime.fromisoformat(m["date"] + "T23:59:00").astimezone(timezone.utc)
        delta = (at - now).days
        if -days_back <= delta <= days_ahead or m.get("status") not in ("done",):
            events.append({"type": "milestone", "id": m["id"], "title": m["title"], "at": at.isoformat(),
                           "days": delta, "kind": m.get("kind"), "status": m.get("status"),
                           "line": m.get("line"), "notes": m.get("notes")})
    events.sort(key=lambda e: e["at"])
    return {"events": events, "targets": targets, "milestones": data["milestones"],
            "watched": watched_keys()}

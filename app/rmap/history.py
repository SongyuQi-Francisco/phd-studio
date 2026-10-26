"""Time travel over research maps (#46): a map as it was at any time, rebuilt from its change log; what
changed between two times; the weekly frames of the time scrubber; the all-projects map (metro lines,
stations, interchanges); and the changes since a research line's last advisor packet.

Replay walks the log in (hlc, id) order with the merge rules of records.py: a field holds its latest
write; a delete is a write too; a record written after its delete is live again, and the edges and
attachments that a node delete took along follow that node back (`deleted_by`, told from the batch the
delete was logged in). Records the log does not know (hand edits, maps older than the log) appear at
their `created_at` with the values they had before their logged updates, and go at their delete stamp.

Times (a date, a week) count by when a change was made, the event's `at` on the Mac that made it; the
order of the changes is the log's (hlc, id). "The map on 10-05" is every change made by then, merged.
Nothing here writes: it reads the map and its log (docs/research-map.md, "Events").
"""
from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Any

from .. import i18n
from . import model, project, registry
from .clock import from_iso, parse
from .records import clocks

META = ("created_at", "updated_at", "deleted", "deleted_by", "clock", "cycle")
KEY_TYPES = ("hypothesis", "subline", "experiment", "result")  # a validation of one of these is a station
KILL = {"active": "kill_partial", "blocked": "kill"}  # a Kill criterion partly triggered / triggered (app/rmap/lines.py)
MAX_WEEKS = 260   # five years of weekly frames
MAX_EVENTS = 300  # events listed in a diff (the counts cover all of them)


# ---- time ----

def ms_of(stamp: str) -> int:
    p = parse(stamp or "")
    return p[0] if p else 0


def _day_ms(d: date) -> int:
    return int(datetime.combine(d, dtime()).timestamp() * 1000)


def _at_ms(ev: dict[str, Any]) -> int:
    """When a change was made (its `at`, local time), else its clock stamp."""
    try:
        return int(datetime.fromisoformat(str(ev["at"])).timestamp() * 1000)
    except (KeyError, TypeError, ValueError):
        return ms_of(ev.get("hlc", ""))


@dataclass(frozen=True)
class Bound:
    """Where replay stops: changes made before a time (`ms`, by their `at`), or the log up to and including
    one event (`key`: its cursor)."""
    ms: int | None = None
    key: tuple[str, str] | None = None

    def has(self, ev: dict[str, Any]) -> bool:
        if self.key is not None:
            return (ev["hlc"], ev["id"]) <= self.key
        return ev["_t"] < self.ms


def bound(text: str | None, *, end: bool = False) -> Bound | None:
    """A replay bound: a cursor ("<hlc>|<id>"), a clock stamp, a local ISO time, or a date: its start, or with
    end=True its end (the start of the next day). None: no bound."""
    if not text:
        return None
    text = str(text).strip()
    if "|" in text and parse(text.split("|", 1)[0]):
        h, i = text.split("|", 1)
        return Bound(key=(h, i))
    if parse(text):
        return Bound(key=(text, ""))
    try:
        if len(text) == 10:
            d = date.fromisoformat(text)
            return Bound(ms=_day_ms(d + timedelta(days=1) if end else d))
        return Bound(ms=int(datetime.fromisoformat(text).timestamp() * 1000))
    except ValueError:
        raise ValueError(i18n.t("rmap_history.bad_time", text=text))


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weeks(first_ms: int, today: date | None = None) -> list[dict[str, Any]]:
    """Monday-to-Sunday weeks from the one holding first_ms to this week: W1 … Wn (at most MAX_WEEKS)."""
    today = today or date.today()
    last = _monday(today)
    start = _monday(datetime.fromtimestamp(first_ms / 1000).date()) if first_ms else last
    start = min(max(start, last - timedelta(weeks=MAX_WEEKS - 1)), last)
    out, d = [], start
    while d <= last:
        out.append({"i": len(out), "label": f"W{len(out) + 1}", "start": d.isoformat(),
                    "end": (d + timedelta(days=6)).isoformat(), "bound": _day_ms(d + timedelta(days=7))})
        d += timedelta(weeks=1)
    return out


# ---- replay ----

def _batch(ev: dict[str, Any]) -> tuple[str, str, str]:
    return (ev.get("device") or "", ev.get("at") or "", ev.get("group") or "")


def _patch(rec: dict[str, Any], changes: dict[str, Any] | None, side: int = 1) -> None:
    for f, pair in (changes or {}).items():
        v = pair[side] if isinstance(pair, list) and len(pair) == 2 else (pair if side else None)
        if v is None:
            rec.pop(f, None)
        else:
            rec[f] = copy.deepcopy(v)


def _clean(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


class State:
    """The records of one map as the log has built them so far."""

    def __init__(self, tl: Timeline):
        self.tl = tl
        self.project: dict[str, Any] = {}
        self.recs: dict[str, dict[str, dict[str, Any]]] = {"node": {}, "edge": {}}

    def apply(self, ev: dict[str, Any]) -> None:
        kind, action = ev.get("kind"), ev.get("action")
        if kind in (None, "log"):
            return
        if kind == "project":
            if action == "add":
                self.project = {"id": project.PROJECT_ID, **copy.deepcopy(ev.get("record") or {})}
            else:
                _patch(self.project, ev.get("changes"))
            return
        book = self.recs.setdefault(kind, {})
        for rid in (ev.get("targets") or []) if ev.get("count") else [ev.get("target")]:
            if not rid:
                continue
            rec = book.get(rid)
            if action == "add" or rec is None:  # an update or delete whose add the log lacks starts from what is known
                data = ev.get("record") if action == "add" and not ev.get("count") else self.tl.current.get((kind, rid), {})
                rec = book[rid] = {**copy.deepcopy(data or {}), "id": rid, "_born": ev["hlc"],
                                   "created_at": self.tl.created.get((kind, rid)) or ev.get("at")}
            if action == "update":
                if not ev.get("count"):
                    _patch(rec, ev.get("changes"))
                rec.pop("_deleted", None)  # written after a delete: live again
                rec.pop("_by", None)
            elif action == "delete":
                rec["_deleted"], rec["_by"] = True, ev.get("_by")
            rec["updated_at"] = ev.get("at") or rec.get("updated_at")

    def node_ids(self) -> set[str]:
        return {i for i, r in self.recs["node"].items() if not r.get("_deleted")}

    def snapshot(self) -> dict[str, Any]:
        """The map at this point, shaped like GET /api/rmap/projects/{slug} (cycles marked, no warnings)."""
        live = self.node_ids()

        def child(r: dict[str, Any]) -> bool:
            return not r.get("_deleted") or (bool(r.get("_by")) and r["_by"] in live)

        nodes = [_clean(r) for i, r in self.recs["node"].items() if i in live]
        edges = [r for r in self.recs["edge"].values() if child(r) and r.get("from") in live and r.get("to") in live]
        cycle = project.back_edges([{**e, "clock": {"from": e["_born"]}} for e in edges])
        edges = [{**_clean(e), **({"cycle": True} if e["id"] in cycle else {})} for e in edges]
        kinds = [*registry.kinds(), *(k for k in self.recs if k not in ("node", "edge") and k not in registry.kinds())]
        atts = {k: [_clean(a) for a in self.recs.get(k, {}).values() if child(a) and a.get("node") in live] for k in kinds}
        warnings = [{"kind": "cycle", "edges": sorted(cycle)}] if cycle else []
        return {"slug": self.tl.slug, "project": _clean(self.project) or self.tl.live["project"], "nodes": nodes,
                "edges": edges, "attachments": atts, "warnings": warnings}


class Timeline:
    """One map's log in replay order, with stand-in events for the records the log does not know."""

    def __init__(self, slug: str):
        p = project.get(slug)
        self.slug = slug
        self.live = p.read(include_deleted=True)
        self.stamp = self.live["stamp"]
        events = p.events(limit=10 ** 9)["events"]
        events.reverse()
        live_recs = [("node", r) for r in self.live["nodes"]] + [("edge", r) for r in self.live["edges"]]
        live_recs += [(k, r) for k, recs in self.live["attachments"].items() for r in recs]
        self.current = {(k, r["id"]): {f: v for f, v in r.items() if f not in META} for k, r in live_recs}
        self.created = {(k, r["id"]): r.get("created_at") for k, r in live_recs if r.get("created_at")}

        # cascaded deletes: an edge or attachment deleted in the same batch as one of its nodes follows that node
        dropped: dict[tuple[str, str, str], set[str]] = {}
        for ev in events:
            if ev.get("kind") == "node" and ev.get("action") == "delete":
                dropped.setdefault(_batch(ev), set()).add(ev.get("target"))
        added, deleted, updates = set(), set(), {}
        for ev in events:
            kind, action = ev.get("kind"), ev.get("action")
            targets = (ev.get("targets") or []) if ev.get("count") else [ev.get("target")]
            if action == "delete" and kind not in ("node", "project", "log"):
                hit = [n for n in ev.get("nodes") or [] if n in dropped.get(_batch(ev), ())]
                if hit:
                    ev["_by"] = hit[0]
            for rid in targets:
                if action == "add":
                    added.add((kind, rid))
                elif action == "delete":
                    deleted.add((kind, rid))
                elif action == "update" and not ev.get("count"):
                    updates.setdefault((kind, rid), []).append(ev)

        extra = []
        if not any(ev.get("kind") == "project" and ev.get("action") == "add" for ev in events):
            proj = self.live["project"]
            stamp = from_iso(proj.get("created_at")) or (events[0]["hlc"] if events else "")
            if stamp:
                extra.append({"id": "~project", "hlc": stamp, "at": proj.get("created_at"), "kind": "project", "action": "add",
                              "target": project.PROJECT_ID, "synthetic": True,
                              "record": {k: v for k, v in proj.items() if k not in META}})
        for kind, r in live_recs:
            key = (kind, r["id"])
            if key in added:
                continue
            data = copy.deepcopy(self.current[key])
            for ev in reversed(updates.get(key, [])):  # the values it had before its logged updates
                _patch(data, ev.get("changes"), side=0)
            stamp = from_iso(r.get("created_at")) or min(clocks(r).values(), default="")
            if not stamp:
                continue
            extra.append({"id": "~" + r["id"], "hlc": stamp, "at": r.get("created_at"), "kind": kind, "action": "add",
                          "target": r["id"], "record": data, "synthetic": True})
            gone = clocks(r).get("deleted")
            if r.get("deleted") and gone and key not in deleted:
                extra.append({"id": "~~" + r["id"], "hlc": gone, "at": r.get("updated_at"), "kind": kind, "action": "delete",
                              "target": r["id"], "_by": r.get("deleted_by"), "synthetic": True})
        self.events = sorted(events + extra, key=lambda e: (e["hlc"], e["id"]))
        for ev in self.events:
            ev["_t"] = _at_ms(ev)
        self.first_ms = min((ev["_t"] for ev in self.events), default=0)

    def state(self, b: Bound | None = None) -> State:
        """Every change within the bound, applied in log order."""
        st = State(self)
        for ev in self.events:
            if b is None or b.has(ev):
                st.apply(ev)
        return st

    def at(self, b: Bound | None = None) -> dict[str, Any]:
        return self.state(b).snapshot()

    def between(self, lo: Bound | None, hi: Bound | None) -> list[dict[str, Any]]:
        """Logged changes after lo up to hi, in log order (stand-ins left out)."""
        return [ev for ev in self.events if not ev.get("synthetic")
                and (lo is None or not lo.has(ev)) and (hi is None or hi.has(ev))]


_cache: dict[str, Timeline] = {}
_lock = threading.Lock()


def timeline(slug: str) -> Timeline:
    """The replay of one map, rebuilt only when its files changed (the stamp of GET /projects/{slug})."""
    p = project.get(slug)
    hit = _cache.get(slug)
    if hit and hit.stamp == p.stamp():
        return hit
    tl = Timeline(slug)
    with _lock:
        _cache[slug] = tl
    return tl


# ---- what the page asks for ----

def map_at(slug: str, at: str | None = None) -> dict[str, Any]:
    """The map as it was at `at` (a date counts to its end), shaped like GET /projects/{slug}."""
    tl = timeline(slug)
    return {**tl.at(bound(at, end=True)), "at": at}


def _week_notes(evs: list[dict[str, Any]]) -> list[str]:
    return [ev["summary"] for ev in evs if ev.get("kind") == "log" and ev.get("summary")][:4]


def frames(slug: str, today: date | None = None) -> dict[str, Any]:
    """The time scrubber's data: the weeks W1 … Wn of this map, and for every record the states it had at the
    end of each week where it changed (`[week, record]`, record None: not on the map then). The last week
    ends now. The front end rebuilds the map of a week by taking each record's last state up to it."""
    tl = timeline(slug)
    ws = weeks(tl.first_ms, today)
    out: dict[str, Any] = {"project": [], "nodes": {}, "edges": {}, "attachments": {}}
    prev: dict[tuple[str, str], str] = {}
    st, done, last, top = State(tl), set(), None, ("", "")
    for w in ws:
        b = None if w is ws[-1] else Bound(ms=w["bound"])
        new = [ev for ev in tl.events if ev["id"] not in done and (b is None or b.has(ev))]
        if new and (new[0]["hlc"], new[0]["id"]) < top:  # made this week but ordered before what is in (a clock ahead): replay again
            st = tl.state(b)
        else:
            for ev in new:
                st.apply(ev)
        done.update(ev["id"] for ev in new)
        top = max([top, *((ev["hlc"], ev["id"]) for ev in new)])
        real = [ev for ev in new if not ev.get("synthetic")]
        snap = st.snapshot()
        w.update({"events": len(real), "nodes": len(snap["nodes"]), "notes": _week_notes(real),
                  "verified": sum(1 for n in snap["nodes"] if n.get("status") == "verified")})
        proj = json.dumps(snap["project"], sort_keys=True, ensure_ascii=False)
        if proj != last:
            out["project"].append([w["i"], snap["project"]])
            last = proj
        cur = {("nodes", r["id"]): r for r in snap["nodes"]} | {("edges", r["id"]): r for r in snap["edges"]}
        cur |= {(f"a:{k}", r["id"]): r for k, recs in snap["attachments"].items() for r in recs}
        sig = {key: json.dumps(r, sort_keys=True, ensure_ascii=False) for key, r in cur.items()}
        for key in [*sig, *(k for k in prev if k not in sig)]:  # first-seen order, the same on every read
            if sig.get(key) == prev.get(key):
                continue
            book = out["attachments"].setdefault(key[0][2:], {}) if key[0].startswith("a:") else out[key[0]]
            book.setdefault(key[1], []).append([w["i"], cur.get(key)])
        prev = sig
    return {"slug": slug, "stamp": tl.stamp, "weeks": [{k: v for k, v in w.items() if k != "bound"} for w in ws], **out}


def _row(n: dict[str, Any]) -> dict[str, Any]:
    return {"id": n["id"], "title": n.get("title"), "type": n.get("type"), "status": n.get("status") or "todo"}


def diff(slug: str, since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """What changed between the map at `since` (a date counts from its start; None: before the first
    change) and at `until` (a date counts to its end; None: now). Returns the map at `until` with the
    nodes and edges gone since drawn as ghosts (`gone: true`), a mark per changed node, the changes by
    kind, and the logged events in between (newest first, at most MAX_EVENTS; `total` counts them all)."""
    tl = timeline(slug)
    lo = bound(since) if since else None
    hi = bound(until, end=True) if until else None
    if lo is not None and hi is not None and lo.ms is not None and hi.ms is not None and lo.ms > hi.ms:  # the other way round
        lo, hi = bound(until), bound(since, end=True)
    a = tl.at(lo) if lo is not None else {"nodes": [], "edges": [], "attachments": {}}
    b = tl.at(hi)
    na, nb = {n["id"]: n for n in a["nodes"]}, {n["id"]: n for n in b["nodes"]}
    added = [_row(n) for i, n in nb.items() if i not in na]
    removed = [_row(n) for i, n in na.items() if i not in nb]
    status, dropped, retitled = [], [], []
    for i in na.keys() & nb.keys():
        sa, sb = na[i].get("status") or "todo", nb[i].get("status") or "todo"
        if sa != sb:
            (dropped if sb in model.FADED and sa not in model.FADED else status).append({**_row(nb[i]), "from": sa, "to": sb})
        if (na[i].get("title") or "") != (nb[i].get("title") or ""):
            retitled.append({**_row(nb[i]), "from": na[i].get("title")})
    order = {n["id"]: k for k, n in enumerate(b["nodes"])}
    for rows in (status, dropped, retitled):
        rows.sort(key=lambda r: order.get(r["id"], 0))
    marks: dict[str, dict[str, Any]] = {}
    for r in retitled:
        marks[r["id"]] = {"mark": "retitled", "title": r["from"]}
    for r in status:
        marks[r["id"]] = {"mark": "status", "from": r["from"], "to": r["to"]}
    for r in dropped:
        marks[r["id"]] = {"mark": "dropped", "from": r["from"], "to": r["to"]}
    for r in added:
        marks[r["id"]] = {"mark": "added"}
    for r in removed:
        marks[r["id"]] = {"mark": "removed"}

    ea, eb = {e["id"]: e for e in a["edges"]}, {e["id"]: e for e in b["edges"]}
    shown = set(nb) | set(na)
    ghosts = [{**{k: v for k, v in e.items() if k != "cycle"}, "gone": True} for i, e in ea.items() if i not in eb]
    edges = [{k: v for k, v in e.items() if k != "cycle"} for e in b["edges"]] + [g for g in ghosts if g["from"] in shown and g["to"] in shown]
    cycle = project.back_edges(edges)
    view = {**b, "nodes": b["nodes"] + [{**na[r["id"]], "gone": True} for r in removed],
            "edges": [{**e, **({"cycle": True} if e["id"] in cycle else {})} for e in edges]}

    names = {**{i: n.get("title") for i, n in na.items()}, **{i: n.get("title") for i, n in nb.items()}}
    attachments = {}
    for kind in sorted(set(a["attachments"]) | set(b["attachments"])):
        xa = {r["id"]: r for r in a["attachments"].get(kind, [])}
        xb = {r["id"]: r for r in b["attachments"].get(kind, [])}
        plus = [{"id": i, "node": r.get("node"), "title": names.get(r.get("node")), "what": r.get("title") or r.get("target")}
                for i, r in xb.items() if i not in xa]
        minus = [{"id": i, "node": r.get("node"), "title": names.get(r.get("node")), "what": r.get("title") or r.get("target")}
                 for i, r in xa.items() if i not in xb]
        if plus or minus:
            attachments[kind] = {"added": plus, "removed": minus}
    evs = tl.between(lo, hi)
    return {
        "slug": slug, "since": since, "until": until, "map": view, "marks": marks,
        "changes": {"added": added, "removed": removed, "status": status, "dropped": dropped, "retitled": retitled,
                    "edges": {"added": sum(1 for i in eb if i not in ea), "removed": len(ghosts)},
                    "attachments": attachments,
                    "notes": [{"at": ev.get("at"), "text": ev["summary"]} for ev in evs if ev.get("kind") == "log" and ev.get("summary")]},
        "events": [_clean(ev) for ev in reversed(evs[-MAX_EVENTS:])], "total": len(evs),
    }


# ---- the all-projects map ----

def stations(tl: Timeline) -> list[dict[str, Any]]:
    """Key validations and decisions of one map, one station per day: a hypothesis, sub-line, experiment or
    result validated; a decision taken; a node refuted or dropped; a Kill criterion partly triggered or triggered."""
    st = State(tl)
    days: dict[str, dict[str, Any]] = {}
    for ev in tl.events:
        st.apply(ev)
        if ev.get("kind") != "node" or ev.get("action") not in ("add", "update"):
            continue
        rec = st.recs["node"].get(ev.get("target")) or {}
        typ = rec.get("type")
        if ev["action"] == "add":
            s = rec.get("status") or "todo"
            what = ("decision" if typ == "decision" else "verified" if s == "verified" and typ in KEY_TYPES
                    else s if s in model.FADED else None)
        elif "status" in (ev.get("changes") or {}):
            s = ev["changes"]["status"][1]
            what = ("decision" if typ == "decision" and s == "verified" else "verified" if s == "verified" and typ in KEY_TYPES
                    else s if s in model.FADED else KILL.get(s) if typ == "kill" else None)
        else:
            what = None
        if not what:
            continue
        day = datetime.fromtimestamp(ev["_t"] / 1000).date().isoformat()
        stn = days.setdefault(day, {"day": day, "ms": ev["_t"], "items": []})
        stn["items"].append({"node": rec.get("id"), "title": rec.get("title"), "type": typ, "what": what, "status": rec.get("status")})
    for stn in days.values():
        stn["kind"] = "decision" if any(x["what"] != "verified" for x in stn["items"]) else "verified"
    return sorted(days.values(), key=lambda s: s["ms"])


def _entity_firsts(tl: Timeline) -> dict[str, dict[str, Any]]:
    """When each entity first showed up in this map (on a node or an attachment), and on which node."""
    out: dict[str, dict[str, Any]] = {}
    for ev in tl.events:
        ent = (ev.get("record") or {}).get("entity") if ev.get("action") == "add" else None
        if ev.get("action") == "update" and "entity" in (ev.get("changes") or {}):
            ent = ev["changes"]["entity"][1]
        if ent and ent not in out:
            node = ev.get("target") if ev.get("kind") == "node" else (ev.get("record") or {}).get("node") or (ev.get("nodes") or [None])[0]
            out[ent] = {"ms": ev["_t"], "at": ev.get("at"), "node": node}
    return out


def overview(today: date | None = None) -> dict[str, Any]:
    """Every map on one time axis: a line per project from its first change to now (or to its archiving),
    its stations, and interchanges where several projects point at the same entity ("dataset:…",
    "paper:…"): placed when the entity became shared, i.e. when the last of them took it up."""
    lines, firsts, titles = [], {}, {}
    now_ms = int(datetime.now().timestamp() * 1000)
    for p in project.listing(archived=True):
        tl = timeline(p["slug"])
        if not tl.events:
            continue
        end = now_ms
        if p.get("archived"):
            arch = [ev for ev in tl.events if ev.get("kind") == "project" and "archived" in (ev.get("changes") or {})]
            end = arch[-1]["_t"] if arch else max(ev["_t"] for ev in tl.events)
        now = tl.state().snapshot()
        titles[p["slug"]] = {n["id"]: n.get("title") for n in now["nodes"]}
        lines.append({"slug": p["slug"], "title": p.get("title"), "line": p.get("line"), "archived": bool(p.get("archived")),
                      "start": tl.first_ms, "end": end, "nodes": len(now["nodes"]),
                      "verified": sum(1 for n in now["nodes"] if n.get("status") == "verified"),
                      "stations": stations(tl)})
        firsts[p["slug"]] = _entity_firsts(tl)
    interchanges = []
    for ent, hits in sorted(project.entities().items()):
        slugs = sorted({h["slug"] for h in hits if h["slug"] in firsts})
        if len(slugs) < 2:
            continue
        stops = []
        for s in slugs:
            f = firsts[s].get(ent) or {}
            node = f.get("node") or next(h["node"] for h in hits if h["slug"] == s)
            stops.append({"slug": s, "ms": f.get("ms") or 0, "at": f.get("at"), "node": node, "title": titles[s].get(node)})
        interchanges.append({"entity": ent, "ms": max(x["ms"] for x in stops), "projects": stops})
    first = min((ln["start"] for ln in lines), default=0)
    return {"weeks": [{k: v for k, v in w.items() if k != "bound"} for w in weeks(first, today)],
            "now": now_ms, "lines": lines, "interchanges": interchanges}


# ---- advisor packets ----

def _line_of(m: dict[str, Any]) -> dict[str, Any]:
    p = m["project"]
    note = p.get("note") or ""
    name = p.get("line") or (note.rsplit("/", 1)[-1].removesuffix(".md") if note else "") or p.get("title") or ""
    return {"name": name, "line": p.get("line"), "path": note or None}


def last_packet(slug: str) -> dict[str, Any] | None:
    """The last advisor packet of this map's research line (path, name, date), if the vault has one."""
    from .. import line_chat
    try:
        return line_chat.previous_packet(_line_of(timeline(slug).live))
    except OSError:
        return None


def map_for_line(line: dict[str, Any]) -> str | None:
    """The slug of a research line's map (direction.lines item: by note path, configured line or name)."""
    from .lines import maps_by_line
    maps = maps_by_line()
    hit = maps.get(line.get("path") or "") or maps.get(line.get("line") or "") or maps.get(line.get("name") or "")
    return hit["slug"] if hit else None


def _q(text: Any) -> str:
    s = str(text or "?")
    return s if len(s) <= 60 else s[:58].rstrip() + "…"


def changes_text(d: dict[str, Any], lang: str) -> list[str]:
    """The changes of a diff as short lines for a prompt, in the given language."""
    def tr(key: str, **kw: Any) -> str:
        # i18n-keys: rmap_history.packet.{added,status,removed,retitled,attached,note,more}
        return i18n.tl(lang, "rmap_history." + key, **kw)

    def st(s: str) -> str:
        return i18n.tl(lang, "rmap.status." + s)  # i18n-keys: rmap.status.{todo,active,verified,blocked,refuted,dropped}

    def typ(k: str) -> str:
        return i18n.tl(lang, "rmap.type." + k)  # i18n-keys: rmap.type.{question,hypothesis,subline,experiment,result,decision,kill,open}

    c, out = d["changes"], []
    for r in c["added"][:15]:
        out.append(tr("packet.added", type=typ(r["type"]), title=_q(r["title"]), status=st(r["status"])))
    for r in c["status"] + c["dropped"]:
        out.append(tr("packet.status", title=_q(r["title"]), old=st(r["from"]), new=st(r["to"])))
    for r in c["removed"]:
        out.append(tr("packet.removed", title=_q(r["title"])))
    for r in c["retitled"]:
        out.append(tr("packet.retitled", old=_q(r["from"]), title=_q(r["title"])))
    for kind, x in c["attachments"].items():
        name = i18n.tl(lang, "rmap_history.kind." + kind) if kind == "evidence" else kind  # i18n-keys: rmap_history.kind.evidence
        if x["added"]:
            out.append(tr("packet.attached", kind=name, n=len(x["added"]), items="; ".join(f"{_q(a['title'])}: {_q(a['what'])}" for a in x["added"][:5])))
    for note in c["notes"][-12:]:
        out.append(tr("packet.note", day=(note["at"] or "")[:10], text=note["text"]))
    if len(c["added"]) > 15:
        out.append(tr("packet.more", n=len(c["added"]) - 15))
    return out


def packet_changes(line: dict[str, Any], lang: str = "zh") -> dict[str, Any] | None:
    """The map changes since a research line's last advisor packet (since its date, that day included; since
    the map began when there is none), for the 起草导师汇报包 prompt (app/line_chat.py). None: no map."""
    slug = map_for_line(line)
    if not slug:
        return None
    from .. import line_chat
    prev = line_chat.previous_packet(line)
    since = prev["date"] if prev and prev.get("date") else None
    d = diff(slug, since, None)
    return {"slug": slug, "title": d["map"]["project"].get("title"), "since": since, "packet": prev,
            "total": d["total"], "lines": changes_text(d, lang)}


__all__ = ["bound", "weeks", "timeline", "map_at", "frames", "diff", "stations", "overview", "last_packet",
           "map_for_line", "changes_text", "packet_changes"]

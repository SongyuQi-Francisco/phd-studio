"""One research map project = one folder in the vault, made of sync-safe stores.

    <research_maps>/<slug>/map.yaml          project (one record, id "project"), nodes, edges
    <research_maps>/<slug>/<kind>.yaml       attachments of one kind (evidence.yaml, ...)
    <research_maps>/<slug>/events/<YYYY-MM>.yaml   the change log, one file per month

Map and attachment records merge field by field (records.py); the log is append-only. Readers apply the
liveness rules of records.py, skip edges and attachments whose node is gone, and mark the flow edges that
close a cycle (two Macs can make one between them). Lock and temp files sit in the data dir under rmap/,
with this device's ingest log: the order in which this Mac first saw each event, local or synced.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from ..config import DATA_DIR, RMAP_DIR
from ..store import Store
from . import model, registry
from .clock import from_iso, parse
from .records import FieldStore, child_live, clocks, live_ids, own_live

HEADER = "# Research map, managed by PhD Studio (研究地图). Edit in the app; see docs/research-map.md.\n"
EVENTS_HEADER = "# Research map change log, managed by PhD Studio. One record per change; never edited.\n"
PROJECT_ID = "project"
LOCKS = DATA_DIR / "rmap"
INGEST = LOCKS / "ingest"


# ---- events: order and cursors ----

def event_hlc(ev: dict[str, Any], index: int = 0) -> str:
    """The event's stamp; events logged before stamps existed sort by `at` and their place in the file."""
    if parse(ev.get("hlc", "")):
        return ev["hlc"]
    base = parse(from_iso(ev.get("at")))
    return f"{base[0]:013d}.{min(index, 0xFFFF):04x}." if base else ""


def cursor(ev: dict[str, Any]) -> str:
    return f"{ev['hlc']}|{ev['id']}"


def _bound(text: str | None) -> tuple[str, str] | None:
    """A cursor ("<hlc>|<id>") or a date / local time ("2026-10-01", "2026-10-01T09:00")."""
    if not text:
        return None
    if "|" in text and parse(text.split("|", 1)[0]):
        h, i = text.split("|", 1)
        return (h, i)
    if parse(text):
        return (text, "")
    stamp = from_iso(text)
    if not stamp:
        raise ValueError(f"不认识的时间或游标：{text}")
    return (stamp, "")


def flow_order(e: dict[str, Any]) -> tuple[str, str]:
    c = clocks(e)
    return (c.get("from") or c.get("kind") or from_iso(e.get("created_at")), e["id"])


def back_edges(edges: list[dict[str, Any]]) -> set[str]:
    """Flow edges (refines, depends) that close a cycle, taken in creation order: the same on every Mac."""
    nxt: dict[str, list[str]] = {}
    out: set[str] = set()

    def reaches(a: str, goal: str) -> bool:
        seen, stack = set(), [a]
        while stack:
            cur = stack.pop()
            if cur == goal:
                return True
            if cur not in seen:
                seen.add(cur)
                stack.extend(nxt.get(cur, []))
        return False

    for e in sorted((e for e in edges if e.get("kind") in model.FLOW), key=flow_order):
        if e["from"] == e["to"] or reaches(e["to"], e["from"]):
            out.add(e["id"])
        else:
            nxt.setdefault(e["from"], []).append(e["to"])
    return out


def dag_edges(m: dict[str, Any]) -> list[dict[str, Any]]:
    """The map's flow edges without the ones marked as closing a cycle: what layouts and metrics walk."""
    return [e for e in m["edges"] if e.get("kind") in model.FLOW and not e.get("cycle")]


class Project:
    def __init__(self, slug: str):
        if not model.valid_slug(slug):
            raise KeyError(slug)
        self.slug = slug
        self.dir = RMAP_DIR / slug
        LOCKS.mkdir(parents=True, exist_ok=True)
        self.map = FieldStore(self.dir / "map.yaml", ("project", "nodes", "edges"), HEADER, name=f"rmap/{slug}.map")

    @property
    def exists(self) -> bool:
        return (self.dir / "map.yaml").exists()

    def kind_store(self, kind: registry.AttachmentKind) -> FieldStore:
        return FieldStore(self.dir / kind.filename, (kind.name,), HEADER, name=f"rmap/{self.slug}.{kind.name}")

    def event_store(self, month: str) -> Store:
        return Store(self.dir / "events" / f"{month}.yaml", ("events",), EVENTS_HEADER,
                     name=f"rmap/{self.slug}.events-{month}")

    def event_months(self) -> list[str]:
        """Months with a log file, newest first (conflict copies are merged by their store, not listed)."""
        d = self.dir / "events"
        if not d.is_dir():
            return []
        return sorted({p.stem for p in d.glob("*.yaml") if re.fullmatch(r"\d{4}-\d{2}", p.stem)}, reverse=True)

    # ---- reading ----
    def read(self, include_deleted: bool = False) -> dict[str, Any]:
        """The whole map: project record, nodes, edges, every registered attachment kind, and warnings."""
        self.ingest()
        data = copy.deepcopy(self.map.load(include_deleted=True))
        project = next((r for r in data["project"] if r["id"] == PROJECT_ID), None) or {"id": PROJECT_ID, "title": self.slug}
        live = live_ids(data["nodes"])
        nodes = [n for n in data["nodes"] if include_deleted or n["id"] in live]
        edges = [e for e in data["edges"] if include_deleted or
                 (child_live(e, live) and e.get("from") in live and e.get("to") in live)]
        cycle = back_edges([e for e in edges if child_live(e, live)])
        for e in edges:
            if e["id"] in cycle:
                e["cycle"] = True
        attachments = {}
        for name, kind in registry.kinds().items():
            recs = self.kind_store(kind).load(include_deleted=True)[name]
            attachments[name] = [r for r in recs if include_deleted or (child_live(r, live) and r.get("node") in live)]
        warnings = [{"kind": "cycle", "edges": sorted(cycle)}] if cycle else []
        return {"slug": self.slug, "project": project, "nodes": nodes, "edges": edges,
                "attachments": attachments, "warnings": warnings, "stamp": self.stamp(),
                "seq": self._ingest_state()["seq"]}

    def _month_events(self, month: str) -> list[dict[str, Any]]:
        rows = self.event_store(month).load()["events"]
        for i, ev in enumerate(rows):
            ev["hlc"] = event_hlc(ev, i)
        return rows

    def events(self, *, before: str | None = None, since: str | None = None, node: str | None = None,
               kind: str | None = None, limit: int = 100, after: int | None = None) -> dict[str, Any]:
        """The log, newest first, ordered by (hlc, id). before / since: a cursor or a date (exclusive).
        after: this Mac's ingest sequence, for live feeds (includes events synced late from an offline Mac).
        Returns {events, next (cursor for the next page, or None), seq (latest ingest sequence here)}."""
        self.ingest()
        state = self._ingest_state()
        lo, hi = _bound(since), _bound(before)
        rows: list[dict[str, Any]] = []
        for month in self.event_months():
            if after is None and lo and month < _month_of(lo[0]):
                break
            if after is None and hi and month > _month_of(hi[0]):
                continue
            for ev in self._month_events(month):
                key = (ev["hlc"], ev["id"])
                if hi and (key >= hi if hi[1] else key[0] >= hi[0]):  # a date compares on the stamp alone
                    continue
                if lo and (key <= lo if lo[1] else key[0] <= lo[0]):
                    continue
                if after is not None and state["ids"].get(ev["id"], 0) <= after:
                    continue
                if node and node not in (ev.get("nodes") or []):
                    continue
                if kind and ev.get("kind") != kind:
                    continue
                rows.append(ev)
        rows.sort(key=lambda e: (e["hlc"], e["id"]), reverse=True)
        page = rows[:limit]
        for ev in page:
            ev["seq"] = state["ids"].get(ev["id"], 0)
            ev["cursor"] = cursor(ev)
        return {"events": page, "next": page[-1]["cursor"] if len(rows) > limit else None, "seq": state["seq"]}

    def stamp(self) -> str:
        return stamp_of(self.dir)

    # ---- this device's ingest log ----
    def _ingest_path(self) -> Path:
        return INGEST / f"{self.slug}.json"

    def _ingest_state(self) -> dict[str, Any]:
        try:
            st = json.loads(self._ingest_path().read_text(encoding="utf-8"))
            if isinstance(st, dict) and isinstance(st.get("ids"), dict):
                return st
        except (OSError, json.JSONDecodeError):
            pass
        return {"seq": 0, "ids": {}, "files": {}, "new": True}

    def ingest(self) -> list[dict[str, Any]]:
        """Number the events this Mac has not seen yet (written here, or synced in) and return them, oldest
        first. The first look at a project only records what is there (nothing is returned)."""
        INGEST.mkdir(parents=True, exist_ok=True)
        with (LOCKS / f"{self.slug}.ingest.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            st = self._ingest_state()
            first = st.pop("new", False)
            d = self.dir / "events"
            changed = sorted({name.split(".sync-conflict-")[0].removesuffix(".yaml")
                              for name, sig in _files(d).items() if st["files"].get(name) != sig})
            if not changed and not first:
                return []
            new: list[dict[str, Any]] = []
            for month in changed:
                if not re.fullmatch(r"\d{4}-\d{2}", month):
                    continue
                for ev in self._month_events(month):
                    if ev["id"] not in st["ids"]:
                        new.append(ev)
            new.sort(key=lambda e: (e["hlc"], e["id"]))
            for ev in new:
                st["seq"] += 1
                st["ids"][ev["id"]] = st["seq"]
                ev["seq"] = st["seq"]
            st["files"] = _files(d)  # as they are now: loading merged the conflict copies away
            tmp = self._ingest_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(st), encoding="utf-8")
            os.replace(tmp, self._ingest_path())
        if first or not new:
            return []
        registry.publish(self.slug, new)  # outside the lock: a subscriber may read the map again
        return new


def _files(folder: Path) -> dict[str, list[int]]:
    out = {}
    for p in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        out[p.name] = [st.st_mtime_ns, st.st_size]
    return out


def _month_of(stamp: str) -> str:
    from datetime import datetime
    p = parse(stamp)
    return datetime.fromtimestamp(p[0] / 1000).strftime("%Y-%m") if p else "0000-00"


def stamp_of(folder: Path) -> str:
    """Changes whenever a YAML file under the folder changes, appears or goes (incl. sync conflict copies)."""
    h = hashlib.sha1()
    if folder.is_dir():
        for p in sorted(folder.rglob("*.yaml")):
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            h.update(f"{p.relative_to(folder)}|{st.st_mtime_ns}|{st.st_size}\n".encode())
    return h.hexdigest()[:16]


def slugs() -> list[str]:
    if not RMAP_DIR.is_dir():
        return []
    return sorted(p.name for p in RMAP_DIR.iterdir()
                  if p.is_dir() and model.valid_slug(p.name) and (p / "map.yaml").exists())


def taken(slug: str) -> bool:
    """Whether a folder by that name exists, ignoring case (the Mac's disk does)."""
    return RMAP_DIR.is_dir() and any(p.name.lower() == slug.lower() for p in RMAP_DIR.iterdir())


def get(slug: str) -> Project:
    """An existing project; KeyError if there is none by that name."""
    p = Project(slug)
    if not p.exists:
        raise KeyError(slug)
    return p


def summary(p: Project, m: dict[str, Any] | None = None) -> dict[str, Any]:
    m = m or p.read()
    counts = {s: 0 for s in model.STATUSES}
    for n in m["nodes"]:
        counts[n.get("status", "todo")] = counts.get(n.get("status", "todo"), 0) + 1
    stamps = [r.get("updated_at", "") for r in [m["project"], *m["nodes"], *m["edges"]]]
    return {**{k: m["project"].get(k) for k in ("title", "line", "note", "description", "archived", "created_at", "uid")},
            "slug": p.slug, "nodes": len(m["nodes"]), "edges": len(m["edges"]), "status": counts,
            "updated_at": max(stamps) if stamps else None, "stamp": m["stamp"], "warnings": m["warnings"]}


def listing(archived: bool = False) -> list[dict[str, Any]]:
    """Every project with counts; two maps with the same title (made on two Macs) are flagged."""
    out = [summary(get(s)) for s in slugs()]
    seen: dict[str, str] = {}
    for p in out:
        key = (p.get("title") or "").strip().lower()
        if key in seen:
            p["warnings"] = [*p["warnings"], {"kind": "same_title", "slug": seen[key]}]
        else:
            seen[key] = p["slug"]
    return [p for p in out if archived or not p.get("archived")]


def entities(key: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Where each entity ("paper:…", "dataset:…") appears, across all projects: nodes and attachments."""
    out: dict[str, list[dict[str, Any]]] = {}
    for s in slugs():
        m = get(s).read()
        titles = {n["id"]: n["title"] for n in m["nodes"]}
        hits = [("node", n["id"], n) for n in m["nodes"]]
        hits += [(kind, a["id"], a) for kind, recs in m["attachments"].items() for a in recs]
        for what, rid, rec in hits:
            ent = rec.get("entity")
            if ent and (key is None or ent == key):
                node = rid if what == "node" else rec.get("node")
                out.setdefault(ent, []).append({"slug": s, "kind": what, "id": rid, "node": node, "title": titles.get(node)})
    return out


__all__ = ["Project", "get", "slugs", "summary", "listing", "stamp_of", "dag_edges", "back_edges", "entities",
           "own_live", "PROJECT_ID"]

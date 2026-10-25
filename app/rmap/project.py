"""One research map project = one folder in the vault, made of sync-safe stores (app/store.py).

    <research_maps>/<slug>/map.yaml          project (one record, id "project"), nodes, edges
    <research_maps>/<slug>/<kind>.yaml       attachments of one kind (evidence.yaml, ...)
    <research_maps>/<slug>/events/<YYYY-MM>.yaml   the change log, one file per month

Every record has `id`, `created_at`, `updated_at`; deletes are tombstones; Syncthing conflict copies are
merged on the next read. Readers skip edges and attachments whose node is gone (the other Mac may have
deleted it). Lock and temp files sit in the data dir under rmap/.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..config import DATA_DIR, RMAP_DIR
from ..store import Store
from . import model
from .registry import KINDS, AttachmentKind

HEADER = "# Research map, managed by PhD Studio (研究地图). Edit in the app; see docs/research-map.md.\n"
EVENTS_HEADER = "# Research map change log, managed by PhD Studio. One record per change; never edited.\n"
PROJECT_ID = "project"
LOCKS = DATA_DIR / "rmap"


class Project:
    def __init__(self, slug: str):
        if not model.valid_slug(slug):
            raise KeyError(slug)
        self.slug = slug
        self.dir = RMAP_DIR / slug
        LOCKS.mkdir(parents=True, exist_ok=True)
        self.map = Store(self.dir / "map.yaml", ("project", "nodes", "edges"), HEADER, name=f"rmap/{slug}.map")

    @property
    def exists(self) -> bool:
        return (self.dir / "map.yaml").exists()

    def kind_store(self, kind: AttachmentKind) -> Store:
        return Store(self.dir / kind.filename, (kind.name,), HEADER, name=f"rmap/{self.slug}.{kind.name}")

    def event_store(self, month: str) -> Store:
        return Store(self.dir / "events" / f"{month}.yaml", ("events",), EVENTS_HEADER,
                     name=f"rmap/{self.slug}.events-{month}")

    def event_months(self) -> list[str]:
        """Months with a log file, newest first (conflict copies are merged by their store, not listed)."""
        d = self.dir / "events"
        if not d.is_dir():
            return []
        return sorted({p.stem for p in d.glob("*.yaml") if len(p.stem) == 7 and p.stem[4] == "-"}, reverse=True)

    # ---- reading ----
    def read(self, include_deleted: bool = False) -> dict[str, Any]:
        """The whole map: project record, nodes, edges and every registered attachment kind."""
        data = self.map.load(include_deleted=True)
        project = next((r for r in data["project"] if r["id"] == PROJECT_ID), None) or {"id": PROJECT_ID, "title": self.slug}
        nodes = [n for n in data["nodes"] if include_deleted or not n.get("deleted")]
        live = {n["id"] for n in data["nodes"] if not n.get("deleted")}
        edges = [e for e in data["edges"] if include_deleted or
                 (not e.get("deleted") and e.get("from") in live and e.get("to") in live)]
        attachments = {}
        for name, kind in KINDS.items():
            recs = self.kind_store(kind).load(include_deleted=True)[name]
            attachments[name] = [r for r in recs if include_deleted or (not r.get("deleted") and r.get("node") in live)]
        return {"slug": self.slug, "project": project, "nodes": nodes, "edges": edges,
                "attachments": attachments, "stamp": self.stamp()}

    def events(self, *, before: str | None = None, since: str | None = None, node: str | None = None,
               kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Newest first. before / since compare with `at` (ISO text); node keeps events touching that node."""
        out: list[dict[str, Any]] = []
        for month in self.event_months():
            if since and month < since[:7]:
                break
            if before and month > before[:7]:
                continue
            rows = self.event_store(month).load()["events"]
            rows = sorted(enumerate(rows), key=lambda x: (x[1].get("at", ""), x[0]), reverse=True)
            for _, ev in rows:
                at = ev.get("at", "")
                if (before and at >= before) or (since and at <= since):
                    continue
                if node and node not in (ev.get("nodes") or []):
                    continue
                if kind and ev.get("kind") != kind:
                    continue
                out.append(ev)
                if len(out) >= limit:
                    return out
        return out

    def stamp(self) -> str:
        return stamp_of(self.dir)


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


def get(slug: str) -> Project:
    """An existing project; KeyError if there is none by that name."""
    p = Project(slug)
    if not p.exists:
        raise KeyError(slug)
    return p


def summary(p: Project) -> dict[str, Any]:
    m = p.read()
    counts = {s: 0 for s in model.STATUSES}
    for n in m["nodes"]:
        counts[n.get("status", "todo")] = counts.get(n.get("status", "todo"), 0) + 1
    stamps = [r.get("updated_at", "") for r in [m["project"], *m["nodes"], *m["edges"]]]
    return {**{k: m["project"].get(k) for k in ("title", "line", "note", "description", "archived", "created_at")},
            "slug": p.slug, "nodes": len(m["nodes"]), "edges": len(m["edges"]), "status": counts,
            "updated_at": max(stamps) if stamps else None, "stamp": m["stamp"]}

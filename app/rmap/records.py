"""Field-level last-writer-wins records, for the research map's stores.

Every record keeps `clock: {field: stamp}` (app/rmap/clock.py), the time each field was last written,
cleared fields included. Two Macs that change different fields of a record both keep their change;
for the same field, the later stamp wins. A record written before clocks existed counts as written
whole at its `updated_at`.

Deleting is a field too (`deleted: true` with its stamp). A record changed after it was deleted (on a
Mac that had not seen the delete yet) is live again, and so is everything the delete took along:
cascaded edges and attachments carry `deleted_by: <node id>` and follow that node.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import yaml

from ..store import Store
from .clock import from_iso

META = ("id", "clock", "created_at", "updated_at")


def clocks(rec: dict[str, Any]) -> dict[str, str]:
    own = rec.get("clock") if isinstance(rec.get("clock"), dict) else {}
    legacy = from_iso(rec.get("updated_at") or rec.get("created_at"))
    return {**{k: legacy for k in rec if k not in META}, **{k: str(v) for k, v in own.items()}}


def last_write(rec: dict[str, Any]) -> str:
    return max(clocks(rec).values(), default="")


def set_field(rec: dict[str, Any], key: str, value: Any, stamp: str) -> None:
    """Write one field (None clears it) and remember when."""
    rec.setdefault("clock", {})
    if not isinstance(rec["clock"], dict):
        rec["clock"] = {}
    if value is None:
        rec.pop(key, None)
    else:
        rec[key] = value
    rec["clock"][key] = stamp


def merge(mine: dict[str, Any], theirs: dict[str, Any]) -> None:
    """Fold another copy of a record into mine, field by field: the later stamp wins each field."""
    cm, ct = clocks(mine), clocks(theirs)
    out: dict[str, str] = {}
    for key in (set(cm) | set(ct)):
        a, b = cm.get(key, ""), ct.get(key, "")
        out[key] = max(a, b)
        if b > a:
            if key in theirs:
                mine[key] = theirs[key]
            else:
                mine.pop(key, None)
    mine["clock"] = out
    created = [x for x in (mine.get("created_at"), theirs.get("created_at")) if x]
    if created:
        mine["created_at"] = min(created)
    mine["updated_at"] = max(mine.get("updated_at", ""), theirs.get("updated_at", ""))


def own_live(rec: dict[str, Any]) -> bool:
    """Not deleted, or changed after the delete."""
    if not rec.get("deleted"):
        return True
    c = clocks(rec)
    d = c.get("deleted", "")
    return any(v > d for k, v in c.items() if k not in ("deleted", "deleted_by"))


def live_ids(nodes: list[dict[str, Any]]) -> set[str]:
    return {n["id"] for n in nodes if own_live(n)}


def child_live(rec: dict[str, Any], live_nodes: set[str]) -> bool:
    """An edge or attachment: live by itself, or deleted only as part of a node delete that was undone."""
    if own_live(rec):
        return True
    return bool(rec.get("deleted_by")) and rec["deleted_by"] in live_nodes


class FieldStore(Store):
    """A Store whose Syncthing conflict copies merge field by field (see the module docstring)."""

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    dumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper)

    @staticmethod
    def _merge_record(mine: dict[str, Any], theirs: dict[str, Any]) -> None:
        merge(mine, theirs)

    @staticmethod
    def _clean(r: dict[str, Any]) -> dict[str, Any]:
        out = Store._clean(r)
        if "clock" in out:  # after the fields, so the YAML still reads top-down
            out["clock"] = out.pop("clock")
        return out


class ShardedStore:
    """The records of one attachment kind, one FieldStore file per node (`<folder>/<node id>.yaml`), behind the
    interface the rest of the map uses (`load`, `editing`). A write rewrites only the files whose records changed, so
    a 10000-row import on one experiment does not make every other node's results part of the dump, and one node's
    records can be read alone (`load_node`). Syncthing conflict copies merge per file, like any FieldStore."""

    def __init__(self, folder: Path, collection: str, header: str, name: str):
        self.folder, self.collection, self.header, self.name = folder, collection, header, name

    def shard(self, node: str) -> FieldStore:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", node or ""):
            raise ValueError(f"no such node: {node!r}")
        return FieldStore(self.folder / f"{node}.yaml", (self.collection,), self.header, name=f"{self.name}.{node}")

    def nodes(self) -> list[str]:
        """Nodes that have a file (a lone conflict copy counts: its base file comes with the next sync)."""
        if not self.folder.is_dir():
            return []
        found = {p.name.split(".sync-conflict-")[0].removesuffix(".yaml") for p in self.folder.glob("*.yaml")}
        return sorted(n for n in found if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", n))

    def load_node(self, node: str, include_deleted: bool = False) -> list[dict[str, Any]]:
        if not (self.folder / f"{node}.yaml").exists() and not list(self.folder.glob(f"{node}.sync-conflict-*.yaml")):
            return []
        return self.shard(node).load(include_deleted=include_deleted)[self.collection]

    def load(self, include_deleted: bool = False) -> dict[str, list[dict[str, Any]]]:
        return {self.collection: [r for n in self.nodes() for r in self.load_node(n, include_deleted)]}

    @staticmethod
    def _mark(recs: list[dict[str, Any]]) -> tuple[int, str, str]:
        """Cheap change detector: how many records, the latest field stamp and the latest `updated_at`. Every write
        of the map's operations sets a stamp later than any stamp the batch has seen, so a changed shard differs."""
        stamp = ""
        for r in recs:
            c = r.get("clock")
            if isinstance(c, dict) and c:
                top = max(c.values())
                if top > stamp:
                    stamp = top
        return (len(recs), stamp, max((r.get("updated_at") or "" for r in recs), default=""))

    @contextmanager
    def editing(self, skip_unchanged: bool = False) -> Iterator[dict[str, list[dict[str, Any]]]]:
        """Every shard under its lock; the block works on one list of all the records and may append new ones (each
        goes to its node's file). Only the files whose records changed are rewritten (the others are not even
        copied). Nothing is written if the block raises."""
        with ExitStack() as stack:
            loaded: dict[str, tuple[FieldStore, dict[str, list[dict[str, Any]]], bool, tuple]] = {}

            def open_shard(node: str) -> list[dict[str, Any]]:
                st = self.shard(node)
                stack.enter_context(st._locked())
                data = st._read(st.path)
                merged = st._merge_conflicts(data)
                loaded[node] = (st, data, merged, self._mark(data[self.collection]))
                return data[self.collection]

            combined = [r for n in self.nodes() for r in open_shard(n)]
            known = {id(r) for r in combined}
            yield {self.collection: combined}
            for r in combined:
                if id(r) in known:
                    continue
                if r["node"] not in loaded:  # the node's first record: its file comes into being
                    open_shard(r["node"])
                loaded[r["node"]][1][self.collection].append(r)
            for st, data, merged, before in loaded.values():
                if merged or self._mark(data[self.collection]) != before:
                    st._write(data)

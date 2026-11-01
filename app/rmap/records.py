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

import fcntl
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import yaml

from ..config import DATA_DIR
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


NODE_FILE = re.compile(r"[A-Za-z0-9_-]{1,64}")
CONFLICT = ".sync-conflict-"
INDEX_VERSION = 1


def node_of(filename: str) -> str:
    """The node a file of a sharded kind belongs to: `<node>.yaml`, or its conflict copy `<node>.sync-conflict-….yaml`."""
    return filename.split(CONFLICT)[0].removesuffix(".yaml")


class ShardedStore:
    """The records of one attachment kind, one FieldStore file per node (`<folder>/<node id>.yaml`). A write opens
    only the files of the nodes it touches (`editing`), so a 10000-row import on one experiment neither reads nor
    rewrites every other node's results, and one node's records can be read alone (`load_node`). Syncthing conflict
    copies merge per file, like any FieldStore.

    A record is found by id through this Mac's index (`index`): record id -> node, kept in the data dir (never synced)
    per file of the kind, conflict copies included, with each file's size and time. Only files whose size or time
    moved are read again, and a missing or broken index is built again from the files. A record never changes node,
    so an index entry can only be missing, never wrong about where a record it knows lives."""

    def __init__(self, folder: Path, collection: str, header: str, name: str):
        self.folder, self.collection, self.header, self.name = folder, collection, header, name
        self._index = DATA_DIR / f"{name}.ids.json"
        self._index_lock = DATA_DIR / f"{name}.ids.lock"

    def shard(self, node: str) -> FieldStore:
        if not NODE_FILE.fullmatch(node or ""):
            raise ValueError(f"no such node: {node!r}")
        return FieldStore(self.folder / f"{node}.yaml", (self.collection,), self.header, name=f"{self.name}.{node}")

    def nodes(self) -> list[str]:
        """Nodes that have a file (a lone conflict copy counts: its base file comes with the next sync)."""
        if not self.folder.is_dir():
            return []
        found = {node_of(p.name) for p in self.folder.glob("*.yaml")}
        return sorted(n for n in found if NODE_FILE.fullmatch(n))

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
    def editing(self, seen: Callable[[list[dict[str, Any]]], None] | None = None) -> Iterator[Shards]:
        """A write's view of the kind (see Shards): each node's file is read and locked when the block first asks
        for it, and stays locked until the block ends. Then only the files whose records changed (or that merged a
        conflict copy) are rewritten, and the index learns what they now hold. Nothing is written if the block raises.
        seen: called with each node's records as they are read (the batch's clock observes them)."""
        with ExitStack() as stack:
            shards = Shards(self, stack, seen)
            yield shards
            shards.finish()

    def reading(self, seen: Callable[[list[dict[str, Any]]], None] | None = None, fresh: bool = False) -> Shards:
        """The same view for a dry run: nothing is locked or written. fresh: a project that does not exist yet."""
        return Shards(self, None, seen, fresh)

    # ---- this Mac's index: record id -> node ----

    def _files(self) -> dict[str, list[int]]:
        """[mtime_ns, size] of every file of the kind, conflict copies included."""
        out = {}
        for p in self.folder.glob("*.yaml") if self.folder.is_dir() else []:
            if not NODE_FILE.fullmatch(node_of(p.name)):
                continue
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            out[p.name] = [st.st_mtime_ns, st.st_size]
        return out

    @contextmanager
    def _index_locked(self) -> Iterator[dict[str, dict[str, Any]]]:
        """The index ({file name: {"sig": [mtime_ns, size], "ids": [...]}}) under its lock, which is taken last and
        held briefly (never while waiting for another lock). The block saves it with `_save_index`."""
        self._index.parent.mkdir(parents=True, exist_ok=True)
        with self._index_lock.open("w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                try:
                    data = json.loads(self._index.read_text(encoding="utf-8"))
                    files = data.get("files") if isinstance(data, dict) and data.get("v") == INDEX_VERSION else None
                except (OSError, ValueError):
                    files = None
                yield {k: v for k, v in (files if isinstance(files, dict) else {}).items()
                       if isinstance(v, dict) and isinstance(v.get("sig"), list) and isinstance(v.get("ids"), list)}
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _save_index(self, files: dict[str, dict[str, Any]]) -> None:
        tmp = self._index.with_name(f"{self._index.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"v": INDEX_VERSION, "files": files}, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self._index)

    def index(self, full: bool = False) -> dict[str, str]:
        """Record id -> node, for every record of the kind on this Mac (a conflict copy's too). The files whose size
        or time moved since the index last saw them are read again; all of them with `full`."""
        with self._index_locked() as files:
            now = self._files()
            changed = bool(set(files) - set(now))
            files = {k: v for k, v in files.items() if k in now}
            for name, sig in now.items():
                if full or files.get(name, {}).get("sig") != sig:
                    try:  # the time and size first, then the content: a file that changes in between is read again next time
                        recs = self.shard(node_of(name))._read(self.folder / name)[self.collection]
                    except OSError:  # gone since (a conflict copy merged away): not in the index, looked at again next time
                        files.pop(name, None)
                    else:
                        files[name] = {"sig": sig, "ids": [r["id"] for r in recs]}
                    changed = True
            if changed:
                self._save_index(files)
        return {rid: node_of(name) for name, e in files.items() for rid in e["ids"]}

    def _remember(self, written: dict[str, tuple[list[int], list[str]]]) -> None:
        """After a write: the nodes' files as written (their size and time, and ids). The conflict copies those files
        merged are gone; dropping every copy of the node is safe, since a copy still there is read again next time."""
        with self._index_locked() as files:
            for node, (sig, ids) in written.items():
                for name in [n for n in files if n.startswith(node + CONFLICT)]:
                    del files[name]
                files[f"{node}.yaml"] = {"sig": sig, "ids": ids}
            self._save_index(files)


class Shards:
    """One batch's view of a sharded kind (ShardedStore.editing / reading). A node's records are read the first time
    the batch asks for them (`node`); records are found by id through the index (`find`). Nothing else of the kind is
    read, so a write costs what the nodes it touches hold, however big the rest of the map's results are."""

    def __init__(self, store: ShardedStore, stack: ExitStack | None,
                 seen: Callable[[list[dict[str, Any]]], None] | None = None, fresh: bool = False):
        self.store, self.stack, self.seen, self.fresh = store, stack, seen, fresh
        self.loaded: dict[str, tuple[FieldStore, dict[str, list[dict[str, Any]]], bool, tuple]] = {}
        self.by_id: dict[str, dict[str, Any]] = {}
        self._ids: dict[str, str] | None = None
        self._full = False

    def node(self, nid: str) -> list[dict[str, Any]]:
        """The node's records, tombstones included; a write may change them and append new ones of this node."""
        coll = self.store.collection
        if nid not in self.loaded:
            st = self.store.shard(nid)
            merged = False
            if self.fresh:
                data: dict[str, list[dict[str, Any]]] = {coll: []}
            elif self.stack is None:
                data = {coll: self.store.load_node(nid, include_deleted=True)}
            else:
                self.stack.enter_context(st._locked())
                data = st._read(st.path)
                merged = st._merge_conflicts(data)
            self.loaded[nid] = (st, data, merged, ShardedStore._mark(data[coll]))
            for r in data[coll]:
                self.by_id.setdefault(r["id"], r)
            if self.seen:
                self.seen(data[coll])
        return self.loaded[nid][1][coll]

    def add(self, rec: dict[str, Any]) -> None:
        self.node(rec["node"]).append(rec)
        self.by_id[rec["id"]] = rec

    def ids(self, full: bool = False) -> dict[str, str]:
        """Record id -> node for the whole kind (the index), as it was when first asked for in this batch."""
        if self.fresh:
            return {}
        if self._ids is None or (full and not self._full):
            self._ids = self.store.index(full=full)
            self._full = self._full or full
        return self._ids

    def find(self, rid: str, node: str | None = None) -> dict[str, Any] | None:
        """A record by id, tombstones included. node: where the caller says it is; only that file is read, and a
        record elsewhere is None."""
        if rid not in self.by_id:
            if node is not None:
                self.node(node)
            else:
                for full in (False, True):  # an id the index does not know: read every file again before saying no
                    nid = self.ids(full).get(rid)
                    if nid is not None:
                        self.node(nid)
                    if rid in self.by_id:
                        break
        rec = self.by_id.get(rid)
        return rec if rec is not None and (node is None or rec.get("node") == node) else None

    def finish(self) -> None:
        """Write the files whose records changed (or that merged a conflict copy) and tell the index; under the locks."""
        coll = self.store.collection
        written: dict[str, tuple[list[int], list[str]]] = {}
        for nid, (st, data, merged, before) in self.loaded.items():
            if merged or ShardedStore._mark(data[coll]) != before:
                stat = st._write(data)
                written[nid] = ([stat.st_mtime_ns, stat.st_size], [r["id"] for r in data[coll]])
        if written:
            self.store._remember(written)

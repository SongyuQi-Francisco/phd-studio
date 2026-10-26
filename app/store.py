"""Small sync-safe YAML stores in the vault (plan, ideas).

A store file holds named collections of records, each with an `id` and an
`updated_at`. Deletes are tombstones (`deleted: true`) so a record removed on
one machine is not resurrected by the other. Syncthing conflict copies are
merged on the next read (newest record wins) and parked outside the vault.
"""
from __future__ import annotations

import copy
import fcntl
import os
import secrets
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import yaml

from .config import DATA_DIR

CONFLICTS_DIR = DATA_DIR / "sync-conflicts"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(3)


class Store:
    loader = yaml.SafeLoader  # subclasses may use the faster CSafeLoader
    dumper = yaml.SafeDumper  # ... and CSafeDumper (several times faster on a big file)

    def __init__(self, path: Path, collections: tuple[str, ...], header: str, name: str | None = None):
        """name: what the lock and temp file in the data dir are called (default: the file's own name);
        give one when several stores share a file name in different folders (research maps)."""
        self.path = path
        self.collections = collections
        self.header = header
        self._lock = DATA_DIR / f"{name or path.stem}.lock"
        self._tmp = DATA_DIR / f"{name or path.stem}{path.suffix}.tmp"

    # ---- file io ----
    def _read(self, path: Path) -> dict[str, list[dict[str, Any]]]:
        if not path.exists():
            return {c: [] for c in self.collections}
        try:
            data = yaml.load(path.read_text(encoding="utf-8"), Loader=self.loader) or {}
        except yaml.YAMLError:
            data = {}
        return {c: [self._clean(r) for r in data.get(c) or [] if isinstance(r, dict) and r.get("id")]
                for c in self.collections}

    @staticmethod
    def _clean(r: dict[str, Any]) -> dict[str, Any]:
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()
                if v not in (None, "", [])}

    def _write(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = yaml.dump({c: [self._clean(r) for r in data[c]] for c in self.collections}, Dumper=self.dumper,
                         allow_unicode=True, sort_keys=False, width=1000)
        self._tmp.write_text(self.header + body, encoding="utf-8")
        os.replace(self._tmp, self.path)

    def _merge_conflicts(self, data: dict[str, list[dict[str, Any]]]) -> bool:
        copies = sorted(self.path.parent.glob(f"{self.path.stem}.sync-conflict-*{self.path.suffix}"))
        for copy in copies:
            other = self._read(copy)
            for c in self.collections:
                mine = {r["id"]: r for r in data[c]}
                for r in other[c]:
                    m = mine.get(r["id"])
                    if m is None:
                        data[c].append(r)
                    else:
                        self._merge_record(m, r)
            CONFLICTS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.move(str(copy), CONFLICTS_DIR / copy.name)
        return bool(copies)

    @staticmethod
    def _merge_record(mine: dict[str, Any], theirs: dict[str, Any]) -> None:
        """Fold the conflict copy's version of a record into ours: the newer updated_at wins, whole."""
        if theirs.get("updated_at", "") > mine.get("updated_at", ""):
            mine.clear()
            mine.update(theirs)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._lock.open("w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    # ---- api ----
    def load(self, include_deleted: bool = False) -> dict[str, list[dict[str, Any]]]:
        """Live (non-deleted) records per collection; tombstones too when asked."""
        with self._locked():
            data = self._read(self.path)
            if self._merge_conflicts(data):
                self._write(data)
        return {c: [r for r in data[c] if include_deleted or not r.get("deleted")] for c in self.collections}

    @contextmanager
    def editing(self, skip_unchanged: bool = False) -> Iterator[dict[str, list[dict[str, Any]]]]:
        """Read-modify-write under the lock; nothing is written if the block raises.
        skip_unchanged: leave the file alone when the block changed nothing (and no conflict copy was merged)."""
        with self._locked():
            data = self._read(self.path)
            merged = self._merge_conflicts(data)
            before = copy.deepcopy(data) if skip_unchanged and not merged else None
            yield data
            if before is None or data != before:
                self._write(data)

    def add(self, collection: str, record: dict[str, Any], prefix: str) -> dict[str, Any]:
        rec = {"id": new_id(prefix), **record, "created_at": now(), "updated_at": now()}
        with self.editing() as data:
            data[collection].append(rec)
        return self._clean(rec)

    def update(self, collection: str, rid: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self.editing() as data:
            rec = next((r for r in data[collection] if r["id"] == rid and not r.get("deleted")), None)
            if rec is None:
                raise KeyError(rid)
            for k, v in patch.items():
                if k not in ("id", "created_at", "updated_at"):
                    rec[k] = v
            rec["updated_at"] = now()
            return self._clean(rec)

    def delete(self, collection: str, rid: str) -> None:
        self.update(collection, rid, {"deleted": True})

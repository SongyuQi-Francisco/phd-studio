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

    @staticmethod
    def _merge_record(mine: dict[str, Any], theirs: dict[str, Any]) -> None:
        merge(mine, theirs)

    @staticmethod
    def _clean(r: dict[str, Any]) -> dict[str, Any]:
        out = Store._clean(r)
        if "clock" in out:  # after the fields, so the YAML still reads top-down
            out["clock"] = out.pop("clock")
        return out

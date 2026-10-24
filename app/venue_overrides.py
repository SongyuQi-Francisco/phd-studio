"""Venue details the user has confirmed on top of the shipped data/venue_details.json.

The shipped file is the baseline. When the assistant re-checks an official CFP and the user confirms the
proposal, the changed fields go to a sync-safe YAML store in the vault (config.VENUE_OVERRIDES_FILE) and
`venues` merges them over the baseline. One record per venue, id = the venue key:

    venues: [{id: acl, key: ACL, details: {...fields that differ from the baseline...},
              ccfddl_seen: {year, link, dates}, updated_at: ...}]

`ccfddl_seen` remembers what ccfddl showed when the details were last verified, so a later ccfddl change
(new link, moved deadlines) can be told apart from a difference that was already looked at.
"""
from __future__ import annotations

import copy
from typing import Any

from .config import VENUE_OVERRIDES_FILE
from .store import Store, now

store = Store(VENUE_OVERRIDES_FILE, ("venues",),
              "# Venue details confirmed in PhD Studio (merged over data/venue_details.json), managed by the app.\n")


def merge_detail(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """`base` with `patch` laid over it: dict fields (format) merge key by key, everything else is replaced."""
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **copy.deepcopy(v)}
        else:
            out[k] = copy.deepcopy(v)
    return out


def load() -> dict[str, dict[str, Any]]:
    """Live override records by venue key."""
    return {r["id"]: r for r in store.load()["venues"]}


def stamp() -> tuple[int, int] | None:
    """Changes whenever the store file (or a Syncthing conflict copy waiting to be merged) changes."""
    path = store.path
    copies = len(list(path.parent.glob(f"{path.stem}.sync-conflict-*{path.suffix}"))) if path.parent.exists() else 0
    try:
        st = path.stat()
    except OSError:
        return (0, copies) if copies else None
    return (st.st_mtime_ns, copies)


def save(key: str, patch: dict[str, Any], seen: dict[str, Any] | None, name: str | None = None) -> dict[str, Any]:
    """Merge `patch` into the venue's override (creating or reviving it) and remember what ccfddl showed."""
    with store.editing() as data:
        rec = next((r for r in data["venues"] if r["id"] == key), None)
        if rec is None or rec.get("deleted"):
            data["venues"] = [r for r in data["venues"] if r["id"] != key]
            rec = {"id": key, "key": name or key.upper(), "details": {}, "created_at": now()}
            data["venues"].append(rec)
        rec["details"] = merge_detail(rec.get("details") or {}, patch)
        rec["ccfddl_seen"] = seen
        rec["updated_at"] = now()
        return store._clean(rec)


def reset(key: str) -> bool:
    """Drop the override: the venue goes back to the shipped details. False when there was none."""
    if key not in load():
        return False
    store.delete("venues", key)
    return True

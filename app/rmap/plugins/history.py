"""Time travel routes (#46): the map at a time, weekly frames for the time scrubber, the changes between
two times, the all-projects map, and the last advisor packet of a map's research line. The logic is in
app/rmap/history.py; everything here only reads.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .. import history

router = APIRouter(prefix="/api/rmap")


def _run(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]) if e.args else "not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/overview")
def overview() -> dict[str, Any]:
    """Every map on one time axis: lines, stations (key validations and decisions), interchanges."""
    return history.overview()


@router.get("/projects/{slug}/history")
def frames(slug: str) -> dict[str, Any]:
    """Weeks W1 … Wn and, per record, its state at the end of each week where it changed."""
    return _run(history.frames, slug)


@router.get("/projects/{slug}/history/at")
def at(slug: str, at: str | None = None) -> dict[str, Any]:
    """The map as it was at `at` (a date counts to its end; a local time; an event cursor)."""
    return _run(history.map_at, slug, at)


@router.get("/projects/{slug}/history/diff")
def diff(slug: str, since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Changes from the map at `since` (a date from its start) to the map at `until` (a date to its end; default now)."""
    return _run(history.diff, slug, since, until)


@router.get("/projects/{slug}/history/packet")
def packet(slug: str) -> dict[str, Any]:
    """The last advisor packet of this map's research line, for 「自上次导师汇报以来」."""
    return {"packet": _run(history.last_packet, slug)}

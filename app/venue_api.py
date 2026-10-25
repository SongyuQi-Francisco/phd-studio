"""Routes for re-checking venue details: freshness, the assistant's pending proposal, and the user's confirm.

Nothing here is reachable by the assistant: its tool only proposes (studio_tools.venue_details_propose);
applying a proposal is the POST below, called from the confirm button in the venue sheet.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from . import config, venue_check

router = APIRouter(prefix="/api/venue-check")


def _guard(fn, *args: Any) -> dict[str, Any]:
    try:
        return fn(*args)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except venue_check.Conflict as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/stale")
def stale() -> dict[str, Any]:
    """Watched venues whose details may be out of date, or that have a proposal waiting."""
    return {"venues": venue_check.stale_watched(), "months": config.VENUE_STALE_MONTHS}


@router.get("/{key}")
def status(key: str) -> dict[str, Any]:
    return _guard(venue_check.status, key)


@router.post("/{key}/apply")
def apply(key: str, body: dict[str, Any]) -> dict[str, Any]:
    return _guard(venue_check.apply, key, str(body.get("proposal_id") or ""))


@router.post("/{key}/dismiss")
def dismiss(key: str, body: dict[str, Any]) -> dict[str, Any]:
    return _guard(venue_check.dismiss, key, str(body.get("proposal_id") or ""))


@router.post("/{key}/verified")
def verified(key: str) -> dict[str, Any]:
    return _guard(venue_check.mark_verified, key)


@router.post("/{key}/reset")
def reset(key: str) -> dict[str, Any]:
    return _guard(venue_check.reset, key)

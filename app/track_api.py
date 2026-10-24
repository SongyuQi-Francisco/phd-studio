"""Routes for the 追踪 page beyond the original radar/follows/labs: top scholars, their activity,
the cross-lab matrix and images (avatars, logos, first-page thumbnails)."""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from . import scholars, tracking

router = APIRouter(prefix="/api/track")
IMG_HEADERS = {"Cache-Control": "max-age=604800"}


@router.get("/scholars")
def scholar_list() -> dict[str, Any]:
    return scholars.listing()


@router.get("/scholars/activity")
def scholar_activity(refresh: bool = False) -> dict[str, Any]:
    return scholars.activity(refresh)


@router.get("/scholars/search")
async def scholar_search(q: str) -> dict[str, Any]:
    if len(q.strip()) < 2:
        return {"results": []}
    try:
        return {"results": await run_in_threadpool(scholars.search_authors, q.strip())}
    except httpx.HTTPError as e:
        raise HTTPException(502, f"OpenAlex 查询失败：{e}")


@router.post("/scholars")
def scholar_add(body: dict[str, Any]) -> dict[str, Any]:
    try:
        rec = scholars.add(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    scholars.refresh_in_background()
    return rec


@router.patch("/scholars/{sid}")
def scholar_update(sid: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return scholars.update(sid, body)
    except KeyError:
        raise HTTPException(404)


@router.delete("/scholars/{sid}")
def scholar_remove(sid: str) -> dict[str, bool]:
    try:
        scholars.remove(sid)
    except KeyError:
        raise HTTPException(404)
    return {"ok": True}


@router.put("/lines/{line}")
def line_keywords(line: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"lines": scholars.set_line_keywords(line, body.get("keywords") or [])}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/follows/feed")
async def follow_feed(refresh: bool = False, days: int = 30) -> dict[str, Any]:
    """Like /api/track/feed but without the 8-per-follow cap, so the cards can count."""
    return {"feed": await run_in_threadpool(tracking.feed, days, 25, refresh)}


@router.get("/labs/matrix")
def lab_matrix() -> dict[str, Any]:
    return tracking.lab_matrix()


@router.get("/avatar/{sid}")
async def avatar(sid: str) -> FileResponse:
    p = await run_in_threadpool(scholars.avatar, sid)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, headers=IMG_HEADERS)


@router.get("/logo/{inst_id}")
async def logo(inst_id: str) -> FileResponse:
    p = await run_in_threadpool(scholars.logo, inst_id)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, headers=IMG_HEADERS)


@router.get("/thumb/{arxiv}")
async def thumb(arxiv: str) -> FileResponse:
    p = await run_in_threadpool(scholars.thumbnail, arxiv)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers=IMG_HEADERS)

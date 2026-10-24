"""Routes for the 追踪 page beyond the original radar/follows/labs: top scholars, their activity,
the cross-lab matrix, images (avatars, logos, first-page thumbnails) and the 「立即巡检」 run."""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from . import scholar_capture, scholar_refresh, scholars, tracker_run, tracking

router = APIRouter(prefix="/api/track")
IMG_HEADERS = {"Cache-Control": "max-age=604800"}


@router.get("/scholars")
def scholar_list() -> dict[str, Any]:
    return scholars.listing()


@router.get("/scholars/activity")
def scholar_activity(refresh: bool = False) -> dict[str, Any]:
    scholar_refresh.maybe_schedule()  # monthly list recompute, when due
    return scholars.activity(refresh)


@router.get("/scholars/refresh")
def list_refresh_status() -> dict[str, Any]:
    return scholar_refresh.status()


@router.post("/scholars/refresh")
def list_refresh_start() -> dict[str, Any]:
    try:
        return scholar_refresh.start(manual=True)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.post("/scholars/refresh/{run_id}/apply")
def list_refresh_apply(run_id: str) -> dict[str, Any]:
    try:
        return scholar_refresh.apply(run_id)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@router.post("/scholars/refresh/{run_id}/dismiss")
def list_refresh_dismiss(run_id: str) -> dict[str, Any]:
    try:
        return scholar_refresh.dismiss(run_id)
    except KeyError:
        raise HTTPException(404)


@router.post("/scholars/snapshot")
def list_snapshot_import(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return scholar_refresh.import_snapshot(str(body.get("text") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/scholars/rules")
def list_rules(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return scholar_refresh.set_rules(body)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.post("/scholars/exclude")
def list_exclude(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return scholar_refresh.exclude(body, str(body.get("reason") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/scholars/exclude/{xid}")
def list_unexclude(xid: str) -> dict[str, bool]:
    try:
        scholar_refresh.unexclude(xid)
    except KeyError:
        raise HTTPException(404)
    return {"ok": True}


@router.post("/scholars/lab-hint")
def list_lab_hint(body: dict[str, Any]) -> dict[str, Any]:
    """Which lab a candidate belongs to (for one-per-lab), when the list and Lab Tracker do not know."""
    try:
        return scholar_refresh.set_lab_hint(body, body.get("lab_id") or None, body.get("lab_name") or None)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/scholars/{sid}/pin")
def scholar_pin(sid: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return scholar_refresh.set_pin(sid, bool(body.get("on", True)))
    except KeyError:
        raise HTTPException(404)


@router.post("/scholars/scholar-window")
def scholar_window() -> dict[str, bool]:
    """Desktop app only: show the Google Scholar window so the user can sign in."""
    if not scholar_capture.show():
        raise HTTPException(400, "只有桌面 App 里才能打开 Scholar 窗口")
    return {"ok": True}


@router.get("/scholars/search")
async def scholar_search(q: str) -> dict[str, Any]:
    if len(q.strip()) < 2:
        return {"results": []}
    try:
        return {"results": await run_in_threadpool(scholars.search_authors, q.strip())}
    except httpx.HTTPError as e:
        raise HTTPException(502, scholars.redact(f"OpenAlex 查询失败：{e}"))


@router.post("/scholars")
def scholar_add(body: dict[str, Any]) -> dict[str, Any]:
    try:
        rec = scholars.add(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    scholars.refresh_in_background(ids={rec["id"]})  # joins a running refresh if there is one
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
    def load() -> dict[str, Any]:  # file reads and the plan.yaml lock stay off the event loop
        return {"feed": tracking.feed(days, 25, refresh), "linkage": tracking.linkage()}
    return await run_in_threadpool(load)


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


@router.post("/thumbs")
def thumbs(body: dict[str, Any]) -> dict[str, Any]:
    """Which first-page thumbnails are ready; missing ones are queued for the paced renderer."""
    return scholars.thumbs_status(list(body.get("ids") or []))


@router.get("/thumb/{arxiv}")
def thumb(arxiv: str) -> FileResponse:
    p = scholars.thumbnail(arxiv)  # only finished files; rendering happens in the background
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers=IMG_HEADERS)


@router.get("/tracker")
def tracker_status() -> dict[str, Any]:
    return tracker_run.status()


@router.post("/tracker/run")
def tracker_start() -> dict[str, Any]:
    try:
        return tracker_run.start()
    except tracker_run.Busy as e:
        raise HTTPException(409, str(e))
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))

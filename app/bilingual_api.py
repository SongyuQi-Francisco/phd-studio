"""Routes for the background bilingual queue (Settings): status, pause, back-fill, remove, retry."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from . import bilingual_queue


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    bilingual_queue.start_worker()
    yield
    bilingual_queue.stop_worker()


router = APIRouter(prefix="/api/bilingual-queue", lifespan=_lifespan)


@router.get("")
def queue_status() -> dict[str, Any]:
    return bilingual_queue.snapshot()


class PauseReq(BaseModel):
    paused: bool


@router.post("/pause")
def queue_pause(req: PauseReq) -> dict[str, Any]:
    return bilingual_queue.set_paused(req.paused)


@router.post("/backfill")
def queue_backfill() -> dict[str, Any]:
    return {"added": bilingual_queue.backfill(), **bilingual_queue.snapshot()}


class CleanupReq(BaseModel):
    days: int


@router.post("/cleanup")
def queue_cleanup(req: CleanupReq) -> dict[str, Any]:
    try:
        return bilingual_queue.cleanup(req.days)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{key}")
def queue_remove(key: str) -> dict[str, Any]:
    if not bilingual_queue.remove(key):
        raise HTTPException(404, "队列里没有这篇")
    return bilingual_queue.snapshot()


@router.post("/{key}/retry")
def queue_retry(key: str) -> dict[str, Any]:
    if not bilingual_queue.retry(key):
        raise HTTPException(409, "这篇没有失败记录，或已在队列里")
    return bilingual_queue.snapshot()

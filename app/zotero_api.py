"""Routes for pushing papers into Zotero and writing the item link back into the note."""
from __future__ import annotations

import re
import subprocess
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from . import library, zotero_push
from .config import load_settings


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    zotero_push.start_auto()
    yield
    zotero_push.stop_auto()


router = APIRouter(prefix="/api/zotero", lifespan=_lifespan)


def _paper_or_404(key: str) -> str:
    try:
        library.validate(key)
    except FileNotFoundError:
        raise HTTPException(404, f"没有这篇论文的笔记：{key}")
    return key


@router.get("/setup")
def setup() -> dict[str, Any]:
    """For Settings: which way into Zotero works, collections to choose from, auto mode, pending links."""
    s = load_settings()
    try:
        colls, links, error = zotero_push.collections(), zotero_push.pending_links(), None
    except zotero_push.SnapshotError as e:
        colls, links, error = [], [], str(e)
    return {**zotero_push.transports(), "secrets_file": zotero_push.secrets_hint(), "collections": colls,
            "collection": s.get("zotero_collection", ""), "auto": zotero_push.auto_status(), "pending": links,
            "snapshot_error": error}


@router.get("/paper/{key}")
def paper_status(key: str) -> dict[str, Any]:
    _paper_or_404(key)
    return zotero_push.status(key)


@router.post("/paper/{key}")
def paper_push(key: str) -> dict[str, Any]:
    _paper_or_404(key)
    try:
        return zotero_push.push(key)
    except zotero_push.ZoteroError as e:
        raise HTTPException(502, str(e))


class LinkReq(BaseModel):
    key: str
    base: str  # digest of the note the user saw the change against


@router.post("/link/{stem}")
def write_link(stem: str, req: LinkReq) -> dict[str, Any]:
    try:
        return zotero_push.write_link(stem, req.key, req.base)
    except FileNotFoundError:
        raise HTTPException(404, "没有这篇笔记")
    except LookupError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


class OpenReq(BaseModel):
    key: str


@router.post("/open")
def open_in_zotero(req: OpenReq) -> dict[str, bool]:
    """Select the item in the Zotero app. Done here because the desktop window can't follow zotero:// links."""
    if not re.fullmatch(r"[A-Z0-9]{8}", req.key):
        raise HTTPException(400, "Zotero 条目编号不对")
    if sys.platform != "darwin":
        raise HTTPException(501, "只在 macOS 上支持")
    subprocess.run(["open", zotero_push.select_link(req.key)], check=False, timeout=10)
    return {"ok": True}

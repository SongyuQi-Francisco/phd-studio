"""First-page thumbnails for paper cards.

Renders page 1 of the paper's local or already-cached PDF (never downloads one) and caches
the PNG in DATA_DIR/thumbs, re-rendering when the PDF is newer. 404 means "no PDF yet";
the front end then draws its own placeholder.

Rendering runs on its own single-thread executor, not the shared request threadpool, so a
long list of first-time thumbnails can never starve the other handlers. PyMuPDF calls hold
library.PDF_LOCK like every other PyMuPDF use in the app.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import i18n, library
from .config import DATA_DIR

THUMBS_DIR = DATA_DIR / "thumbs"
WIDTH = 360  # px; shown at <= 180 CSS px, so sharp on Retina
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="thumbs")
_inflight: dict[str, asyncio.Future] = {}

router = APIRouter()


def _paths(key: str) -> tuple[Path, Path] | None:
    pdf = library.local_pdf(key)
    return (pdf, THUMBS_DIR / f"{library.safe_name(key)}.png") if pdf else None


def _fresh(pdf: Path, out: Path) -> bool:
    return out.exists() and out.stat().st_mtime >= pdf.stat().st_mtime


def render(key: str) -> Path | None:
    paths = _paths(key)
    if paths is None:
        return None
    pdf, out = paths
    if _fresh(pdf, out):
        return out
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with library.PDF_LOCK, pymupdf.open(pdf) as doc:
            page = doc[0]
            zoom = WIDTH / page.rect.width
            png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
    except Exception:  # noqa: BLE001 - a broken PDF just gets the placeholder
        return None
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(png)
    tmp.replace(out)
    return out


def _known(key: str) -> bool:
    try:
        library.validate(key)  # a paper note or an arxiv:<id> key; nothing path-like
    except (FileNotFoundError, ValueError):
        return False
    return True


class KeysReq(BaseModel):
    keys: list[str]


@router.post("/api/thumbs")
def available(req: KeysReq) -> dict[str, list[str]]:
    """Which of these papers have a local PDF, so the page only asks for thumbnails that exist."""
    return {"have": [k for k in req.keys[:500] if _known(k) and library.local_pdf(k)]}


@router.get("/api/thumb/{key:path}")
async def thumb(key: str) -> FileResponse:
    if not _known(key):
        raise HTTPException(404, i18n.t("track.thumb.no_paper"))
    paths = _paths(key)
    if paths is None:
        raise HTTPException(404, i18n.t("track.thumb.no_pdf"))
    if not _fresh(*paths):
        # One render per key at a time; concurrent requests for it share the result.
        fut = _inflight.get(key)
        if fut is None:
            fut = asyncio.get_running_loop().run_in_executor(_pool, render, key)
            _inflight[key] = fut
            fut.add_done_callback(lambda _f: _inflight.pop(key, None))
        if await asyncio.shield(fut) is None:  # a client that goes away must not cancel the shared render
            raise HTTPException(404, i18n.t("track.thumb.bad_pdf"))
    return FileResponse(paths[1], media_type="image/png", headers={"Cache-Control": "max-age=86400"})

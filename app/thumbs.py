"""First-page thumbnails for paper cards.

Renders page 1 of the paper's local or already-cached PDF (never downloads one) and caches
the PNG in DATA_DIR/thumbs, re-rendering when the PDF is newer. 404 means "no PDF yet";
the front end then draws its own placeholder.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pymupdf
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import library
from .config import DATA_DIR

THUMBS_DIR = DATA_DIR / "thumbs"
WIDTH = 360  # px; shown at <= 180 CSS px, so sharp on Retina
_lock = threading.Lock()

router = APIRouter()


def render(key: str) -> Path | None:
    pdf = library.local_pdf(key)
    if pdf is None:
        return None
    out = THUMBS_DIR / f"{library.safe_name(key)}.png"
    if out.exists() and out.stat().st_mtime >= pdf.stat().st_mtime:
        return out
    with _lock:  # MuPDF is not thread-safe across documents rendered at once
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with pymupdf.open(pdf) as doc:
                page = doc[0]
                pix = page.get_pixmap(matrix=pymupdf.Matrix(WIDTH / page.rect.width, WIDTH / page.rect.width), alpha=False)
                tmp = out.with_suffix(".tmp")
                tmp.write_bytes(pix.tobytes("png"))
                tmp.replace(out)
        except Exception:  # noqa: BLE001 - a broken PDF just gets the placeholder
            return None
    return out


class KeysReq(BaseModel):
    keys: list[str]


@router.post("/api/thumbs")
def available(req: KeysReq) -> dict[str, list[str]]:
    """Which of these papers have a local PDF, so the page only asks for thumbnails that exist."""
    return {"have": [k for k in req.keys[:500] if _known(k) and library.local_pdf(k)]}


def _known(key: str) -> bool:
    try:
        library.validate(key)  # a paper note or an arxiv:<id> key; nothing path-like
    except (FileNotFoundError, ValueError):
        return False
    return True


@router.get("/api/thumb/{key:path}")
async def thumb(key: str) -> FileResponse:
    if not _known(key):
        raise HTTPException(404, "没有这篇论文")
    path = await run_in_threadpool(render, key)
    if path is None:
        raise HTTPException(404, "还没有本地 PDF")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=86400"})

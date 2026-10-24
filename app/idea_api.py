"""Routes for idea tags and merging; plain list / add / edit stay in main.py."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from . import idea_ops

router = APIRouter(prefix="/api/ideas")


def _run(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except KeyError as e:
        raise HTTPException(404, f"没有这条想法：{e.args[0]}")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/merge")
def merge(body: dict[str, Any]) -> dict[str, Any]:
    return _run(idea_ops.merge, body.get("ids") or [], text=body.get("text"), line=body.get("line"))


@router.post("/{rid}/tags")
def tags(rid: str, body: dict[str, Any]) -> dict[str, Any]:
    return _run(idea_ops.tag, rid, add=body.get("add") or [], remove=body.get("remove") or [])


@router.post("/{rid}/restore")
def restore(rid: str) -> dict[str, Any]:
    return _run(idea_ops.restore, rid)


@router.post("/{rid}/unmerge")
def unmerge(rid: str) -> dict[str, Any]:
    return _run(idea_ops.unmerge, rid)

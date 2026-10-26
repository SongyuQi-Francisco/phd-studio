"""Peer layer (#47): registers the `peer` attachment kind (app/rmap/peers.py) and its routes.

    GET  /api/rmap/peers/overview                 the 分支 × 同行 table of every map, gaps, collision risks
    GET  /api/rmap/projects/{slug}/peers/analysis one map: peer counts per node, gaps, risks
    GET  /api/rmap/peers/suggestions?slug=&node=  papers likely to belong on a node, to confirm or dismiss
    POST /api/rmap/peers/dismiss {key, aliases, slugs}   stop suggesting a paper for these maps
    POST /api/rmap/peers/attach {slug, node, kind, set}  hang a peer / evidence unless it already hangs there
    GET  /api/rmap/peers/paper?stem=              the reader's open paper: ids, entity, the nodes it fits, where it hangs

The page, the reader and the suggestions attach through /peers/attach, so the same paper is not hung twice
on a node; changing or removing goes through the generic attachment routes / batches.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .. import peers, project

router = APIRouter(prefix="/api/rmap")


def _run(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]) if e.args else "not found")
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))


@router.get("/peers/overview")
def overview() -> dict[str, Any]:
    return peers.overview()


@router.get("/projects/{slug}/peers/analysis")
def analysis(slug: str) -> dict[str, Any]:
    return _run(lambda: peers.analyze(project.get(slug).slug))


@router.get("/peers/suggestions")
def suggestions(slug: str | None = None, node: str | None = None) -> dict[str, Any]:
    if slug:
        _run(project.get, slug)
    return {"suggestions": peers.suggestions(slug, node)}


@router.post("/peers/dismiss")
def dismiss(body: dict[str, Any]) -> dict[str, Any]:
    slugs = body.get("slugs") or ([body["slug"]] if body.get("slug") else [])
    for s in slugs:
        _run(project.get, s)
    return _run(peers.dismiss, body.get("key") or "", slugs, body.get("aliases") or [])


@router.post("/peers/attach")
def attach(body: dict[str, Any]) -> dict[str, Any]:
    slug = body.get("slug") or ""
    _run(project.get, slug)
    return _run(peers.attach, slug, body.get("node") or "", body.get("kind") or "peer", body.get("set") or {})


@router.get("/peers/paper")
def paper(stem: str) -> dict[str, Any]:
    info = _run(peers.paper_info, stem)
    text = " ".join([info["text"], " ".join(info["tags"])])
    return {**{k: v for k, v in info.items() if k not in ("text", "aliases")}, "aliases": sorted(info["aliases"]),
            "matches": peers.match_text(info["title"], text, key=info["entity"], note=info["note"], keys=info["aliases"]),
            "attached": peers.attached(info["aliases"])}

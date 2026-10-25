"""Routes under /api/rmap. Edits here are the user's own (local YAML, no approval card), like the
reading list and the plan; the assistant goes through proposals the user confirms.

Plugin routers (app/rmap/plugins/*.py with a `router`) are mounted before the core routes.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..config import RMAP_DIR, STUDIO_DIR
from . import canvas, lines, model, ops, project, proposals, registry
from .registry import load_plugins

EXT_DIR = STUDIO_DIR / "web" / "rmap" / "ext"

router = APIRouter()
core = APIRouter(prefix="/api/rmap")


def _run(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]) if e.args else "没有找到")
    except ValueError as e:
        raise HTTPException(400, str(e))


def _who(body: dict[str, Any]) -> str:
    return body.get("who") or "user"


@core.get("/meta")
def meta() -> dict[str, Any]:
    """Vocabulary for the front end: node types (with column hints), statuses, edge kinds, attachment kinds."""
    return {"node_types": [{"key": k, **v} for k, v in model.NODE_TYPES.items()], "statuses": list(model.STATUSES),
            "faded": list(model.FADED), "closed": list(model.CLOSED), "edge_kinds": list(model.EDGE_KINDS),
            "flow": list(model.FLOW), "max_ops": ops.MAX_OPS, "max_import_ops": ops.MAX_IMPORT_OPS,
            "kinds": [{"name": k.name, "fields": list(k.fields), "required": list(k.required), "log": k.log,
                       "node_types": list(k.node_types), **k.extra} for k in registry.kinds().values()]}


@core.get("/ext")
def ext() -> dict[str, list[str]]:
    """Front-end extension files in web/rmap/ext/, loaded by the page shell (scripts in name order)."""
    if not EXT_DIR.is_dir():
        return {"scripts": [], "styles": []}
    files = sorted(p.name for p in EXT_DIR.iterdir() if p.is_file() and not p.name.startswith((".", "_")))
    return {"scripts": [f"/rmap/ext/{f}" for f in files if f.endswith(".js")],
            "styles": [f"/rmap/ext/{f}" for f in files if f.endswith(".css")]}


@core.get("/stamp")
def stamp_all() -> dict[str, str]:
    """Changes when any map changes (other Mac included); poll it to refresh views across projects."""
    return {"stamp": project.stamp_of(RMAP_DIR)}


@core.get("/projects")
def projects(archived: bool = False) -> dict[str, Any]:
    return {"projects": project.listing(archived), "stamp": project.stamp_of(RMAP_DIR)}


@core.get("/entities")
def entities(key: str | None = None) -> dict[str, Any]:
    """Where each entity ("paper:…", "dataset:…") appears across all maps (shared stations, shared peers)."""
    return {"entities": project.entities(key)}


@core.post("/projects")
def create(body: dict[str, Any]) -> dict[str, Any]:
    return _run(ops.create_project, body.get("title") or "", slug=body.get("slug"), line=body.get("line"),
                note=body.get("note"), description=body.get("description"), who=_who(body))


@core.get("/projects/{slug}")
def read(slug: str, deleted: bool = False) -> dict[str, Any]:
    p = _run(project.get, slug)
    m = p.read(include_deleted=deleted)
    if not deleted:
        try:  # the canvas follows changes that came without an event (hand edits, the other Mac)
            canvas.write(p, m)
        except OSError:
            pass
    return m


@core.patch("/projects/{slug}")
def update_project(slug: str, body: dict[str, Any]) -> dict[str, Any]:
    patch = {k: v for k, v in body.items() if k != "who"}
    return _run(ops.apply, slug, [{"op": "project.update", "set": patch}], who=_who(body))


@core.get("/projects/{slug}/stamp")
def stamp(slug: str) -> dict[str, str]:
    p = _run(project.get, slug)
    return {"stamp": p.stamp()}


@core.get("/projects/{slug}/events")
def events(slug: str, before: str | None = None, since: str | None = None, after: int | None = None,
           node: str | None = None, kind: str | None = None, limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    """Newest first. Pages: pass `next` back as `before`. before / since: a cursor or a date. after: this
    Mac's ingest sequence (`seq` of an earlier answer): every event seen since, synced-late ones included."""
    p = _run(project.get, slug)
    return _run(p.events, before=before, since=since, after=after, node=node, kind=kind, limit=limit)


@core.post("/projects/{slug}/batch")
def batch(slug: str, body: dict[str, Any]) -> dict[str, Any]:
    return _run(ops.apply, slug, body.get("ops") or [], who=_who(body), summary=body.get("summary"))


@core.post("/projects/{slug}/nodes")
def add_node(slug: str, body: dict[str, Any]) -> dict[str, Any]:
    fields = {k: v for k, v in body.items() if k not in ("parent", "edge", "who")}
    r = _run(ops.apply, slug, [{"op": "node.add", "ref": "$new", "set": fields, "parent": body.get("parent"),
                                "edge": body.get("edge")}], who=_who(body))
    return {**r, "id": r["refs"]["$new"]}


@core.patch("/projects/{slug}/nodes/{nid}")
def update_node(slug: str, nid: str, body: dict[str, Any]) -> dict[str, Any]:
    patch = {k: v for k, v in body.items() if k != "who"}
    return _run(ops.apply, slug, [{"op": "node.update", "id": nid, "set": patch}], who=_who(body))


@core.delete("/projects/{slug}/nodes/{nid}")
def delete_node(slug: str, nid: str) -> dict[str, Any]:
    return _run(ops.apply, slug, [{"op": "node.delete", "id": nid}])


@core.post("/projects/{slug}/edges")
def add_edge(slug: str, body: dict[str, Any]) -> dict[str, Any]:
    r = _run(ops.apply, slug, [{"op": "edge.add", "ref": "$new", "from": body.get("from"), "to": body.get("to"),
                                "kind": body.get("kind"), "label": body.get("label")}], who=_who(body))
    return {**r, "id": r["refs"]["$new"]}


@core.patch("/projects/{slug}/edges/{eid}")
def update_edge(slug: str, eid: str, body: dict[str, Any]) -> dict[str, Any]:
    patch = {k: v for k, v in body.items() if k != "who"}
    return _run(ops.apply, slug, [{"op": "edge.update", "id": eid, "set": patch}], who=_who(body))


@core.delete("/projects/{slug}/edges/{eid}")
def delete_edge(slug: str, eid: str) -> dict[str, Any]:
    return _run(ops.apply, slug, [{"op": "edge.delete", "id": eid}])


@core.get("/projects/{slug}/attachments/{kind}")
def attachments(slug: str, kind: str, node: str | None = None) -> dict[str, Any]:
    if kind not in registry.kinds():
        raise HTTPException(404, f"没有这种挂载：{kind}")
    m = _run(lambda: project.get(slug).read())
    return {kind: [a for a in m["attachments"][kind] if not node or a.get("node") == node]}


@core.post("/projects/{slug}/attachments/{kind}")
def add_attachment(slug: str, kind: str, body: dict[str, Any]) -> dict[str, Any]:
    if kind not in registry.kinds():
        raise HTTPException(404, f"没有这种挂载：{kind}")
    fields = {k: v for k, v in body.items() if k not in ("node", "who")}
    r = _run(ops.apply, slug, [{"op": "attach.add", "ref": "$new", "kind": kind, "node": body.get("node"), "set": fields}],
             who=_who(body))
    return {**r, "id": r["refs"]["$new"]}


@core.patch("/projects/{slug}/attachments/{kind}/{aid}")
def update_attachment(slug: str, kind: str, aid: str, body: dict[str, Any]) -> dict[str, Any]:
    if kind not in registry.kinds():
        raise HTTPException(404, f"没有这种挂载：{kind}")
    patch = {k: v for k, v in body.items() if k != "who"}
    return _run(ops.apply, slug, [{"op": "attach.update", "kind": kind, "id": aid, "set": patch}], who=_who(body))


@core.delete("/projects/{slug}/attachments/{kind}/{aid}")
def delete_attachment(slug: str, kind: str, aid: str) -> dict[str, Any]:
    if kind not in registry.kinds():
        raise HTTPException(404, f"没有这种挂载：{kind}")
    return _run(ops.apply, slug, [{"op": "attach.delete", "kind": kind, "id": aid}])


# ---- the assistant's proposals and first maps drafted from research-line notes ----


@core.get("/proposals")
def list_proposals(slug: str | None = None) -> dict[str, Any]:
    return {"proposals": proposals.pending(slug)}


@core.post("/proposals/{pid}/apply")
def apply_proposal(pid: str) -> dict[str, Any]:
    return _run(proposals.apply, pid)


@core.delete("/proposals/{pid}")
def discard_proposal(pid: str) -> dict[str, Any]:
    _run(proposals.discard, pid)
    return {"ok": True}


@core.get("/lines")
def research_lines() -> dict[str, Any]:
    """Research lines with a note, and the map each one has (for drafting a first map)."""
    return {"lines": lines.line_notes()}


@core.post("/seed")
def seed(body: dict[str, Any]) -> dict[str, Any]:
    """Draft a first map straight from a research-line note (no assistant) as a proposal to confirm."""
    d = _run(lines.draft, body.get("line") or "")
    return _run(proposals.propose, ops_=d["ops"], summary=d["summary"], create=d["create"], kind="seed", source="note")


for _mod in load_plugins():
    if getattr(_mod, "router", None) is not None:
        router.include_router(_mod.router)
router.include_router(core)

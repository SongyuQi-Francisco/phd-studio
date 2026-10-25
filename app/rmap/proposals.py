"""Changes the assistant proposes to a research map (a one-sentence progress update, a first map drafted
from a research-line note). Nothing reaches the map until the user confirms the proposal on the map page.

A proposal is checked when it is made (ops.simulate: the same validation as a real write) and stored per
Mac in state/<device>/rmap_proposals.json, where it is made and confirmed. If the map changed in between
and the batch no longer fits, confirming fails with the reason and the proposal stays.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from typing import Any

from ..config import STATE_DIR
from ..store import now
from . import model, ops, project

PROPOSALS_FILE = STATE_DIR / "rmap_proposals.json"
MAX_PENDING = 30
_lock = threading.Lock()


def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(rows: list[dict[str, Any]]) -> None:
    tmp = PROPOSALS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PROPOSALS_FILE)


def propose(*, ops_: list[dict[str, Any]], summary: str, slug: str | None = None, create: dict[str, Any] | None = None,
            kind: str = "update", source: str = "assistant") -> dict[str, Any]:
    """Check a batch and keep it for the user. slug: an existing map; create: {title, line, note, description}
    for a map that does not exist yet (the batch then runs against an empty one)."""
    if bool(slug) == bool(create):
        raise ValueError("给 project（已有课题）或 new_project（新课题）其中一个")
    summary = model._text(summary or "", "summary", 500)
    if not summary:
        raise ValueError("需要一句话说明（summary）")
    if create is not None:
        create = {k: v for k, v in model.project_patch({k: create.get(k) for k in ("title", "line", "note", "description")
                                                         if create.get(k) is not None}).items() if v is not None}
        if not create.get("title"):
            raise ValueError("新课题需要 title")
    sim = ops.simulate(slug, ops_, who="assistant", summary=summary)
    rec = {"id": "rp" + secrets.token_hex(4), "slug": slug, "create": create, "kind": kind, "source": source,
           "summary": summary, "ops": ops_, "events": sim["events"], "created_at": now(),
           "stamp": project.get(slug).stamp() if slug else None}
    with _lock:
        rows = _load()
        rows.append(rec)
        _save(rows[-MAX_PENDING:])
    return rec


def pending(slug: str | None = None) -> list[dict[str, Any]]:
    """Newest first; with slug, the ones for that map and the ones that would create a map."""
    rows = [r for r in _load() if not slug or r.get("slug") in (slug, None)]
    return sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)


def _take(pid: str) -> dict[str, Any]:
    rec = next((r for r in _load() if r.get("id") == pid), None)
    if rec is None:
        raise KeyError(f"没有这条提议：{pid}")
    return rec


def discard(pid: str) -> None:
    with _lock:
        rows = _load()
        if not any(r.get("id") == pid for r in rows):
            raise KeyError(f"没有这条提议：{pid}")
        _save([r for r in rows if r.get("id") != pid])


def apply(pid: str) -> dict[str, Any]:
    """Write a confirmed proposal (as the assistant's change, with its summary as the log line)."""
    with _lock:
        rec = _take(pid)
        slug = rec.get("slug")
        try:
            if slug:
                result = ops.apply(slug, rec["ops"], who="assistant", summary=rec["summary"])
            else:
                create = rec["create"]
                slug = ops.create_project(create["title"], line=create.get("line"), note=create.get("note"),
                                          description=create.get("description"), who="assistant")["slug"]
                result = ops.apply(slug, rec["ops"], who="assistant", summary=rec["summary"])
        except (KeyError, ValueError) as e:
            msg = e.args[0] if e.args else str(e)
            raise ValueError(f"地图在提议之后变了，这条提议对不上了：{msg}") from e
        _save([r for r in _load() if r.get("id") != pid])
    return {"slug": slug, **result}


_ACTION = {"add": "新增", "update": "修改", "delete": "删除"}


def describe(rec: dict[str, Any]) -> list[str]:
    """Short lines for the assistant to relay (the page shows its own, translated, rendering)."""
    names = {e["target"]: (e.get("record") or {}).get("title") or e.get("title") for e in rec["events"] if e["kind"] == "node"}
    out = []
    for e in rec["events"]:
        if e["kind"] == "log":
            out.append(f"日志：{e.get('summary')}")
        elif e["kind"] == "node":
            ch = "，".join(f"{k}: {v[0]} → {v[1]}" for k, v in (e.get("changes") or {}).items())
            out.append(f"{_ACTION[e['action']]}节点「{names.get(e['target'])}」" + (f"（{ch}）" if ch else ""))
        elif e["kind"] == "edge":
            a, b = (e.get("nodes") or [None, None])[:2]
            out.append(f"{_ACTION[e['action']]}连线「{names.get(a, a)}」→「{names.get(b, b)}」")
        else:
            out.append(f"{_ACTION[e['action']]}{e['kind']}「{e.get('title')}」：{e.get('summary') or ''}")
    return out

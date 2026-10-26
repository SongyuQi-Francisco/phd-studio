"""Changes the assistant proposes to a research map (a one-sentence progress update, a first map drafted
from a research-line note). Nothing reaches the map until the user confirms the proposal on the map page.

A proposal is checked when it is made (ops.simulate: the same validation as a real write) and stored per
Mac in state/<device>/rmap_proposals.json, where it is made and confirmed. It also keeps the clock stamps
of every field it changes and of every record it deletes, as they were when it was shown: if any of them
moved before the user confirms (an edit here, or one synced from the other Mac), confirming fails with
what changed, and the proposal stays, so nothing newer is overwritten by an older view.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from typing import Any

from .. import i18n
from ..config import STATE_DIR
from ..store import now
from . import model, ops, project, registry
from .records import child_live, clocks, last_write, live_ids, own_live

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
           "base": _base(slug, sim["events"], ops_) if slug else [], "absent": _absent(slug, ops_) if slug else []}
    with _lock:
        rows = _load()
        rows.append(rec)
        _save(rows[-MAX_PENDING:])
    return rec


def _records(slug: str) -> tuple[dict[tuple[str, str], dict[str, Any]], set[str]]:
    """(kind, id) -> raw record with its clock, tombstones included; and the live node ids."""
    m = project.get(slug).read(include_deleted=True)
    out = {("node", n["id"]): n for n in m["nodes"]}
    out.update({("edge", e["id"]): e for e in m["edges"]})
    out[("project", project.PROJECT_ID)] = m["project"]
    for kind, recs in m["attachments"].items():
        out.update({(kind, r["id"]): r for r in recs})
    return out, live_ids(m["nodes"])


def _base(slug: str, events: list[dict[str, Any]], ops_: list[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """What the proposal was made against: the stamps of the fields it changes, of the records it deletes
    (all of the record: "*"), and of the existing nodes it hangs new things on (only that they live).
    Kinds logged per batch (bulk imports) have no per-record events, so their changes are read off the operations."""
    recs, _ = _records(slug)
    base: dict[tuple[str, str], dict[str, str]] = {}
    for op in ops_:
        if op.get("op") in ("attach.update", "attach.delete") and isinstance(op.get("id"), str) and (op.get("kind"), op["id"]) in recs:
            key = (op["kind"], op["id"])
            if op["op"] == "attach.delete":
                base.setdefault(key, {})["*"] = last_write(recs[key])
            else:
                base.setdefault(key, {}).update({f: clocks(recs[key]).get(f, "") for f in op.get("set") or {}})
    for ev in events:
        key = (ev["kind"], ev.get("target") or "")
        rec = recs.get(key)
        if ev["action"] == "update" and rec is not None:
            base.setdefault(key, {}).update({f: clocks(rec).get(f, "") for f in ev.get("changes") or {}})
        elif ev["action"] == "delete" and rec is not None:
            base.setdefault(key, {})["*"] = last_write(rec)
        elif ev["action"] == "add":
            for nid in ev.get("nodes") or []:
                if ("node", nid) in recs:
                    base.setdefault(("node", nid), {})
    return [{"kind": k, "id": i, "title": (recs[(k, i)].get("title") if k == "node" else None), "fields": f}
            for (k, i), f in base.items()]


def _absent(slug: str, ops_: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The records an `attach.add` would create, for kinds that say what tells two of them apart (`identity`): none
    of them may exist by the time the proposal is confirmed, or confirming would write the measurement twice."""
    out = []
    for op in ops_:
        kind = registry.kinds().get(op.get("kind")) if op.get("op") == "attach.add" else None
        if kind and kind.identity and isinstance(op.get("node"), str) and not op["node"].startswith("$"):
            try:
                ident = list(kind.identity(kind.clean(op.get("set") or {}, new=True)))
            except ValueError:
                continue
            out.append({"kind": kind.name, "node": op["node"], "identity": ident})
    return out


def stale(rec: dict[str, Any]) -> list[str]:
    """What moved since the proposal was shown: lines like 「Tool use helps」.status (empty if nothing)."""
    if not rec.get("slug"):
        return []
    recs, live = _records(rec["slug"])
    out = []
    for b in rec.get("base") or []:
        cur = recs.get((b["kind"], b["id"]))
        name = i18n.t("rmap.record", kind=b["kind"], title=b.get("title") or b["id"])
        alive = cur is not None and (own_live(cur) if b["kind"] in ("node", "project") else child_live(cur, live))
        if not alive:
            out.append(i18n.t("rmap.gone", record=name))
            continue
        moved = [f for f, stamp in b["fields"].items()
                 if (last_write(cur) if f == "*" else clocks(cur).get(f, "")) != stamp]
        if moved:
            out.append(i18n.t("rmap.moved", record=name, fields=", ".join("*" if f == "*" else f for f in moved)))
    kinds = registry.kinds()
    for a in rec.get("absent") or []:
        kind = kinds.get(a["kind"])
        node = recs.get(("node", a["node"]))
        if kind is None or kind.identity is None or node is None:
            continue
        for (k, _), r in recs.items():
            if k == a["kind"] and r.get("node") == a["node"] and child_live(r, live) and list(kind.identity(r)) == a["identity"]:
                out.append(i18n.t("rmap.exists", record=i18n.t("rmap.record", kind="node", title=node.get("title") or a["node"]),
                                  kind=a["kind"], what=kind.line(r)))
                break
    return out


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
        moved = stale(rec)
        if moved:
            raise ValueError(i18n.t("rmap.stale", what="; ".join(moved)))
        try:
            if slug:
                result = ops.apply(slug, rec["ops"], who="assistant", summary=rec["summary"])
            else:
                create = rec["create"]
                slug = ops.create_project(create["title"], line=create.get("line"), note=create.get("note"),
                                          description=create.get("description"), who="assistant")["slug"]
                result = ops.apply(slug, rec["ops"], who="assistant", summary=rec["summary"])
        except (KeyError, ValueError) as e:
            raise ValueError(i18n.t("rmap.stale", what=e.args[0] if e.args else str(e))) from e
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
        elif e.get("count"):  # a kind logged per batch: how many, the sentence of the proposal says which
            out.append(f"{_ACTION[e['action']]}{e['kind']} {e['count']} 条")
        else:
            out.append(f"{_ACTION[e['action']]}{e['kind']}「{e.get('title')}」：{e.get('summary') or ''}")
    return out

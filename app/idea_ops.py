"""Idea operations on top of plan.idea_store: free tags, merging ideas, restoring merged ones.

Merging creates a new idea (the survivor) and marks the originals `merged` (hidden by default,
never deleted), so it needs no tombstone and survives a concurrent edit on the other Mac:

  survivor  {merged_from: [ids], merged_sources: [snapshot of each original at merge time], ...}
  original  {status: merged, merged_into: <survivor id>, premerge_status: <what it was>}

The originals keep their own record, so restoring one is just putting its status back. Whichever
Mac wrote last wins per record (store.py), so after a sync the two sides can disagree; `listing()`
reconciles that in memory by trusting the original's own `merged_into`.
"""
from __future__ import annotations

from typing import Any, Iterable

from . import plan
from .plan import MERGED, idea_store
from .store import Store, new_id, now

_PROTECTED = ("id", "created_at", "updated_at", "deleted", "merged_from", "merged_sources", "merged_into",
              "premerge_status")
_NOT_SNAPSHOT = ("updated_at", "deleted", "merged_from", "merged_sources", "merged_into", "premerge_status")


def _folded(tags: Iterable[str]) -> set[str]:
    return {t.casefold() for t in tags}


def _live(data: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in data["ideas"] if not r.get("deleted")}


def _find(live: dict[str, dict[str, Any]], rid: str) -> dict[str, Any]:
    if rid not in live:
        raise KeyError(rid)
    return live[rid]


# ---------- reading ----------

def _reconcile(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Survivors only list the originals that still say they were merged into them.

    An original edited on the other Mac after the merge wins as a plain idea again; the survivor
    must not keep claiming it. Originals that no longer exist (deleted) stay as provenance.
    """
    by_id = {r["id"]: r for r in recs}
    out = []
    for r in recs:
        kids = r.get("merged_from")
        if kids:
            keep = [k for k in kids if k not in by_id or (by_id[k].get("status") == MERGED
                                                          and by_id[k].get("merged_into") == r["id"])]
            if keep != kids:
                r = {**r, "merged_from": keep,
                     "merged_sources": [s for s in r.get("merged_sources", []) if s.get("id") in keep]}
                r = {k: v for k, v in r.items() if v not in ([], None)}
        out.append(r)
    return out


def listing(status: str | None = None, line: str | None = None, tags: Iterable[str] | None = None,
            include_merged: bool = False) -> list[dict[str, Any]]:
    """Live ideas, newest first. Merged-away ones only with include_merged or status='merged'.

    `tags` must all be present (case-insensitive).
    """
    want = _folded(plan.norm_tags(tags)) if tags else set()
    out = []
    for r in _reconcile(idea_store.load()["ideas"]):
        s = r.get("status", "new")
        if (status and s != status) or (not status and s == MERGED and not include_merged):
            continue
        if line and r.get("line") != line:
            continue
        if want - _folded(r.get("tags", [])):
            continue
        out.append(r)
    return sorted(out, key=lambda i: i.get("created_at", ""), reverse=True)


def tag_counts(ideas: Iterable[dict[str, Any]]) -> list[tuple[str, int]]:
    """(tag, how many ideas) most used first; spellings that differ only by case count together."""
    n: dict[str, int] = {}
    spelling: dict[str, str] = {}
    for i in ideas:
        for t in i.get("tags", []):
            k = t.casefold()
            spelling.setdefault(k, t)
            n[k] = n.get(k, 0) + 1
    return sorted(((spelling[k], c) for k, c in n.items()), key=lambda x: (-x[1], x[0].casefold()))


# ---------- editing ----------

def update(rid: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Edit text / line / status / note / tags. Merge bookkeeping only changes through merge / restore."""
    bad = [k for k in patch if k in _PROTECTED]
    if bad:
        raise ValueError(f"cannot edit {', '.join(bad)} directly")
    patch = dict(patch)
    if "text" in patch and not str(patch["text"] or "").strip():
        raise ValueError("idea text is empty")
    if "tags" in patch:
        patch["tags"] = plan.norm_tags(patch["tags"])
    st = patch.get("status")
    if st is not None and st not in plan.I_STATUS:
        raise ValueError("status 'merged' is only set by merging" if st == MERGED
                         else f"status must be one of {plan.I_STATUS}")
    with idea_store.editing() as data:
        rec = _find(_live(data), rid)
        if st is not None and rec.get("status") == MERGED:
            raise ValueError("this idea was merged into another one; restore it first")
        for k, v in patch.items():
            rec[k] = str(v).strip() if k == "text" else v
        rec["updated_at"] = now()
        return Store._clean(rec)


def tag(rid: str, add: Iterable[str] = (), remove: Iterable[str] = ()) -> dict[str, Any]:
    """Add and remove tags in one step. Applied to what is stored right now, so two quick edits (the app
    and the CLI, say) never overwrite each other's tag changes."""
    add, drop = plan.norm_tags(add), _folded(plan.norm_tags(remove))
    with idea_store.editing() as data:
        rec = _find(_live(data), rid)
        have = [t for t in rec.get("tags", []) if t.casefold() not in drop]
        have += [t for t in add if t.casefold() not in _folded(have)]
        if have != rec.get("tags", []):
            rec["tags"] = have
            rec["updated_at"] = now()
        return Store._clean(rec)


# ---------- merging ----------

def merge(ids: list[str], text: str | None = None, line: str | None = None, source: str = "self") -> dict[str, Any]:
    """Merge two or more ideas into a new one and hide the originals (status `merged`).

    The survivor's text is `text`, or the first idea's text. Tags are the union, the paper/page is the one
    of the original whose text was kept (else the first that has one), the line is `line` or the most common
    one, and every original is kept
    as a snapshot (text, paper, page, source, tags, ...) in `merged_sources`.
    """
    ids = list(dict.fromkeys(ids))
    if len(ids) < 2:
        raise ValueError("merging needs at least two ideas")
    with idea_store.editing() as data:
        live = _live(data)
        srcs = [_find(live, i) for i in ids]
        for s in srcs:
            if s.get("status") == MERGED:
                raise ValueError(f"{s['id']} was already merged into another idea")
            if s.get("status") == "promoted":
                raise ValueError(f"{s['id']} is already a note; merge before 整理成笔记")
        stamp = now()
        lines = [s["line"] for s in srcs if s.get("line")]
        text = (text or "").strip() or srcs[0]["text"]
        with_paper = next((s for s in srcs if s.get("paper") and s["text"] == text), None) \
            or next((s for s in srcs if s.get("paper")), {})  # the paper of the text that was kept, else the first
        rec: dict[str, Any] = {
            "id": new_id("i"),
            "text": text,
            "line": line or (max(dict.fromkeys(lines), key=lines.count) if lines else None),
            "paper": with_paper.get("paper"), "page": with_paper.get("page"),
            "tags": plan.norm_tags([t for s in srcs for t in s.get("tags", [])]),
            "note": next((s["note"] for s in srcs if s.get("note")), None),
            "status": "kept" if any(s.get("status") == "kept" for s in srcs) else "new",
            "source": source,
            "merged_from": ids,
            "merged_sources": [{k: v for k, v in Store._clean(s).items() if k not in _NOT_SNAPSHOT} for s in srcs],
            "created_at": min(s.get("created_at", stamp) for s in srcs),
            "updated_at": stamp,
        }
        for s in srcs:
            s.update(premerge_status=s.get("status", "new"), status=MERGED, merged_into=rec["id"], updated_at=stamp)
        data["ideas"].append(rec)
        return Store._clean(rec)


def _unmerge_one(rec: dict[str, Any], live: dict[str, dict[str, Any]], stamp: str) -> None:
    into = live.get(rec.get("merged_into", ""))
    was = rec.pop("premerge_status", None)
    rec["status"] = was if was in plan.I_STATUS else "kept"
    rec.pop("merged_into", None)
    rec["updated_at"] = stamp
    if into and rec["id"] in into.get("merged_from", []):
        into["merged_from"] = [k for k in into["merged_from"] if k != rec["id"]]
        into["merged_sources"] = [s for s in into.get("merged_sources", []) if s.get("id") != rec["id"]]
        into["updated_at"] = stamp


def restore(rid: str) -> dict[str, Any]:
    """Bring a merged-away idea back with the status it had; the survivor stops listing it."""
    with idea_store.editing() as data:
        live = _live(data)
        rec = _find(live, rid)
        if rec.get("status") != MERGED:
            raise ValueError(f"{rid} is not a merged idea")
        _unmerge_one(rec, live, now())
        return Store._clean(rec)


def unmerge(rid: str) -> dict[str, Any]:
    """Undo a whole merge: restore every original of survivor `rid` and delete the survivor."""
    with idea_store.editing() as data:
        live = _live(data)
        rec = _find(live, rid)
        kids = [r for r in live.values() if r.get("status") == MERGED and r.get("merged_into") == rid]
        if not kids:
            raise ValueError(f"{rid} has no merged ideas to restore")
        if rec.get("status") in (MERGED, "promoted"):
            raise ValueError(f"{rid} is {rec['status']}; restore the originals one by one")
        stamp = now()
        for k in kids:
            _unmerge_one(k, live, stamp)
        rec.update(deleted=True, updated_at=stamp)
        return {"unmerged": rid, "restored": [k["id"] for k in kids]}

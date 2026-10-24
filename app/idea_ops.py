"""Idea operations on top of plan.idea_store: free tags, merging ideas, restoring merged ones.

Merging creates a new idea (the survivor) and marks the originals `merged` (hidden by default,
never deleted), so it needs no tombstone and survives a concurrent edit on the other Mac:

  survivor  {merged_from: [ids], merged_sources: [snapshot of each original at merge time], merge_untouched, ...}
  original  {status: merged, merged_into: <survivor id>, premerge_status: <what it was>}

The originals keep their own record, so restoring one is just putting its status back and never writes
the survivor (another Mac may be editing it). Whichever Mac wrote last wins per record (store.py), so
after a sync the two sides can disagree; `listing()` derives who belongs to a survivor on read, by
trusting the original's own `merged_into`.
"""
from __future__ import annotations

from typing import Any, Iterable

from . import plan
from .plan import MERGED, idea_store
from .store import Store, new_id, now

_MERGE_FIELDS = ("merged_from", "merged_sources", "merged_into", "merge_untouched", "premerge_status")
_PROTECTED = ("id", "created_at", "updated_at", "deleted", *_MERGE_FIELDS)
_NOT_SNAPSHOT = ("updated_at", "deleted", *_MERGE_FIELDS)


def _folded(tags: Iterable[str]) -> set[str]:
    return {t.casefold() for t in tags}


def _stored_tags(rec: dict[str, Any]) -> list[str]:
    """An idea's tags as the app shows them. Ideas saved before tags were cleaned may hold '#x' or 'a, b'."""
    return plan.norm_tags(rec.get("tags"), strict=False)


def _live(data: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in data["ideas"] if not r.get("deleted")}


def _find(live: dict[str, dict[str, Any]], rid: str) -> dict[str, Any]:
    if rid not in live:
        raise KeyError(rid)
    return live[rid]


# ---------- reading ----------

def _reconcile(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The view of the stored ideas: clean tags, and survivors list only the originals that still say they
    were merged into them.

    An original restored, or edited on the other Mac after the merge, is a plain idea again and the
    survivor must not keep claiming it. Originals that no longer exist (deleted) stay as provenance.
    """
    by_id = {r["id"]: r for r in recs}
    out = []
    for r in recs:
        if r.get("tags") and _stored_tags(r) != r["tags"]:
            r = {**r, "tags": _stored_tags(r)}
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
    want = _folded(plan.norm_tags(tags, strict=False)) if tags else set()
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

def _set_tags(rec: dict[str, Any], add: list[str], drop: set[str]) -> bool:
    have = [t for t in _stored_tags(rec) if t.casefold() not in drop]
    have += [t for t in add if t.casefold() not in _folded(have)]
    if have == rec.get("tags", []):
        return False
    rec["tags"] = have
    return True


def update(rid: str, patch: dict[str, Any], *, restore: bool = False, tags_add: Iterable[str] = (),
           tags_remove: Iterable[str] = ()) -> dict[str, Any]:
    """Edit text / line / status / note / tags in one step (all or nothing), optionally restoring a
    merged-away idea first and adding / removing single tags. Merge bookkeeping only changes through
    merge / restore / unmerge."""
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
    add, drop = plan.norm_tags(tags_add), _folded(plan.norm_tags(tags_remove, strict=False))
    with idea_store.editing() as data:
        rec = _find(_live(data), rid)
        stamp = now()
        if restore:
            if rec.get("status") != MERGED:
                raise ValueError(f"{rid} is not a merged idea")
            _unmerge_one(rec, stamp)
        if st is not None and rec.get("status") == MERGED:
            raise ValueError("this idea was merged into another one; restore it first")
        for k, v in patch.items():
            rec[k] = str(v).strip() if k == "text" else v
        changed = _set_tags(rec, add, drop) if (add or drop) else False
        if restore or patch or changed:
            rec["updated_at"] = stamp
            rec.pop("merge_untouched", None)
        return Store._clean(rec)


def tag(rid: str, add: Iterable[str] = (), remove: Iterable[str] = ()) -> dict[str, Any]:
    """Add and remove tags in one step. Applied to what is stored right now, so two quick edits (the app
    and the CLI, say) never overwrite each other's tag changes."""
    return update(rid, {}, tags_add=add, tags_remove=remove)


# ---------- merging ----------

def merge(ids: list[str], text: str | None = None, line: str | None = None, source: str = "self") -> dict[str, Any]:
    """Merge two or more ideas into a new one and hide the originals (status `merged`).

    The survivor's text is `text`, or the first idea's text. Tags are the union, the paper/page is the one
    of the original whose text was kept (else the first that has one), and the line is `line` (an empty
    string means no line) or, when not given, the most common one. Every original is kept as a snapshot
    (text, paper, page, source, tags, ...) in `merged_sources`.
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
            "line": (line or None) if line is not None else (max(dict.fromkeys(lines), key=lines.count) if lines else None),
            "paper": with_paper.get("paper"), "page": with_paper.get("page"),
            "tags": plan.norm_tags([t for s in srcs for t in _stored_tags(s)], strict=False),
            "note": next((s["note"] for s in srcs if s.get("note")), None),
            "status": "kept" if any(s.get("status") == "kept" for s in srcs) else "new",
            "source": source,
            "merged_from": ids,
            "merged_sources": [{k: v for k, v in Store._clean(s).items() if k not in _NOT_SNAPSHOT} for s in srcs],
            "created_at": min(s.get("created_at", stamp) for s in srcs),
            "merge_untouched": True,  # until someone edits the merged idea; unmerge may then delete it safely
            "updated_at": stamp,
        }
        for s in srcs:
            s.update(premerge_status=s.get("status", "new"), status=MERGED, merged_into=rec["id"], updated_at=stamp)
        data["ideas"].append(rec)
        return Store._clean(rec)


def _unmerge_one(rec: dict[str, Any], stamp: str) -> None:
    """Put an original back. The survivor is left alone: it derives its originals on read."""
    was = rec.pop("premerge_status", None)
    rec["status"] = was if was in plan.I_STATUS else "kept"
    rec.pop("merged_into", None)
    rec["updated_at"] = stamp


def restore(rid: str) -> dict[str, Any]:
    """Bring a merged-away idea back with the status it had; the survivor stops listing it."""
    return update(rid, {}, restore=True)


def unmerge(rid: str) -> dict[str, Any]:
    """Undo a whole merge: restore every original of survivor `rid`.

    A survivor nobody has touched since the merge is deleted. One that was edited (text, tags, line,
    ...) is kept with status `dropped`, so what was written there is never lost.
    """
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
            _unmerge_one(k, stamp)
        pristine = bool(rec.get("merge_untouched"))
        if pristine:
            rec.update(deleted=True, updated_at=stamp)
        else:
            for f in ("merged_from", "merged_sources", "merge_untouched"):
                rec.pop(f, None)
            rec.update(status="dropped", updated_at=stamp)
        return {"unmerged": rid, "restored": [k["id"] for k in kids], "survivor": "deleted" if pristine else "dropped"}

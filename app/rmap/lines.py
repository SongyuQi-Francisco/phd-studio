"""Research lines and their maps: which map belongs to a line (for the 方向 page), and a first map drafted
from a line's note (pitch, hypotheses, first experiment, Kill criteria, related notes, latest status).

The draft is a starting point the assistant refines and the user confirms; nothing here writes a map.
"""
from __future__ import annotations

import re
from typing import Any

from .. import notes
from ..config import PAPERS_DIR, VAULT_DIR
from . import project

HYPOTHESES = ("Hypotheses", "Hypothesis", "Research Hypotheses", "假设", "路径", "Paths")
KILL_STATE = {"clear": "todo", "partial": "active", "triggered": "blocked"}
TITLE = 80  # a node title is one line on the map; the full text goes into the description


def maps_by_line() -> dict[str, dict[str, Any]]:
    """Live (not archived) maps keyed by their research line and by their note path."""
    out: dict[str, dict[str, Any]] = {}
    for slug in project.slugs():
        s = project.summary(project.get(slug))
        if s.get("archived"):
            continue
        info = {"slug": slug, "title": s["title"], "nodes": s["nodes"], "verified": s["status"].get("verified", 0)}
        for key in (s.get("line"), s.get("note")):
            if key:
                out.setdefault(key, info)
    return out


def annotate(lines: list[dict[str, Any]]) -> None:
    """Add `map` to each research line (direction.lines) that has one."""
    try:
        maps = maps_by_line()
    except OSError:
        return
    for ln in lines:
        hit = maps.get(ln.get("path") or "") or maps.get(ln.get("line") or "") or maps.get(ln.get("name") or "")
        if hit:
            ln["map"] = hit


def _sentence(text: str, limit: int) -> str:
    text = notes.strip_links(re.sub(r"\s+", " ", text or "")).strip()
    first = re.split(r"(?<=[.!?。！？])\s", text, maxsplit=1)[0]
    first = first if len(first) <= limit else first[: limit - 1].rstrip() + "…"
    return first


def find_note(line: str) -> dict[str, Any] | None:
    """A research-line note by configured line name, note name or vault path."""
    from .. import direction
    key = line.strip().lower()
    for ln in direction.lines():
        if ln["kind"] != "research-line" or not ln.get("path"):
            continue
        if key in {str(ln.get("line") or "").lower(), ln["name"].lower(), ln["path"].lower(), ln["path"].lower().removesuffix(".md")}:
            return ln
    return None


def _evidence(target: str) -> dict[str, Any] | None:
    name = target.split("|")[0].split("#")[0].strip()
    if not name:
        return None
    if (PAPERS_DIR / f"{name}.md").exists():
        return {"type": "paper", "target": name}
    hit = notes.find_by_name(name)
    return {"type": "note", "target": notes.rel(hit), "title": name} if hit else None


def draft(line: str) -> dict[str, Any]:
    """A first map for a research line, as a proposal body: {create, ops, summary, source}."""
    ln = find_note(line)
    if ln is None:
        raise KeyError(f"没有找到这条研究线的笔记：{line}")
    n = notes.read(VAULT_DIR / ln["path"])
    secs = n["sections"]
    active = str(n["frontmatter"].get("status") or "active") == "active"
    pitch = notes.find_section(secs, "One-line Pitch")
    working = notes.first_paragraph(notes.find_section(secs, "Working Title"))
    # the question is the working title (else the line itself); the pitch is its description
    q_title = _sentence(working, TITLE) or ln.get("line") or ln["name"]
    ops_: list[dict[str, Any]] = [{"op": "node.add", "ref": "$q", "set": {
        "type": "question", "title": q_title, "status": "active" if active else "todo",
        "body": notes.strip_links(notes.first_paragraph(pitch)) or None}}]

    hyps = [notes.strip_links(b) for b in notes.bullets(notes.find_section(secs, *HYPOTHESES))]
    if not hyps:  # one path to start from: the pitch's first claim
        hyps = [notes.strip_links(notes.first_paragraph(pitch)) or ln["name"]]
    for i, h in enumerate(hyps[:8]):
        ops_.append({"op": "node.add", "ref": f"$h{i}", "parent": "$q", "set": {
            "type": "hypothesis", "title": _sentence(h, TITLE), "status": "active" if active and i == 0 else "todo",
            "body": h if len(_sentence(h, 1000)) > TITLE or len(h) > len(_sentence(h, 1000)) else None}})

    first = notes.first_paragraph(notes.find_section(secs, "First Experiment"))
    if first:
        ops_.append({"op": "node.add", "ref": "$x", "parent": "$h0", "set": {
            "type": "experiment", "title": _sentence(first, TITLE), "status": "active" if active else "todo",
            "body": notes.strip_links(first)}})
    for i, k in enumerate(ln.get("kills") or []):
        ops_.append({"op": "node.add", "ref": f"$k{i}", "parent": "$h0", "set": {
            "type": "kill", "title": _sentence(k["text"], TITLE), "status": KILL_STATE.get(k.get("state"), "todo"),
            "body": k["text"] if len(k["text"]) > TITLE else None}})

    seen: set[str] = set()
    for target, _alias in notes.WIKILINK.findall(notes.find_section(secs, "Related", "Evidence", "References")):
        ev = _evidence(target)
        if ev and ev["target"] not in seen:
            seen.add(ev["target"])
            ops_.append({"op": "attach.add", "kind": "evidence", "node": "$q", "set": {**ev, "stance": "context"}})
    ops_.append({"op": "attach.add", "kind": "evidence", "node": "$q",
                 "set": {"type": "note", "target": ln["path"], "title": ln["name"], "stance": "context"}})

    latest = ln.get("latest_status") or ""
    summary = f"从研究线笔记起草第一版地图：{ln['name']}" + (f"（最新状态 {latest[:120]}）" if latest else "")
    return {"create": {"title": ln.get("line") or ln["name"], "line": ln.get("line"), "note": ln["path"]},
            "ops": ops_, "summary": summary, "source": ln["path"]}


def line_notes() -> list[dict[str, Any]]:
    """Research lines that have a note to draft from, and whether they already have a map."""
    from .. import direction
    maps = maps_by_line()
    out = []
    for ln in direction.lines():
        if ln["kind"] == "research-line" and ln.get("path"):
            out.append({"name": ln["name"], "line": ln.get("line"), "path": ln["path"],
                        "map": maps.get(ln["path"]) or maps.get(ln.get("line") or "")})
    return out

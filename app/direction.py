"""Direction page: research lines / ideas, idea radar ranking, roadmap phases, reading courses.

Everything is parsed from the vault on each request; nothing is stored here.
"""
from __future__ import annotations

import re
from typing import Any

from . import line_chat, notes, plan, reading
from .config import IDEA_RADAR, IDEAS_DIR, PROFILE, ROADMAP

_KILL_HIT = re.compile(r"\b(K\d+)\b[^\n]{0,80}?(partially triggered|部分触发|triggered|已触发|触发)", re.I)
_KILL_HIT_REV = re.compile(r"(partially triggered|部分触发|triggered|已触发|触发)[^\n]{0,40}?\b(K\d+)\b", re.I)


def _line_for(name: str, fm: dict[str, Any]) -> str | None:
    """Which configured research line ('Project A', 'Project B') a note belongs to."""
    hay = (name + " " + " ".join(map(str, fm.get("tags") or []))).lower().replace(" ", "")
    for ln in PROFILE.get("lines", []):
        if ln.lower().replace(" ", "") in hay:
            return ln
    return None


def _kill_state(body: str) -> dict[str, str]:
    """K-id -> 'triggered' | 'partial', from mentions anywhere in the note."""
    state: dict[str, str] = {}
    hits = [(m.group(1), m.group(2)) for m in _KILL_HIT.finditer(body)]
    hits += [(m.group(2), m.group(1)) for m in _KILL_HIT_REV.finditer(body)]
    for kid, word in hits:
        kid = kid.upper()
        level = "partial" if "partial" in word.lower() or "部分" in word else "triggered"
        if state.get(kid) != "triggered":
            state[kid] = level
    return state


def lines() -> list[dict[str, Any]]:
    data = plan.timeline(days_back=0, days_ahead=500)
    items = reading.load()
    idea_list = plan.ideas()
    out = []
    for p in sorted(IDEAS_DIR.glob("*.md")):
        n = notes.read(p)
        fm = n["frontmatter"]
        kind = fm.get("type")
        if kind not in ("research-line", "idea"):
            continue
        secs = n["sections"]
        line = _line_for(n["name"], fm)
        kill_text = notes.find_section(secs, "Kill Criteria", "Kill criteria")
        states = _kill_state(n["body"])
        kills = []
        for b in notes.bullets(kill_text):
            m = re.match(r"(K\d+)", b, re.I)
            kid = m.group(1).upper() if m else None
            kills.append({"id": kid, "text": notes.strip_links(b), "state": states.get(kid or "", "clear")})
        latest_status = ""
        dated = [k for k in secs if re.match(r"20\d\d-\d\d-\d\d", k)]
        if dated:
            latest_status = f"{sorted(dated)[-1]}：" + notes.strip_links(notes.first_paragraph(secs[sorted(dated)[-1]]))[:240]
        out.append({
            "path": n["path"], "name": n["name"], "kind": kind, "line": line,
            "status": fm.get("status"), "priority": fm.get("priority"), "horizon": fm.get("horizon"),
            "created": fm.get("created"),
            "pitch": notes.strip_links(notes.first_paragraph(notes.find_section(secs, "One-line Pitch", "Working Title")))[:300],
            "first_experiment": notes.strip_links(notes.first_paragraph(notes.find_section(secs, "First Experiment")))[:300],
            "kills": kills, "latest_status": latest_status,
            "milestones": [e for e in data["events"] if e["type"] == "milestone" and line and e.get("line") == line],
            "targets": [t for t in data["targets"] if line and t.get("line") == line],
            "deadlines": [e for e in data["events"] if e["type"] == "target" and line and (e.get("target") or {}).get("line") == line][:3],
            "papers": sum(1 for i in items if line and line in i.get("lines", []) and i["status"] not in ("dismissed",)),
            "ideas": sum(1 for i in idea_list if line and i.get("line") == line and i.get("status") != "dropped"),
            "obsidian_url": n["obsidian_url"], "mtime": n["mtime"],
        })
    # Configured lines with no research-line note of their own (e.g. a topic tracked elsewhere).
    covered = {x["line"] for x in out if x["line"]}
    for ln in PROFILE.get("lines", []):
        if ln in covered:
            continue
        out.append({
            "path": None, "name": ln, "kind": "research-line", "line": ln, "status": "active", "priority": None,
            "horizon": None, "created": None, "pitch": "", "first_experiment": "", "kills": [], "latest_status": "",
            "milestones": [e for e in data["events"] if e["type"] == "milestone" and e.get("line") == ln],
            "targets": [t for t in data["targets"] if t.get("line") == ln],
            "deadlines": [e for e in data["events"] if e["type"] == "target" and (e.get("target") or {}).get("line") == ln][:3],
            "papers": sum(1 for i in items if ln in i.get("lines", []) and i["status"] != "dismissed"),
            "ideas": sum(1 for i in idea_list if i.get("line") == ln and i.get("status") != "dropped"),
            "obsidian_url": None, "mtime": 0, "virtual": True,
        })
    out.sort(key=lambda x: (x["kind"] != "research-line", str(x["status"]) in ("paused", "archived"),
                            x["priority"] if isinstance(x["priority"], int) else 9, x["name"]))
    line_chat.annotate(out)  # chat key, chat size and last review packet per research line
    return out


def radar() -> list[dict[str, Any]]:
    """Rows of the Idea Radar's priority table."""
    if not IDEA_RADAR.exists():
        return []
    secs = notes.read(IDEA_RADAR)["sections"]
    table = notes.find_section(secs, "Priority Ranking")
    rows = []
    for ln in table.splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0].isdigit():
            m = notes.WIKILINK.search(cells[1])
            rows.append({"rank": int(cells[0]), "name": m.group(1) if m else cells[1],
                         "confidence": cells[2], "why": notes.strip_links(cells[3]) if len(cells) > 3 else ""})
    return rows


def roadmap() -> dict[str, Any]:
    if not ROADMAP.exists():
        return {"phases": []}
    n = notes.read(ROADMAP)
    phases = []
    for head, text in n["sections"].items():
        m = re.match(r"(Year\s*\d+|第.+年)\s*[:：]?\s*(.*)", head)
        if not m:
            continue
        goal = re.search(r"Goal:\s*(.+)", text)
        phases.append({"label": m.group(1), "title": m.group(2), "goal": notes.strip_links(goal.group(1)) if goal else "",
                       "deliverables": [notes.strip_links(b) for b in notes.bullets(
                           text.split("Deliverables:", 1)[1].split("Success criteria:", 1)[0])][:5] if "Deliverables:" in text else []})
    positioning = notes.strip_links(notes.first_paragraph(notes.find_section(n["sections"], "One-Sentence Positioning")))
    fm = n["frontmatter"]  # Year 1 starts at phd_start (or start / created) for the "you are here" marker
    start = str(fm.get("phd_start") or fm.get("start") or fm.get("created") or "")[:10]  # date part of a date or datetime
    return {"positioning": positioning, "phases": phases, "path": n["path"], "obsidian_url": n["obsidian_url"], "start": start}


def courses() -> list[dict[str, Any]]:
    out = []
    for overview in sorted(IDEAS_DIR.glob("*/00 *.md")):
        n = notes.read(overview)
        rows = [ln for ln in n["body"].splitlines() if re.match(r"^\|\s*\d+\s*\|", ln)]
        lessons = [p for p in overview.parent.glob("*.md") if not p.name.startswith("00 ")]
        def finished(r: str) -> bool:
            last = r.strip().strip("|").split("|")[-1]
            return "✅" in r or ("[[" in last and "待" not in last)
        done = sum(1 for r in rows if finished(r))
        title = next((ln[2:].strip() for ln in n["body"].splitlines() if ln.startswith("# ")), overview.parent.name)
        out.append({"name": overview.parent.name, "title": title, "path": n["path"],
                    "obsidian_url": n["obsidian_url"], "total": max(len(rows), len(lessons)),
                    "done": done if rows else len(lessons), "line": _line_for(overview.parent.name, n["frontmatter"]),
                    "lessons": [{"name": p.stem, "path": notes.rel(p)} for p in sorted(lessons)]})
    return out

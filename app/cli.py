"""`studio` command line: lets any agent session or script push papers to the reading list.

    studio push --paper "Attention 2017" --tier T1 --line "Project A" --reason "..." --by "planning session"
    studio push --arxiv 2606.17328 --reason "..." --by "weekly tracker" --source tracker
    studio list [--status inbox] [--line "Project A"]
    studio done "Attention 2017" --depth deep --verdict key --takeaway "..."
    studio milestone "Draft experiments" --date 2026-11-15 --line "Project A" [--kind gate] [--notes "..."]
    studio milestone list [--status todo] [--line "Project A"] [--json]
    studio milestone done "Draft experiments"
    studio idea "use X as a verifier for Y" [--line "Project A"]
    studio idea list [--status new] [--line "Project A"] [--json]
    studio deadlines [--days 90]

`studio milestone "Title" ...` and `studio idea "text"` are short for `milestone add` / `idea add`.
Works whether or not the web app is running (the YAML file is locked while writing).
"""
from __future__ import annotations

import argparse
import json
import sys

from . import plan, reading


def _row(i: dict) -> str:
    tag = i.get("tier") or ""
    lines = ",".join(i.get("lines", []))
    return f"{i['id']}  {i['status']:9} {tag:3} {i['key'][:44]:44} {lines:16} {i.get('reason', '')[:70]}"


def _milestone_row(m: dict) -> str:
    return f"{m['id']}  {m.get('date', ''):10} {m.get('status', 'todo'):5} {m.get('kind', 'milestone'):9} {m.get('line', ''):16} {m.get('title', '')[:70]}"


def _idea_row(i: dict) -> str:
    return f"{i['id']}  {i.get('status', 'new'):8} {i.get('line', ''):16} {i['text'][:70]}"


_SUBCOMMANDS = {"milestone": ("add", "list", "done"), "idea": ("add", "list")}


def _default_to_add(argv: list[str]) -> list[str]:
    """`studio milestone "Title" ...` and `studio idea "text"` mean `... add`."""
    if len(argv) > 1 and argv[0] in _SUBCOMMANDS and argv[1] not in (*_SUBCOMMANDS[argv[0]], "-h", "--help"):
        return [argv[0], "add", *argv[1:]]
    return argv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="studio", description="PhD Studio reading list")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("push", help="add a paper to the reading list (lands in the inbox)")
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--paper", help="paper note name, e.g. 'Attention 2017'")
    who.add_argument("--arxiv", help="arXiv id for a paper without a note yet")
    p.add_argument("--title", help="title for an arXiv-only paper (fetched from arXiv if omitted)")
    p.add_argument("--reason", required=True, help="one line: why this paper, what to look for")
    p.add_argument("--tier", choices=reading.TIERS, help="urgency: T1 = this week")
    p.add_argument("--line", action="append", default=[], help="research line / group; repeatable")
    p.add_argument("--due", help="YYYY-MM-DD")
    p.add_argument("--by", dest="pushed_by", help="who is pushing, e.g. the session name")
    p.add_argument("--source", choices=reading.SOURCES, default="claude")
    p.add_argument("--force", action="store_true", help="re-add even if dismissed or already read")

    ls = sub.add_parser("list", help="show the reading list")
    ls.add_argument("--status", choices=reading.STATUSES)
    ls.add_argument("--line")
    ls.add_argument("--json", action="store_true")

    d = sub.add_parser("done", help="mark a paper as read")
    d.add_argument("key", help="item id, note name or arxiv:<id>")
    d.add_argument("--depth", choices=reading.DEPTHS, default="deep")
    d.add_argument("--verdict", choices=reading.VERDICTS)
    d.add_argument("--takeaway")

    m = sub.add_parser("milestone", help="milestones and gates in the plan: add (the default), list, done")
    msub = m.add_subparsers(dest="action", required=True)
    ma = msub.add_parser("add", help="add a milestone / gate to the plan")
    ma.add_argument("title")
    ma.add_argument("--date", required=True, help="YYYY-MM-DD")
    ma.add_argument("--line")
    ma.add_argument("--kind", choices=plan.KINDS, default="milestone")
    ma.add_argument("--notes")
    ml = msub.add_parser("list", help="show the plan's milestones, soonest first")
    ml.add_argument("--status", choices=plan.M_STATUS)
    ml.add_argument("--line")
    ml.add_argument("--json", action="store_true")
    md = msub.add_parser("done", help="mark a milestone as done")
    md.add_argument("key", help="milestone id, title, or a part of the title")

    i = sub.add_parser("idea", help="the idea inbox: add (the default), list")
    isub = i.add_subparsers(dest="action", required=True)
    ia = isub.add_parser("add", help="add an idea to the idea inbox")
    ia.add_argument("text")
    ia.add_argument("--line")
    il = isub.add_parser("list", help="show the idea inbox, newest first")
    il.add_argument("--status", choices=plan.I_STATUS)
    il.add_argument("--line")
    il.add_argument("--json", action="store_true")

    dl = sub.add_parser("deadlines", help="upcoming watched-venue deadlines, targets and milestones")
    dl.add_argument("--days", type=int, default=90)

    a = ap.parse_args(_default_to_add(list(sys.argv[1:] if argv is None else argv)))
    try:
        if a.cmd == "push":
            item, what = reading.push(paper=a.paper, arxiv=a.arxiv, title=a.title, reason=a.reason,
                                      source=a.source, pushed_by=a.pushed_by, tier=a.tier,
                                      lines=a.line, due=a.due, force=a.force)
            print(f"{what}: {item['id']} {reading.key_of(item)} [{item['status']}]")
        elif a.cmd == "list":
            items = reading.load()
            reading.summary(items)
            sel = [i for i in items if (not a.status or i["status"] == a.status)
                   and (not a.line or a.line in i.get("lines", []))]
            if a.json:
                print(json.dumps(sel, ensure_ascii=False, indent=2))
            else:
                print("\n".join(_row(i) for i in sel) or "(empty)")
        elif a.cmd == "done":
            items = reading.load()
            item = reading.find(items, a.key)
            if item is None:
                raise reading.ReadingError(f"not on the reading list: {a.key}")
            patch = {"status": "done", "depth": a.depth}
            if a.verdict:
                patch["verdict"] = a.verdict
            if a.takeaway:
                patch["takeaway"] = a.takeaway
            item = reading.update(item["id"], patch)
            print(f"done: {item['id']} {reading.key_of(item)}")
        elif a.cmd == "milestone" and a.action == "add":
            rec = plan.add_milestone(a.title, a.date, line=a.line, kind=a.kind, notes=a.notes)
            print(f"added: {rec['id']} {rec['date']} {rec['title']}")
        elif a.cmd == "milestone" and a.action == "list":
            sel = [m for m in plan.milestones() if (not a.status or m.get("status", "todo") == a.status)
                   and (not a.line or m.get("line") == a.line)]
            if a.json:
                print(json.dumps(sel, ensure_ascii=False, indent=2))
            else:
                print("\n".join(_milestone_row(m) for m in sel) or "(empty)")
        elif a.cmd == "milestone":
            ms = plan.find_milestone(a.key, prefer_open=True)
            if ms.get("status") == "done":
                print(f"already done: {ms['id']} {ms['date']} {ms['title']}")
            else:
                ms = plan.update("milestones", ms["id"], {"status": "done"})
                print(f"done: {ms['id']} {ms['date']} {ms['title']}")
        elif a.cmd == "idea" and a.action == "add":
            rec = plan.add_idea(a.text, line=a.line, source="claude")
            print(f"added: {rec['id']} {rec['text'][:60]}")
        elif a.cmd == "idea":
            sel = [i for i in plan.ideas() if (not a.status or i.get("status", "new") == a.status)
                   and (not a.line or i.get("line") == a.line)]
            if a.json:
                print(json.dumps(sel, ensure_ascii=False, indent=2))
            else:
                print("\n".join(_idea_row(i) for i in sel) or "(empty)")
        elif a.cmd == "deadlines":
            for e in plan.timeline(days_back=0, days_ahead=a.days)["events"]:
                if e["days"] >= 0:
                    tag = {"venue": "venue", "target": "TARGET", "milestone": "plan"}[e["type"]]
                    print(f"{e['at'][:16].replace('T', ' ')} UTC  {e['days']:>3}d  {tag:<6} {e['title']}")
    except (reading.ReadingError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

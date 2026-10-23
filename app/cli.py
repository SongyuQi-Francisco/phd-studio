"""`studio` command line: lets any agent session or script push papers to the reading list.

    studio push --paper "Attention 2017" --tier T1 --line "Project A" --reason "..." --by "planning session"
    studio push --arxiv 2606.17328 --reason "..." --by "weekly tracker" --source tracker
    studio list [--status inbox] [--line "Project A"]
    studio done "Attention 2017" --depth deep --verdict key --takeaway "..."

Works whether or not the web app is running (the YAML file is locked while writing).
"""
from __future__ import annotations

import argparse
import json
import sys

from . import reading


def _row(i: dict) -> str:
    tag = i.get("tier") or ""
    lines = ",".join(i.get("lines", []))
    return f"{i['id']}  {i['status']:9} {tag:3} {i['key'][:44]:44} {lines:16} {i.get('reason', '')[:70]}"


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

    a = ap.parse_args(argv)
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
    except reading.ReadingError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

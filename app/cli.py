"""`studio` command line: lets any agent session or script push papers to the reading list.

    studio push --paper "Attention 2017" --tier T1 --line "Project A" --reason "..." --by "planning session"
    studio push --arxiv 2606.17328 --reason "..." --by "weekly tracker" --source tracker
    studio list [--status inbox] [--line "Project A"]
    studio done "Attention 2017" --depth deep --verdict key --takeaway "..."
    studio milestone "Draft experiments" --date 2026-11-15 --line "Project A" [--kind gate] [--notes "..."]
    studio milestone list [--status todo] [--line "Project A"] [--json]
    studio milestone done "Draft experiments"
    studio idea "use X as a verifier for Y" [--line "Project A"] [--tag verifier]
    studio idea list [--status new] [--line "Project A"] [--tag verifier] [--all] [--json]
    studio idea tag i1a2b3 +verifier -todo
    studio idea merge i1a2b3 i4d5e6 [--text "the merged idea"]
    studio idea restore i1a2b3
    studio deadlines [--days 90]
    studio remind [--check [--dry-run] [--now 2026-11-01T09:00] | --test] [--json]

`studio milestone "Title" ...` and `studio idea "text"` are short for `milestone add` / `idea add`.
`idea list` hides ideas that were merged into another one (--all or --status merged shows them).
Works whether or not the web app is running (the YAML file is locked while writing).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from . import idea_ops, plan, reading


def _row(i: dict) -> str:
    tag = i.get("tier") or ""
    lines = ",".join(i.get("lines", []))
    return f"{i['id']}  {i['status']:9} {tag:3} {i['key'][:44]:44} {lines:16} {i.get('reason', '')[:70]}"


def _milestone_row(m: dict) -> str:
    return f"{m['id']}  {m.get('date', ''):10} {m.get('status', 'todo'):5} {m.get('kind', 'milestone'):9} {m.get('line', ''):16} {m.get('title', '')[:70]}"


def _idea_row(i: dict) -> str:
    tags = " ".join("#" + t for t in i.get("tags", []))
    return f"{i['id']}  {i.get('status', 'new'):8} {i.get('line', ''):16} {tags:20} {i['text'][:70]}"


def _tag_ops(tokens: list[str]) -> tuple[list[str], list[str]]:
    """`+x -y z` -> (add [x, z], remove [y]). A bare word adds."""
    add, drop = [], []
    for t in tokens:
        (drop if t.startswith("-") else add).append(t.lstrip("+-"))
    return add, drop


_SUBCOMMANDS = {"milestone": ("add", "list", "done"), "idea": ("add", "list", "tag", "merge", "restore")}


def _default_to_add(argv: list[str]) -> list[str]:
    """`studio milestone "Title" ...` and `studio idea "text"` mean `... add`."""
    if len(argv) > 1 and argv[0] in _SUBCOMMANDS and argv[1] not in (*_SUBCOMMANDS[argv[0]], "-h", "--help"):
        return [argv[0], "add", *argv[1:]]
    return argv


def _remind(a: argparse.Namespace) -> int:
    from . import reminders

    if a.now and not (a.check and a.dry_run):
        raise ValueError("--now only works with --check --dry-run")
    if a.test:
        out = reminders.test_notification()
        print(json.dumps(out, ensure_ascii=False) if a.json else
              f"sent via {out['via']}" if out["ok"] else f"failed ({out['via']}): {out.get('error')}")
        return 0 if out["ok"] else 1
    if a.check:
        now = datetime.fromisoformat(a.now).astimezone() if a.now else None
        out = reminders.check(now=now, dry_run=a.dry_run)
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(f"{out['at']}  {out['device']}" + (f"  skipped: {out['skipped']}" if out.get("skipped") else ""))
            for d in out["due"]:
                print(f"  {'would send' if a.dry_run else 'due'}  {d['title']}  ({d['subtitle']}; {d['body']})")
            for t in out["sent"]:
                print(f"  sent  {t}")
            for f in out["failed"]:
                print(f"  failed  {f['title']}: {f['error']}")
        return 1 if out["failed"] else 0
    st = reminders.status()
    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0
    cfg, ag = st["settings"], st["agent"]
    names = {d["id"]: d["name"] for d in st["devices"]}
    print(f"reminders {'on' if cfg['enabled'] else 'off'} · leads {cfg['leads']} at {cfg['time']}"
          f" · sending Mac {names.get(st['designated'], st['designated'] or '-')} (this Mac: {st['device_name']})"
          f" · LaunchAgent {'installed' if ag['installed'] else 'not installed'}")
    for u in st["upcoming"]:
        print(f"  {u['on']}  {u['title']}  ({u['subtitle']})")
    return 0


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

    i = sub.add_parser("idea", help="the idea inbox: add (the default), list, tag, merge, restore")
    isub = i.add_subparsers(dest="action", required=True)
    ia = isub.add_parser("add", help="add an idea to the idea inbox")
    ia.add_argument("text")
    ia.add_argument("--line")
    ia.add_argument("--tag", action="append", default=[], help="free-form tag; repeatable")
    il = isub.add_parser("list", help="show the idea inbox, newest first")
    il.add_argument("--status", choices=plan.I_STATUS_ALL)
    il.add_argument("--line")
    il.add_argument("--tag", action="append", default=[], help="only ideas with this tag; repeat to require several")
    il.add_argument("--all", action="store_true", help="also list ideas merged into another one")
    il.add_argument("--json", action="store_true")
    it = isub.add_parser("tag", help="add or remove tags: studio idea tag <id> +verifier -todo")
    it.add_argument("id")
    it.add_argument("ops", nargs=argparse.REMAINDER, help="+tag adds, -tag removes (a bare tag adds)")
    im = isub.add_parser("merge", help="merge two or more ideas into one (the originals are kept, hidden)")
    im.add_argument("ids", nargs="+")
    im.add_argument("--text", help="text of the merged idea (default: the first idea's)")
    im.add_argument("--line")
    ir = isub.add_parser("restore", help="bring back an idea that was merged into another one")
    ir.add_argument("id")

    dl = sub.add_parser("deadlines", help="upcoming watched-venue deadlines, targets and milestones")
    dl.add_argument("--days", type=int, default=90)

    r = sub.add_parser("remind", help="deadline reminders (macOS notifications); no flag shows what is coming")
    rg = r.add_mutually_exclusive_group()
    rg.add_argument("--check", action="store_true", help="send the reminders that are due (what the LaunchAgent runs)")
    rg.add_argument("--test", action="store_true", help="send a test notification")
    r.add_argument("--dry-run", action="store_true", help="with --check: show what would be sent; send and record nothing")
    r.add_argument("--now", help="with --check --dry-run: pretend it is this local time, e.g. 2026-11-01T09:00")
    r.add_argument("--json", action="store_true")

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
            rec = plan.add_idea(a.text, line=a.line, tags=a.tag, source="claude")
            print(f"added: {rec['id']} {rec['text'][:60]}")
        elif a.cmd == "idea" and a.action == "tag":
            add, drop = _tag_ops(a.ops)
            if not (add or drop):
                raise ValueError("nothing to do: give tags like +x -y")
            rec = idea_ops.tag(a.id, add, drop)
            print(f"{rec['id']}  " + (" ".join("#" + t for t in rec.get("tags", [])) or "(no tags)"))
        elif a.cmd == "idea" and a.action == "merge":
            rec = idea_ops.merge(a.ids, text=a.text, line=a.line, source="claude")
            print(f"merged: {rec['id']} <- {' '.join(rec['merged_from'])}  {rec['text'][:60]}")
        elif a.cmd == "idea" and a.action == "restore":
            rec = idea_ops.restore(a.id)
            print(f"restored: {rec['id']} [{rec['status']}] {rec['text'][:60]}")
        elif a.cmd == "idea":
            sel = idea_ops.listing(a.status, a.line, a.tag, include_merged=a.all)
            if a.json:
                print(json.dumps(sel, ensure_ascii=False, indent=2))
            else:
                print("\n".join(_idea_row(i) for i in sel) or "(empty)")
        elif a.cmd == "deadlines":
            for e in plan.timeline(days_back=0, days_ahead=a.days)["events"]:
                if e["days"] >= 0:
                    tag = {"venue": "venue", "target": "TARGET", "milestone": "plan"}[e["type"]]
                    print(f"{e['at'][:16].replace('T', ' ')} UTC  {e['days']:>3}d  {tag:<6} {e['title']}")
        elif a.cmd == "remind":
            return _remind(a)
    except KeyError as e:
        if a.cmd != "idea":
            raise
        print(f"error: no idea {e.args[0]}", file=sys.stderr)
        return 2
    except (reading.ReadingError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

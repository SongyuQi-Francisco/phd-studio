"""`studio map ...`: lets experiment scripts and other Claude sessions report results on a research map and
move a node's status, without the app running. Every write is one `ops.apply` batch (who="cli"), locked and
merged field by field like the page's own edits, so the other Mac and an open page see it like any change.

    studio map list [--all] [--json]
    studio map show PROJECT [--json]
    studio map status PROJECT NODE verified [--note "why"]
    studio map note PROJECT "ran the ablation, tool use did not help" [--node NODE]
    studio map result add PROJECT NODE recall@10=0.412 ndcg@10=0.35 --config "Ours k=8" --dataset "MS MARCO" [--baseline] [--status active]
    studio map result list PROJECT [NODE] [--metric recall@10] [--json]
    studio map result import PROJECT NODE results.csv [--col config=method --col value=score ...] [--watch] [--dry-run]
    studio map result sync [PROJECT] [--force] [--loop [SECONDS]]

PROJECT is a slug, title or research-line name; NODE an id, an id prefix or (a part of) its title.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import model as map_model, ops, project
from .results import importer, mapping as maps, model, refs, tabular


def add_parser(sub: Any) -> None:
    m = sub.add_parser("map", help="research maps: list, show, move a node's status, report experiment results")
    msub = m.add_subparsers(dest="action", required=True)

    ml = msub.add_parser("list", help="the projects with their node counts")
    ml.add_argument("--all", action="store_true", help="also archived projects")
    ml.add_argument("--json", action="store_true")

    ms = msub.add_parser("show", help="a project's nodes as a tree (ids, status, number of results)")
    ms.add_argument("project")
    ms.add_argument("--json", action="store_true")

    st = msub.add_parser("status", help="set a node's status")
    st.add_argument("project")
    st.add_argument("node")
    st.add_argument("to", choices=map_model.STATUSES)
    st.add_argument("--note", help="a line for the change log")

    nt = msub.add_parser("note", help="add a line to a project's change log")
    nt.add_argument("project")
    nt.add_argument("text")
    nt.add_argument("--node", help="the node it is about")

    r = msub.add_parser("result", help="experiment results: add, list, import, sync")
    rsub = r.add_subparsers(dest="sub", required=True)

    ra = rsub.add_parser("add", help="report measurements on an experiment / result node: METRIC=VALUE ...")
    ra.add_argument("project")
    ra.add_argument("node")
    ra.add_argument("metrics", nargs="+", metavar="METRIC=VALUE")
    ra.add_argument("--config", required=True, help="the configuration or method measured, e.g. 'Ours k=8'")
    ra.add_argument("--dataset")
    ra.add_argument("--date", help="YYYY-MM-DD (default today)")
    ra.add_argument("--run", help="seed or run id, when one config has several runs")
    ra.add_argument("--baseline", action="store_true", help="this config is a baseline")
    ra.add_argument("--lower", action="store_true", help="smaller is better for these metrics (else guessed from the name)")
    ra.add_argument("--source", default="cli", help="where it came from (default: cli)")
    ra.add_argument("--note")
    ra.add_argument("--status", choices=map_model.STATUSES, help="also set the node's status, in the same write")
    ra.add_argument("--summary", help="the line for the change log")

    rl = rsub.add_parser("list", help="the results recorded on a project or one node")
    rl.add_argument("project")
    rl.add_argument("node", nargs="?")
    rl.add_argument("--metric")
    rl.add_argument("--json", action="store_true")

    ri = rsub.add_parser("import", help="import a CSV / TSV / JSON / JSON-lines file ('-' reads stdin) onto a node")
    ri.add_argument("project")
    ri.add_argument("node")
    ri.add_argument("file")
    ri.add_argument("--col", action="append", default=[], metavar="ROLE=COLUMN",
                    help="which column is the config / dataset / metric / value / date / run / baseline; "
                         "ROLE=@text fixes one value. Default: the mapping used for this file before, else a guess")
    ri.add_argument("--metrics", nargs="+", metavar="COLUMN", help="wide table: these columns are metrics")
    ri.add_argument("--baseline", action="append", default=[], metavar="CONFIG", help="a config that is a baseline; repeatable")
    ri.add_argument("--lower", action="append", default=[], metavar="METRIC", help="a metric where smaller is better; repeatable")
    ri.add_argument("--date", help="date for rows without one (default: the file's date, or today)")
    ri.add_argument("--format", choices=model.FORMATS)
    ri.add_argument("--watch", action="store_true", help="re-read this file whenever it changes (this Mac, while the app or --loop runs)")
    ri.add_argument("--unwatch", action="store_true")
    ri.add_argument("--no-remember", action="store_true", help="don't keep the file and its mapping on the node")
    ri.add_argument("--dry-run", action="store_true", help="check it and say what would change; write nothing")
    ri.add_argument("--json", action="store_true")

    rs = rsub.add_parser("sync", help="read the files this Mac watches now")
    rs.add_argument("project", nargs="?")
    rs.add_argument("--force", action="store_true", help="even when the file did not change")
    rs.add_argument("--loop", nargs="?", const=10.0, type=float, metavar="SECONDS", help="keep watching (Ctrl-C stops)")
    rs.add_argument("--json", action="store_true")


def _json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _pair(text: str) -> tuple[str, str]:
    name, sep, value = text.rpartition("=")
    if not sep or not name.strip() or not value.strip():
        raise ValueError(f"expected METRIC=VALUE, got {text!r}")
    return name.strip(), value.strip()


def _children(m: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    by_id = {n["id"]: n for n in m["nodes"]}
    kids: dict[str, list[dict[str, Any]]] = {}
    has_parent = set()
    for e in sorted(m["edges"], key=lambda e: e.get("created_at", "")):
        if e["kind"] == "refines" and e["from"] in by_id and e["to"] in by_id:
            kids.setdefault(e["from"], []).append(by_id[e["to"]])
            has_parent.add(e["to"])
    return kids, [n for n in m["nodes"] if n["id"] not in has_parent]


def _show(slug: str, as_json: bool) -> int:
    m = project.get(slug).read()
    counts: dict[str, int] = {}
    for a in m["attachments"]["results"]:
        counts[a["node"]] = counts.get(a["node"], 0) + 1
    if as_json:
        _json({"project": {"slug": slug, **{k: m["project"].get(k) for k in ("title", "line")}},
               "nodes": [{"id": n["id"], "type": n["type"], "status": n.get("status", "todo"), "title": n["title"],
                          "results": counts.get(n["id"], 0)} for n in m["nodes"]],
               "edges": [{k: e.get(k) for k in ("id", "from", "to", "kind")} for e in m["edges"]]})
        return 0
    print(f"{m['project'].get('title')}  ({slug})" + (f"  line: {m['project']['line']}" if m["project"].get("line") else ""))
    kids, roots = _children(m)
    seen: set[str] = set()

    def walk(n: dict[str, Any], depth: int) -> None:
        if n["id"] in seen:
            return
        seen.add(n["id"])
        extra = f"  [{counts[n['id']]} results]" if counts.get(n["id"]) else ""
        print(f"{n['id']}  {n.get('status', 'todo'):9} {n['type']:10} {'  ' * depth}{n['title']}{extra}")
        for c in kids.get(n["id"], []):
            walk(c, depth + 1)

    for n in roots:
        walk(n, 0)
    return 0


def _list(all_: bool, as_json: bool) -> int:
    rows = project.listing(archived=all_)
    if as_json:
        _json(rows)
        return 0
    for p in rows:
        st = " ".join(f"{k} {v}" for k, v in p["status"].items() if v)
        print(f"{p['slug']}  {p.get('title', ''):28} {p.get('line') or '':16} {p['nodes']} nodes  {st}")
    if not rows:
        print("(no research maps)")
    return 0


def _status(slug: str, node_key: str, to: str, note: str | None) -> int:
    m = project.get(slug).read()
    node = refs.find_node(m, node_key)
    if node.get("status", "todo") == to:
        print(f"unchanged: {node['id']} {node['title']} is already {to}")
        return 0
    ops.apply(slug, [{"op": "node.update", "id": node["id"], "set": {"status": to}}], who="cli",
              summary=note or f"{node['title']}: {node.get('status', 'todo')} → {to}")
    print(f"{node['id']} {node['title']}: {node.get('status', 'todo')} -> {to}")
    return 0


def _note(slug: str, text: str, node_key: str | None) -> int:
    nodes = []
    if node_key:
        nodes = [refs.find_node(project.get(slug).read(), node_key)["id"]]
    ops.apply(slug, [{"op": "log", "text": text, "nodes": nodes}], who="cli")
    print("noted")
    return 0


def _add(a: argparse.Namespace, slug: str) -> int:
    base: dict[str, Any] = {"config": a.config}
    for key, val in (("dataset", a.dataset), ("date", a.date), ("run", a.run), ("note", a.note)):
        if val:
            base[key] = val
    if a.baseline:
        base["baseline"] = True
    if a.lower:
        base["better"] = "lower"
    items = [{**base, "metric": name, "value": value} for name, value in map(_pair, a.metrics)]
    r = importer.report(slug, a.node, items, status=a.status, summary=a.summary, source=a.source)
    if not r["applied"]:
        print(f"unchanged: {len(items)} result(s) already recorded")
        return 0
    print(f"recorded: {r['added']} added, {r['updated']} updated on {r['node']}" + (f"; status -> {a.status}" if a.status else ""))
    return 0


def _results_list(slug: str, node_key: str | None, metric: str | None, as_json: bool) -> int:
    m = project.get(slug).read()
    node = refs.find_node(m, node_key) if node_key else None
    titles = {n["id"]: n["title"] for n in m["nodes"]}
    rows = [a for a in m["attachments"]["results"] if (not node or a["node"] == node["id"])
            and (not metric or a.get("metric", "").lower() == metric.lower())]
    rows.sort(key=lambda a: (a["node"], a.get("metric", "").lower(), a.get("dataset") or "", a.get("date") or "", a.get("config", "")))
    if as_json:
        _json([{**{k: v for k, v in a.items() if k not in ("clock", "created_at", "updated_at")}, "node_title": titles.get(a["node"])} for a in rows])
        return 0
    for a in rows:
        print(f"{a['id']}  {a.get('date') or '-':10} {'*' if a.get('baseline') else ' '} {a.get('config', ''):24} {a.get('dataset') or '':14} "
              f"{a.get('metric', '').upper():14} {a['value']:>10g}  {'' if node else titles.get(a['node'], '')}")
    if not rows:
        print("(no results)")
    return 0


def _mapping_args(a: argparse.Namespace) -> dict[str, Any]:
    """Overrides typed on the command line, on top of whatever mapping the importer finds."""
    m: dict[str, Any] = {}
    for spec in a.col:
        role, sep, col = spec.partition("=")
        role = role.strip()
        if not sep or role not in model.ROLES or not col:
            raise ValueError(f"--col expects ROLE=COLUMN with ROLE one of {', '.join(model.ROLES)}; got {spec!r}")
        m[role] = {"const": col[1:]} if col.startswith("@") else {"col": col}
    if a.metrics:
        m["metrics"] = a.metrics
    if a.baseline:
        m["baselines"] = a.baseline
    if a.lower:
        m["lower"] = a.lower
    return m


def _import(a: argparse.Namespace, slug: str) -> int:
    over = _mapping_args(a)
    if a.file == "-":
        text, path = sys.stdin.read(), None
        label = "stdin"
    else:
        text, path, label = None, str(Path(a.file).expanduser().resolve()), None
    table = tabular.read_file(path, in_roots=False) if path else tabular.parse(text or "", a.format)
    shown = table.meta.get("path")
    known = project.get(slug).read()["attachments"]["result_sources"]
    chosen = maps.remembered(table, known, shown) or maps.guess(table)
    if over:
        if "metrics" in over:  # wide form replaces a metric / value pair
            chosen = {k: v for k, v in chosen.items() if k not in ("metric", "value")}
        chosen = {**chosen, **over}
    watch = True if a.watch else False if a.unwatch else None
    r = importer.run_import(slug, a.node, text=text, path=path, fmt=a.format, mapping=chosen, remember=not a.no_remember,
                            watch=watch, label=label, default_date=a.date, who="cli", dry_run=a.dry_run, trusted=True)
    if a.json:
        _json(r)
        return 0
    head = "would import" if a.dry_run else "imported"
    print(f"{head}: {r['added']} added, {r['updated']} updated, {r['unchanged']} unchanged"
          + (f", {r['skipped']} rows skipped" if r["skipped"] else "") + f" from {r['source']}"
          + (" [watching]" if watch else ""))
    for s in r.get("skipped_rows", [])[:3]:
        print(f"  skipped row {s['row']}: {s['reason']}" + (f" ({s.get('metric')}: {s.get('cell')})" if s.get("cell") else ""))
    for line in r.get("sample", [])[:5]:
        print(f"  {line}")
    return 0


def _sync(slug: str | None, force: bool, loop: float | None, as_json: bool) -> int:
    def once() -> list[dict[str, Any]]:
        done = importer.sync(slug, force=force)
        for d in done:
            if as_json:
                print(json.dumps(d, ensure_ascii=False))
            elif d.get("error"):
                print(f"error: {d['path']}: {d['error']}", file=sys.stderr)
            else:
                print(f"synced: {d['path']}: {d['added']} added, {d['updated']} updated")
        return done

    if loop is None:
        done = once()
        if not done and not as_json:
            print("nothing changed" if importer.watched(slug) else "no watched files on this Mac")
        return 1 if any(d.get("error") for d in done) else 0
    try:
        while True:
            once()
            time.sleep(max(1.0, loop))
    except KeyboardInterrupt:
        return 0


def run(a: argparse.Namespace) -> int:
    """Run a parsed `studio map ...` command; errors are ValueError / KeyError for cli.main to print."""
    if a.action == "list":
        return _list(a.all, a.json)
    if a.action == "result" and a.sub == "sync":
        if a.project:
            a.project = refs.find_project(a.project)
        return _sync(a.project, a.force, a.loop, a.json)
    slug = refs.find_project(a.project)
    if a.action == "show":
        return _show(slug, a.json)
    if a.action == "status":
        return _status(slug, a.node, a.to, a.note)
    if a.action == "note":
        return _note(slug, a.text, a.node)
    if a.sub == "add":
        return _add(a, slug)
    if a.sub == "list":
        return _results_list(slug, a.node, a.metric, a.json)
    return _import(a, slug)


__all__ = ["add_parser", "run"]

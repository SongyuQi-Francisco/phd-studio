"""Obsidian Canvas view of a map (<project>/map.canvas): generated, read-only.

Written only after a write made on this Mac (an event subscriber that skips events synced from the other
Mac: that Mac writes its own canvas, and Syncthing brings it over), and only when the content changes, so
the two Macs never race over the file. Writes take a lock and a temp file of their own in the data dir;
Syncthing conflict copies of the canvas are removed (it is regenerated anyway). Labels follow the vault
language (`[profile] vault_language`), so both Macs write the same text.

Same layering as the page (web/rmap/layout.js): columns from refines edges and the node types, rows along
refines only (depends edges are cross-links), pinned positions kept and kept clear of other nodes.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import secrets
from typing import Any

from .. import i18n
from ..config import DATA_DIR, DEVICE
from . import model
from .project import LOCKS, Project, back_edges, dag_edges, flow_order, get
from .registry import on_event

log = logging.getLogger(__name__)

W, H, GX, GY = 280, 112, 90, 24      # canvas node size and gaps (fixed height: at most 2 evidence lines)
EVIDENCE_LINES = 2
WEB_W, WEB_H, WEB_GX, WEB_GY = 150, 46, 62, 14  # the page's grid, to carry pinned positions over
WEB_PAD_X, WEB_PAD_Y = 22, 44
COLOR = {"todo": "#5b6476", "active": "#3987e5", "verified": "#1baf7a", "blocked": "#d9a23a", "refuted": "#5b6476",
         "dropped": "#5b6476"}


def _order(n: dict[str, Any]) -> tuple:
    return (n.get("order", 1e9), n.get("created_at", ""), n["id"])


def _overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) < W + GX / 2 and abs(a[1] - b[1]) < H + GY / 2


def positions(m: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Node id -> (x, y) on the canvas."""
    nodes = {n["id"]: n for n in m["nodes"]}
    flow = [e for e in dag_edges(m) if e.get("from") in nodes and e.get("to") in nodes]
    cycle = back_edges(flow)  # already marked by read(); again for a map put together by hand
    parents: dict[str, list[str]] = {i: [] for i in nodes}   # refines parents: columns and rows
    kids: dict[str, list[str]] = {i: [] for i in nodes}
    order_in: dict[str, int] = {i: 0 for i in nodes}          # all flow edges: the walk order
    order_out: dict[str, list[str]] = {i: [] for i in nodes}
    for e in sorted(flow, key=flow_order):
        if e["id"] in cycle:
            continue
        order_out[e["from"]].append(e["to"])
        order_in[e["to"]] += 1
        if e["kind"] == "refines":
            parents[e["to"]].append(e["from"])
            kids[e["from"]].append(e["to"])
    col: dict[str, int] = {}
    queue = [n["id"] for n in sorted((nodes[i] for i in nodes if not order_in[i]), key=_order)]
    indeg = dict(order_in)
    while queue:
        i = queue.pop(0)
        hint = model.NODE_TYPES.get(nodes[i].get("type"), {}).get("col")
        col[i] = max([hint or 0, *[col[p] + 1 for p in parents[i] if p in col]])
        for k in order_out[i]:
            indeg[k] -= 1
            if not indeg[k]:
                queue.append(k)
    row: dict[str, float] = {}
    placed: set[str] = set()
    nxt = [0.0]

    def place(i: str) -> None:
        placed.add(i)
        own = [k for k in sorted(kids[i], key=lambda k: _order(nodes[k])) if k not in placed]
        placed.update(own)
        rows = []
        for k in own:
            place(k)
            if k in row:
                rows.append(row[k])
        if nodes[i].get("pin"):
            return
        if rows:
            row[i] = (min(rows) + max(rows)) / 2
        else:
            row[i] = nxt[0]
            nxt[0] += 1

    for r in sorted((nodes[i] for i in nodes if not parents[i]), key=_order):
        if nxt[0]:
            nxt[0] += 0.6
        place(r["id"])
    pos: dict[str, tuple[float, float]] = {}
    for i, n in nodes.items():
        if n.get("pin"):
            pin = n["pin"]
            pos[i] = (round((pin["x"] - WEB_PAD_X) * (W + GX) / (WEB_W + WEB_GX)),
                      round((pin["y"] - WEB_PAD_Y) * (H + GY) / (WEB_H + WEB_GY)))
    taken = list(pos.values())
    for i in sorted((i for i in nodes if i not in pos), key=lambda i: (col.get(i, 0), row.get(i, 0), i)):
        x, y = col.get(i, 0) * (W + GX), row.get(i, 0) * (H + GY)
        while any(_overlaps((x, y), p) for p in taken):  # keep clear of pinned nodes (and of nodes moved for them)
            y += H + GY
        pos[i] = (x, round(y))
        taken.append(pos[i])
    return pos


def _evidence_line(a: dict[str, Any]) -> str:
    if a.get("type") == "url":
        return f"- [{a.get('title') or a['target']}]({a['target']})"
    if a.get("type") == "paper":
        return f"- [[{a['target']}]]"
    target = a["target"].removesuffix(".md")
    return f"- [[{target}|{a.get('title') or target.rsplit('/', 1)[-1]}]]"


def render(m: dict[str, Any]) -> str:
    pos = positions(m)
    ev: dict[str, list[dict[str, Any]]] = {}
    for a in m["attachments"].get("evidence", []):
        ev.setdefault(a["node"], []).append(a)
    nodes = [{"id": "rmap-header", "type": "text", "x": 0, "y": -150, "width": 3 * W, "height": 90, "color": "#5b6476",
              "text": f"# {m['project'].get('title', m['slug'])}\n{i18n.tv('rmap.canvas_header')}"}]
    for n in sorted(m["nodes"], key=lambda n: n["id"]):
        x, y = pos[n["id"]]
        st = n.get("status", "todo")
        # i18n-keys: rmap.type.{question,hypothesis,subline,experiment,result,decision,kill,open} rmap.status.{todo,active,verified,blocked,refuted,dropped}
        text = f"**{n['title']}**\n{i18n.tv('rmap.type.' + n.get('type', 'hypothesis'))} · {i18n.tv('rmap.status.' + st)}"
        items = sorted(ev.get(n["id"], []), key=lambda a: a["id"])
        if items:
            text += "\n" + "\n".join(_evidence_line(a) for a in items[:EVIDENCE_LINES])
            if len(items) > EVIDENCE_LINES:
                text += "\n" + i18n.tv("rmap.canvas_more", n=len(items) - EVIDENCE_LINES)
        nodes.append({"id": n["id"], "type": "text", "x": x, "y": y, "width": W, "height": H,
                      "color": COLOR.get(st, "#5b6476"), "text": text})
    status = {n["id"]: n.get("status", "todo") for n in m["nodes"]}
    edges = []
    for e in sorted(m["edges"], key=lambda e: e["id"]):
        if e["from"] not in pos or e["to"] not in pos:
            continue
        lateral = e.get("kind") not in model.FLOW
        row = {"id": e["id"], "fromNode": e["from"], "fromSide": "bottom" if lateral else "right",
               "toNode": e["to"], "toSide": "top" if lateral else "left", "color": COLOR.get(status[e["to"]], "#5b6476")}
        # i18n-keys: rmap.edge.{refines,depends,alternative,conflicts}
        kind = "" if e.get("kind") == "refines" else i18n.tv("rmap.edge." + e.get("kind", "refines"))
        label = " · ".join(x for x in (kind, e.get("label") or "") if x)
        if label:
            row["label"] = label
        if lateral:
            row["toEnd"] = "none"
        edges.append(row)
    return json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=1) + "\n"


def write(p: Project, m: dict[str, Any] | None = None) -> bool:
    """Bring map.canvas up to date; True if it was (re)written."""
    m = m or p.read()
    text = render(m)
    path = p.dir / "map.canvas"
    LOCKS.mkdir(parents=True, exist_ok=True)
    with (LOCKS / f"{p.slug}.canvas.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for copy in p.dir.glob("map.sync-conflict-*.canvas"):  # generated: nothing to merge
            copy.unlink(missing_ok=True)
        try:
            if path.exists() and path.read_text(encoding="utf-8") == text:
                return False
        except OSError:
            pass
        tmp = DATA_DIR / "rmap" / f"{p.slug}.canvas.{os.getpid()}.{secrets.token_hex(3)}.tmp"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    return True


@on_event
def _after_write(slug: str, events: list[dict[str, Any]]) -> None:
    if not any(e.get("device") == DEVICE for e in events):
        return  # only from the other Mac: its own canvas comes over with them
    try:
        write(get(slug))
    except (KeyError, OSError):
        log.exception("could not write the canvas of %s", slug)

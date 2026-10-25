"""Obsidian Canvas view of a map (<project>/map.canvas): generated, read-only, rewritten after every change
and whenever the map is read and the file is out of date (hand edits, a sync from the other Mac).

Same layering as the page (web/rmap/layout.js) in a coarser grid, so it reads the same in Obsidian.
The output is deterministic (no timestamps), so two Macs write identical files and Syncthing has no
conflict to make; the file is only written when its content changes.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from ..config import DATA_DIR
from . import model
from .project import LOCKS, Project, back_edges, dag_edges, flow_order, get
from .registry import on_event

log = logging.getLogger(__name__)

W, H, GX, GY = 280, 96, 90, 24       # canvas node size and gaps
WEB_W, WEB_H, WEB_GX, WEB_GY = 150, 46, 62, 14  # the page's grid, to carry pinned positions over
STATUS = {"todo": "未开始", "active": "进行中", "verified": "已验证", "blocked": "没打通", "refuted": "证伪", "dropped": "放弃"}
TYPE = {"question": "问题", "hypothesis": "路径/假设", "subline": "子线", "experiment": "实验", "result": "结果",
        "decision": "决策", "kill": "Kill criteria", "open": "开放问题"}
EDGE = {"refines": "", "depends": "依赖", "alternative": "备选", "conflicts": "冲突"}
COLOR = {"todo": "#5b6476", "active": "#3987e5", "verified": "#1baf7a", "blocked": "#d9a23a", "refuted": "#5b6476",
         "dropped": "#5b6476"}


def _order(n: dict[str, Any]) -> tuple:
    return (n.get("order", 1e9), n.get("created_at", ""), n["id"])


def positions(m: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Node id -> (x, y) on the canvas: columns from refines edges and type columns, rows depth first."""
    nodes = {n["id"]: n for n in m["nodes"]}
    out: dict[str, list[str]] = {i: [] for i in nodes}
    inn: dict[str, list[str]] = {i: [] for i in nodes}
    refines: set[tuple[str, str]] = set()
    flow = [e for e in dag_edges(m) if e.get("from") in nodes and e.get("to") in nodes]
    cycle = back_edges(flow)  # already marked by read(); again for a map put together by hand
    for e in sorted(flow, key=flow_order):
        a, b = e["from"], e["to"]
        if e["id"] in cycle:
            continue
        out[a].append(b)
        inn[b].append(a)
        if e["kind"] == "refines":
            refines.add((a, b))
    col: dict[str, int] = {}
    indeg = {i: len(inn[i]) for i in nodes}
    queue = sorted((nodes[i] for i in nodes if not indeg[i]), key=_order)
    queue_ids = [n["id"] for n in queue]
    while queue_ids:
        i = queue_ids.pop(0)
        hint = model.NODE_TYPES.get(nodes[i].get("type"), {}).get("col")
        col[i] = max([hint or 0, *[col[p] + 1 for p in inn[i] if (p, i) in refines]])
        for k in out[i]:
            indeg[k] -= 1
            if not indeg[k]:
                queue_ids.append(k)
    row: dict[str, float] = {}
    placed: set[str] = set()
    nxt = [0.0]

    def place(i: str) -> None:
        placed.add(i)
        own = [k for k in sorted(out[i], key=lambda k: _order(nodes[k])) if k not in placed]
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

    for r in sorted((nodes[i] for i in nodes if not inn[i]), key=_order):
        if nxt[0]:
            nxt[0] += 0.6
        place(r["id"])
    pos = {}
    for i, n in nodes.items():
        if n.get("pin"):
            pin = n["pin"]
            pos[i] = (round(pin["x"] * (W + GX) / (WEB_W + WEB_GX)), round(pin["y"] * (H + GY) / (WEB_H + WEB_GY)))
        else:
            pos[i] = (col.get(i, 0) * (W + GX), round(row.get(i, 0) * (H + GY)))
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
              "text": f"# {m['project'].get('title', m['slug'])}\n由 PhD Studio 生成，只读：请在工作台的「研究地图」里修改，这里的改动会被覆盖。"}]
    for n in sorted(m["nodes"], key=lambda n: n["id"]):
        x, y = pos[n["id"]]
        st = n.get("status", "todo")
        text = f"**{n['title']}**\n{TYPE.get(n.get('type'), n.get('type'))} · {STATUS.get(st, st)}"
        if ev.get(n["id"]):
            text += "\n" + "\n".join(_evidence_line(a) for a in sorted(ev[n["id"]], key=lambda a: a["id"]))
        nodes.append({"id": n["id"], "type": "text", "x": x, "y": y, "width": W,
                      "height": H + 24 * min(4, len(ev.get(n["id"], []))), "color": COLOR.get(st, "#5b6476"), "text": text})
    status = {n["id"]: n.get("status", "todo") for n in m["nodes"]}
    edges = []
    for e in sorted(m["edges"], key=lambda e: e["id"]):
        if e["from"] not in pos or e["to"] not in pos:
            continue
        lateral = e.get("kind") not in model.FLOW
        row = {"id": e["id"], "fromNode": e["from"], "fromSide": "bottom" if lateral else "right",
               "toNode": e["to"], "toSide": "top" if lateral else "left", "color": COLOR.get(status[e["to"]], "#5b6476")}
        label = " · ".join(x for x in (EDGE.get(e.get("kind"), ""), e.get("label") or "") if x)
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
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    LOCKS.mkdir(parents=True, exist_ok=True)
    tmp = DATA_DIR / "rmap" / f"{p.slug}.canvas.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return True


@on_event
def _after_write(slug: str, events: list[dict[str, Any]]) -> None:
    try:
        write(get(slug))
    except (KeyError, OSError):
        log.exception("could not write the canvas of %s", slug)

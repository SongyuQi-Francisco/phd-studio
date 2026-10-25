"""Every change to a research map goes through `apply`: a batch of operations, validated as a whole,
written under the map's lock, and logged as events (one per changed record, sharing a `group`).

Operations (ids may be "$name" refs to records added earlier in the same batch):

    {"op": "node.add",      "ref": "$q", "set": {type, title, status, body, pin, order, tags, ext},
                            "parent": <node>, "edge": "refines"}      # parent: also adds parent -> node
    {"op": "node.update",   "id": <node>, "set": {...}}
    {"op": "node.delete",   "id": <node>}                              # tombstones its edges + attachments
    {"op": "edge.add",      "ref": "$e", "from": <node>, "to": <node>, "kind": "refines", "label": "..."}
    {"op": "edge.update",   "id": <edge>, "set": {kind, label}}
    {"op": "edge.delete",   "id": <edge>}
    {"op": "attach.add",    "ref": "$v", "kind": "evidence", "node": <node>, "set": {...}}
    {"op": "attach.update", "kind": "evidence", "id": <id>, "set": {...}}
    {"op": "attach.delete", "kind": "evidence", "id": <id>}
    {"op": "log",           "text": "one line for the change log"}
    {"op": "project.update", "set": {title, line, note, description, archived}}
"""
from __future__ import annotations

import secrets
from contextlib import ExitStack
from typing import Any

from ..config import DEVICE, RMAP_DIR
from ..store import now
from . import model
from .project import PROJECT_ID, Project, get
from .registry import KINDS, publish

OPS = ("node.add", "node.update", "node.delete", "edge.add", "edge.update", "edge.delete",
       "attach.add", "attach.update", "attach.delete", "log", "project.update")
MAX_OPS = 400


def _rid(prefix: str, taken: set[str], nbytes: int = 4) -> str:
    while True:
        rid = prefix + secrets.token_hex(nbytes)
        if rid not in taken:
            taken.add(rid)
            return rid


def _snap(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if k not in ("created_at", "updated_at", "deleted") and v not in (None, "", [])}


def _changes(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[Any]]:
    return {k: [old.get(k), v] for k, v in new.items() if old.get(k) != v}


def _reaches(edges: list[dict[str, Any]], start: str, goal: str) -> bool:
    """Whether goal is reachable from start along live flow edges."""
    nxt: dict[str, list[str]] = {}
    for e in edges:
        if not e.get("deleted") and e.get("kind") in model.FLOW:
            nxt.setdefault(e["from"], []).append(e["to"])
    seen, stack = set(), [start]
    while stack:
        cur = stack.pop()
        if cur == goal:
            return True
        if cur not in seen:
            seen.add(cur)
            stack.extend(nxt.get(cur, []))
    return False


class _Batch:
    def __init__(self, p: Project, data: dict[str, list[dict[str, Any]]], who: str, group: str | None):
        self.p, self.data, self.who, self.group = p, data, who, group
        self.refs: dict[str, str] = {}
        self.events: list[dict[str, Any]] = []
        self.att: dict[str, list[dict[str, Any]]] = {}  # attachment records per kind, filled by _kind_data
        self.stack: ExitStack | None = None
        self.taken = {r["id"] for c in data.values() for r in c}
        self.at = now()

    # ---- lookups ----
    def ref(self, v: Any, what: str = "id") -> str:
        if not isinstance(v, str) or not v:
            raise ValueError(f"缺少 {what}")
        if v.startswith("$"):
            if v not in self.refs:
                raise ValueError(f"{v} 在这批操作里还没定义")
            return self.refs[v]
        return v

    def node(self, nid: str) -> dict[str, Any]:
        rec = next((n for n in self.data["nodes"] if n["id"] == nid and not n.get("deleted")), None)
        if rec is None:
            raise KeyError(f"没有这个节点：{nid}")
        return rec

    def edge(self, eid: str) -> dict[str, Any]:
        rec = next((e for e in self.data["edges"] if e["id"] == eid and not e.get("deleted")), None)
        if rec is None:
            raise KeyError(f"没有这条连线：{eid}")
        return rec

    def kind_data(self, name: str) -> list[dict[str, Any]]:
        if name not in KINDS:
            raise ValueError(f"没有这种挂载：{name}")
        if name not in self.att:
            store = self.p.kind_store(KINDS[name])
            self.att[name] = self.stack.enter_context(store.editing(skip_unchanged=True))[name]
            self.taken |= {r["id"] for r in self.att[name]}
        return self.att[name]

    def define(self, op: dict[str, Any], rid: str) -> None:
        ref = op.get("ref")
        if ref is not None:
            if not (isinstance(ref, str) and ref.startswith("$") and len(ref) > 1) or ref in self.refs:
                raise ValueError(f"ref 应为没用过的 $名字：{ref}")
            self.refs[ref] = rid

    def log(self, kind: str, action: str, target: str, nodes: list[str], *, title: str | None = None,
            record: dict[str, Any] | None = None, changes: dict[str, Any] | None = None,
            summary: str | None = None) -> None:
        self.events.append({"kind": kind, "action": action, "target": target, "nodes": nodes, "title": title,
                            "record": record, "changes": changes, "summary": summary})

    # ---- operations ----
    def node_add(self, op: dict[str, Any]) -> None:
        fields = model.node_patch(op.get("set") or {})
        parent = self.node(self.ref(op["parent"], "parent")) if op.get("parent") else None
        rec = {"id": _rid("n", self.taken), **fields, "created_at": self.at, "updated_at": self.at}
        self.data["nodes"].append(rec)
        self.define(op, rec["id"])
        self.log("node", "add", rec["id"], [rec["id"]], title=rec["title"], record=_snap(rec))
        if parent:
            self.edge_add({"from": parent["id"], "to": rec["id"], "kind": op.get("edge") or "refines"})

    def node_update(self, op: dict[str, Any]) -> None:
        rec = self.node(self.ref(op.get("id")))
        new = model.node_patch(op.get("set") or {}, rec)
        changes = _changes(rec, new)
        if not changes:
            return
        for k, v in new.items():
            if v is None:
                rec.pop(k, None)
            else:
                rec[k] = v
        rec["updated_at"] = self.at
        self.log("node", "update", rec["id"], [rec["id"]], title=rec["title"], changes=changes)

    def node_delete(self, op: dict[str, Any]) -> None:
        rec = self.node(self.ref(op.get("id")))
        for e in self.data["edges"]:
            if not e.get("deleted") and rec["id"] in (e.get("from"), e.get("to")):
                self._tomb(e)
                self.log("edge", "delete", e["id"], [e["from"], e["to"]], record=_snap(e))
        for name in KINDS:
            for a in self.kind_data(name):
                if not a.get("deleted") and a.get("node") == rec["id"]:
                    self._tomb(a)
                    self.log(name, "delete", a["id"], [rec["id"]], title=rec["title"], record=_snap(a))
        self._tomb(rec)
        self.log("node", "delete", rec["id"], [rec["id"]], title=rec["title"], record=_snap(rec))

    def _tomb(self, rec: dict[str, Any]) -> None:
        rec["deleted"] = True
        rec["updated_at"] = self.at

    def edge_add(self, op: dict[str, Any]) -> None:
        src, dst = self.node(self.ref(op.get("from"), "from")), self.node(self.ref(op.get("to"), "to"))
        kind = op.get("kind") or "refines"
        if kind not in model.EDGE_KINDS:
            raise ValueError(f"kind 只能是 {', '.join(model.EDGE_KINDS)}")
        if src["id"] == dst["id"]:
            raise ValueError("不能连到自己")
        pair = {(src["id"], dst["id"])} | ({(dst["id"], src["id"])} if kind not in model.FLOW else set())
        if any(not e.get("deleted") and e.get("kind") == kind and (e["from"], e["to"]) in pair for e in self.data["edges"]):
            raise ValueError("这两个节点之间已经有这种连线")
        if kind in model.FLOW and _reaches(self.data["edges"], dst["id"], src["id"]):
            raise ValueError("这条连线会形成环：地图从左到右，后面的节点不能再指回前面")
        label = model.edge_patch({"label": op["label"]}).get("label") if op.get("label") else None
        rec = {"id": _rid("e", self.taken), "from": src["id"], "to": dst["id"], "kind": kind, "label": label,
               "created_at": self.at, "updated_at": self.at}
        rec = {k: v for k, v in rec.items() if v is not None}
        self.data["edges"].append(rec)
        self.define(op, rec["id"])
        self.log("edge", "add", rec["id"], [src["id"], dst["id"]], record=_snap(rec))

    def edge_update(self, op: dict[str, Any]) -> None:
        rec = self.edge(self.ref(op.get("id")))
        new = model.edge_patch(op.get("set") or {})
        changes = _changes(rec, new)
        if not changes:
            return
        if "kind" in changes and new["kind"] in model.FLOW and rec["kind"] not in model.FLOW:
            if _reaches(self.data["edges"], rec["to"], rec["from"]):
                raise ValueError("改成这种连线会形成环")
        for k, v in new.items():
            if v is None:
                rec.pop(k, None)
            else:
                rec[k] = v
        rec["updated_at"] = self.at
        self.log("edge", "update", rec["id"], [rec["from"], rec["to"]], changes=changes)

    def edge_delete(self, op: dict[str, Any]) -> None:
        rec = self.edge(self.ref(op.get("id")))
        self._tomb(rec)
        self.log("edge", "delete", rec["id"], [rec["from"], rec["to"]], record=_snap(rec))

    def attach_add(self, op: dict[str, Any]) -> None:
        name = op.get("kind")
        recs = self.kind_data(name)
        kind = KINDS[name]
        node = self.node(self.ref(op.get("node"), "node"))
        if kind.node_types and node.get("type") not in kind.node_types:
            raise ValueError(f"{name} 只能挂在 {', '.join(kind.node_types)} 节点上")
        fields = {k: v for k, v in kind.clean(op.get("set") or {}, new=True).items() if v not in (None, "", [])}
        rec = {"id": _rid(kind.prefix, self.taken), "node": node["id"], **fields, "created_at": self.at, "updated_at": self.at}
        recs.append(rec)
        self.define(op, rec["id"])
        self.log(name, "add", rec["id"], [node["id"]], title=node["title"], record=_snap(rec), summary=kind.line(rec))

    def _attachment(self, op: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        recs = self.kind_data(op.get("kind"))
        rid = self.ref(op.get("id"))
        rec = next((r for r in recs if r["id"] == rid and not r.get("deleted")), None)
        if rec is None:
            raise KeyError(f"没有这条 {op.get('kind')}：{rid}")
        return recs, rec

    def attach_update(self, op: dict[str, Any]) -> None:
        _, rec = self._attachment(op)
        kind = KINDS[op["kind"]]
        new = kind.clean(op.get("set") or {}, new=False)
        changes = _changes(rec, new)
        if not changes:
            return
        for k, v in new.items():
            if v in (None, "", []):
                rec.pop(k, None)
            else:
                rec[k] = v
        missing = [k for k in kind.required if rec.get(k) in (None, "", [])]
        if missing:
            raise ValueError(f"{kind.name} 不能去掉：{', '.join(missing)}")
        rec["updated_at"] = self.at
        node = next((n for n in self.data["nodes"] if n["id"] == rec.get("node")), {})
        self.log(kind.name, "update", rec["id"], [rec.get("node")], title=node.get("title"), changes=changes)

    def attach_delete(self, op: dict[str, Any]) -> None:
        _, rec = self._attachment(op)
        self._tomb(rec)
        node = next((n for n in self.data["nodes"] if n["id"] == rec.get("node")), {})
        self.log(op["kind"], "delete", rec["id"], [rec.get("node")], title=node.get("title"), record=_snap(rec))

    def log_op(self, op: dict[str, Any]) -> None:
        text = model._text(op.get("text") or "", "text", 1000)
        if not text:
            raise ValueError("log 需要 text")
        self.log("log", "note", "", [self.ref(n) for n in op.get("nodes") or []], summary=text)

    def project_update(self, op: dict[str, Any]) -> None:
        rec = next((r for r in self.data["project"] if r["id"] == PROJECT_ID), None)
        if rec is None:
            rec = {"id": PROJECT_ID, "title": self.p.slug, "created_at": self.at}
            self.data["project"].append(rec)
        new = model.project_patch(op.get("set") or {})
        changes = _changes(rec, new)
        if not changes:
            return
        for k, v in new.items():
            if v is None:
                rec.pop(k, None)
            else:
                rec[k] = v
        rec["updated_at"] = self.at
        self.log("project", "update", PROJECT_ID, [], title=rec.get("title"), changes=changes)

    def run(self, op: dict[str, Any]) -> None:
        name = op.get("op")
        if name not in OPS:
            raise ValueError(f"不认识的操作：{name}")
        fn = {"node.add": self.node_add, "node.update": self.node_update, "node.delete": self.node_delete,
              "edge.add": self.edge_add, "edge.update": self.edge_update, "edge.delete": self.edge_delete,
              "attach.add": self.attach_add, "attach.update": self.attach_update, "attach.delete": self.attach_delete,
              "log": self.log_op, "project.update": self.project_update}[name]
        fn(op)


def _write_events(p: Project, rows: list[dict[str, Any]]) -> None:
    by_month: dict[str, list[dict[str, Any]]] = {}
    for ev in rows:
        by_month.setdefault(ev["at"][:7], []).append(ev)
    for month, evs in by_month.items():
        with p.event_store(month).editing() as data:
            taken = {r["id"] for r in data["events"]}
            for ev in evs:
                while ev["id"] in taken:  # vanishingly rare; ids are 12 hex digits
                    ev["id"] = _rid("ev", taken, 6)
                taken.add(ev["id"])
                data["events"].append(ev)


def apply(slug: str, ops: list[dict[str, Any]], *, who: str = "user", summary: str | None = None) -> dict[str, Any]:
    """Run a batch against an existing project. All-or-nothing: an invalid op raises and nothing is written.
    summary: a line for the change log (also what a one-sentence update said). Returns refs, events, stamp."""
    p = get(slug)
    if who not in model.WHO:
        raise ValueError(f"who 只能是 {', '.join(model.WHO)}")
    if not isinstance(ops, list) or not ops:
        raise ValueError("没有要做的改动")
    if len(ops) > MAX_OPS:
        raise ValueError(f"一次最多 {MAX_OPS} 个操作")
    if summary:
        ops = [*ops, {"op": "log", "text": summary}]
    group = secrets.token_hex(4) if len(ops) > 1 else None
    with ExitStack() as stack:
        data = stack.enter_context(p.map.editing(skip_unchanged=True))
        b = _Batch(p, data, who, group)
        b.stack = stack
        for op in ops:
            if not isinstance(op, dict):
                raise ValueError("每个操作应为对象")
            b.run(op)
    # The log is written once the records are: a failed write leaves no event for a change that never happened.
    rows = []
    taken: set[str] = set()
    for ev in b.events:
        row = {"id": _rid("ev", taken, 6), "at": b.at, "who": who, "device": DEVICE, **ev}
        if group:
            row["group"] = group
        rows.append({k: v for k, v in row.items() if v not in (None, "", [], {})})
    if rows:
        _write_events(p, rows)
        publish(slug, rows)
    return {"refs": b.refs, "events": rows, "stamp": p.stamp()}


def create_project(title: str, *, slug: str | None = None, line: str | None = None, note: str | None = None,
                   description: str | None = None, who: str = "user") -> dict[str, Any]:
    fields = model.project_patch({"title": title, "line": line, "note": note, "description": description})
    base = slug or model.slugify(fields["title"]) or "project"
    if not model.valid_slug(base):
        raise ValueError("课题文件夹名只能用字母、数字和 -")
    RMAP_DIR.mkdir(parents=True, exist_ok=True)
    cand, n = base, 2
    while (RMAP_DIR / cand).exists():
        if slug:
            raise ValueError(f"已经有这个课题：{slug}")
        cand, n = f"{base[:44]}-{n}", n + 1
    p = Project(cand)
    at = now()
    rec = {"id": PROJECT_ID, **{k: v for k, v in fields.items() if v is not None}, "created_at": at, "updated_at": at}
    with p.map.editing() as data:
        data["project"] = [rec]
    row = {"id": _rid("ev", set(), 6), "at": at, "who": who, "device": DEVICE, "kind": "project", "action": "add",
           "target": PROJECT_ID, "title": rec["title"], "record": _snap(rec)}
    _write_events(p, [row])
    publish(p.slug, [row])
    return {**rec, "slug": p.slug}

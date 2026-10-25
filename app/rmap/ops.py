"""Every change to a research map goes through `apply`: a batch of operations, validated as a whole,
written under the map's lock (all or nothing), and logged as events (one per changed record, sharing a
`group`). Every field written gets a clock stamp (clock.py), so two Macs' changes merge field by field.

Operations (ids may be "$name" refs to records added earlier in the same batch):

    {"op": "node.add",      "ref": "$q", "set": {type, title, status, body, pin, order, tags, entity, ext},
                            "parent": <node>, "edge": "refines"}      # parent: also adds parent -> node
    {"op": "node.update",   "id": <node>, "set": {...}}
    {"op": "node.delete",   "id": <node>}                              # its edges + attachments go with it
    {"op": "edge.add",      "ref": "$e", "from": <node>, "to": <node>, "kind": "refines", "label": "..."}
    {"op": "edge.update",   "id": <edge>, "set": {kind, label}}
    {"op": "edge.delete",   "id": <edge>}
    {"op": "attach.add",    "ref": "$v", "kind": "evidence", "node": <node>, "set": {...}}
    {"op": "attach.update", "kind": "evidence", "id": <id>, "set": {...}}
    {"op": "attach.delete", "kind": "evidence", "id": <id>}
    {"op": "log",           "text": "one line for the change log", "nodes": [<node>, ...]}
    {"op": "project.update", "set": {title, line, note, description, archived}}

`simulate` runs the same checks without writing.
"""
from __future__ import annotations

import copy
import secrets
from contextlib import ExitStack
from typing import Any

from ..config import DEVICE, RMAP_DIR
from ..store import now
from . import model, registry
from .clock import Clock
from .project import PROJECT_ID, Project, get, taken
from .records import child_live, clocks, last_write, live_ids, own_live, set_field

OPS = ("node.add", "node.update", "node.delete", "edge.add", "edge.update", "edge.delete",
       "attach.add", "attach.update", "attach.delete", "log", "project.update")
MAX_OPS = 400          # one action of a person or the assistant
MAX_IMPORT_OPS = 20000  # an import (who="import" / "cli"): a results CSV, a first map; still one atomic batch


def _rid(prefix: str, taken_ids: set[str], nbytes: int = 4) -> str:
    while True:
        rid = prefix + secrets.token_hex(nbytes)
        if rid not in taken_ids:
            taken_ids.add(rid)
            return rid


def _snap(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items()
            if k not in ("created_at", "updated_at", "deleted", "deleted_by", "clock") and v not in (None, "", [])}


def _changes(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[Any]]:
    return {k: [old.get(k), v] for k, v in new.items() if old.get(k) != v}


class _Batch:
    def __init__(self, p: Project, data: dict[str, list[dict[str, Any]]], who: str, group: str | None, fresh: bool = False):
        self.p, self.data, self.who, self.group = p, data, who, group
        self.fresh = fresh  # a dry run against a project that does not exist yet
        self.refs: dict[str, str] = {}
        self.events: list[dict[str, Any]] = []
        self.bulk: dict[tuple[str, str], dict[str, Any]] = {}  # (kind, action) -> one event, for kinds logged per batch
        self.att: dict[str, list[dict[str, Any]]] = {}
        self.att_by: dict[str, dict[str, dict[str, Any]]] = {}
        self.stack: ExitStack | None = None
        self.taken = {r["id"] for c in data.values() for r in c}
        self.nodes = {n["id"]: n for n in data["nodes"]}
        self.edges = {e["id"]: e for e in data["edges"]}
        self.live = live_ids(data["nodes"])
        self.clock = Clock()
        for c in data.values():
            for r in c:
                self.clock.observe(last_write(r))
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
        rec = self.nodes.get(nid)
        if rec is None or nid not in self.live:
            raise KeyError(f"没有这个节点：{nid}")
        return rec

    def edge(self, eid: str) -> dict[str, Any]:
        rec = self.edges.get(eid)
        if rec is None or not child_live(rec, self.live):
            raise KeyError(f"没有这条连线：{eid}")
        return rec

    def live_edges(self) -> list[dict[str, Any]]:
        return [e for e in self.data["edges"] if child_live(e, self.live) and e.get("from") in self.live and e.get("to") in self.live]

    def kind_data(self, name: str) -> list[dict[str, Any]]:
        kinds = registry.kinds()
        if name not in kinds:
            raise ValueError(f"没有这种挂载：{name}")
        if name not in self.att:
            store = self.p.kind_store(kinds[name])
            if self.stack is None:  # a dry run: read only (nothing there yet for a project not yet created)
                self.att[name] = [] if self.fresh else copy.deepcopy(store.load(include_deleted=True)[name])
            else:
                self.att[name] = self.stack.enter_context(store.editing(skip_unchanged=True))[name]
            self.att_by[name] = {r["id"]: r for r in self.att[name]}
            for r in self.att[name]:
                self.taken.add(r["id"])
                self.clock.observe(last_write(r))
        return self.att[name]

    def define(self, op: dict[str, Any], rid: str) -> None:
        ref = op.get("ref")
        if ref is not None:
            if not (isinstance(ref, str) and ref.startswith("$") and len(ref) > 1) or ref in self.refs:
                raise ValueError(f"ref 应为没用过的 $名字：{ref}")
            self.refs[ref] = rid

    def log(self, kind: str, action: str, target: str, nodes: list[str], stamp: str, *, title: str | None = None,
            record: dict[str, Any] | None = None, changes: dict[str, Any] | None = None,
            summary: str | None = None) -> None:
        self.events.append({"hlc": stamp, "kind": kind, "action": action, "target": target, "nodes": nodes,
                            "title": title, "record": record, "changes": changes, "summary": summary})

    def write(self, rec: dict[str, Any], fields: dict[str, Any], stamp: str) -> None:
        """Set fields with their stamps; a record touched after a delete is made plainly live again."""
        if rec.get("deleted"):
            set_field(rec, "deleted", None, stamp)
            set_field(rec, "deleted_by", None, stamp)
        for k, v in fields.items():
            set_field(rec, k, v, stamp)
        rec["updated_at"] = self.at

    def tomb(self, rec: dict[str, Any], stamp: str, by: str | None = None) -> None:
        set_field(rec, "deleted", True, stamp)
        set_field(rec, "deleted_by", by, stamp)
        rec["updated_at"] = self.at

    def new(self, prefix: str, fields: dict[str, Any], stamp: str) -> dict[str, Any]:
        rec = {"id": _rid(prefix, self.taken), "created_at": self.at, "updated_at": self.at, "clock": {}}
        for k, v in fields.items():
            if v is not None:
                set_field(rec, k, v, stamp)
        return rec

    # ---- operations ----
    def node_add(self, op: dict[str, Any]) -> None:
        fields = model.node_patch(op.get("set") or {})
        parent = self.node(self.ref(op["parent"], "parent")) if op.get("parent") else None
        stamp = self.clock.tick()
        rec = self.new("n", fields, stamp)
        self.data["nodes"].append(rec)
        self.nodes[rec["id"]] = rec
        self.live.add(rec["id"])
        self.define(op, rec["id"])
        self.log("node", "add", rec["id"], [rec["id"]], stamp, title=rec["title"], record=_snap(rec))
        if parent:
            self.edge_add({"from": parent["id"], "to": rec["id"], "kind": op.get("edge") or "refines"})

    def node_update(self, op: dict[str, Any]) -> None:
        rec = self.node(self.ref(op.get("id")))
        new = model.node_patch(op.get("set") or {}, rec)
        changes = _changes(rec, new)
        if not changes:
            return
        stamp = self.clock.tick()
        self.write(rec, {k: new[k] for k in changes}, stamp)
        self.log("node", "update", rec["id"], [rec["id"]], stamp, title=rec["title"], changes=changes)

    def node_delete(self, op: dict[str, Any]) -> None:
        rec = self.node(self.ref(op.get("id")))
        for e in self.live_edges():
            if rec["id"] in (e.get("from"), e.get("to")):
                stamp = self.clock.tick()
                self.tomb(e, stamp, rec["id"])
                self.log("edge", "delete", e["id"], [e["from"], e["to"]], stamp, record=_snap(e))
        for name, kind in registry.kinds().items():
            for a in self.kind_data(name):
                if a.get("node") == rec["id"] and child_live(a, self.live):
                    stamp = self.clock.tick()
                    self.tomb(a, stamp, rec["id"])
                    self._log_attach(kind, "delete", a, rec, stamp, record=_snap(a))
        stamp = self.clock.tick()
        self.tomb(rec, stamp)
        self.live.discard(rec["id"])
        self.log("node", "delete", rec["id"], [rec["id"]], stamp, title=rec["title"], record=_snap(rec))

    def edge_add(self, op: dict[str, Any]) -> None:
        src, dst = self.node(self.ref(op.get("from"), "from")), self.node(self.ref(op.get("to"), "to"))
        kind = op.get("kind") or "refines"
        if kind not in model.EDGE_KINDS:
            raise ValueError(f"kind 只能是 {', '.join(model.EDGE_KINDS)}")
        if src["id"] == dst["id"]:
            raise ValueError("不能连到自己")
        pair = {(src["id"], dst["id"])} | ({(dst["id"], src["id"])} if kind not in model.FLOW else set())
        live = self.live_edges()
        if any(e.get("kind") == kind and (e["from"], e["to"]) in pair for e in live):
            raise ValueError("这两个节点之间已经有这种连线")
        if kind in model.FLOW and _reaches(live, dst["id"], src["id"]):
            raise ValueError("这条连线会形成环：地图从左到右，后面的节点不能再指回前面")
        label = model.edge_patch({"label": op["label"]}).get("label") if op.get("label") else None
        stamp = self.clock.tick()
        rec = self.new("e", {"from": src["id"], "to": dst["id"], "kind": kind, "label": label}, stamp)
        self.data["edges"].append(rec)
        self.edges[rec["id"]] = rec
        self.define(op, rec["id"])
        self.log("edge", "add", rec["id"], [src["id"], dst["id"]], stamp, record=_snap(rec))

    def edge_update(self, op: dict[str, Any]) -> None:
        rec = self.edge(self.ref(op.get("id")))
        new = model.edge_patch(op.get("set") or {})
        changes = _changes(rec, new)
        if not changes:
            return
        if "kind" in changes and new["kind"] in model.FLOW and rec["kind"] not in model.FLOW:
            if _reaches(self.live_edges(), rec["to"], rec["from"]):
                raise ValueError("改成这种连线会形成环")
        stamp = self.clock.tick()
        self.write(rec, {k: new[k] for k in changes}, stamp)
        self.log("edge", "update", rec["id"], [rec["from"], rec["to"]], stamp, changes=changes)

    def edge_delete(self, op: dict[str, Any]) -> None:
        rec = self.edge(self.ref(op.get("id")))
        stamp = self.clock.tick()
        self.tomb(rec, stamp)
        self.log("edge", "delete", rec["id"], [rec["from"], rec["to"]], stamp, record=_snap(rec))

    def _log_attach(self, kind: registry.AttachmentKind, action: str, rec: dict[str, Any], node: dict[str, Any],
                    stamp: str, **kw: Any) -> None:
        if kind.log == "batch":  # one event per batch and action, listing the records
            ev = self.bulk.get((kind.name, action))
            if ev is None:
                self.log(kind.name, action, "", [], stamp, summary=None)
                ev = self.bulk[(kind.name, action)] = self.events[-1]
                ev["targets"], ev["count"] = [], 0
            ev["targets"].append(rec["id"])
            ev["count"] += 1
            if node["id"] not in ev["nodes"]:
                ev["nodes"].append(node["id"])
            return
        self.log(kind.name, action, rec["id"], [node["id"]], stamp, title=node.get("title"), summary=kw.pop("summary", None), **kw)

    def attach_add(self, op: dict[str, Any]) -> None:
        name = op.get("kind")
        recs = self.kind_data(name)
        kind = registry.kinds()[name]
        node = self.node(self.ref(op.get("node"), "node"))
        if kind.node_types and node.get("type") not in kind.node_types:
            raise ValueError(f"{name} 只能挂在 {', '.join(kind.node_types)} 节点上")
        fields = {k: v for k, v in kind.clean(op.get("set") or {}, new=True).items() if v not in (None, "", [])}
        kind.check_record(fields)
        stamp = self.clock.tick()
        rec = self.new(kind.prefix, {"node": node["id"], **fields}, stamp)
        recs.append(rec)
        self.att_by[name][rec["id"]] = rec
        self.define(op, rec["id"])
        self._log_attach(kind, "add", rec, node, stamp, record=_snap(rec), summary=kind.line(rec))

    def _attachment(self, op: dict[str, Any]) -> dict[str, Any]:
        self.kind_data(op.get("kind"))
        rid = self.ref(op.get("id"))
        rec = self.att_by[op["kind"]].get(rid)
        if rec is None or not child_live(rec, self.live) or rec.get("node") not in self.live:
            raise KeyError(f"没有这条 {op.get('kind')}：{rid}")
        return rec

    def attach_update(self, op: dict[str, Any]) -> None:
        rec = self._attachment(op)
        kind = registry.kinds()[op["kind"]]
        new = kind.clean(op.get("set") or {}, new=False)
        new = {k: (None if v in ("", []) else v) for k, v in new.items()}
        changes = _changes(rec, new)
        if not changes:
            return
        after = {**{k: v for k, v in rec.items() if k not in ("clock",)}, **new}
        missing = [k for k in kind.required if after.get(k) in (None, "", [])]
        if missing:
            raise ValueError(f"{kind.name} 不能去掉：{', '.join(missing)}")
        kind.check_record({k: v for k, v in after.items() if v is not None})
        stamp = self.clock.tick()
        self.write(rec, {k: new[k] for k in changes}, stamp)
        self._log_attach(kind, "update", rec, self.nodes.get(rec.get("node"), {"id": rec.get("node")}), stamp, changes=changes)

    def attach_delete(self, op: dict[str, Any]) -> None:
        rec = self._attachment(op)
        kind = registry.kinds()[op["kind"]]
        stamp = self.clock.tick()
        self.tomb(rec, stamp)
        self._log_attach(kind, "delete", rec, self.nodes.get(rec.get("node"), {"id": rec.get("node")}), stamp, record=_snap(rec))

    def log_op(self, op: dict[str, Any]) -> None:
        text = model._text(op.get("text") or "", "text", 1000)
        if not text:
            raise ValueError("log 需要 text")
        self.log("log", "note", "", [self.ref(n) for n in op.get("nodes") or []], self.clock.tick(), summary=text)

    def project_update(self, op: dict[str, Any]) -> None:
        rec = next((r for r in self.data["project"] if r["id"] == PROJECT_ID), None)
        if rec is None:
            rec = {"id": PROJECT_ID, "title": self.p.slug, "created_at": self.at}
            self.data["project"].append(rec)
        new = model.project_patch(op.get("set") or {})
        changes = _changes(rec, new)
        if not changes:
            return
        stamp = self.clock.tick()
        self.write(rec, {k: new[k] for k in changes}, stamp)
        self.log("project", "update", PROJECT_ID, [], stamp, title=rec.get("title"), changes=changes)

    def run(self, op: dict[str, Any]) -> None:
        name = op.get("op")
        if name not in OPS:
            raise ValueError(f"不认识的操作：{name}")
        fn = {"node.add": self.node_add, "node.update": self.node_update, "node.delete": self.node_delete,
              "edge.add": self.edge_add, "edge.update": self.edge_update, "edge.delete": self.edge_delete,
              "attach.add": self.attach_add, "attach.update": self.attach_update, "attach.delete": self.attach_delete,
              "log": self.log_op, "project.update": self.project_update}[name]
        fn(op)


def _reaches(edges: list[dict[str, Any]], start: str, goal: str) -> bool:
    """Whether goal is reachable from start along the given (live) flow edges."""
    nxt: dict[str, list[str]] = {}
    for e in edges:
        if e.get("kind") in model.FLOW:
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


def _rows(b: _Batch, who: str, group: str | None) -> list[dict[str, Any]]:
    rows = []
    taken_ids: set[str] = set()
    for ev in b.events:
        row = {"id": _rid("ev", taken_ids, 6), "at": b.at, "who": who, "device": DEVICE, **ev}
        if group:
            row["group"] = group
        rows.append({k: v for k, v in row.items() if v not in (None, "", [], {})})
    return rows


def _check(ops: list[dict[str, Any]], who: str, summary: str | None) -> tuple[list[dict[str, Any]], str | None]:
    if who not in model.WHO:
        raise ValueError(f"who 只能是 {', '.join(model.WHO)}")
    if not isinstance(ops, list) or not ops:
        raise ValueError("没有要做的改动")
    limit = MAX_IMPORT_OPS if who in ("import", "cli") else MAX_OPS
    if len(ops) > limit:
        raise ValueError(f"一次最多 {limit} 个操作" + ("（导入用 who=import，最多 %d 个）" % MAX_IMPORT_OPS if limit == MAX_OPS else ""))
    if any(not isinstance(op, dict) for op in ops):
        raise ValueError("每个操作应为对象")
    if summary:
        ops = [*ops, {"op": "log", "text": summary}]
    return ops, (secrets.token_hex(4) if len(ops) > 1 else None)


def simulate(slug: str | None, ops: list[dict[str, Any]], *, who: str = "user", summary: str | None = None) -> dict[str, Any]:
    """What apply would do, without writing: the events it would log and its refs. slug=None: against a new,
    empty project (a first map drafted before the project exists). Raises like apply on an invalid batch."""
    p = get(slug) if slug else Project("draft")
    ops, group = _check(ops, who, summary)
    data = copy.deepcopy(p.map.load(include_deleted=True)) if slug else {"project": [], "nodes": [], "edges": []}
    b = _Batch(p, data, who, group, fresh=not slug)
    for op in ops:
        b.run(op)
    return {"refs": b.refs, "events": _rows(b, who, group)}


def _write_events(p: Project, rows: list[dict[str, Any]]) -> None:
    by_month: dict[str, list[dict[str, Any]]] = {}
    for ev in rows:
        by_month.setdefault(ev["at"][:7], []).append(ev)
    for month, evs in by_month.items():
        with p.event_store(month).editing() as data:
            ids = {r["id"] for r in data["events"]}
            for ev in evs:
                while ev["id"] in ids:  # vanishingly rare; ids are 12 hex digits
                    ev["id"] = _rid("ev", ids, 6)
                ids.add(ev["id"])
                data["events"].append(ev)


def apply(slug: str, ops: list[dict[str, Any]], *, who: str = "user", summary: str | None = None) -> dict[str, Any]:
    """Run a batch against an existing project. All-or-nothing: an invalid op raises and nothing is written.
    summary: a line for the change log (also what a one-sentence update said). Returns refs, events, stamp."""
    p = get(slug)
    ops, group = _check(ops, who, summary)
    p.ingest()  # so the log of this batch is told apart from what was already there
    with ExitStack() as stack:
        data = stack.enter_context(p.map.editing(skip_unchanged=True))
        b = _Batch(p, data, who, group)
        b.stack = stack
        for op in ops:
            b.run(op)
    # The log is written once the records are: a failed write leaves no event for a change that never happened.
    rows = _rows(b, who, group)
    if rows:
        _write_events(p, rows)
        b.clock.save()
        p.ingest()  # numbers the new events on this Mac and hands them to the event subscribers
    return {"refs": b.refs, "events": rows, "stamp": p.stamp()}


def create_project(title: str, *, slug: str | None = None, line: str | None = None, note: str | None = None,
                   description: str | None = None, who: str = "user") -> dict[str, Any]:
    """A new map. Its folder is the title's slug plus a short id, so two Macs that create the same title
    while apart get two folders, never one merged map. A title already in use (ignoring case) is refused."""
    fields = model.project_patch({"title": title, "line": line, "note": note, "description": description})
    from .project import listing
    if any((p.get("title") or "").strip().lower() == fields["title"].lower() for p in listing(archived=True)):
        raise ValueError(f"已经有叫「{fields['title']}」的课题")
    uid = secrets.token_hex(4)
    if slug is None:
        slug = f"{(model.slugify(fields['title']) or 'project')[:40].strip('-')}-{uid[:4]}"
    if not model.valid_slug(slug):
        raise ValueError("课题文件夹名只能用小写字母、数字和 -")
    if taken(slug):
        raise ValueError(f"已经有这个课题文件夹：{slug}")
    RMAP_DIR.mkdir(parents=True, exist_ok=True)
    p = Project(slug)
    clock = Clock()
    stamp = clock.tick()
    at = now()
    rec = {"id": PROJECT_ID, "created_at": at, "updated_at": at, "clock": {}}
    for k, v in {**fields, "uid": uid}.items():
        if v is not None:
            set_field(rec, k, v, stamp)
    with p.map.editing() as data:
        data["project"] = [rec]
    p.ingest()
    row = {"id": _rid("ev", set(), 6), "at": at, "hlc": stamp, "who": who, "device": DEVICE, "kind": "project",
           "action": "add", "target": PROJECT_ID, "title": rec["title"], "record": _snap(rec)}
    _write_events(p, [row])
    clock.save()
    p.ingest()
    return {**{k: v for k, v in rec.items() if k != "clock"}, "slug": p.slug}


__all__ = ["apply", "simulate", "create_project", "MAX_OPS", "MAX_IMPORT_OPS", "own_live", "clocks"]

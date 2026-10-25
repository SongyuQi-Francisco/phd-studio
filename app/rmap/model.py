"""Research map vocabulary and record validation (docs/research-map.md is the long form).

Keys are English and stable (they are what the YAML files hold); labels live in web/rmap/strings.js.
"""
from __future__ import annotations

import math
import re
from typing import Any

# Node types, in the left-to-right order of the map's columns. `col` is the column hint for the layout
# (None: wherever the graph puts it, next to its parent).
NODE_TYPES: dict[str, dict[str, Any]] = {
    "question": {"col": 0},     # 问题
    "hypothesis": {"col": 1},   # 路径/假设
    "subline": {"col": 2},      # 子线
    "experiment": {"col": 3},   # 实验
    "result": {"col": 4},       # 结果
    "decision": {"col": None},  # 决策
    "kill": {"col": None},      # Kill criteria
    "open": {"col": None},      # 开放问题
}

# 未开始 / 进行中 / 已验证 / 没打通 / 证伪 / 放弃. Refuted and dropped nodes stay on the map, faded.
STATUSES = ("todo", "active", "verified", "blocked", "refuted", "dropped")
FADED = ("refuted", "dropped")
CLOSED = ("verified", "refuted", "dropped")  # nothing left to do on this node

# refines and depends run left to right (from = broader / earlier); alternative and conflicts are
# lateral and symmetric. Only refines + depends decide the layout's layers and must stay acyclic.
EDGE_KINDS = ("refines", "depends", "alternative", "conflicts")
FLOW = ("refines", "depends")

# Who made a change (events record it with the device name).
WHO = ("user", "assistant", "cli", "import", "system")

TITLE_MAX = 200
BODY_MAX = 20000
LABEL_MAX = 80
TAG_MAX = 30


def _text(v: Any, name: str, limit: int, *, line: bool = True) -> str:
    if not isinstance(v, str):
        raise ValueError(f"{name} 应为文字")
    v = re.sub(r"\s+", " ", v).strip() if line else v.strip()
    if len(v) > limit:
        raise ValueError(f"{name} 太长（最多 {limit} 字）")
    return v


def _num(v: Any, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{name} 应为数字")
    return round(float(v), 1)


def _choice(v: Any, allowed: tuple[str, ...] | dict[str, Any], name: str) -> str:
    if v not in allowed:
        raise ValueError(f"{name} 只能是 {', '.join(allowed)}")
    return v


def _tags(v: Any) -> list[str]:
    if not isinstance(v, list):
        raise ValueError("tags 应为列表")
    out: list[str] = []
    for t in v:
        t = _text(t, "tag", 40).lstrip("#")
        if t and t not in out:
            out.append(t)
    if len(out) > TAG_MAX:
        raise ValueError(f"标签最多 {TAG_MAX} 个")
    return out


def _pin(v: Any) -> dict[str, float] | None:
    if v is None:
        return None
    if not isinstance(v, dict) or set(v) != {"x", "y"}:
        raise ValueError("pin 应为 {x, y}")
    return {"x": _num(v["x"], "pin.x"), "y": _num(v["y"], "pin.y")}


def _ext(v: Any, old: Any) -> dict[str, Any] | None:
    """`ext` holds extension data per extension key; a patch replaces only the keys it names (None drops one)."""
    if not isinstance(v, dict):
        raise ValueError("ext 应为对象")
    out = dict(old) if isinstance(old, dict) else {}
    for k, val in v.items():
        if not isinstance(k, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", k):
            raise ValueError(f"ext 的键不合法：{k}")
        if val is None:
            out.pop(k, None)
        else:
            out[k] = val
    return out or None


NODE_FIELDS = ("type", "title", "status", "body", "pin", "order", "tags", "ext")
EDGE_FIELDS = ("kind", "label")
PROJECT_FIELDS = ("title", "line", "note", "description", "archived")


def node_patch(patch: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validated fields of a new node (old=None) or of a change to one. Unknown fields are an error."""
    unknown = set(patch) - set(NODE_FIELDS)
    if unknown:
        raise ValueError(f"节点没有这些字段：{', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for k, v in patch.items():
        if k == "type":
            out[k] = _choice(v, NODE_TYPES, "type")
        elif k == "title":
            out[k] = _text(v, "title", TITLE_MAX)
            if not out[k]:
                raise ValueError("节点标题不能为空")
        elif k == "status":
            out[k] = _choice(v, STATUSES, "status")
        elif k == "body":
            out[k] = _text(v or "", "body", BODY_MAX, line=False) or None
        elif k == "pin":
            out[k] = _pin(v)
        elif k == "order":
            out[k] = None if v is None else _num(v, "order")
        elif k == "tags":
            out[k] = _tags(v or []) or None
        elif k == "ext":
            out[k] = _ext(v, (old or {}).get("ext"))
    if old is None:
        if not out.get("title"):
            raise ValueError("节点需要标题")
        out.setdefault("type", "hypothesis")
        out.setdefault("status", "todo")
    return out


def edge_patch(patch: dict[str, Any]) -> dict[str, Any]:
    unknown = set(patch) - set(EDGE_FIELDS)
    if unknown:
        raise ValueError(f"连线只能改 kind、label，没有：{', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    if "kind" in patch:
        out["kind"] = _choice(patch["kind"], EDGE_KINDS, "kind")
    if "label" in patch:
        out["label"] = _text(patch["label"] or "", "label", LABEL_MAX) or None
    return out


def project_patch(patch: dict[str, Any]) -> dict[str, Any]:
    unknown = set(patch) - set(PROJECT_FIELDS)
    if unknown:
        raise ValueError(f"课题没有这些字段：{', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for k, v in patch.items():
        if k == "archived":
            out[k] = True if v else None
        elif k == "description":
            out[k] = _text(v or "", k, BODY_MAX, line=False) or None
        else:
            out[k] = _text(v or "", k, TITLE_MAX) or None
    if "title" in patch and not out.get("title"):
        raise ValueError("课题需要标题")
    return out


def slugify(title: str) -> str:
    """Folder name for a project: lower-case letters and digits (any script) joined by '-'."""
    s = re.sub(r"[^\w]+", "-", title.lower(), flags=re.UNICODE).strip("-_")
    return s[:48].strip("-_")


def valid_slug(slug: str) -> bool:
    return bool(re.fullmatch(r"[^\W_][\w-]{0,47}", slug, flags=re.UNICODE))

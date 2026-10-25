"""Extension points of the research map backend.

Later phases add their own modules under app/rmap/plugins/ and use these hooks instead of editing the
core files. Plugins are imported once, in name order, the first time any research map code needs the
registry (a page, the CLI, the assistant, a test): `kinds()` and `ext_validator()` load them.

- `attachment_kind(...)`: a new kind of thing hung on nodes (peers, results), with its own YAML file
  in each project folder and the generic CRUD routes /api/rmap/projects/{slug}/attachments/{kind}.
- `ext_field(key, validator)`: validate what a plugin keeps in `node.ext[key]`.
- `on_event(fn)`: called with the project slug and the events this Mac had not seen yet, after every
  write here and whenever events written on the other Mac arrive (see project.Project.ingest).
- a module-level `router` (APIRouter) in a plugin is mounted next to the core routes; its paths
  must start with /api/rmap/ and must not reuse a core path (see docs/research-map.md).
"""
from __future__ import annotations

import importlib
import json
import logging
import pkgutil
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

log = logging.getLogger(__name__)

Validator = Callable[[Any], Any]  # value -> clean value (None drops the field); raise ValueError to refuse

# A thing several projects can point at: "<namespace>:<key>", e.g. paper:2310.11511, doi:10.1145/3626772.3657834,
# scholar:openalex:A5023888391, lab:<slug>, dataset:msmarco, metric:recall@10, method:colbert.
ENTITY = re.compile(r"^[a-z][a-z0-9_]{1,15}:\S{1,200}$")


def entity(v: Any) -> str | None:
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not ENTITY.match(v.strip()):
        raise ValueError("entity 应为「命名空间:键」，如 paper:2310.11511、dataset:msmarco")
    return v.strip()


@dataclass(frozen=True)
class AttachmentKind:
    name: str                          # "evidence", "peer", "result": the URL and event `kind`
    prefix: str                        # id prefix of its records
    fields: dict[str, Validator]       # every field a record may have besides id/node/entity/timestamps
    required: tuple[str, ...] = ()
    file: str = ""                     # file in the project folder; default <name>.yaml
    node_types: tuple[str, ...] = ()   # node types it may hang on; empty = any
    summary: Callable[[dict[str, Any]], str] | None = None  # one line for the event log
    check: Callable[[dict[str, Any]], None] | None = None   # whole record, after a change; raise ValueError
    log: str = "each"                  # "each": an event per record; "batch": one per batch (bulk imports)
    extra: dict[str, Any] = field(default_factory=dict)     # free-form, passed to the front end via /meta

    @property
    def filename(self) -> str:
        return self.file or f"{self.name}.yaml"

    def clean(self, patch: dict[str, Any], *, new: bool) -> dict[str, Any]:
        unknown = set(patch) - set(self.fields) - {"entity"}
        if unknown:
            raise ValueError(f"{self.name} 没有这些字段：{', '.join(sorted(unknown))}")
        out = {k: (entity(v) if k == "entity" else self.fields[k](v)) for k, v in patch.items()}
        if new:
            missing = [k for k in self.required if out.get(k) in (None, "", [])]
            if missing:
                raise ValueError(f"{self.name} 缺少：{', '.join(missing)}")
        return out

    def check_record(self, rec: dict[str, Any]) -> None:
        if self.check:
            self.check(rec)

    def line(self, rec: dict[str, Any]) -> str:
        if self.summary:
            return self.summary(rec)
        return str(rec.get("title") or rec.get("target") or rec.get("id"))


KINDS: dict[str, AttachmentKind] = {}
EXT: dict[str, Validator] = {}
EXT_MAX = 4000  # characters of JSON for an ext value no plugin validates
_SUBSCRIBERS: list[Callable[[str, list[dict[str, Any]]], None]] = []
_RESERVED = {"node", "edge", "project", "log"}  # event kinds the core uses


def attachment_kind(kind: AttachmentKind) -> AttachmentKind:
    if kind.name in _RESERVED or not kind.name.isidentifier():
        raise ValueError(f"attachment kind name not allowed: {kind.name}")
    if kind.name in KINDS and KINDS[kind.name] != kind:
        raise ValueError(f"attachment kind already registered: {kind.name}")
    if any(k.filename == kind.filename for k in KINDS.values() if k.name != kind.name) or kind.filename == "map.yaml":
        raise ValueError(f"attachment file already in use: {kind.filename}")
    if kind.log not in ("each", "batch"):
        raise ValueError("log must be 'each' or 'batch'")
    KINDS[kind.name] = kind
    return kind


def ext_field(key: str, validator: Validator) -> Validator:
    """Validate node.ext[key] with `validator` (value -> clean value; raise ValueError)."""
    if key in EXT and EXT[key] is not validator:
        raise ValueError(f"ext key already registered: {key}")
    EXT[key] = validator
    return validator


def clean_ext(key: str, value: Any) -> Any:
    load_plugins()
    if key in EXT:
        return EXT[key](value)
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        raise ValueError(f"ext.{key} 应为 JSON 数据")
    if len(text) > EXT_MAX:
        raise ValueError(f"ext.{key} 太大（最多 {EXT_MAX} 字符；注册校验器后可放更多）")
    return value


def kinds() -> dict[str, AttachmentKind]:
    """Every attachment kind, the plugins' included."""
    load_plugins()
    return KINDS


def on_event(fn: Callable[[str, list[dict[str, Any]]], None]) -> Callable[[str, list[dict[str, Any]]], None]:
    """Subscribe to new events (usable as a decorator). Runs in the thread that found them; keep it quick."""
    _SUBSCRIBERS.append(fn)
    return fn


def publish(slug: str, events: list[dict[str, Any]]) -> None:
    for fn in list(_SUBSCRIBERS):
        try:
            fn(slug, events)
        except Exception:  # a broken extension must not undo or fail the write
            log.exception("research map event subscriber %r failed", fn)


_loaded: list[ModuleType] | None = None
_loading = threading.RLock()


def load_plugins() -> list[ModuleType]:
    """Import app/rmap/plugins/* once (sorted by name); a plugin that fails to import is logged and skipped."""
    global _loaded
    if _loaded is not None:
        return _loaded
    with _loading:
        if _loaded is None:
            from . import plugins
            mods: list[ModuleType] = []
            _loaded = mods  # a plugin that asks for kinds() while importing gets the ones so far, not a loop
            for info in sorted(pkgutil.iter_modules(plugins.__path__), key=lambda m: m.name):
                if info.name.startswith("_"):
                    continue
                try:
                    mods.append(importlib.import_module(f"{plugins.__name__}.{info.name}"))
                except Exception:
                    log.exception("research map plugin %s failed to load", info.name)
    return _loaded

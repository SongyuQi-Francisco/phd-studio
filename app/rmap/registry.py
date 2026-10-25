"""Extension points of the research map backend.

Later phases add their own modules under app/rmap/plugins/ (imported once, in name order, when the API
loads) and use these hooks instead of editing the core files:

- `attachment_kind(...)`: a new kind of thing hung on nodes (peers, results), with its own YAML file
  in each project folder and the generic CRUD routes /api/rmap/projects/{slug}/attachments/{kind}.
- `on_event(fn)`: called after every write with the project slug and the events just logged.
- a module-level `router` (APIRouter) in a plugin is mounted next to the core routes; its paths
  must start with /api/rmap/ and must not reuse a core path (see docs/research-map.md).
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

log = logging.getLogger(__name__)

Validator = Callable[[Any], Any]  # value -> clean value (None drops the field); raise ValueError to refuse


@dataclass(frozen=True)
class AttachmentKind:
    name: str                          # "evidence", "peer", "result": the URL and event `kind`
    prefix: str                        # id prefix of its records
    fields: dict[str, Validator]       # every field a record may have besides id/node/timestamps
    required: tuple[str, ...] = ()
    file: str = ""                     # file in the project folder; default <name>.yaml
    node_types: tuple[str, ...] = ()   # node types it may hang on; empty = any
    summary: Callable[[dict[str, Any]], str] | None = None  # one line for the event log
    extra: dict[str, Any] = field(default_factory=dict)     # free-form, passed to the front end via /meta

    @property
    def filename(self) -> str:
        return self.file or f"{self.name}.yaml"

    def clean(self, patch: dict[str, Any], *, new: bool) -> dict[str, Any]:
        unknown = set(patch) - set(self.fields)
        if unknown:
            raise ValueError(f"{self.name} 没有这些字段：{', '.join(sorted(unknown))}")
        out = {k: self.fields[k](v) for k, v in patch.items()}
        if new:
            missing = [k for k in self.required if out.get(k) in (None, "", [])]
            if missing:
                raise ValueError(f"{self.name} 缺少：{', '.join(missing)}")
        return out

    def line(self, rec: dict[str, Any]) -> str:
        if self.summary:
            return self.summary(rec)
        return str(rec.get("title") or rec.get("target") or rec.get("id"))


KINDS: dict[str, AttachmentKind] = {}
_SUBSCRIBERS: list[Callable[[str, list[dict[str, Any]]], None]] = []
_RESERVED = {"node", "edge", "project", "log"}  # event kinds the core uses


def attachment_kind(kind: AttachmentKind) -> AttachmentKind:
    if kind.name in _RESERVED or not kind.name.isidentifier():
        raise ValueError(f"attachment kind name not allowed: {kind.name}")
    if kind.name in KINDS and KINDS[kind.name] != kind:
        raise ValueError(f"attachment kind already registered: {kind.name}")
    if any(k.filename == kind.filename for k in KINDS.values() if k.name != kind.name) or kind.filename == "map.yaml":
        raise ValueError(f"attachment file already in use: {kind.filename}")
    KINDS[kind.name] = kind
    return kind


def on_event(fn: Callable[[str, list[dict[str, Any]]], None]) -> Callable[[str, list[dict[str, Any]]], None]:
    """Subscribe to writes (usable as a decorator). Runs in the writing thread; keep it quick."""
    _SUBSCRIBERS.append(fn)
    return fn


def publish(slug: str, events: list[dict[str, Any]]) -> None:
    for fn in list(_SUBSCRIBERS):
        try:
            fn(slug, events)
        except Exception:  # a broken extension must not undo or fail the write
            log.exception("research map event subscriber %r failed", fn)


_loaded: list[ModuleType] | None = None


def load_plugins() -> list[ModuleType]:
    """Import app/rmap/plugins/* once (sorted by name); a plugin that fails to import is logged and skipped."""
    global _loaded
    if _loaded is None:
        from . import plugins
        _loaded = []
        for info in sorted(pkgutil.iter_modules(plugins.__path__), key=lambda m: m.name):
            if info.name.startswith("_"):
                continue
            try:
                _loaded.append(importlib.import_module(f"{plugins.__name__}.{info.name}"))
            except Exception:
                log.exception("research map plugin %s failed to load", info.name)
    return _loaded

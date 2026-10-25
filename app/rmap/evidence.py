"""Evidence: the one attachment kind the core ships (paper notes, vault notes, files, links on a node)."""
from __future__ import annotations

import re
from typing import Any

from .registry import AttachmentKind, attachment_kind

TYPES = ("paper", "note", "file", "url")  # paper = a paper note by name; note / file = vault-relative path
STANCES = ("supports", "contradicts", "context")
SCHEMES = ("http", "https", "obsidian", "zotero")  # what a link may open; never javascript:, data:, file:
_SCHEME = re.compile(r"^([a-z][a-z0-9+.-]*):", re.I)


def _type(v: Any) -> str:
    if v not in TYPES:
        raise ValueError(f"evidence type 只能是 {', '.join(TYPES)}")
    return v


def _target(v: Any) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError("evidence 需要 target（论文笔记名、vault 路径或链接）")
    v = v.strip()
    if len(v) > 500 or "\n" in v or "\r" in v:
        raise ValueError("target 太长或不是一行")
    return v


def _check(rec: dict[str, Any]) -> None:
    """A link opens only with a safe scheme; a note, file or paper name is a plain vault path."""
    target, scheme = rec.get("target", ""), _SCHEME.match(rec.get("target", ""))
    if rec.get("type") == "url":
        if not scheme or scheme.group(1).lower() not in SCHEMES:
            raise ValueError(f"链接只能是 {' / '.join(SCHEMES)}")
    elif scheme or target.startswith(("/", "~", "\\")) or ".." in re.split(r"[/\\]", target):
        raise ValueError("论文笔记名、笔记和文件只能写 vault 里的相对路径")


def _line(limit: int):
    def clean(v: Any) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("应为文字")
        v = re.sub(r"\s+", " ", v).strip()
        if len(v) > limit:
            raise ValueError(f"最多 {limit} 字")
        return v or None
    return clean


def _stance(v: Any) -> str | None:
    if v in (None, ""):
        return None
    if v not in STANCES:
        raise ValueError(f"stance 只能是 {', '.join(STANCES)}")
    return v


def _summary(rec: dict[str, Any]) -> str:
    return rec.get("title") or rec.get("target", "")


EVIDENCE = attachment_kind(AttachmentKind(
    name="evidence", prefix="v",
    fields={"type": _type, "target": _target, "title": _line(200), "note": _line(500), "stance": _stance},
    required=("type", "target"), summary=_summary, check=_check,
    extra={"types": list(TYPES), "stances": list(STANCES), "schemes": list(SCHEMES)},
))

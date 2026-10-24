"""Excerpts: append a quoted passage with its page to a section of the paper's note (and undo it).

This is the one direct write into a paper note. It only ever appends one line (or removes
exactly that line again), the user picks the section and sees the line before it is written.
"""
from __future__ import annotations

import fcntl
import os
import re
from typing import Any

from .config import DATA_DIR
from .library import note_path

NEW_SECTION = "Excerpts"
_LOCK = DATA_DIR / "excerpt.lock"
_TMP = DATA_DIR / "excerpt.md.tmp"  # outside the synced vault; os.replace moves it in atomically
_HEADING = re.compile(r"^## +(.+?)\s*$", re.M)
_FM = re.compile(r"^---\n.*?\n---\n?", re.S)


def headings(text: str) -> list[str]:
    body = text[m.end():] if (m := _FM.match(text)) else text
    return [h.strip() for h in _HEADING.findall(body)]


def line_for(text: str, page: int | None, comment: str | None = None) -> str:
    quote = re.sub(r"\s+", " ", text).strip().replace('"', "'")
    line = f'- "{quote}"' + (f" [p.{page}]" if page else "")
    if comment and comment.strip():
        line += " — " + re.sub(r"\s+", " ", comment).strip()
    return line


def _write(path, text: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _TMP.write_text(text, encoding="utf-8")
    os.replace(_TMP, path)


def _locked():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = _LOCK.open("w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def _section_end(text: str, name: str) -> int | None:
    """Offset just after the last non-blank line of section `## name`, or None if it isn't there."""
    for m in _HEADING.finditer(text):
        if m.group(1).strip() != name:
            continue
        nxt = _HEADING.search(text, m.end())
        end = nxt.start() if nxt else len(text)
        return m.end() + len(text[m.end():end].rstrip())
    return None


def append(stem: str, text: str, page: int | None, section: str, comment: str | None = None) -> dict[str, Any]:
    if not text.strip():
        raise ValueError("没有选中文字")
    path = note_path(stem)
    line = line_for(text, page, comment)
    f = _locked()
    try:
        src = path.read_text(encoding="utf-8")
        if line in src.splitlines():
            return {"line": line, "section": section, "created": False, "duplicate": True}
        created = False
        at = _section_end(src, section)
        if at is None:
            if section != NEW_SECTION:
                raise KeyError(section)
            # A new "## Excerpts" goes before Links / Claim Gate (template order), else at the end.
            anchor = next((m.start() for m in _HEADING.finditer(src) if m.group(1).strip() in ("Links", "Claim Gate")), None)
            block = f"## {NEW_SECTION}\n\n{line}\n\n"
            new = src[:anchor] + block + src[anchor:] if anchor is not None else src.rstrip() + "\n\n" + block.rstrip() + "\n"
            created = True
        else:
            # Right after the section's last line: joins a list directly, a paragraph or the heading gets a blank line first.
            last = src[:at].rsplit("\n", 1)[-1]
            sep = "\n" if re.match(r"\s*(?:[-*]|\d+\.)\s", last) else "\n\n"
            new = src[:at] + sep + line + src[at:]
        _write(path, new)
    finally:
        f.close()
    return {"line": line, "section": section, "created": created, "duplicate": False}


def undo(stem: str, line: str, section: str, created: bool = False) -> bool:
    """Remove that exact line again (and the Excerpts heading if it was created for it and is now empty)."""
    path = note_path(stem)
    f = _locked()
    try:
        src = path.read_text(encoding="utf-8")
        lines = src.split("\n")
        if line not in lines:
            return False
        i = len(lines) - 1 - lines[::-1].index(line)
        del lines[i]
        if 0 < i < len(lines) and lines[i - 1] == "" and lines[i] == "":
            del lines[i]  # don't leave a double blank line behind
        new = "\n".join(lines)
        if created:
            new = re.sub(rf"^## {re.escape(section)}\s*\n(?=\s*(?:## |\Z))\s*", "", new, flags=re.M)
        _write(path, new)
    finally:
        f.close()
    return True

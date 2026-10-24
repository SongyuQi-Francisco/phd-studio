"""Excerpts: append a quoted passage with its page to a section of the paper's note (and undo it).

This is the one direct write into a paper note. It only ever inserts one line (plus a new
"## Excerpts" heading when that section doesn't exist yet), the user sees the line before it is
written, and undo removes exactly the inserted text again, so the note comes back byte for byte.
The note is rewritten in place: same file, same creation time and newline style, any volume.
"""
from __future__ import annotations

import fcntl
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import DATA_DIR
from .library import note_path

NEW_SECTION = "Excerpts"
_LOCK = DATA_DIR / "excerpt.lock"
_FM = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_HEADING = re.compile(r"## +(.+?)\s*$")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
_LIST_ITEM = re.compile(r"\s*(?:[-*+]|\d+[.)])\s")
# Backslash-escaped so PDF text can't become HTML or math, here or in Obsidian ("<s>" tokens, "<img onerror>").
_MD_SPECIAL = re.compile(r"([\\<>&$])")


def _lines(text: str) -> list[tuple[int, int, str]]:
    """(start offset, end offset without the newline, content) per line."""
    out, pos = [], 0
    for raw in text.splitlines(keepends=True):
        content = raw.rstrip("\r\n")
        out.append((pos, pos + len(content), content))
        pos += len(raw)
    return out


def _headings(text: str) -> list[tuple[str, int]]:
    """'## ' headings outside the front matter and fenced code blocks: (name, line index)."""
    m = _FM.match(text)
    body = m.end() if m else 0
    out, fence = [], ""
    for i, (start, _, line) in enumerate(_lines(text)):
        if start < body:
            continue
        f = _FENCE.match(line)
        if fence:
            if f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence) and not line.strip().strip(fence[0]):
                fence = ""
        elif f:
            fence = f.group(1)
        elif h := _HEADING.match(line):
            out.append((h.group(1).strip(), i))
    return out


def headings(text: str) -> list[str]:
    return [name for name, _ in _headings(text)]


def line_for(text: str, page: int | None, comment: str | None = None) -> str:
    quote = _MD_SPECIAL.sub(r"\\\1", re.sub(r"\s+", " ", text).strip().replace('"', "'"))
    line = f'- "{quote}"' + (f" [p.{page}]" if page else "")
    if comment and comment.strip():
        line += " — " + re.sub(r"\s+", " ", comment).strip()
    return line


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")  # bytes: keep CRLF as it is


def _write(path: Path, text: str) -> None:
    # In place (not temp + rename): keeps the file's identity, creation time and xattrs, works on any volume.
    data = text.encode("utf-8")
    with path.open("r+b") as f:
        f.write(data)
        f.truncate()
        f.flush()
        os.fsync(f.fileno())


@contextmanager
def _locked() -> Iterator[None]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _insertion(src: str, section: str, line: str) -> tuple[int, str, bool]:
    """Where and what to insert: (offset, inserted text, whether the section is new)."""
    nl = "\r\n" if "\r\n" in src else "\n"
    lines = _lines(src)
    heads = _headings(src)
    for n, (name, i) in enumerate(heads):
        if name != section:
            continue
        stop = heads[n + 1][1] if n + 1 < len(heads) else len(lines)
        last = next((j for j in range(stop - 1, i, -1) if lines[j][2].strip()), i)
        # Right after the section's last non-blank line (all of it): a list continues, anything else gets a blank line.
        sep = nl if last != i and _LIST_ITEM.match(lines[last][2]) else nl + nl
        return lines[last][1], sep + line, False
    if section != NEW_SECTION:
        raise KeyError(section)
    block = f"## {NEW_SECTION}{nl}{nl}{line}{nl}"
    # A new "## Excerpts" goes before Links / Claim Gate (template order), else at the end.
    anchor = next((i for name, i in heads if name in ("Links", "Claim Gate")), None)
    if anchor is not None:
        return lines[anchor][0], block + nl, True
    lead = "" if not src or src.endswith(("\n\n", "\r\n\r\n")) else nl if src.endswith("\n") else nl + nl
    return len(src), lead + block, True


def append(stem: str, text: str, page: int | None, section: str, comment: str | None = None) -> dict[str, Any]:
    if not text.strip():
        raise ValueError("没有选中文字")
    path = note_path(stem)
    line = line_for(text, page, comment)
    with _locked():
        src = _read(path)
        if line in (ln.strip() for ln in src.splitlines()):
            return {"line": line, "section": section, "created": False, "duplicate": True, "inserted": None}
        at, inserted, created = _insertion(src, section, line)
        mtime_ns = path.stat().st_mtime_ns
        _write(path, src[:at] + inserted + src[at:])
    return {"line": line, "section": section, "created": created, "duplicate": False, "inserted": inserted,
            "mtime_ns": mtime_ns}


def _find_inserted(src: str, inserted: str) -> int:
    """Offset of the inserted text, only where it still stands as whole lines."""
    i = len(src)
    while (i := src.rfind(inserted, 0, i)) >= 0:
        before_ok = inserted[0] in "\r\n" or i == 0 or src[i - 1] == "\n"
        after = src[i + len(inserted):]
        after_ok = inserted[-1] == "\n" or not after or after[0] in "\r\n"
        if before_ok and after_ok:
            return i
    return -1


def undo(stem: str, line: str, inserted: str | None = None, section: str = NEW_SECTION,
         created: bool = False, mtime_ns: int | None = None) -> dict[str, bool]:
    """Remove the excerpt again. Byte-exact when the note wasn't edited around it since (and then the
    modification time from before the excerpt comes back too); otherwise the line itself is removed
    (and an Excerpts heading created for it, if it is now empty)."""
    path = note_path(stem)
    with _locked():
        src = _read(path)
        if inserted and (i := _find_inserted(src, inserted)) >= 0:
            _write(path, src[:i] + src[i + len(inserted):])
            if mtime_ns:
                os.utime(path, ns=(path.stat().st_atime_ns, mtime_ns))
            return {"ok": True, "exact": True}
        lines = src.splitlines(keepends=True)
        hits = [n for n, ln in enumerate(lines) if ln.strip() == line.strip()]
        if not hits:
            return {"ok": False, "exact": False}
        del lines[hits[-1]]
        new = "".join(lines)
        if created:
            new = re.sub(rf"^## {re.escape(section)}[ \t]*\r?\n(?:[ \t]*\r?\n)*(?=## |\Z)", "", new, flags=re.M)
        _write(path, new)
    return {"ok": True, "exact": False}

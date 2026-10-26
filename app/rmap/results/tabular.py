"""Reading a results table: pasted text or a CSV / TSV / JSON / JSON-lines file into columns and rows.

Nothing here knows what the columns mean (mapping.py does). A file is only read when the user (or a script)
names it: an allowed extension, a regular file, at most MAX_BYTES, and never inside the folders that hold
secrets. Cells keep their type (JSON numbers stay numbers, CSV cells are text).
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ... import config, i18n
from ...config import DATA_DIR, SECRETS_DIR, STATE_ROOT

MAX_BYTES = 8_000_000
MAX_ROWS = 19_000  # one import is one atomic batch of at most 20000 operations (ops.MAX_IMPORT_OPS)
EXT_FORMAT = {".csv": "csv", ".tsv": "tsv", ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl"}
# Folders whose files are never read, even inside a results root.
_HOME = Path.home()
DENIED = [SECRETS_DIR, DATA_DIR, STATE_ROOT, _HOME / ".ssh", _HOME / ".aws", _HOME / ".gnupg", _HOME / ".config",
          _HOME / "Library" / "Keychains", _HOME / "Library" / "Application Support"]


@dataclass
class Table:
    columns: list[str]
    rows: list[dict[str, Any]]
    format: str
    truncated: bool = False
    meta: dict[str, Any] = field(default_factory=dict)  # file info: path, mtime (date), size
    delimiter: str = ","  # of a csv / tsv: ";" is the sign of a file written where "," is the decimal mark


def _unique(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for i, n in enumerate(names):
        n = " ".join(str(n).split()) or f"column {i + 1}"
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n} ({seen[n]})")
    return out


def sniff(text: str) -> str:
    """csv | tsv | json | jsonl | md for a piece of text (a spreadsheet paste is tab separated)."""
    body = text.lstrip("﻿ \t\r\n")
    if body[:1] in "[{":
        lines = [ln for ln in body.splitlines() if ln.strip()]
        if len(lines) > 1 and all(ln.lstrip().startswith("{") and ln.rstrip().endswith("}") for ln in lines):
            return "jsonl"
        return "json"
    first = next((ln for ln in body.splitlines() if ln.strip()), "")
    if first.lstrip().startswith("|") and first.count("|") >= 2:
        return "md"
    return "tsv" if first.count("\t") >= max(1, first.count(",")) and "\t" in first else "csv"


def _delimiter(text: str, fmt: str) -> str:
    if fmt == "tsv":
        return "\t"
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    counts = {d: first.count(d) for d in (",", ";", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def _grid(rows: list[list[Any]], fmt: str, delimiter: str = ",") -> Table:
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if len(rows) < 2:
        raise ValueError(i18n.t("rmap_results.table_empty"))
    cols = _unique([str(c) for c in rows[0]])
    out = []
    for r in rows[1:]:
        r = list(r) + [""] * (len(cols) - len(r))
        out.append({c: (v.strip() if isinstance(v, str) else v) for c, v in zip(cols, r)})
    return Table(cols, out[:MAX_ROWS], fmt, truncated=len(out) > MAX_ROWS, delimiter=delimiter)


def _markdown(text: str) -> Table:
    rows = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append([re.sub(r"[*_`]", "", c) for c in cells])
    return _grid(rows, "md")


def _flat(prefix: str, v: Any, out: dict[str, Any], depth: int = 0) -> None:
    if isinstance(v, dict) and depth < 3:
        for k, x in v.items():
            _flat(f"{prefix}.{k}" if prefix else str(k), x, out, depth + 1)
    elif isinstance(v, float) and not math.isfinite(v):
        out[prefix] = None  # NaN and Infinity are valid in Python's JSON and are no measurement
    elif isinstance(v, (str, int, float, bool)) or v is None:
        out[prefix] = v
    # lists and deeper objects are not cells


def _objects(data: Any) -> list[dict[str, Any]]:
    """The rows hidden in a JSON document: a list of objects, the first list of objects under a key
    ({"runs": [...]}), an object of objects keyed by name ({"BM25": {...}, "Ours": {...}}) or one flat object."""
    if isinstance(data, list):
        if data and all(isinstance(x, dict) for x in data):
            return data
        if data and all(isinstance(x, list) for x in data):
            g = _grid(data, "json")
            return g.rows
        raise ValueError(i18n.t("rmap_results.json_shape"))
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return v
        kids = {k: v for k, v in data.items() if isinstance(v, dict)}
        if kids and len(kids) >= len(data) / 2:
            return [{"name": k, **v} for k, v in kids.items()]
        return [data]
    raise ValueError(i18n.t("rmap_results.json_shape"))


def _json_table(items: list[dict[str, Any]], fmt: str) -> Table:
    rows, cols = [], []
    for it in items[:MAX_ROWS]:
        flat: dict[str, Any] = {}
        _flat("", it, flat)
        flat = {k: v for k, v in flat.items() if k}
        for k in flat:
            if k not in cols:
                cols.append(k)
        rows.append(flat)
    if not rows or not cols:
        raise ValueError(i18n.t("rmap_results.table_empty"))
    return Table(cols, rows, fmt, truncated=len(items) > MAX_ROWS)


def _json_load(text: str) -> Any:
    return json.loads(text, parse_constant=lambda _: None)  # NaN, Infinity, -Infinity become empty cells


def parse(text: str, fmt: str | None = None) -> Table:
    """Pasted text or a file's content. fmt: csv | tsv | json | jsonl (default: sniffed)."""
    text = text.lstrip("\ufeff")
    if not text.strip():
        raise ValueError(i18n.t("rmap_results.table_empty"))
    fmt = fmt or sniff(text)
    try:
        if fmt == "md":
            return _markdown(text)
        if fmt in ("csv", "tsv"):
            delim = _delimiter(text, fmt)
            return _grid(list(csv.reader(io.StringIO(text), delimiter=delim)), fmt, delim)
        if fmt == "jsonl":
            return _json_table([_json_load(ln) for ln in text.splitlines() if ln.strip()], fmt)
        if fmt == "json":
            return _json_table(_objects(_json_load(text)), fmt)
    except (json.JSONDecodeError, csv.Error, RecursionError) as e:
        raise ValueError(i18n.t("rmap_results.unreadable", error=str(e)[:120])) from e
    raise ValueError(i18n.t("rmap_results.bad_format", allowed="csv, tsv, json, jsonl"))


_COMMA_DECIMAL = (re.compile(r"[-+]?\d{1,3}(\.\d{3})+,\d+"), re.compile(r"[-+]?\d+,(\d{1,2}|\d{4,})"))
_POINT_DECIMAL = (re.compile(r"[-+]?\d{1,3}(,\d{3})+\.\d+"), re.compile(r"[-+]?\d+\.(\d{1,2}|\d{4,})"))


def decimal_mark(table: Table) -> str:
    """The decimal mark a table is written with: "," or ".". The cells vote (1.234,5 and 0,5 are commas; 1,234.5 and
    0.5 are points); with no vote a ";"-separated file counts as written in a comma locale, anything else as points."""
    comma = point = 0
    for row in table.rows[:500]:
        for v in row.values():
            if not isinstance(v, str):
                continue
            cell = v.strip().split(" ")[0]
            comma += any(p.fullmatch(cell) for p in _COMMA_DECIMAL)
            point += any(p.fullmatch(cell) for p in _POINT_DECIMAL)
    if comma != point:
        return "," if comma > point else "."
    return "," if table.delimiter == ";" else "."


# ---- files ----

def collapse(path: Path) -> str:
    """How a path is kept in a record: under the home folder as ~/..., so two Macs with different user names agree."""
    try:
        return "~/" + path.relative_to(_HOME).as_posix()
    except ValueError:
        return str(path)


def roots() -> list[Path]:
    """The folders results files may be read from: `[paths] results_roots` in studio.toml (none: paste only)."""
    out = []
    for r in config.RESULTS_ROOTS:
        try:
            out.append(Path(r).resolve())
        except OSError:
            continue
    return out


def resolve(path: str, *, in_roots: bool = True) -> Path:
    """A results file the user named: an absolute path or ~/, a regular .csv / .tsv / .json / .jsonl file of at most
    8 MB, with symbolic links resolved first, inside one of the results roots (in_roots=False: the command line, where
    the person at the shell names the file themselves), and never in a folder of secrets or of this app's own data.
    ValueError says what is wrong."""
    raw = (path or "").strip()
    if not raw:
        raise ValueError(i18n.t("rmap_results.no_path"))
    p = Path(os.path.expanduser(raw))
    if not p.is_absolute():
        raise ValueError(i18n.t("rmap_results.path_relative", path=raw[:120]))
    p = p.resolve()
    if not p.is_file():
        raise ValueError(i18n.t("rmap_results.path_missing", path=raw[:120]))
    if p.suffix.lower() not in EXT_FORMAT:
        raise ValueError(i18n.t("rmap_results.path_ext", ext=p.suffix or "?", allowed=", ".join(EXT_FORMAT)))
    if in_roots and not any(p.is_relative_to(r) for r in roots()):
        raise ValueError(i18n.t("rmap_results.path_outside", path=raw[:120]))
    for d in DENIED:
        try:
            if p.is_relative_to(d.resolve()):
                raise ValueError(i18n.t("rmap_results.path_denied", path=raw[:120]))
        except OSError:
            continue
    if p.stat().st_size > MAX_BYTES:
        raise ValueError(i18n.t("rmap_results.path_big", mb=MAX_BYTES // 1_000_000))
    return p


def read_file(path: str, *, in_roots: bool = True) -> Table:
    p = resolve(path, in_roots=in_roots)
    try:
        text = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = p.read_text(encoding="latin-1")
    except OSError as e:
        raise ValueError(i18n.t("rmap_results.unreadable", error=str(e)[:120])) from e
    st = p.stat()
    tb = parse(text, EXT_FORMAT[p.suffix.lower()])
    tb.meta = {"path": collapse(p), "size": st.st_size, "mtime_ns": st.st_mtime_ns,
               "date": datetime.fromtimestamp(st.st_mtime).date().isoformat()}
    return tb

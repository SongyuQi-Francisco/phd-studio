"""The two attachment kinds of the results feature and the checks on their fields.

    results         one measurement: config, dataset, metric, value, date, source (+ run, baseline, better, note)
    result_sources  a CSV / JSON output file an experiment node reads its results from: path, the column mapping
                    that was used (so the next import needs no mapping), and whether this Mac watches the file
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

from ... import i18n
from ..registry import AttachmentKind

NODE_TYPES = ("experiment", "result")
BETTER = ("higher", "lower")
FORMATS = ("csv", "tsv", "json", "jsonl")
ROLES = ("config", "dataset", "metric", "value", "date", "run", "baseline")  # what a column can mean
TEXT = {"config": 120, "dataset": 80, "metric": 60, "run": 60, "source": 300, "note": 300}
PATH_MAX = 500

# Metrics where a smaller number is the better one (so a bar chart does not crown the worst config).
_LOWER = re.compile(r"(loss|error|err\b|latency|time|perplexity|\bppl\b|\bmae\b|\bmse\b|\brmse\b|\bwer\b|\bcer\b|\bfid\b|"
                    r"cost|memory|\bmem\b|\bms\b|flops|params|size|regret|hallucinat|fail|drift|gap)", re.I)


def lower_is_better(metric: str) -> bool:
    return bool(_LOWER.search(str(metric or "")))


def direction(rec: dict[str, Any]) -> str:
    """"higher" or "lower": what counts as better for this record's metric."""
    return rec.get("better") or ("lower" if lower_is_better(rec.get("metric", "")) else "higher")


# ---- field checks (a value goes in, the clean one comes out; ValueError says why not) ----

def text(name: str, limit: int) -> Callable[[Any], str | None]:
    def check(v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            raise ValueError(i18n.t("rmap_results.bad_text", field=name))
        s = " ".join(str(v).split())
        if len(s) > limit:
            raise ValueError(i18n.t("rmap_results.too_long", field=name, n=limit))
        return s or None
    return check


_NUM = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(?:%|ms|s|x|×)?\s*(?:(?:±|\+/-|\+-).*|\(.*\))?\s*$")
_THOUSANDS = re.compile(r"\s*[-+]?[1-9]\d{0,2}(,\d{3})+\s*[%a-z×]*\s*")


def parse_number(v: Any, decimal: str | None = None) -> float | None:
    """A number out of a cell: 0.412, "41.2%", "1,234.5", "0.412 ± 0.003". None when it is not one.
    decimal: the decimal mark of the file ("." or ","); the other one is then a thousands separator. Without it
    each cell is judged alone ("1,234.5" is 1234.5, "0,412" is 0.412, "1,234" is a thousand): a table should say."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    s = str(v).strip().replace("\u2212", "-").replace("\u00a0", " ")
    if not s:
        return None
    if decimal == ",":
        s = s.replace(".", "").replace(",", ".")
    elif decimal == ".":
        s = s.replace(",", "")
    elif "," in s:
        head = re.split(r"[±(]|\+/-|\+-", s, maxsplit=1)[0]
        if "." in head or _THOUSANDS.fullmatch(head):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".", 1)
    m = _NUM.match(s)
    if not m:
        return None
    n = float(m.group(1))
    return n if math.isfinite(n) else None


def number(v: Any) -> float:
    n = parse_number(v)
    if n is None:
        raise ValueError(i18n.t("rmap_results.bad_value", value=str(v)[:40]))
    return round(n, 6)


def parse_date(v: Any) -> str | None:
    """YYYY-MM-DD out of an ISO date or time, YYYY/MM/DD, YYYYMMDD or a Unix timestamp; None when it is not one."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float)) or re.fullmatch(r"\d{9,13}(\.\d+)?", str(v).strip()):
        try:
            n = float(v)
            n = n / 1000 if n > 1e11 else n
            return datetime.fromtimestamp(n, timezone.utc).astimezone().date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(v).strip().replace("/", "-").replace(".", "-")
    if re.fullmatch(r"\d{8}", s):
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def day(v: Any) -> str | None:
    if v in (None, ""):
        return None
    d = parse_date(v)
    if d is None:
        raise ValueError(i18n.t("rmap_results.bad_date", value=str(v)[:30]))
    return d


def flag(v: Any) -> bool | None:
    if isinstance(v, str):
        v = v.strip().lower() in ("1", "true", "yes", "y", "t", "baseline", "base", "x", "✓")
    return True if v else None


def better(v: Any) -> str | None:
    if v in (None, ""):
        return None
    if v not in BETTER:
        raise ValueError(i18n.t("rmap_results.bad_better", allowed=", ".join(BETTER)))
    return v


def line(rec: dict[str, Any]) -> str:
    """One line for the change log and proposals: Ours · MS MARCO · RECALL@10 = 0.412."""
    parts = [rec.get("config"), rec.get("dataset"), (rec.get("metric") or "").upper()]
    value = rec.get("value")
    return " · ".join(str(p) for p in parts if p) + (f" = {value:g}" if isinstance(value, (int, float)) else "")


def identity(rec: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    """What tells two measurements of one node apart: where they came from, the config, dataset, metric and run, and
    the date, unless the date is only the day a table without dates was imported (then re-reading the table, on any
    day, finds the same measurement again)."""
    return (rec.get("source") or "", rec.get("config") or "", rec.get("dataset") or "", rec.get("metric") or "",
            rec.get("run") or "", "" if rec.get("date_inferred") else rec.get("date") or "")


def day_of(rec: dict[str, Any]) -> str:
    """The day a measurement counts for: its date, else the day it was recorded. Always text (a date edited by hand
    may read back as a number or a YAML date)."""
    return str(rec.get("date") or rec.get("created_at") or "")[:10]


def _text(v: Any) -> str:
    return "" if v is None else str(v)


def _by_code_units(s: str) -> bytes:
    """Sort key that orders text as JavaScript's < does (UTF-16 code units): ties in the results panel's ranking
    (web/rmap/results/data.js `compare`) break the same way here."""
    return s.encode("utf-16-be", "surrogatepass")


SUGGEST = 12  # metrics and configs a digest names, most recently measured first: what the add form suggests


def _recent(records: list[dict[str, Any]], field: str) -> list[str]:
    last: dict[str, str] = {}
    for r in records:
        v = _text(r.get(field))
        if v:
            last[v] = max(last.get(v, ""), day_of(r))
    return sorted(last, key=lambda v: last[v], reverse=True)[:SUGGEST]  # stable: first seen among equals


def digest(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """What the map shows of a node's results (GET map sends this instead of the records): how many there are, the
    most recently measured metric (and dataset; more records breaks a tie), the latest value of its best config that
    is not a baseline (the best baseline when there is nothing else), the latest date, and the metrics and configs
    measured (most recent first, at most SUGGEST of each). A config's latest value is the mean of its runs on its
    latest date, ranked as in the results panel (web/rmap/results/data.js `compare`). Fields edited by hand into
    other types are read as text; values that are not finite numbers are left out."""
    if not records:
        return None
    groups: dict[str, dict[str, Any]] = {}
    for r in records:
        metric, dataset = _text(r.get("metric")), _text(r.get("dataset"))
        g = groups.setdefault(f"{metric.lower()}\0{dataset.lower()}", {"metric": metric, "dataset": dataset, "n": 0, "last": ""})
        g["n"] += 1
        g["last"] = max(g["last"], day_of(r))
    top = max(groups.values(), key=lambda g: (g["last"], g["n"]))  # the first one seen among equals
    out = {"count": len(records), "metric": top["metric"], "dataset": top["dataset"], "last": top["last"] or None,
           "metrics": _recent(records, "metric"), "configs": _recent(records, "config")}
    rs = [r for r in records if _text(r.get("metric")).lower() == top["metric"].lower() and _text(r.get("dataset")) == top["dataset"]]
    lower = direction(next((r for r in rs if r.get("better")), rs[0])) == "lower"
    by_config: dict[str, list[dict[str, Any]]] = {}
    for r in rs:
        by_config.setdefault(_text(r.get("config")), []).append(r)
    rows = []
    for config, recs in by_config.items():
        last = max(day_of(r) for r in recs)
        vals = [float(r["value"]) for r in recs if day_of(r) == last and isinstance(r.get("value"), (int, float))
                and not isinstance(r["value"], bool) and math.isfinite(r["value"])]
        if vals:
            rows.append({"config": config, "value": sum(vals) / len(vals), "baseline": any(r.get("baseline") for r in recs)})
    rows.sort(key=lambda x: (x["value"] if lower else -x["value"], _by_code_units(x["config"])))
    row = next((x for x in rows if not x["baseline"]), rows[0] if rows else None)
    return {**out, "config": row["config"], "value": row["value"]} if row else out


def _check_result(rec: dict[str, Any]) -> None:
    if rec.get("value") is not None and not math.isfinite(rec["value"]):
        raise ValueError(i18n.t("rmap_results.bad_value", value=str(rec["value"])))


RESULTS = AttachmentKind(
    name="results", prefix="rs", node_types=NODE_TYPES, log="batch",
    fields={"config": text("config", TEXT["config"]), "dataset": text("dataset", TEXT["dataset"]),
            "metric": text("metric", TEXT["metric"]), "value": number, "date": day, "run": text("run", TEXT["run"]),
            "baseline": flag, "better": better, "source": text("source", TEXT["source"]), "note": text("note", TEXT["note"]),
            "date_inferred": flag},
    required=("config", "metric", "value"), summary=line, check=_check_result, shard="node", identity=identity,
    digest=digest, extra={"better": list(BETTER), "formats": list(FORMATS), "roles": list(ROLES)})


def _columns(v: Any) -> list[str] | None:
    if v in (None, []):
        return None
    if not isinstance(v, list) or len(v) > 200 or not all(isinstance(c, str) and len(c) <= 200 for c in v):
        raise ValueError(i18n.t("rmap_results.bad_columns"))
    return v


def _mapping(v: Any) -> dict[str, Any] | None:
    if v in (None, {}):
        return None
    if not isinstance(v, dict):
        raise ValueError(i18n.t("rmap_results.bad_mapping"))
    from . import mapping  # the one place that knows what a mapping looks like
    return mapping.clean(v)


def _format(v: Any) -> str | None:
    if v in (None, ""):
        return None
    if v not in FORMATS:
        raise ValueError(i18n.t("rmap_results.bad_format", allowed=", ".join(FORMATS)))
    return v


SOURCES = AttachmentKind(
    name="result_sources", prefix="rf", node_types=NODE_TYPES, file="result_sources.yaml",
    fields={"path": text("path", PATH_MAX), "format": _format, "columns": _columns, "mapping": _mapping, "watch": flag,
            "device": text("device", 80)},
    required=("path",), summary=lambda rec: str(rec.get("path") or ""))

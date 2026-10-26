"""Which column of a results table means what, and the records that come out of it.

A mapping is a small JSON object, kept on the source record so the next import of the same file needs none:

    {"config":  {"col": "method"},            # a column ...
     "dataset": {"const": "MS MARCO"},        # ... or one fixed value for the whole table
     "metric":  {"col": "metric"}, "value": {"col": "score"},      # long form: a row per measurement
     "metrics": ["recall@10", "ndcg@10"],     # wide form instead: each listed column is a metric
     "date": {"col": "date"}, "run": {"col": "seed"}, "baseline": {"col": "is_baseline"},
     "baselines": ["BM25", "DPR"],            # configs that are baselines, by name
     "lower": ["latency_ms"]}                 # metrics where smaller is better (else guessed from the name)

`guess` proposes one from the header (and from the mapping remembered for the same header), `records` applies it.
"""
from __future__ import annotations

import re
from typing import Any

from ... import i18n
from . import model
from .tabular import Table

_NAMES = {
    "config": ("config", "configuration", "method", "model", "system", "variant", "setting", "algorithm", "approach",
               "name", "key", "experiment", "exp", "runname", "run_name", "ablation"),
    "dataset": ("dataset", "data", "benchmark", "corpus", "task", "split", "set", "domain"),
    "metric": ("metric", "metricname", "measure", "measurement"),
    "value": ("value", "score", "result", "val", "metricvalue", "number"),
    "date": ("date", "day", "timestamp", "time", "created", "createdat", "datetime", "at", "when"),
    "run": ("seed", "run", "runid", "trial", "fold", "replicate", "repeat", "rep"),
    "baseline": ("baseline", "isbaseline", "base"),
}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _ref(spec: Any) -> tuple[str, str] | None:
    """("col", name) | ("const", text) for a mapping entry, None when it is empty."""
    if isinstance(spec, dict):
        if spec.get("col") not in (None, ""):
            return ("col", str(spec["col"]))
        if spec.get("const") not in (None, ""):
            return ("const", str(spec["const"]))
    return None


def clean(m: dict[str, Any]) -> dict[str, Any]:
    """A mapping as stored: only the known entries, each a {col} or {const}, names kept short."""
    out: dict[str, Any] = {}
    for role in model.ROLES:
        r = _ref(m.get(role))
        if r:
            out[role] = {r[0]: r[1][:200]}
    for key in ("metrics", "baselines", "lower"):
        v = m.get(key)
        if v in (None, []):
            continue
        if not isinstance(v, list) or len(v) > 200 or not all(isinstance(x, str) and 0 < len(x) <= 200 for x in v):
            raise ValueError(i18n.t("rmap_results.bad_mapping"))
        out[key] = list(dict.fromkeys(v))
    return out


def _numeric_share(table: Table, col: str) -> float:
    cells = [r.get(col) for r in table.rows[:200] if r.get(col) not in (None, "")]
    if not cells:
        return 0.0
    return sum(model.parse_number(c) is not None for c in cells) / len(cells)


def guess(table: Table) -> dict[str, Any]:
    """A first mapping from the column names; wide form when there is no metric / value pair."""
    cols = table.columns
    norm = {c: _norm(c) for c in cols}
    taken: set[str] = set()
    found: dict[str, str] = {}
    for role in ("metric", "value", "baseline", "date", "dataset", "run", "config"):
        for want in _NAMES[role]:
            hit = next((c for c in cols if norm[c] == _norm(want) and c not in taken), None)
            if hit:
                found[role] = hit
                taken.add(hit)
                break
    m: dict[str, Any] = {}
    long_form = "metric" in found and "value" in found
    if not long_form:
        # wide: every numeric column that is not a label is a metric
        extra = [c for c in cols if c not in taken and _numeric_share(table, c) >= 0.6]
        if "value" in found:
            extra.insert(0, found.pop("value"))
        if extra:
            m["metrics"] = extra
            taken.update(extra)
        found.pop("metric", None)
    if "config" not in found:
        label = next((c for c in cols if c not in taken and _numeric_share(table, c) < 0.3), None)
        if label:
            found["config"] = label
    for role, col in found.items():
        m[role] = {"col": col}
    return m


def signature(columns: list[str]) -> list[str]:
    return sorted(columns)


def remembered(table: Table, sources: list[dict[str, Any]], path: str | None = None) -> dict[str, Any] | None:
    """The mapping used before for this file, or for a file with the same header, if its columns are all still there."""
    sig, best = signature(table.columns), None
    for s in sources:
        mp, cols = s.get("mapping"), s.get("columns")
        if not mp or not cols or not _uses_only(mp, set(table.columns)):
            continue
        if path and s.get("path") == path:
            return mp
        if signature(cols) == sig and best is None:
            best = mp
    return best


def _uses_only(mp: dict[str, Any], cols: set[str]) -> bool:
    for role in model.ROLES:
        r = _ref(mp.get(role))
        if r and r[0] == "col" and r[1] not in cols:
            return False
    return all(c in cols for c in mp.get("metrics", []))


def check(mp: dict[str, Any], table: Table) -> None:
    """ValueError when the mapping cannot give a measurement: what is missing, in words."""
    cols = set(table.columns)
    for role in model.ROLES:
        r = _ref(mp.get(role))
        if r and r[0] == "col" and r[1] not in cols:
            raise ValueError(i18n.t("rmap_results.no_column", column=r[1]))
    for c in mp.get("metrics", []):
        if c not in cols:
            raise ValueError(i18n.t("rmap_results.no_column", column=c))
    if not _ref(mp.get("config")):
        raise ValueError(i18n.t("rmap_results.need_config"))
    if not mp.get("metrics") and not (_ref(mp.get("metric")) and _ref(mp.get("value"))):
        raise ValueError(i18n.t("rmap_results.need_metric"))


def _cell(row: dict[str, Any], spec: Any) -> Any:
    r = _ref(spec)
    if not r:
        return None
    return row.get(r[1]) if r[0] == "col" else r[1]


def _text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v == int(v):
        v = int(v)
    s = " ".join(str(v).split())
    return s or None


def records(table: Table, mp: dict[str, Any], *, default_date: str, source: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(records, skipped): one record per measurement; a skipped entry says which row and why."""
    check(mp, table)
    baselines = {b.lower() for b in mp.get("baselines", [])}
    lower = {m.lower() for m in mp.get("lower", [])}
    out: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for i, row in enumerate(table.rows, start=2 if table.format in ("csv", "tsv", "md") else 1):
        config = _text(_cell(row, mp.get("config")))
        if not config:
            skipped.append({"row": i, "reason": "config"})
            continue
        base = {"config": config, "dataset": _text(_cell(row, mp.get("dataset"))), "run": _text(_cell(row, mp.get("run")))}
        d = model.parse_date(_cell(row, mp.get("date"))) if _ref(mp.get("date")) else None
        base["date"] = d or default_date
        if config.lower() in baselines or (_ref(mp.get("baseline")) and model.flag(_cell(row, mp.get("baseline")))):
            base["baseline"] = True
        if source:
            base["source"] = source
        pairs: list[tuple[str, Any]] = []
        if mp.get("metrics"):
            pairs = [(c, row.get(c)) for c in mp["metrics"]]
        else:
            pairs = [(_text(_cell(row, mp.get("metric"))) or "", _cell(row, mp.get("value")))]
        for metric, raw in pairs:
            if raw in (None, ""):
                continue
            v = model.parse_number(raw)
            if not metric or v is None:
                skipped.append({"row": i, "reason": "value", "metric": metric, "cell": str(raw)[:30]})
                continue
            rec = {**base, "metric": metric[:model.TEXT["metric"]], "value": round(v, 6)}
            if metric.lower() in lower:
                rec["better"] = "lower"
            out.append({k: x for k, x in rec.items() if x is not None})
    return out, skipped


def configs(table: Table, mp: dict[str, Any]) -> list[str]:
    """The distinct config names (in order) a mapping finds, for ticking the baselines."""
    seen: dict[str, None] = {}
    for row in table.rows:
        c = _text(_cell(row, mp.get("config")))
        if c:
            seen.setdefault(c)
    return list(seen)

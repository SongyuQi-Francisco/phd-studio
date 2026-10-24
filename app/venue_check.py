"""Re-checking a venue's details: the assistant proposes, the user confirms the diff, the vault stores it.

Flow: the 「让助手核对」 button hands the assistant `check_prompt()`. The assistant reads the official CFP and
calls the workbench tool `venue_details_propose`, which only calls `propose()` here: a pending proposal per
venue, kept per device in the state dir (nothing synced, nothing applied). The venue sheet shows the diff;
`apply()` runs only from the user's confirm click and writes venue_overrides in the vault.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

from . import plan, venue_fresh, venue_overrides, venues
from .config import STATE_DIR, VAULT_DIR, VENUE_OVERRIDES_FILE

PROPOSALS_FILE = STATE_DIR / "venue_proposals.json"
STATUSES = ("confirmed", "previous-edition")
# Fields a proposal may change, in the order the diff shows them.
FIELDS = [("status", "状态"), ("edition", "届次"), ("edition_year", "届次年份"), ("dates", "会议日期"), ("location", "地点"),
          ("homepage", "官网"), ("cfp_url", "CFP 页面"), ("deadlines", "各赛道截稿"), ("tracks", "赛道与页数"),
          ("format.template", "模板"), ("format.review_model", "审稿模式"), ("format.notable_rules", "投稿规则"),
          ("notes", "备注"), ("sources", "来源")]
_STR = ("edition", "dates", "location")
_URL = ("homepage", "cfp_url")
_ROWS = {"deadlines": ("track", "abstract", "paper"), "tracks": ("name", "page_limit", "notes")}
_lock = threading.Lock()


class Conflict(Exception):
    """The details changed after the proposal was made, so its diff no longer holds."""


# ---------- pending proposals (per device) ----------


def _load() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    tmp = PROPOSALS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PROPOSALS_FILE)


def pending() -> dict[str, dict[str, Any]]:
    with _lock:
        return _load()


def _drop(key: str) -> None:
    with _lock:
        data = _load()
        if data.pop(key, None) is not None:
            _save(data)


# ---------- validating what the assistant sends ----------


def _text(v: Any, name: str) -> str | None:
    if v is None:
        return None
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        raise ValueError(f"{name} 必须是文字")
    return str(v).strip() or None


def _url(v: Any, name: str) -> str:
    u = _text(v, name) or ""
    if not re.match(r"https?://\S+$", u):
        raise ValueError(f"{name} 必须是 http(s) 网址：{u!r}")
    return u


def _strings(v: Any, name: str) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        raise ValueError(f"{name} 必须是文字列表")
    return [t for t in (_text(x, name) for x in v) if t]


def clean_patch(details: dict[str, Any], sources: list[str]) -> dict[str, Any]:
    """Validated fields of a proposal; unknown fields are refused. `sources` is merged into the patch."""
    if not isinstance(details, dict):
        raise ValueError("details 必须是对象")
    allowed = {f.split(".")[0] for f, _ in FIELDS}
    extra = sorted(set(details) - allowed)
    if extra:
        raise ValueError(f"不能修改这些字段：{', '.join(extra)}（核对日期由系统在确认时写入）。可改字段：{', '.join(sorted(allowed))}")
    out: dict[str, Any] = {}
    for k, v in details.items():
        if v is None:
            continue
        if k in _STR:
            out[k] = _text(v, k)
        elif k in _URL:
            out[k] = _url(v, k)
        elif k == "status":
            if v not in STATUSES:
                raise ValueError(f"status 只能是 {' / '.join(STATUSES)}")
            out[k] = v
        elif k == "edition_year":
            if not isinstance(v, int) or isinstance(v, bool) or not 2000 <= v <= 2100:
                raise ValueError("edition_year 必须是年份（整数）")
            out[k] = v
        elif k in _ROWS:
            if not isinstance(v, list) or not all(isinstance(r, dict) for r in v):
                raise ValueError(f"{k} 必须是对象列表")
            for r in v:
                if set(r) - set(_ROWS[k]):
                    raise ValueError(f"{k} 的每一项只能有 {'、'.join(_ROWS[k])}")
            out[k] = [{f: _text(r.get(f), f) for f in _ROWS[k]} for r in v]
        elif k == "format":
            if not isinstance(v, dict) or set(v) - {"template", "review_model", "notable_rules"}:
                raise ValueError("format 只能有 template、review_model、notable_rules")
            out[k] = {f: (_strings(x, f"format.{f}") if f == "notable_rules" else _text(x, f"format.{f}"))
                      for f, x in v.items() if x is not None}
        elif k == "notes":
            out[k] = _strings(v, k) if isinstance(v, list) else _text(v, k)
        elif k == "sources":
            sources = [*sources, *_strings(v, k)]
    srcs = [_url(s, "sources") for s in sources]
    if not srcs:
        raise ValueError("必须附上你读过的官方网址（sources）")
    out["_sources"] = list(dict.fromkeys(srcs))
    if "edition" in out and "edition_year" not in out:
        year = venue_fresh.edition_year({"edition": out["edition"]})
        if year is None:
            raise ValueError("edition 里没有年份，请同时给出 edition_year")
        out["edition_year"] = year
    return {k: v for k, v in out.items() if v is not None}


# ---------- diff ----------


def _value(d: dict[str, Any], field: str) -> Any:
    head, _, sub = field.partition(".")
    v = d.get(head)
    return (v or {}).get(sub) if sub else v


def _line(x: Any) -> str:
    if isinstance(x, dict) and "track" in x:
        dates = " · ".join(p for p in (x.get("abstract") and f"摘要 {x['abstract']}", x.get("paper") and f"全文 {x['paper']}") if p)
        return f"{x.get('track') or '主会'}：{dates or '—'}"
    if isinstance(x, dict) and "name" in x:
        return f"{x['name']}：{x.get('page_limit') or '—'}" + (f"（{x['notes']}）" if x.get("notes") else "")
    return str(x)


def _lines(v: Any) -> list[str]:
    if v in (None, "", [], {}):
        return []
    return [_line(x) for x in v] if isinstance(v, list) else [str(v)]


def diff_detail(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Per changed field, the removed (-) and added (+) lines."""
    out = []
    for field, label in FIELDS:
        a, b = _lines(_value(before, field)), _lines(_value(after, field))
        if a == b:
            continue
        lines = []
        for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op != "equal":
                lines += [{"op": "-", "text": t} for t in a[i1:i2]] + [{"op": "+", "text": t} for t in b[j1:j2]]
        out.append({"field": field, "label": label, "lines": lines})
    return out


def _digest(detail: dict[str, Any] | None) -> str:
    d = {k: v for k, v in (detail or {}).items() if k != "verified_at"}
    return hashlib.sha1(json.dumps(d, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:12]


def _after(before: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    p = {k: v for k, v in patch.items() if k != "_sources"}
    p["sources"] = list(dict.fromkeys([*(before.get("sources") or []), *patch.get("_sources", [])]))
    return venue_overrides.merge_detail(before, p)


# ---------- propose / view / apply ----------


def _entry(venue: str) -> dict[str, Any]:
    e = venues.catalog().get(venues._key(venue))
    if not e:
        raise ValueError(f"unknown venue {venue}")
    return e


def propose(venue: str, details: dict[str, Any], sources: list[str], summary: str = "") -> dict[str, Any]:
    """Record the assistant's proposed update for a venue (replacing its earlier pending one). Applies nothing."""
    e = _entry(venue)
    before = e.get("detail") or {}
    patch = clean_patch(details or {}, list(sources or []))
    if not before and "status" not in patch:
        raise ValueError(f"{e['acronym']} 还没有详情记录，提案里要给出 status 和 edition 等字段")
    rec = {"id": "p" + hashlib.sha1(os.urandom(8)).hexdigest()[:6], "venue": e["key"], "acronym": e["acronym"],
           "summary": (summary or "").strip(), "details": patch, "base": _digest(before),
           "created_at": datetime.now().isoformat(timespec="seconds")}
    with _lock:
        data = _load()
        data[e["key"]] = rec
        _save(data)
    v = view(rec)
    return {**v, "changes": len(v["diff"])}


def view(rec: dict[str, Any]) -> dict[str, Any]:
    """A proposal as the UI shows it: the diff against the details as they are now."""
    e = _entry(rec["venue"])
    before = e.get("detail") or {}
    diff = diff_detail(before, _after(before, rec["details"]))
    return {"id": rec["id"], "venue": rec["venue"], "acronym": rec["acronym"], "summary": rec["summary"],
            "created_at": rec["created_at"], "sources": rec["details"].get("_sources", []), "diff": diff,
            "outdated": rec["base"] != _digest(before)}


def proposal(venue: str) -> dict[str, Any] | None:
    rec = pending().get(venues._key(venue))
    return view(rec) if rec else None


def _commit(key: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Store `patch` (fields to change; may be empty) as the venue's override, dated today."""
    e = _entry(key)
    before = e.get("detail") or {}
    if not before and not patch:
        raise ValueError(f"{e['acronym']} 还没有详情，没有可核对的内容")
    patch = {**patch, "verified_at": date.today().isoformat()}
    after = venue_overrides.merge_detail(before, patch)
    saved = venue_overrides.save(e["key"], patch, venue_fresh.fingerprint(e, venue_fresh.edition_year(after)), name=e["acronym"])
    venues._catalog.cache_clear()
    return saved


def apply(venue: str, proposal_id: str) -> dict[str, Any]:
    """Write a pending proposal to the vault. Called only from the user's confirm."""
    key = venues._key(venue)
    rec = pending().get(key)
    if not rec or rec["id"] != proposal_id:
        raise LookupError("这条核对结果已经处理过或不存在")
    e = _entry(key)
    if rec["base"] != _digest(e.get("detail")):
        raise Conflict("会议详情在助手核对之后又变了（比如另一台电脑已更新），请重新核对")
    patch = {k: v for k, v in rec["details"].items() if k != "_sources"}
    patch["sources"] = list(dict.fromkeys([*((e.get("detail") or {}).get("sources") or []), *rec["details"].get("_sources", [])]))
    saved = _commit(key, patch)
    _drop(key)
    return {"ok": True, "venue": key, "verified_at": saved["details"]["verified_at"]}


def dismiss(venue: str, proposal_id: str) -> dict[str, Any]:
    key = venues._key(venue)
    rec = pending().get(key)
    if not rec or rec["id"] != proposal_id:
        raise LookupError("这条核对结果已经处理过或不存在")
    _drop(key)
    return {"ok": True, "venue": key}


def mark_verified(venue: str) -> dict[str, Any]:
    """The user checked it themselves and nothing changed: only the verified date (and what ccfddl shows) is recorded."""
    key = venues._key(venue)
    saved = _commit(key, {})
    return {"ok": True, "venue": key, "verified_at": saved["details"]["verified_at"]}


def reset(venue: str) -> dict[str, Any]:
    key = venues._key(venue)
    ok = venue_overrides.reset(key)
    venues._catalog.cache_clear()
    return {"ok": ok, "venue": key}


# ---------- what the UI and the assistant read ----------


def status(venue: str) -> dict[str, Any]:
    e = _entry(venue)
    ov = e.get("override")
    return {"venue": e["key"], "acronym": e["acronym"], "freshness": venue_fresh.check(e), "proposal": proposal(e["key"]),
            "override": {"updated_at": ov.get("updated_at")} if ov else None, "prompt": check_prompt(e["key"]),
            "store": str(VENUE_OVERRIDES_FILE.relative_to(VAULT_DIR) if VENUE_OVERRIDES_FILE.is_relative_to(VAULT_DIR)
                         else VENUE_OVERRIDES_FILE)}


def stale_watched() -> list[dict[str, Any]]:
    """Watched venues whose details look out of date or that have a proposal waiting for confirmation."""
    cat, waiting, out = venues.catalog(), pending(), []
    for k in plan.watched_keys():
        e = cat.get(k)
        f = venue_fresh.check(e) if e else None
        if e and ((f and f["stale"]) or k in waiting):
            out.append({"key": k, "acronym": e["acronym"], "core": e["core"], "ccf": e["ccf"], "pending": k in waiting,
                        "reasons": f["reasons"] if f else [], "edition_year": f and f["edition_year"],
                        "verified_at": f and f["verified_at"]})
    return sorted(out, key=lambda r: (not r["pending"], -len(r["reasons"]), r["acronym"]))


def check_prompt(venue: str) -> str:
    """What 「让助手核对」 sends to the workbench assistant."""
    e = _entry(venue)
    d = e.get("detail") or {}
    f = venue_fresh.check(e)
    reasons = "；".join(r["text"] for r in (f or {}).get("reasons", [])) or "手动发起"
    ccf_link = next((ed.get("link") for ed in reversed(e.get("ccfddl_editions") or []) if ed.get("link")), None)
    starts = "；".join(f"{n}：{u}" for n, u in (("CFP", d.get("cfp_url")), ("官网", d.get("homepage")), ("ccfddl 的当前链接", ccf_link)) if u)
    now = f"现在记录的是 {d['edition']}（核对于 {d.get('verified_at') or '未知'}）" if d.get("edition") else "现在还没有这个会议的详情记录"
    return f"""请核对 {e['acronym']} 的最新官方 CFP，看工作台里记录的投稿信息有没有过期。
{now}。触发核对的原因：{reasons}。
步骤：
1. 先用 venue_search 查 "{e['acronym']}"，看现在记录的内容。
2. 读官方页面，只采信官网，不要用第三方转载。起点：{starts or '会议官网'}。如果已经有更新的一届，找那一届的 CFP。
3. 对照记录，用 venue_details_propose 提交更新提案：details 只写有变化的字段（deadlines、tracks、format.notable_rules 这类列表要写完整的新列表，截稿日期保留官网原文并带时区）；sources 填你实际读过的官方网址；官网已发布这一届的 CFP 就 status=confirmed，只找到上一届的就 previous-edition；换届时同时给 edition 和 edition_year。核对后没有变化也要提交（details 留空，只更新核对日期）。
4. 这个工具只提交提案，不会改任何数据；我会在会议详情页看差异后确认。提交后用一两句话告诉我改了什么、哪些没查到。"""

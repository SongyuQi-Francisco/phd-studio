"""Venue details the user has confirmed on top of the shipped data/venue_details.json.

The shipped file is the baseline. When the assistant re-checks an official CFP and the user confirms the
proposal, the changed fields go to a sync-safe YAML store in the vault (config.VENUE_OVERRIDES_FILE) and
`venues` merges them over the baseline. One record per venue, id = the venue key:

    venues: [{id: acl, key: ACL, details: {...fields that differ from the baseline...},
              base: {edition_year, verified_at}, ccfddl_seen: {...}, updated_at: ...}]

- `details` may hold `field: null` to remove a field the baseline has.
- `base` is the shipped edition and check date the override was confirmed against. Once the shipped file
  moves past what the user confirmed (a newer edition, or a later check date) the shipped data wins.
- `ccfddl_seen` remembers what ccfddl showed when the details were last verified (see venue_fresh).

The file is edited by hand too, so every record is validated on read; a record that does not hold up is
skipped with a warning instead of breaking the venue library.
"""
from __future__ import annotations

import copy
import logging
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

from . import venue_fresh
from .config import VENUE_OVERRIDES_FILE
from .store import Store, now

log = logging.getLogger(__name__)

store = Store(VENUE_OVERRIDES_FILE, ("venues",),
              "# Venue details confirmed in PhD Studio (merged over data/venue_details.json), managed by the app.\n")

STATUSES = ("confirmed", "previous-edition")
TEXT_FIELDS = ("edition", "dates", "location")
URL_FIELDS = ("homepage", "cfp_url")
ROWS = {"deadlines": ("track", "abstract", "paper"), "tracks": ("name", "page_limit", "notes")}
FORMAT_FIELDS = ("template", "review_model", "notable_rules")
FIELDS = ("status", "edition", "edition_year", "dates", "location", "homepage", "cfp_url", "deadlines", "tracks",
          "format", "notes", "sources")
# Fields (and format sub-fields) that may be removed outright; the identity of the details may not.
REMOVABLE = ("dates", "location", "homepage", "cfp_url", "deadlines", "tracks", "notes",
             "format.template", "format.review_model", "format.notable_rules")
_HOST = re.compile(r"^\w([\w-]*\w)?(\.\w([\w-]*\w)?)*$")
_BAD_URL_CHARS = re.compile(r"[\s<>\"'`\\^{}|]")


# ---------- validating details (proposals and what was stored) ----------


def text(v: Any, name: str) -> str | None:
    if v is None:
        return None
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        raise ValueError(f"{name} 必须是文字")
    return str(v).strip() or None


def url(v: Any, name: str) -> str:
    u = text(v, name) or ""
    try:
        parts = urlsplit(u)
        parts.port  # noqa: B018 - a bad port raises
    except ValueError:
        parts = None
    if (parts is None or parts.scheme not in ("http", "https") or not parts.hostname or not _HOST.match(parts.hostname)
            or _BAD_URL_CHARS.search(u)):
        raise ValueError(f"{name} 必须是 http(s) 网址：{u!r}")
    return u


def strings(v: Any, name: str) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        raise ValueError(f"{name} 必须是文字列表")
    return [t for t in (text(x, name) for x in v) if t]


def _day(v: Any) -> str | None:
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        return None


def clean_details(details: Any, *, stored: bool = False) -> dict[str, Any]:
    """Validated venue details. Raises ValueError for the first thing that does not hold up.

    A proposal (`stored=False`) may only carry the fields above, and None values are dropped. Stored details may
    also carry `verified_at`, unknown fields are ignored with a warning, and None is kept for a removable field
    (it removes that field from the shipped details).
    """
    if not isinstance(details, dict):
        raise ValueError("details 必须是对象")
    allowed = set(FIELDS) | ({"verified_at"} if stored else set())
    extra = sorted(set(details) - allowed)
    if extra and not stored:
        raise ValueError(f"不能修改这些字段：{', '.join(extra)}（核对日期由系统在确认时写入）。可改字段：{', '.join(sorted(FIELDS))}")
    if extra:
        log.warning("venue override: ignoring unknown fields %s", ", ".join(map(str, extra)))
    out: dict[str, Any] = {}
    for k, v in details.items():
        if k not in allowed:
            continue
        if v is None:
            if stored and k in REMOVABLE:
                out[k] = None
            continue
        if k in TEXT_FIELDS:
            out[k] = text(v, k)
        elif k in URL_FIELDS:
            out[k] = url(v, k)
        elif k == "status":
            if v not in STATUSES:
                raise ValueError(f"status 只能是 {' / '.join(STATUSES)}")
            out[k] = v
        elif k == "edition_year":
            if not isinstance(v, int) or isinstance(v, bool) or not 2000 <= v <= 2100:
                raise ValueError("edition_year 必须是年份（整数）")
            out[k] = v
        elif k in ROWS:
            if not isinstance(v, list) or not all(isinstance(r, dict) for r in v):
                raise ValueError(f"{k} 必须是对象列表")
            for r in v:
                if set(r) - set(ROWS[k]):
                    raise ValueError(f"{k} 的每一项只能有 {'、'.join(ROWS[k])}")
            out[k] = [{f: text(r.get(f), f) for f in ROWS[k]} for r in v]
        elif k == "format":
            if not isinstance(v, dict) or set(v) - set(FORMAT_FIELDS):
                raise ValueError(f"format 只能有 {'、'.join(FORMAT_FIELDS)}")
            fmt = {}
            for f, x in v.items():
                if x is None:
                    if stored and f"format.{f}" in REMOVABLE:
                        fmt[f] = None
                    continue
                fmt[f] = strings(x, f"format.{f}") if f == "notable_rules" else text(x, f"format.{f}")
            out[k] = fmt
        elif k == "notes":
            out[k] = strings(v, k) if isinstance(v, list) else text(v, k)
        elif k == "sources":
            out[k] = [url(s, "sources") for s in strings(v, k)]
        elif k == "verified_at":
            day = _day(v)
            if day is None:
                raise ValueError(f"verified_at 必须是日期：{v!r}")
            out[k] = day
    return {k: v for k, v in out.items() if v is not None or (stored and k in REMOVABLE)}


def tombstones(fields: list[str] | None) -> dict[str, Any]:
    """Details patch that removes these (REMOVABLE) fields: `{"cfp_url": None, "format": {"template": None}}`."""
    out: dict[str, Any] = {}
    for f in fields or []:
        if f not in REMOVABLE:
            raise ValueError(f"不能删除字段 {f!r}。可删除：{', '.join(REMOVABLE)}")
        head, _, sub = f.partition(".")
        if sub:
            out.setdefault(head, {})[sub] = None
        else:
            out[head] = None
    return out


# ---------- merging ----------


def merge_detail(base: dict[str, Any], patch: dict[str, Any], keep_none: bool = False) -> dict[str, Any]:
    """`base` with `patch` laid over it: dict fields (format) merge key by key, everything else is replaced.

    A None in the patch removes that field from the result; with `keep_none` it is kept instead, so a patch
    that is itself an override can carry the removal on to the shipped details.
    """
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if v is None:
            if keep_none:
                out[k] = None
            else:
                out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_detail(out[k], v, keep_none)
        elif isinstance(v, dict) and not keep_none:
            out[k] = {kk: copy.deepcopy(vv) for kk, vv in v.items() if vv is not None}
        else:
            out[k] = copy.deepcopy(v)
    return out


def base_of(bundled: dict[str, Any] | None) -> dict[str, Any] | None:
    """The shipped details' edition and check date, as recorded with an override."""
    if not bundled:
        return None
    v = venue_fresh.verified_on(bundled)
    return {"edition_year": venue_fresh.edition_year(bundled), "verified_at": v.isoformat() if v else None}


def outdated(bundled: dict[str, Any] | None, rec: dict[str, Any]) -> bool:
    """The shipped details moved past what this override was confirmed against: a newer edition, or a later check.

    Then the shipped data wins as a whole, so an old override never mixes with a newer edition's fields.
    """
    base = rec.get("base")
    if not bundled or not base:
        return False
    d = rec.get("details") or {}

    def rank(year: Any, checked: Any) -> tuple[int, date]:
        return (year if isinstance(year, int) else 0, venue_fresh.verified_on({"verified_at": checked}) or date.min)

    mine = rank(d.get("edition_year") or base.get("edition_year"), d.get("verified_at") or base.get("verified_at"))
    return rank(venue_fresh.edition_year(bundled), bundled.get("verified_at")) > mine


# ---------- reading and writing the store ----------


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _instants(v: Any) -> list[str]:
    out = []
    for x in v if isinstance(v, list) else []:
        s = x.isoformat() if isinstance(x, datetime) else str(x)
        try:
            datetime.fromisoformat(s)
        except ValueError:
            continue
        out.append(s)
    return out


def _edition_seen(v: Any) -> dict[str, Any]:
    v = v if isinstance(v, dict) else {}
    return {"link": str(v.get("link") or ""), "dates": _instants(v.get("dates"))}


def clean_seen(v: Any) -> dict[str, Any] | None:
    """What ccfddl showed at the last check, whatever shape a hand edit left it in."""
    if not isinstance(v, dict):
        return None
    newer = v.get("newer") if isinstance(v.get("newer"), dict) else {}
    return {"year": _int(v.get("year")), "upcoming": _int(v.get("upcoming")), **_edition_seen(v),
            "newer": {str(k): _edition_seen(x) for k, x in newer.items()}}


def _clean_base(v: Any) -> dict[str, Any] | None:
    if not isinstance(v, dict):
        return None
    return {"edition_year": _int(v.get("edition_year")), "verified_at": _day(v.get("verified_at"))}


def _valid(rec: dict[str, Any]) -> dict[str, Any] | None:
    try:
        if not isinstance(rec.get("id"), str):
            raise ValueError("id 必须是文字")
        return {**rec, "details": clean_details(rec.get("details") or {}, stored=True),
                "ccfddl_seen": clean_seen(rec.get("ccfddl_seen")), "base": _clean_base(rec.get("base"))}
    except (ValueError, TypeError, AttributeError) as e:
        log.warning("venue override %r skipped: %s", rec.get("id"), e)
        return None


def load() -> dict[str, dict[str, Any]]:
    """Live, valid override records by venue key."""
    try:
        records = store.load()["venues"]
    except Exception as e:  # noqa: BLE001 - a hand-edited file must not take the venue library down
        log.warning("venue overrides unreadable (%s): %s", store.path, e)
        return {}
    out = {}
    for r in records:
        ok = _valid(r)
        if ok:
            out[ok["id"]] = ok
    return out


def stamp() -> tuple[int, int] | None:
    """Changes whenever the store file (or a Syncthing conflict copy waiting to be merged) changes."""
    path = store.path
    copies = len(list(path.parent.glob(f"{path.stem}.sync-conflict-*{path.suffix}"))) if path.parent.exists() else 0
    try:
        st = path.stat()
    except OSError:
        return (0, copies) if copies else None
    return (st.st_mtime_ns, copies)


def save(key: str, patch: dict[str, Any], seen: dict[str, Any] | None, name: str | None = None,
         base: dict[str, Any] | None = None, fresh: bool = False) -> dict[str, Any]:
    """Merge `patch` into the venue's override (creating or reviving it; `fresh` starts over), remembering what
    ccfddl showed and which shipped details (`base`) it was confirmed against."""
    with store.editing() as data:
        rec = next((r for r in data["venues"] if r["id"] == key), None)
        if rec is None or rec.get("deleted") or fresh:
            data["venues"] = [r for r in data["venues"] if r["id"] != key]
            rec = {"id": key, "key": name or key.upper(), "details": {}, "created_at": now()}
            data["venues"].append(rec)
        rec["details"] = merge_detail(rec.get("details") or {}, patch, keep_none=True)
        rec["ccfddl_seen"] = seen
        rec["base"] = base
        rec["updated_at"] = now()
        return store._clean(rec)


def reset(key: str) -> bool:
    """Drop the override: the venue goes back to the shipped details. False when there was none."""
    try:
        known = {r["id"] for r in store.load()["venues"]}
    except Exception:  # noqa: BLE001 - nothing readable, nothing to reset
        return False
    if key not in known:
        return False
    store.delete("venues", key)
    return True

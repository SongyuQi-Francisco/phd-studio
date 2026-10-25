"""Conference catalog: CORE (ICORE2026) + CCF ranks, deadlines, acceptance rates.

Sources, refreshed weekly into the data dir (nothing is shipped in the repo):
- ccfddl/ccf-deadlines (MIT): CCF/CORE ranks, per-edition deadlines with timezone, acceptance rates
- CORE portal export (ICORE2026): the full CORE conference ranking list
Curated per-venue details (tracks, page limits, format, review model) live in
data/venue_details.json with the official URLs they were taken from, the edition they describe
(`edition_year`) and when they were checked (`verified_at`). Details the user re-checked and confirmed in the
app are kept in a vault file (venue_overrides) and merged over the shipped ones; venue_fresh says when
details have gone stale.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import tarfile
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml

from . import venue_fresh, venue_overrides
from .config import DATA_DIR, STUDIO_DIR

VENUE_DIR = DATA_DIR / "venues"
CCFDDL_DIR = VENUE_DIR / "ccfddl"
CORE_CSV = VENUE_DIR / "icore.csv"
STAMP = VENUE_DIR / "fetched_at"
DETAILS_FILE = STUDIO_DIR / "data" / "venue_details.json"
CCFDDL_URL = "https://codeload.github.com/ccfddl/ccf-deadlines/tar.gz/refs/heads/main"
CORE_SOURCE = "ICORE2026"
CORE_URL = ("https://portal.core.edu.au/conf-ranks/?search=&by=all&source=" + CORE_SOURCE
            + "&sort=arank&page=1&do=Export")
MAX_AGE = timedelta(days=7)
CCF_FIELDS = {
    "DS": "体系结构/并行/存储", "NW": "计算机网络", "SC": "网络与信息安全", "SE": "软件工程/系统",
    "DB": "数据库/数据挖掘/信息检索", "CT": "计算理论", "CG": "图形学与多媒体", "AI": "人工智能",
    "HI": "人机交互与普适计算", "MX": "交叉/综合/新兴",
}
_refresh_lock = threading.Lock()
log = logging.getLogger(__name__)


def _key(acronym: str) -> str:
    return re.sub(r"[^a-z0-9]", "", acronym.lower())


def _match(k: str, cat: dict[str, Any]) -> str:
    """Map a ccfddl title onto the CORE acronym when they differ only by a prefix (SIGKDD -> KDD)."""
    if k in cat:
        return k
    for pre in ("ieeeacm", "ieee", "acm", "sigops", "sig", "usenix"):
        if k.startswith(pre) and k[len(pre):] in cat:
            return k[len(pre):]
    return k


# ---------- fetching ----------


def fetched_at() -> str | None:
    return STAMP.read_text().strip() if STAMP.exists() else None


def refresh(force: bool = False) -> dict[str, Any]:
    """Download both sources if missing or older than a week."""
    with _refresh_lock:
        if not force and STAMP.exists() and time.time() - STAMP.stat().st_mtime < MAX_AGE.total_seconds():
            return {"ok": True, "fetched_at": fetched_at(), "skipped": True}
        VENUE_DIR.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": "Mozilla/5.0 PhD-Studio"}
        r = httpx.get(CCFDDL_URL, headers=headers, follow_redirects=True, timeout=60)
        r.raise_for_status()
        tmp = VENUE_DIR / "ccfddl.new"
        if tmp.exists():
            _rmtree(tmp)
        with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
            for m in tar.getmembers():
                parts = m.name.split("/", 1)
                if len(parts) < 2 or not parts[1].startswith(("conference/", "accept_rates/")):
                    continue
                if not m.isfile() or not m.name.endswith((".yml", ".yaml")) or ".." in m.name:
                    continue
                dest = tmp / parts[1]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(tar.extractfile(m).read())
        c = httpx.get(CORE_URL, headers=headers, follow_redirects=True, timeout=60)
        c.raise_for_status()
        if CCFDDL_DIR.exists():
            _rmtree(CCFDDL_DIR)
        tmp.rename(CCFDDL_DIR)
        CORE_CSV.write_text(c.text, encoding="utf-8")
        STAMP.write_text(datetime.now().isoformat(timespec="seconds"))
        _catalog.cache_clear()
        return {"ok": True, "fetched_at": fetched_at()}


def refresh_in_background() -> None:
    def run() -> None:
        try:
            refresh()
        except Exception:  # noqa: BLE001 - offline is fine; the cached copy keeps working
            pass
    threading.Thread(target=run, daemon=True).start()


def _rmtree(p: Path) -> None:
    for child in sorted(p.rglob("*"), reverse=True):
        child.unlink() if child.is_file() else child.rmdir()
    p.rmdir()


# ---------- parsing ----------


def _tz(name: str | None) -> timezone:
    if not name or name.upper() == "AOE":
        return timezone(timedelta(hours=-12))
    m = re.match(r"(?:UTC|GMT)\s*([+-]\d{1,2})(?::?(\d{2}))?$", name.strip(), re.I)
    if m:
        h = int(m.group(1))
        mins = int(m.group(2) or 0) * (1 if h >= 0 else -1)
        return timezone(timedelta(hours=h, minutes=mins))
    if name.upper() in ("UTC", "GMT"):
        return timezone.utc
    return timezone(timedelta(hours=-12))


def _when(value: Any, tz: timezone) -> str | None:
    """'2027-01-14 23:59:59' in the conference's timezone -> ISO UTC; None for TBD."""
    if not value or not isinstance(value, str) or value.strip().upper().startswith("TBD"):
        return None
    try:
        dt = datetime.strptime(value.strip()[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(value.strip()[:10], "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except ValueError:
            return None
    return dt.replace(tzinfo=tz).astimezone(timezone.utc).isoformat()


def _accept_rates() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for f in (CCFDDL_DIR / "accept_rates").rglob("*.yml") if CCFDDL_DIR.exists() else []:
        try:
            for item in yaml.safe_load(f.read_text(encoding="utf-8")) or []:
                rates = [{"year": r.get("year"), "rate": r.get("rate"), "submitted": r.get("submitted"),
                          "accepted": r.get("accepted"), "source": r.get("source")}
                         for r in item.get("accept_rates", []) if r.get("rate") is not None]
                out[_key(str(item.get("title", "")))] = rates
        except yaml.YAMLError:
            continue
    return out


def _details() -> dict[str, Any]:
    """The shipped details, the vault overrides merged over them, and how each override was used.

    `details` has the effective details by venue key, `overrides` the override records that were applied,
    `bundled` the shipped details alone, and `outdated` the overrides left out because the shipped details
    moved past what they were confirmed against.
    """
    bundled: dict[str, dict[str, Any]] = {}
    if DETAILS_FILE.exists():
        data = json.loads(DETAILS_FILE.read_text(encoding="utf-8"))
        for v in data.get("venues", []):
            bundled[_key(v["key"])] = {"verified_at": data.get("verified_at"), **v}
    details, applied, outdated = dict(bundled), {}, {}
    for k, rec in venue_overrides.load().items():
        if venue_overrides.outdated(bundled.get(k), rec):
            outdated[k] = rec
            continue
        details[k] = venue_overrides.merge_detail(bundled.get(k) or {"key": rec.get("key") or k.upper()}, rec["details"])
        applied[k] = rec
    return {"details": details, "overrides": applied, "bundled": bundled, "outdated": outdated}


@lru_cache(maxsize=1)
def _catalog(_stamp: Any) -> dict[str, dict[str, Any]]:
    cat: dict[str, dict[str, Any]] = {}
    # CORE: the authoritative list for CORE ranks.
    if CORE_CSV.exists():
        for row in csv.reader(io.StringIO(CORE_CSV.read_text(encoding="utf-8"))):
            if len(row) < 5 or not row[2].strip():
                continue
            k = _key(row[2])
            if k in cat:
                continue
            cat[k] = {"key": k, "acronym": row[2].strip(), "title": row[1].strip(), "core": row[4].strip(),
                      "core_id": row[0], "ccf": None, "field": None, "editions": [], "dblp": None}
    # ccfddl: CCF rank + deadlines; adds CCF-only venues too.
    for f in sorted((CCFDDL_DIR / "conference").rglob("*.yml")) if CCFDDL_DIR.exists() else []:
        try:
            items = yaml.safe_load(f.read_text(encoding="utf-8")) or []
        except yaml.YAMLError:
            continue
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or "title" not in item:
                continue
            k = _match(_key(str(item["title"])), cat)
            rank = item.get("rank") or {}
            e = cat.setdefault(k, {"key": k, "acronym": str(item["title"]), "title": item.get("description") or "",
                                   "core": None, "core_id": None, "ccf": None, "field": None,
                                   "editions": [], "dblp": None})
            ccf = str(rank.get("ccf") or "N")
            e["ccf"] = ccf if ccf in ("A", "B", "C") else None
            if not e["core"] and str(rank.get("core") or "N") not in ("N", ""):
                e["core"] = str(rank["core"])
            e["field"] = CCF_FIELDS.get(item.get("sub"), item.get("sub"))
            e["dblp"] = item.get("dblp")
            if not e["title"]:
                e["title"] = item.get("description") or ""
            for conf in item.get("confs") or []:
                tz = _tz(conf.get("timezone"))
                timeline = []
                for t in conf.get("timeline") or []:
                    timeline.append({"abstract": _when(t.get("abstract_deadline"), tz),
                                     "deadline": _when(t.get("deadline"), tz),
                                     "comment": t.get("comment")})
                e["editions"].append({"year": conf.get("year"), "link": conf.get("link"),
                                      "date": conf.get("date"), "place": conf.get("place"),
                                      "timezone": conf.get("timezone") or "AoE", "timeline": timeline})
    rates = {_match(k, cat): v for k, v in _accept_rates().items()}  # accept_rates files use the ccfddl title (SIGKDD)
    loaded = _details()
    details, overrides = loaded["details"], loaded["overrides"]
    now = datetime.now(timezone.utc)
    for k, e in cat.items():
        e["editions"].sort(key=lambda x: x.get("year") or 0)
        e["ccfddl_editions"] = list(e["editions"])  # before the CFP-derived edition below is added
        e["accept_rates"] = rates.get(k, [])
        e["detail"] = details.get(k)
        e["override"] = overrides.get(k)
        e["override_outdated"] = loaded["outdated"].get(k)
        e["bundled"] = loaded["bundled"].get(k)
        official = _official_edition(e["detail"])
        if official and not any(datetime.fromisoformat(t[kind]) >= now for ed in e["editions"]
                                for t in ed["timeline"] for kind in ("abstract", "deadline") if t.get(kind)):
            e["editions"].append(official)
    return cat


_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?\s*\(?\s*(AoE|UTC\s*[+-]\s*\d{1,2}|GMT\s*[+-]\s*\d{1,2}|UTC|PT|PST|PDT)?", re.I)
_NAMED_TZ = {"PT": "UTC-8", "PST": "UTC-8", "PDT": "UTC-7"}
_NON_PAPER = re.compile(r"workshop|tutorial|industry|doctoral|consortium|demo|proposal|camera|notification|"
                        r"rebuttal|registration|mentor|student|volunteer|panel|challenge|presentation|talk", re.I)


def _parse_official(text: str | None) -> str | None:
    """'2027-01-22 23:59 AoE' (as written on a CFP page) -> ISO UTC."""
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    tz = _NAMED_TZ.get((m.group(3) or "").upper(), (m.group(3) or "AoE").replace(" ", ""))
    return _when(f"{m.group(1)} {(m.group(2) or '23:59')}:59", _tz(tz))


def _official_edition(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """Deadlines from the verified CFP details, used when ccfddl has none upcoming."""
    if not d or d.get("status") != "confirmed":
        return None
    timeline = []
    for row in d.get("deadlines") or []:
        if _NON_PAPER.search(row.get("track") or ""):
            continue
        a, p = _parse_official(row.get("abstract")), _parse_official(row.get("paper"))
        if a or p:
            timeline.append({"abstract": a, "deadline": p, "comment": f"{row.get('track') or '主会'}（官网）"})
    if not timeline:
        return None
    return {"year": venue_fresh.edition_year(d), "link": d.get("cfp_url") or d.get("homepage"),
            "date": d.get("dates"), "place": d.get("location"), "timezone": "官网", "timeline": timeline}


def catalog() -> dict[str, dict[str, Any]]:
    stamp = STAMP.stat().st_mtime if STAMP.exists() else 0.0
    if not stamp:
        try:
            refresh()
            stamp = STAMP.stat().st_mtime
        except Exception:  # noqa: BLE001
            pass
    return _catalog((stamp, venue_overrides.stamp()))  # confirming a re-check rebuilds the catalog


def next_deadline(e: dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    """The next upcoming deadline (abstract or paper) across editions; else the latest past one."""
    now = now or datetime.now(timezone.utc)
    upcoming, latest = [], None
    for ed in e["editions"]:
        for t in ed["timeline"]:
            for kind in ("abstract", "deadline"):
                if not t.get(kind):
                    continue
                dt = datetime.fromisoformat(t[kind])
                row = {"year": ed["year"], "kind": kind, "at": t[kind], "comment": t.get("comment"),
                       "link": ed.get("link"), "date": ed.get("date"), "place": ed.get("place"),
                       "timezone": ed.get("timezone")}
                if dt >= now:
                    upcoming.append((dt, row))
                elif latest is None or dt > datetime.fromisoformat(latest["at"]):
                    latest = row
    if upcoming:
        upcoming.sort(key=lambda x: x[0])
        nxt = dict(upcoming[0][1])
        paper = next((r for _, r in upcoming if r["year"] == nxt["year"] and r["kind"] == "deadline"), None)
        nxt["paper_at"] = paper["at"] if paper else None
        nxt["days"] = (upcoming[0][0] - now).days
        return nxt
    if latest:
        return {**latest, "past": True}
    return None


def freshness(e: dict[str, Any]) -> dict[str, Any] | None:
    """venue_fresh.check, but a bad record can never take the venue library down with it."""
    try:
        return venue_fresh.check(e)
    except Exception:  # noqa: BLE001
        log.warning("freshness check failed for %s", e.get("key"), exc_info=True)
        return None


def summary(e: dict[str, Any]) -> dict[str, Any]:
    return {"key": e["key"], "acronym": e["acronym"], "title": e["title"], "core": e["core"],
            "ccf": e["ccf"], "field": e["field"], "next": next_deadline(e),
            "has_detail": bool(e.get("detail")), "freshness": freshness(e),
            "accept_rate": (e["accept_rates"][-1] if e["accept_rates"] else None)}


def search(q: str = "", ccf: str = "", core: str = "", limit: int = 200) -> list[dict[str, Any]]:
    q = q.strip().lower()
    order = {"A*": 0, "A": 1, "B": 2, "C": 3}
    rows = []
    for e in catalog().values():
        if q and q not in e["acronym"].lower() and q not in (e["title"] or "").lower():
            continue
        if ccf and e["ccf"] != ccf:
            continue
        if core and e["core"] != core:
            continue
        rows.append(e)
    def rank(e: dict[str, Any]) -> tuple:
        acr = e["acronym"].lower()
        hit = 0 if q and acr == q else 1 if q and acr.startswith(q) else 2 if q and q in acr else 3
        return (hit, min(order.get(e["core"] or "", 9), order.get(e["ccf"] or "", 9)), e["acronym"])
    rows.sort(key=rank)
    return [summary(e) for e in rows[:limit]]


def get(key: str) -> dict[str, Any] | None:
    e = catalog().get(_key(key))
    if not e:
        return None
    return {**summary(e), "editions": e["editions"][-3:], "accept_rates": e["accept_rates"],
            "detail": e.get("detail"), "overridden": bool(e.get("override")), "core_source": CORE_SOURCE,
            "core_url": f"https://portal.core.edu.au/conf-ranks/{e['core_id']}/" if e.get("core_id") else None,
            "dblp_url": f"https://dblp.org/db/conf/{e['dblp']}/" if e.get("dblp") else None}


def stats() -> dict[str, Any]:
    cat = catalog()
    return {"total": len(cat), "core": sum(1 for e in cat.values() if e["core"] in ("A*", "A", "B", "C")),
            "ccf": sum(1 for e in cat.values() if e["ccf"]), "fetched_at": fetched_at(),
            "core_source": CORE_SOURCE}

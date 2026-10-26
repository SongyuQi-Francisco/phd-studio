"""Reading list: one YAML file in the vault, shared by the web UI and the `studio` CLI.

States: inbox -> queued (tier T1/T2/T3) -> reading -> done, with side exits
snoozed (until a date, then back to inbox) and dismissed (kept, so it is never
pushed again). Urgency (tier) is set before reading; depth/verdict/takeaway after.
Every save also regenerates a read-only summary note next to the YAML file.

The vault may be synced between computers (Syncthing). Each item carries an
updated_at stamp, so when both machines edit the list before syncing, the
resulting sync-conflict copy is merged back automatically (newest edit per item
wins) and moved out of the vault.
"""
from __future__ import annotations

import fcntl
import os
import re
import secrets
import shutil
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import httpx
import yaml

from . import i18n, library
from .config import DATA_DIR, PAPERS_DIR, PROFILE, READING_LIST, READING_QUEUE

STATUSES = ("inbox", "queued", "reading", "done", "snoozed", "dismissed")
TIERS = ("T1", "T2", "T3")
SOURCES = ("tracker", "claude", "advisor", "self")
DEPTHS = ("skim", "deep")
VERDICTS = ("key", "useful", "irrelevant")
CAPS = {"T1": 5, "queued": 12}
STALE_DAYS = 21
FIELDS = ("id", "paper", "arxiv", "ext", "title", "url", "status", "tier", "lines", "source", "pushed_by",
          "pushed_at", "reason", "due", "snooze_until", "opened_at", "read_at", "depth", "verdict",
          "takeaway", "updated_at")

HEADER = """# Reading list, managed by PhD Studio. Edit through the app or the CLI:
#   studio push --paper "Note Name" --tier T1 --line "Project A" --reason "why" --by "who"
#   studio push --arxiv 2606.17328 --reason "why" --by "who"
# status: inbox | queued | reading | done | snoozed | dismissed   tier: T1 (this week) | T2 | T3
"""
VIEW_BEGIN = "<!-- BEGIN STUDIO_READING_LIST -->"  # legacy block in the Reading Queue note
VIEW_END = "<!-- END STUDIO_READING_LIST -->"
READING_VIEW = READING_LIST.with_name("Reading List.md")
_LOCK = DATA_DIR / "reading.lock"
_TMP = DATA_DIR / "reading.yaml.tmp"  # outside the synced vault; os.replace moves it in atomically
CONFLICTS_DIR = DATA_DIR / "sync-conflicts"
_ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arXiv[: ]\s*)(\d{4}\.\d{4,5})", re.I)


def today() -> str:
    return date.today().isoformat()


@contextmanager
def _locked() -> Iterator[None]:
    with _LOCK.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _norm(item: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k in FIELDS:
        v = item.get(k)
        if isinstance(v, date):
            v = v.isoformat()
        if v in (None, "", []):
            continue
        out[k] = v
    out.setdefault("lines", [])
    return out


def _load_raw() -> list[dict[str, Any]]:
    if not READING_LIST.exists():
        return []
    data = yaml.safe_load(READING_LIST.read_text(encoding="utf-8")) or {}
    return [_norm(i) for i in data.get("items", [])]


def _save_raw(items: list[dict[str, Any]]) -> None:
    clean = []
    for i in items:
        d = _norm(i)
        if not d["lines"]:
            d.pop("lines")
        clean.append(d)
    body = yaml.safe_dump({"items": clean}, allow_unicode=True, sort_keys=False, width=1000)
    _TMP.write_text(HEADER + body, encoding="utf-8")
    os.replace(_TMP, READING_LIST)
    _write_view(clean)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stamp_changed(before: dict[str, dict[str, Any]], items: list[dict[str, Any]]) -> bool:
    """Set updated_at on items that are new or changed since `before`; True if any."""
    changed = len(before) != len(items)
    for i in items:
        old = before.get(i["id"])
        cur = {k: v for k, v in _norm(i).items() if k != "updated_at"}
        if old is None or {k: v for k, v in old.items() if k != "updated_at"} != cur:
            i["updated_at"] = _now()
            changed = True
    return changed


def _conflict_copies(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.stem}.sync-conflict-*{path.suffix}"))


def _park(conflict: Path) -> None:
    CONFLICTS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(conflict), CONFLICTS_DIR / conflict.name)


def _merge_conflicts(items: list[dict[str, Any]]) -> bool:
    """Fold Syncthing conflict copies of the list back in: newest edit per item wins."""
    copies = _conflict_copies(READING_LIST)
    for copy in copies:
        try:
            other = [_norm(i) for i in (yaml.safe_load(copy.read_text(encoding="utf-8")) or {}).get("items", [])]
        except yaml.YAMLError:
            other = []
        for theirs in other:
            mine = find(items, theirs["id"]) or find(items, key_of(theirs))
            if mine is None:
                items.append(theirs)
            elif theirs.get("updated_at", "") > mine.get("updated_at", ""):
                lines = sorted(set(mine.get("lines", [])) | set(theirs.get("lines", [])))
                mine.clear()
                mine.update(theirs, lines=lines)
        _park(copy)
    return bool(copies)


def _wake_snoozed(items: list[dict[str, Any]]) -> bool:
    changed = False
    for i in items:
        if i["status"] == "snoozed" and i.get("snooze_until", "9999") <= today():
            i["status"] = "inbox"
            i.pop("snooze_until", None)
            changed = True
    return changed


def _link_notes(items: list[dict[str, Any]]) -> bool:
    """Attach items without a note (arXiv id or DOI/OpenReview/ACL key) to a paper note once one exists."""
    loose = [i for i in items if not i.get("paper") and (i.get("arxiv") or i.get("ext"))]
    if not loose:
        return False
    by_arxiv, by_ext = library.arxiv_index(), library.ext_index()
    changed = False
    for i in loose:
        if stem := (by_arxiv.get(i.get("arxiv")) or by_ext.get(i.get("ext"))):
            i["paper"] = stem
            changed = True
    return changed


def load() -> list[dict[str, Any]]:
    with _locked():
        if not READING_LIST.exists():
            items = import_reading_queue()
            _save_raw(items)
            return items
        items = _load_raw()
        before = {i["id"]: dict(i) for i in items}
        merged = _merge_conflicts(items)
        _wake_snoozed(items)
        _link_notes(items)
        stale_view = (not READING_VIEW.exists() or bool(_conflict_copies(READING_VIEW))
                      or (READING_QUEUE.exists() and VIEW_BEGIN in READING_QUEUE.read_text(encoding="utf-8")))
        if _stamp_changed(before, items) or merged or stale_view:
            _save_raw(items)
        return items


@contextmanager
def editing() -> Iterator[list[dict[str, Any]]]:
    with _locked():
        items = _load_raw()
        before = {i["id"]: dict(i) for i in items}
        _merge_conflicts(items)
        yield items
        _stamp_changed(before, items)
        _save_raw(items)


def key_of(item: dict[str, Any]) -> str:
    """The reader's paper key: the note stem, or before a note exists arxiv:<id>, or the ext key
    (doi:<doi>, openreview:<id>, acl:<id>) of a paper that came in by DOI or an OpenReview/ACL link."""
    return item.get("paper") or (f"arxiv:{item['arxiv']}" if item.get("arxiv") else item.get("ext") or item["id"])


def find(items: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    for i in items:
        if i["id"] == key or i.get("paper") == key or (i.get("arxiv") and f"arxiv:{i['arxiv']}" == key) \
                or (i.get("ext") and i["ext"] == key):
            return i
    return None


def norm_title(title: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def _same_title(items: list[dict[str, Any]], title: str | None) -> dict[str, Any] | None:
    """An item that already carries this exact title: the same paper captured through another door
    (an arXiv link, then its OpenReview or DOI link, or the other way round)."""
    want = norm_title(title)
    if len(want) < 12:
        return None
    for i in items:
        if norm_title(i.get("title")) == want:
            return i
    return None


# ---------- mutations ----------


class ReadingError(ValueError):
    pass


def push(*, paper: str | None = None, arxiv: str | None = None, ext: str | None = None, title: str | None = None,
         url: str | None = None, reason: str, source: str = "self", pushed_by: str | None = None, tier: str | None = None,
         lines: list[str] | None = None, due: str | None = None, status: str | None = None,
         force: bool = False) -> tuple[dict[str, Any], str]:
    """Add a paper, or update the existing entry. Returns (item, what happened)."""
    if not reason.strip():
        raise ReadingError("reason is required: one line on why this paper should be read")
    if source not in SOURCES:
        raise ReadingError(f"source must be one of {SOURCES}")
    if tier and tier not in TIERS:
        raise ReadingError(f"tier must be one of {TIERS}")
    if arxiv:
        arxiv = re.sub(r"v\d+$", "", arxiv.strip().removeprefix("arXiv:").removeprefix("arxiv:"))
    if ext and not library.ext_key(ext):
        raise ReadingError("ext must look like doi:<doi>, openreview:<id> or acl:<id>")
    if paper:
        try:
            library.note_path(paper)
        except FileNotFoundError:
            raise ReadingError(f"no paper note named '{paper}'; use --arxiv for papers without a note")
    elif arxiv or ext:  # an arXiv paper may also be known by its DOI: a note can carry either
        paper = (library.arxiv_index().get(arxiv) if arxiv else None) or (library.ext_index().get(ext) if ext else None)
    else:
        raise ReadingError("give --paper (note name), --arxiv or an ext key (doi:/openreview:/acl:)")
    if not title and arxiv and not paper and not find(_load_raw(), f"arxiv:{arxiv}"):
        # The title is what a second door (an OpenReview or DOI item of the same paper) is recognised by,
        # so it is known before the duplicate check; asked here, not under the list's lock.
        title = fetch_arxiv_title(arxiv)

    with editing() as items:
        existing = find(items, paper) if paper else None
        existing = existing or (find(items, f"arxiv:{arxiv}") if arxiv else None) or (find(items, ext) if ext else None)
        existing = existing or (_same_title(items, title) if not paper else None)
        if existing:
            if existing["status"] == "dismissed" and not force:
                return existing, "dismissed earlier; not re-added (use --force to bring it back)"
            if existing["status"] == "done" and not force:
                return existing, f"already read on {existing.get('read_at', '?')}"
            if ext and not existing.get("ext"):
                existing["ext"] = ext
            existing["lines"] = sorted(set(existing.get("lines", [])) | set(lines or []))
            if tier and (not existing.get("tier") or tier < existing["tier"]):
                existing["tier"] = tier
                if existing["status"] == "inbox":
                    existing["status"] = "queued"
            if due:
                existing["due"] = due
            if existing["status"] in ("dismissed", "done", "snoozed"):
                existing["status"] = "inbox"
            existing["reason"] = f"{existing.get('reason', '')} | {reason}".strip(" |")
            return existing, f"already listed ({existing['status']}); merged"
        item = {
            "id": "r" + secrets.token_hex(3),
            "paper": paper,
            "arxiv": arxiv,
            "ext": ext,
            "url": url,
            "title": title,
            "status": status or ("queued" if tier and source == "self" else "inbox"),
            "tier": tier,
            "lines": sorted(set(lines or [])),
            "source": source,
            "pushed_by": pushed_by,
            "pushed_at": today(),
            "reason": reason.strip(),
            "due": due,
        }
        items.append(item)
        return _norm(item), "added"


def update(item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    with editing() as items:
        item = find(items, item_id)
        if item is None:
            raise ReadingError(f"no reading-list item {item_id}")
        if "status" in patch and patch["status"] not in STATUSES:
            raise ReadingError("bad status")
        if patch.get("tier") and patch["tier"] not in TIERS:
            raise ReadingError("bad tier")
        if patch.get("depth") and patch["depth"] not in DEPTHS:
            raise ReadingError("bad depth")
        if patch.get("verdict") and patch["verdict"] not in VERDICTS:
            raise ReadingError("bad verdict")
        for k in ("status", "tier", "lines", "due", "snooze_until", "depth", "verdict", "takeaway", "reason"):
            if k in patch:
                item[k] = patch[k]
        if patch.get("tier") and item["status"] == "inbox":
            item["status"] = "queued"
        if item["status"] == "done":
            item.setdefault("read_at", today())
        if item["status"] != "snoozed":
            item.pop("snooze_until", None)
        return _norm(item)


def mark_opened(key: str) -> dict[str, Any] | None:
    """Opening a paper in the reader moves its entry to `reading`."""
    with editing() as items:
        item = find(items, key)
        if item is None:
            return None
        item["opened_at"] = today()
        if item["status"] in ("inbox", "queued"):
            item["status"] = "reading"
        return _norm(item)


# ---------- views ----------


def summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    t = date.today()
    week_ago = (t - timedelta(days=7)).isoformat()
    for i in items:
        i["age_days"] = (t - date.fromisoformat(i["pushed_at"])).days if i.get("pushed_at") else 0
        i["stale"] = i["status"] == "inbox" and i["age_days"] > STALE_DAYS
        i["key"] = key_of(i)
        if not i.get("title"):
            i["title"] = i.get("paper") or i.get("arxiv") or i.get("ext")
    active_inbox = [i for i in items if i["status"] == "inbox" and not i["stale"]]
    queued = [i for i in items if i["status"] == "queued"]
    return {
        "inbox": len(active_inbox),
        "T1": sum(1 for i in queued if i.get("tier") == "T1"),
        "queued": len(queued),
        "reading": sum(1 for i in items if i["status"] == "reading"),
        "done_week": sum(1 for i in items if i["status"] == "done" and i.get("read_at", "") >= week_ago),
        "caps": CAPS,
        "lines": sorted({ln for i in items for ln in i.get("lines", [])}),
    }


def _link(i: dict[str, Any]) -> str:
    if i.get("paper"):
        return f"[[{i['paper']}]]"
    if i.get("arxiv"):
        return f"[{i.get('title') or i['arxiv']}](https://arxiv.org/abs/{i['arxiv']})"
    label = i.get("title") or i.get("ext") or i["id"]
    return f"[{label}]({i['url']})" if i.get("url") else label


def _write_view(items: list[dict[str, Any]]) -> None:
    """Regenerate the read-only summary note (a separate file, so hand edits never collide)."""
    def rows(sel: list[dict[str, Any]]) -> list[str]:
        return [f"- {_link(i)}" + (f" · {', '.join(i['lines'])}" if i.get("lines") else "")
                + (f" — {i['reason']}" if i.get("reason") else "") for i in sel]
    order = {"T1": 0, "T2": 1, "T3": 2}
    queued = sorted((i for i in items if i["status"] == "queued"),
                    key=lambda i: (order.get(i.get("tier"), 3), i.get("due") or "9", i.get("pushed_at", "")))
    reading = [i for i in items if i["status"] == "reading"]
    cutoff = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    inbox = [i for i in items if i["status"] == "inbox" and i.get("pushed_at", "") >= cutoff]
    parts = ["---", "type: reading-list", "generated: true", "---", "", "# " + i18n.tv("reading.view.title"), "",
             "> " + i18n.tv("reading.view.generated", file=READING_LIST.name), ""]
    if reading:
        parts += ["## " + i18n.tv("reading.view.reading"), *rows(reading), ""]
    for tier in TIERS:
        sel = [i for i in queued if i.get("tier") == tier]
        if sel:
            parts += [f"## {tier}", *rows(sel), ""]
    if inbox:
        parts += ["## " + i18n.tv("reading.view.inbox", n=len(inbox)), *rows(inbox), ""]
    text = "\n".join(parts)
    for copy in _conflict_copies(READING_VIEW):
        _park(copy)  # generated file: just regenerate
    if not READING_VIEW.exists() or READING_VIEW.read_text(encoding="utf-8") != text:
        READING_VIEW.write_text(text, encoding="utf-8")
    # Earlier versions wrote a block into the hand-written queue note; take it out once.
    if READING_QUEUE.exists():
        q = READING_QUEUE.read_text(encoding="utf-8")
        if VIEW_BEGIN in q and VIEW_END in q:
            a, rest = q.split(VIEW_BEGIN, 1)
            _, b = rest.split(VIEW_END, 1)
            READING_QUEUE.write_text(a.rstrip("\n") + "\n\n" + b.lstrip("\n"), encoding="utf-8")


def fetch_arxiv_title(arxiv: str | None) -> str | None:
    if not arxiv:
        return None
    try:
        r = httpx.get("https://export.arxiv.org/api/query", params={"id_list": arxiv}, timeout=10)
        m = re.search(r"<entry>.*?<title>(.*?)</title>", r.text, re.S)
        return " ".join(m.group(1).split()) if m else None
    except httpx.HTTPError:
        return None


# ---------- one-time import of the hand-written Reading Queue ----------

_DATE_RE = re.compile(r"(20\d\d-\d\d-\d\d)")


def import_reading_queue() -> list[dict[str, Any]]:
    """Turn the existing Reading Queue note into inbox items, keeping each section's date.

    Sections older than STALE_DAYS land in the collapsed "untriaged" archive, so
    the inbox starts with only recent pushes. "Full text read" lines become done.
    """
    if not READING_QUEUE.exists():
        return []
    text = READING_QUEUE.read_text(encoding="utf-8")
    created = (_DATE_RE.search(text) or [None, today()])[1]
    by_arxiv = library.arxiv_index()
    items: dict[str, dict[str, Any]] = {}
    section, sec_date, lines_tag, source, by = "", created, [], "claude", ""
    for raw in text.splitlines():
        if raw.startswith("## "):
            section = raw[3:].strip()
            m = _DATE_RE.search(section)
            sec_date = m.group(1) if m else created
            low = section.lower()
            lines_tag = [ln for ln in PROFILE.get("lines", []) if ln.lower() in low][:1]
            source = "tracker" if "tracker" in low else "claude"
            by = f"Reading Queue · {section}"
            continue
        line = raw.strip()
        if not section or not line or line.startswith("<!--"):
            continue
        done = line.lower().startswith("- full text read")
        if len(_ARXIV_RE.findall(line)) > 1:
            pieces = re.split(r" · |;\s+", line)
        else:
            pieces = [line]
        label = ""
        if m := re.match(r"^(?:(?:\d+\.|-)\s*)?([^:\[]{3,80}):\s+(?!https?://)", line):
            label = m.group(1).strip()
        for piece in pieces:
            stems = [s for s in re.findall(r"\[\[([^\]|#]+)", piece) if (PAPERS_DIR / f"{s}.md").exists()]
            ids = _ARXIV_RE.findall(piece)
            text = re.sub(r"^(\d+\.|-)\s*", "", piece)
            text = re.sub(r"https?://\S+|arXiv[: ]\s*\d{4}\.\d{4,5}", "", text, flags=re.I)
            if label and text.startswith(label):
                text = text[len(label):]
            text = text.strip(" :—-·,.")
            targets: list[tuple[str | None, str | None, str | None, str]] = []
            if len(stems) > 1 or (stems and not text.replace("[[" + stems[0] + "]]", "").strip(" :—-·,.")):
                targets = [(s, None, None, label or section) for s in stems]
            elif stems:
                reason = re.sub(r"^\[\[[^\]]+\]\]\s*[:—-]?\s*", "", text).replace("[[", "").replace("]]", "")
                targets = [(stems[0], None, None, " · ".join(x for x in (label, reason) if x) or section)]
            else:
                for aid in ids:
                    parts = re.split(r"\s[—–-]\s|:\s", text, maxsplit=1)
                    title = parts[0].strip(' "')[:120] or None
                    reason = " · ".join(x for x in (label, parts[1].strip(' ".') if len(parts) > 1 else "") if x)
                    targets.append((by_arxiv.get(aid), aid, title, reason or section))
            for stem, aid, title, reason in targets:
                k = stem or f"arxiv:{aid}"
                prev = items.get(k)
                if prev and prev["pushed_at"] >= sec_date:
                    prev["lines"] = sorted(set(prev["lines"]) | set(lines_tag))
                    continue
                items[k] = {
                    "id": "r" + secrets.token_hex(3), "paper": stem, "arxiv": aid, "title": title,
                    "status": "done" if done else "inbox", "lines": sorted(set(lines_tag) | set(prev["lines"] if prev else [])),
                    "source": source, "pushed_by": by, "pushed_at": sec_date,
                    "reason": reason[:300], "read_at": sec_date if done else None,
                    "depth": "deep" if done else None,
                }
    return [_norm(i) for i in items.values()]

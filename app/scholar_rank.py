"""Rules for recomputing the top-scholar list. Pure functions (no config, no I/O) so they can be
unit-tested; app/scholar_refresh.py feeds them Scholar rankings and OpenAlex data.

- Candidates: Google Scholar label rankings (several label spellings, merged by user id) plus the
  current list, ordered by Scholar total citations.
- A candidate is kept when it has at least `min_papers` field papers in the recent window
  (title/abstract topics must hit every topic group, e.g. RecSys AND (LLM or Agent); workshop
  and tutorial items do not count), is not citation noise (most citations from non-field work or
  from mega-collaborations), is still publishing, and is not on the exclude list.
- The list is the best-cited candidates that pass (by default one per lab), plus pinned
  scholars on top; pinned scholars never take a ranked slot and are never removed.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Callable, Iterable

NOT_PAPER = re.compile(r"workshop|tutorial|proceedings|preface|\bsummary of\b|panel|keynote|doctoral consortium", re.I)


class CheckUnavailable(RuntimeError):
    """The data needed to judge any candidate cannot be fetched (offline, out of quota): stop."""


class Unresolved(RuntimeError):
    """This one candidate cannot be identified (no or ambiguous OpenAlex author): skip them."""


# ---------- names and keys ----------

def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z ]+", " ", s.lower().replace("-", "").replace("‐", "")).split())


def same_person(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("scholar_id") and b.get("scholar_id"):
        return a["scholar_id"] == b["scholar_id"]
    if a.get("openalex") and b.get("openalex"):
        return a["openalex"] == b["openalex"]
    return norm_name(a.get("name", "")) == norm_name(b.get("name", ""))


def parse_count(num: str, unit: str = "") -> int:
    n = float(num.replace(",", ""))
    return int(round(n * {"k": 1_000, "m": 1_000_000}.get(unit.lower(), 1)))


# ---------- snapshots ----------

_SNAP_ROW = re.compile(r"^\s*(\d+)\.\s+(.+?)\s+·\s+([\w-]{12})\s+·\s+([\d.,]+)\s*([kKmM]?)(?:\s+·\s+(.*))?\s*$")
_SNAP_PI = re.compile(r"^\s*-\s+(.+?)\s+·\s+([\d.,]+)\s*([kKmM]?)\s+·\s+(.*)$")
_DATE = re.compile(r"(20\d\d-\d\d-\d\d)")


def parse_snapshot_md(text: str) -> dict[str, Any]:
    """A pasted ranking: '12. Name · scholarid12ch · 65k · Affiliation · note' rows, optionally a
    '- Name · 76k · Affiliation' list of extra people found by name search."""
    m = _DATE.search(text)
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        r = _SNAP_ROW.match(line)
        if r:
            rest = [x.strip() for x in (r.group(6) or "").split(" · ")]
            note = " · ".join(rest[1:]) if len(rest) > 1 else ""
            out.append({"name": r.group(2).strip(), "scholar_id": r.group(3), "citations": parse_count(r.group(4), r.group(5)),
                        "affiliation": "" if rest[0] in ("—", "-") else rest[0], "note": note, "via": "label",
                        "scholar_rank": int(r.group(1)), **flags_from_note(note)})
            continue
        p = _SNAP_PI.match(line)
        if p:
            note = p.group(4).strip()
            aff = note.split(" · ")[0]
            out.append({"name": p.group(1).strip(), "scholar_id": None, "citations": parse_count(p.group(2), p.group(3)),
                        "affiliation": aff, "note": "", "via": "name"})
    return {"date": m.group(1) if m else None, "entries": out}


def flags_from_note(note: str) -> dict[str, Any]:
    low = note.lower()
    return {"noise": "noise" in low, "deceased": "deceased" in low or "已故" in note}


def merge_entries(*lists: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per person (Scholar user id, else name): highest citation count wins, labels union."""
    merged: list[dict[str, Any]] = []
    for lst in lists:
        for e in lst:
            hit = next((m for m in merged if same_person(m, e)), None)
            if hit is None:
                merged.append(dict(e, labels=list(e.get("labels") or [])))
                continue
            for k, v in e.items():
                if k == "labels":
                    hit["labels"] = sorted(set(hit["labels"]) | set(v or []))
                elif k == "citations":
                    hit[k] = max(hit.get(k) or 0, v or 0)
                elif k in ("noise", "deceased"):
                    hit[k] = bool(hit.get(k)) or bool(v)
                elif not hit.get(k) and v:
                    hit[k] = v
    return sorted(merged, key=lambda e: -(e.get("citations") or 0))


# ---------- topic checks ----------

def compile_model(model: dict[str, Any]) -> dict[str, Any]:
    fl = re.IGNORECASE
    return {"gate": [re.compile(p, fl) for p in model.get("domain_gate", {}).get("patterns", [])],
            "topics": [(t["id"], [re.compile(p, fl) for p in t["patterns"]]) for t in model.get("topics", [])]}


def topics_of(text: str, compiled: dict[str, Any]) -> set[str]:
    return {tid for tid, pats in compiled["topics"] if any(p.search(text) for p in pats)}


def field_papers(works: Iterable[dict[str, Any]], compiled: dict[str, Any], groups: list[list[str]],
                 since: str | None = None) -> list[str]:
    """Titles of research papers whose topics hit every group (one entry per title)."""
    seen: set[str] = set()
    out: list[str] = []
    for w in works:
        title = w.get("title") or ""
        if not title or NOT_PAPER.search(title) or (since and (w.get("date") or "") < since):
            continue
        hits = topics_of(title + " " + (w.get("abstract") or ""), compiled)
        if not all(set(g) & hits for g in groups):
            continue
        k = re.sub(r"[^a-z0-9]", "", title.lower())[:80]
        if k not in seen:
            seen.add(k)
            out.append(title)
    return out


def noise_verdict(top_works: list[dict[str, Any]], compiled: dict[str, Any], *, min_field_share: float = 0.1,
                  mega_team: int = 30, mega_share: float = 0.5) -> str | None:
    """Why a Scholar citation count does not reflect the field, or None. `top_works` are the
    author's most-cited works: {title, abstract?, cites, team}, `team` = institutions (or
    authors) on the paper; physics-style collaborations run into the hundreds."""
    total = sum(w.get("cites") or 0 for w in top_works)
    if total <= 0:
        return None
    mega = sum(w.get("cites") or 0 for w in top_works if (w.get("team") or 0) >= mega_team)
    if mega / total >= mega_share:
        return f"被引主要来自超大合作组论文（{round(100 * mega / total)}%）"
    field = sum(w.get("cites") or 0 for w in top_works
                if any(p.search((w.get("title") or "") + " " + (w.get("abstract") or "")) for p in compiled["gate"]))
    if field / total < min_field_share:
        return f"被引几乎都来自领域外论文（领域内仅 {round(100 * field / total)}%）"
    return None


def inactive_verdict(last_work: str | None, today: date, years: int = 3) -> str | None:
    if not last_work:
        return "查不到发表记录"
    if (today - date.fromisoformat(last_work[:10])).days > years * 365:
        return f"{years} 年内没有新发表（最近 {last_work[:7]}）"
    return None


# ---------- ranking ----------

def rank(candidates: list[dict[str, Any]], check: Callable[[dict[str, Any]], dict[str, Any]], *, top_n: int = 10,
         alternates: int = 5, min_papers: int = 3, pinned: Iterable[dict[str, Any]] = (),
         excluded: Iterable[dict[str, Any]] = (), one_per_lab: bool = True) -> dict[str, Any]:
    """Walk candidates by citations; `check(c)` (called lazily, only as far as needed) returns
    {field_papers, noise, inactive, ...}.

    - The list is the `top_n` best-cited candidates that pass, plus every pinned person on top
      (pins never take a ranked slot and are never dropped).
    - Excluded people are never used. People are matched by Scholar id, else OpenAlex id, else name.
    - one_per_lab: a candidate whose `lab` already has someone on the list, among the alternates
      or pinned, is skipped ("同实验室已有 X"); a lab's first member who fails the filter does not
      block the next one. Candidates without a known lab are never grouped.
    """
    pinned, excluded = list(pinned), list(excluded)
    ordered = sorted(candidates, key=lambda c: -(c.get("citations") or 0))
    is_pin = [any(same_person(c, p) for p in pinned) for c in ordered]
    ranked: list[dict[str, Any]] = []
    pins: list[dict[str, Any]] = []
    alts: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    lab_of: dict[str, str] = {}  # lab -> who holds it
    if one_per_lab:
        for c, pin in zip(ordered, is_pin):
            if pin and c.get("lab"):
                lab_of.setdefault(c["lab"], c.get("name", ""))
    for i, c in enumerate(ordered):
        if is_pin[i]:
            pins.append(dict(c, pinned=True, verdict=_safe_check(check, c)))
            continue
        if len(ranked) >= top_n and len(alts) >= alternates:
            if not any(is_pin[i:]):
                break
            continue
        if any(same_person(c, x) for x in excluded):
            skipped.append(dict(c, reason="在排除名单里"))
            continue
        if c.get("deceased"):
            skipped.append(dict(c, reason="已故"))
            continue
        if c.get("noise"):
            skipped.append(dict(c, reason="快照标注：被引来自领域外的大合作"))
            continue
        if one_per_lab and c.get("lab") in lab_of:
            skipped.append(dict(c, reason=f"同实验室已有 {lab_of[c['lab']]}", same_lab=lab_of[c["lab"]]))
            continue
        try:
            v = check(c)
        except Unresolved as e:
            skipped.append(dict(c, reason=f"无法核实：{e}", unchecked=True))
            continue
        reason = v.get("noise") or v.get("inactive")
        if not reason and (v.get("field_papers") or 0) < min_papers:
            reason = f"近期领域论文 {v.get('field_papers') or 0} 篇（需 ≥{min_papers}）"
        if reason:
            skipped.append(dict(c, reason=reason, verdict=v))
            continue
        (ranked if len(ranked) < top_n else alts).append(dict(c, verdict=v))
        if one_per_lab and c.get("lab"):
            lab_of[c["lab"]] = c.get("name", "")
    ranked.sort(key=lambda c: -(c.get("citations") or 0))
    pins.sort(key=lambda c: -(c.get("citations") or 0))
    chosen = ranked + pins  # pinned people rank after the ranked ones
    for n, c in enumerate(chosen, 1):
        c["rank"] = n
    return {"list": chosen, "alternates": alts, "skipped": skipped}


def _safe_check(check: Callable[[dict[str, Any]], dict[str, Any]], c: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return check(c)
    except (CheckUnavailable, Unresolved):
        return None


def diff(current: list[dict[str, Any]], proposed: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Who enters, who leaves (pinned never leave) and how ranks move."""
    enter = [p for p in proposed if not any(same_person(p, c) for c in current)]
    leave = [c for c in current if not c.get("pinned") and not any(same_person(p, c) for p in proposed)]
    moves = []
    for p in proposed:
        c = next((c for c in current if same_person(p, c)), None)
        if c is not None:
            moves.append({"name": p.get("name"), "from": c.get("rank"), "to": p.get("rank")})
    return {"enter": enter, "leave": leave, "moves": moves}


# ---------- identity, retries, applying ----------

_STOP = {"university", "of", "the", "and", "institute", "school", "college", "department", "technology", "science",
         "research", "national", "professor", "lab", "laboratory", "center", "centre", "computer", "univ"}


def pick_author(results: list[dict[str, Any]], name: str, affiliation: str | None,
                field_topics: Iterable[str] = ()) -> tuple[dict[str, Any] | None, str]:
    """The OpenAlex author behind a Scholar name: (author, "high" | "low"), or (None, "none").
    Same surname required. High confidence needs the Scholar affiliation to show up in the
    author's institutions, or — without a usable affiliation — the field topic on exactly one
    namesake. A common name with no such evidence is "low": never trusted for the list."""
    want = {w for w in re.findall(r"[a-z]{4,}", (affiliation or "").lower()) if w not in _STOP}
    field_topics = set(field_topics)
    rows = []
    for a in results:
        if norm_name(a.get("display_name", "")).split()[-1:] != norm_name(name).split()[-1:]:
            continue
        insts = " ".join(i.get("display_name", "") for i in a.get("last_known_institutions") or [])
        insts += " " + " ".join((x.get("institution") or {}).get("display_name", "") for x in (a.get("affiliations") or [])[:15])
        overlap = len([w for w in want if w in insts.lower()]) / len(want) if want else 0.0
        topics = {(t.get("id") or "").rsplit("/", 1)[-1] for t in a.get("topics") or []}
        rows.append((a, overlap, bool(topics & field_topics)))
    if not rows:
        return None, "none"
    score = lambda r: (r[0].get("cited_by_count") or 0) * (1 + 3 * r[1]) * (2 if r[2] else 1)  # noqa: E731
    best = max(rows, key=score)
    if best[1] > 0:
        return best[0], "high"
    in_field = [r for r in rows if r[2]]
    if in_field:  # no usable affiliation: the field topic decides, if it singles one out
        return max(in_field, key=score)[0], "high" if len(in_field) == 1 else "low"
    return best[0], "low"


def backoff_seconds(failures: int, base: float = 6 * 3600, cap: float = 7 * 86400) -> float:
    """Wait before the next automatic attempt after `failures` failures in a row: 6 h, 12 h, … ≤ 7 days."""
    if failures <= 0:
        return 0.0
    return min(cap, base * 2 ** (failures - 1))


def plan_apply(current: list[dict[str, Any]], run: dict[str, Any], excluded: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Exactly the change the user saw, re-checked now: add the shown entrants (unless excluded
    since or already there), remove the shown leavers (unless pinned since), update ranks of the
    people in the shown ranking. Anyone else on the list (added by hand or synced in after the
    suggestion) is left alone. Final order: the suggestion's ranking, then untouched people by
    their old rank, then pinned people."""
    excluded = list(excluded)
    on_list = lambda p: next((c for c in current if same_person(p, c)), None)  # noqa: E731
    add, notes = [], []
    for p in run.get("enter") or []:
        if any(same_person(p, x) for x in excluded):
            notes.append(f"{p.get('name')}：已在排除名单里，没有加入")
        elif on_list(p):
            notes.append(f"{p.get('name')}：已经在名单里")
        else:
            add.append(p)
    remove = []
    for p in run.get("leave") or []:
        c = on_list(p)
        if c is None:
            continue
        if c.get("pinned"):
            notes.append(f"{c.get('name')}：已固定，没有移出")
        else:
            remove.append(c)
    removed = {id(c) for c in remove}
    stay = [c for c in current if id(c) not in removed]
    ranking = run.get("ranking") or []
    pos = lambda c: next((i for i, p in enumerate(ranking) if same_person(p, c)), None)  # noqa: E731
    final: list[tuple[int, int, float, dict[str, Any]]] = []
    for c in stay:
        i = pos(c)
        group = 2 if c.get("pinned") else (0 if i is not None else 1)
        final.append((group, i if i is not None else 10_000, c.get("rank") or 999, c))
    for p in add:
        final.append((2 if p.get("pinned") else 0, pos(p) if pos(p) is not None else 10_000, 999, p))
    final.sort(key=lambda t: (t[0], t[1], t[2]))
    order = [(n, t[3]) for n, t in enumerate(final, 1)]
    return {"add": add, "remove": remove, "order": order, "notes": notes}

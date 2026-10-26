"""Peers (同行, #47): the papers, scholars and labs working near a node, and what the map shows about them.

A peer is an attachment kind (`peers.yaml` in each project folder), so it syncs field by field, is logged
and goes with its node like evidence does:

    {node, type: paper|scholar|lab, title, relation: same|competing|builds_on, url, paper, authors, venue,
     date, note, source, entity}

relation: 同一做法 (same approach) / 竞争做法 (a competing approach to the same end) / 在其基础上 (the node
builds on it). `entity` names the peer across projects (paper:<arXiv id>, doi:…, openreview:…, acl:…,
scholar:openalex:A…, lab:<slug>; see `entity_for`).

One paper can arrive with different ids (an arXiv id here, an OpenReview forum there, only a title in the
reading list), so a record is known by all its aliases (`aliases`: its entity, the ids in its link, its
paper note, a paper's normalised title); records sharing any alias are one peer. Two records of the same
peer on a node (confirmed on both Macs, or again from the reader) are merged when read and counted once;
`attach` does not add a second one. web/rmap/peerlib.js derives the same aliases.

Analysis (peerlib.js applies the same rule; the thresholds reach it through /meta):
- 空白 (gap): an open branch (hypothesis or sub-line, not closed) with no peer.
- 撞车风险 (collision risk): a node in progress with at least DENSE distinct peers doing the same or a
  competing approach, or SAME_DENSE doing the same one.

Suggestions: papers the app already has on disk (the latest weekly runs, the cached follow feeds, the
followed scholars' cached works, the reading list) matched to likely nodes by keywords. Nothing is attached
by itself: the user confirms each one (as a peer or as evidence) or dismisses it. Dismissals are kept per
map in `<project>/peer_suggestions.yaml` (field-merged like the map, so both Macs stop showing them).
Reading these sources never goes to the network; results are cached until a map or a source changes.
"""
from __future__ import annotations

import hashlib
import math
import re
import secrets
import threading
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .. import i18n, sources
from ..config import PAPERS_DIR, PLAN_FILE, READING_LIST, RMAP_DIR
from ..store import now
from . import model, ops, project, registry
from .clock import Clock
from .evidence import _SCHEME, _line
from .project import HEADER
from .records import FieldStore, clocks, own_live, set_field

TYPES = ("paper", "scholar", "lab")
RELATIONS = ("same", "competing", "builds_on")  # 同一做法 / 竞争做法 / 在其基础上
SOURCES = ("manual", "tracker", "follow", "scholar", "reading", "reader", "assistant")
GAP_TYPES = ("hypothesis", "subline")  # branches: a gap is an open one nobody else works on
RISK_RELATIONS = ("same", "competing")
DENSE = 3       # same + competing peers that make a node in progress crowded
SAME_DENSE = 2  # ... or this many doing the very same approach
MATCH_TYPES = {"question": 0.7, "hypothesis": 1.0, "subline": 1.1, "experiment": 1.0}  # nodes a paper may match


def _choice(allowed: tuple[str, ...], key: str):
    def clean(v: Any) -> str:
        if v not in allowed:
            # i18n-keys: rmap_peers.err.{type,relation,source}
            raise ValueError(i18n.t(key, allowed=", ".join(allowed)))
        return v
    return clean


def _title(v: Any) -> str:
    v = _line(300)(v)
    if not v:
        raise ValueError(i18n.t("rmap_peers.err.title"))
    return v


def _url(v: Any) -> str | None:
    v = _line(500)(v)
    if v and not re.match(r"^https?://", v, re.I):
        raise ValueError(i18n.t("rmap_peers.err.url"))
    return v


def _paper(v: Any) -> str | None:
    """A paper note by name: a plain vault path, like evidence's paper targets."""
    v = _line(300)(v)
    if v and (_SCHEME.match(v) or v.startswith(("/", "~", "\\")) or ".." in re.split(r"[/\\]", v)):
        raise ValueError(i18n.t("rmap_peers.err.paper"))
    return v


def _date(v: Any) -> str | None:
    if v in (None, ""):
        return None
    v = str(v).strip()[:10]
    if not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", v):
        raise ValueError(i18n.t("rmap_peers.err.date"))
    return v


def _summary(rec: dict[str, Any]) -> str:
    return rec.get("title") or ""


PEER = registry.attachment_kind(registry.AttachmentKind(
    name="peer", prefix="p", file="peers.yaml",
    fields={"type": _choice(TYPES, "rmap_peers.err.type"), "title": _title,
            "relation": _choice(RELATIONS, "rmap_peers.err.relation"), "url": _url, "paper": _paper,
            "authors": _line(300), "venue": _line(80), "date": _date, "note": _line(500),
            "source": _choice(SOURCES, "rmap_peers.err.source")},
    required=("type", "title", "relation"), summary=_summary,
    extra={"types": list(TYPES), "relations": list(RELATIONS), "sources": list(SOURCES), "gap_types": list(GAP_TYPES),
           "risk_relations": list(RISK_RELATIONS), "dense": DENSE, "same_dense": SAME_DENSE},
))


# ---- entities and aliases: one peer, whatever ids it came with ----

def key_text(text: str) -> str:
    """A name as an entity key: lower-case letters and digits (any script) joined by '-'."""
    s = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\W_]+", "-", s, flags=re.UNICODE).strip("-")[:120].rstrip("-")


def entity_for(kind: str, *, title: str | None = None, arxiv: str | None = None, ext: str | None = None,
               doi: str | None = None, openalex: str | None = None, lab: str | None = None) -> str | None:
    """The entity key of a peer: paper:<arXiv id> / the paper's doi:, openreview:, acl: key / paper:<title>;
    scholar:openalex:<id> / scholar:<name>; lab:<lab id or name>."""
    if kind == "paper":
        if arxiv:
            return f"paper:{re.sub(r'v\d+$', '', arxiv.strip())}"
        if ext and registry.ENTITY.match(ext):
            return ext
        if doi:
            return sources.ext_key("doi", doi)
    elif kind == "scholar" and openalex:
        return f"scholar:openalex:{openalex.strip()}"
    elif kind == "lab" and lab:
        return f"lab:{key_text(lab)}" if key_text(lab) else None
    k = key_text(title or "")
    return f"{'paper' if kind == 'paper' else kind}:{k}" if k else None


TITLE_MIN = 12  # a normalised title shorter than this names nothing for sure
_ARXIV_LINK = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})", re.I)
_ARXIV_BARE = re.compile(r"(?:arxiv:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_ARXIV_DOI = re.compile(r"^doi:10\.48550\|arxiv\.(\d{4}\.\d{4,5})", re.I)


def ids_in(text: str | None) -> set[str]:
    """The paper keys a link or id names: paper:<arXiv id> (abs / pdf / html links, with or without a
    version, a query or a trailing slash), and the openreview: / acl: / doi: keys of app/sources.py."""
    s = (text or "").strip()
    out: set[str] = set()
    if m := _ARXIV_LINK.search(s) or _ARXIV_BARE.fullmatch(s):
        out.add(f"paper:{m.group(1)}")
    if k := sources.ext_from_url(s):
        m = _ARXIV_DOI.match(k)
        out.add(f"paper:{m.group(1)}" if m else k)
    return out


def title_key(title: str | None) -> str | None:
    k = key_text(title or "")
    return f"paper:{k}" if len(k) >= TITLE_MIN else None


def aliases(rec: dict[str, Any], kind: str = "peer") -> set[str]:
    """Every key a peer (or an evidence item) is known by: its entity, the ids in its link, its paper note
    (note:<name>) and, for a paper, its normalised title. web/rmap/peerlib.js aliasesOf is the same."""
    out = {rec["entity"]} if rec.get("entity") else set()
    typ = rec.get("type")
    if kind == "evidence":
        if typ == "paper" and rec.get("target"):
            out.add(f"note:{rec['target']}")
        elif typ == "url":
            out |= ids_in(rec.get("target"))
        paperish = typ in ("paper", "url")
    else:
        out |= ids_in(rec.get("url"))
        if rec.get("paper"):
            out.add(f"note:{rec['paper']}")
        paperish = typ == "paper"
    if paperish:
        if tk := title_key(rec.get("title")):
            out.add(tk)
    elif typ in ("scholar", "lab") and key_text(rec.get("title") or ""):
        out.add(f"{typ}:{key_text(rec['title'])}")
    return out


_note_index: dict[str, Any] = {}


def _note_keys(stem: str) -> set[str]:
    """The ids a paper note knows about itself: its arXiv id, DOI / OpenReview / ACL key and title."""
    from .. import library
    try:
        stamp = PAPERS_DIR.stat().st_mtime_ns
        if _note_index.get("stamp") != stamp:  # rebuilt when a note is added, removed or renamed
            rev: dict[str, set[str]] = {}
            for a, st in library.arxiv_index().items():
                rev.setdefault(st, set()).add(f"paper:{a}")
            for k, st in library.ext_index().items():
                rev.setdefault(st, set()).add(k)
            _note_index.clear()
            _note_index.update(stamp=stamp, ids=rev, titles={})
    except OSError:
        return set()
    out = set(_note_index["ids"].get(stem, ()))
    try:
        path = library.note_path(stem)
        mtime = path.stat().st_mtime_ns
        hit = _note_index["titles"].get(stem)
        if not hit or hit[0] != mtime:
            hit = (mtime, title_key(library._parse_note(path).get("title")))
            _note_index["titles"][stem] = hit
        if hit[1]:
            out.add(hit[1])
    except (OSError, ValueError):  # no such note (any more)
        pass
    return out


def known_aliases(rec: dict[str, Any], kind: str = "peer") -> set[str]:
    """aliases() plus what the paper note it names says (evidence hung before #47 has no entity)."""
    out = aliases(rec, kind)
    for a in list(out):
        if a.startswith("note:"):
            out |= _note_keys(a[5:])
    return out


def _groups(recs: list[dict[str, Any]], keys) -> list[list[dict[str, Any]]]:
    """Records that share a key, transitively, in the order their first record appears."""
    parent = list(range(len(recs)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    owner: dict[str, int] = {}
    for i, r in enumerate(recs):
        for k in keys(r):
            if k in owner:
                a, b = root(owner[k]), root(i)
                if a != b:
                    parent[max(a, b)] = min(a, b)
            else:
                owner[k] = i
    out: dict[int, list[dict[str, Any]]] = {}
    for i, r in enumerate(recs):
        out.setdefault(root(i), []).append(r)
    return list(out.values())


RELATION_RANK = {"same": 3, "competing": 2, "builds_on": 1}
_created = lambda r: (min(clocks(r).values(), default=""), r.get("id", ""))  # noqa: E731


def merged(peers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per distinct peer: its first record, with the closest relation among its records, every
    record id (`ids`), every alias, and the fields the first one lacks taken from the others."""
    out = []
    for g in _groups(sorted(peers, key=_created), aliases):
        first = dict(g[0])
        first["relation"] = max((p.get("relation") for p in g), key=lambda r: RELATION_RANK.get(r, 0))
        for p in g[1:]:
            for k in ("url", "paper", "authors", "venue", "date", "note", "entity"):
                if not first.get(k) and p.get(k):
                    first[k] = p[k]
        first["ids"] = [p["id"] for p in g]
        first["aliases"] = sorted(set().union(*(aliases(p) for p in g)))
        out.append(first)
    return out


# ---- analysis ----

def by_node(m: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Each node's distinct peers (duplicate records merged)."""
    raw: dict[str, list[dict[str, Any]]] = {}
    for p in m["attachments"].get("peer", []):
        raw.setdefault(p["node"], []).append(p)
    return {nid: merged(ps) for nid, ps in raw.items()}


def stats(n: dict[str, Any], peers: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts by relation over distinct peers, and whether the node is a gap or at risk of a collision."""
    peers = merged(peers) if any("ids" not in p for p in peers) else peers
    rel = {r: sum(1 for p in peers if p.get("relation") == r) for r in RELATIONS}
    dense = sum(rel[r] for r in RISK_RELATIONS)
    status = n.get("status") or "todo"
    return {"n": len(peers), **rel,
            "gap": n.get("type") in GAP_TYPES and status not in model.CLOSED and not peers,
            "risk": status == "active" and (dense >= DENSE or rel["same"] >= SAME_DENSE)}


def _order(m: dict[str, Any]) -> list[dict[str, Any]]:
    """Nodes depth first along refines (the map's rows), roots and siblings by creation."""
    nodes = {n["id"]: n for n in m["nodes"]}
    kids: dict[str, list[str]] = {}
    has_parent: set[str] = set()
    for e in project.dag_edges(m):
        if e["kind"] == "refines" and e["from"] in nodes and e["to"] in nodes:
            kids.setdefault(e["from"], []).append(e["to"])
            has_parent.add(e["to"])
    created = lambda i: (nodes[i].get("order") if nodes[i].get("order") is not None else 1e12,  # noqa: E731
                         min(clocks(nodes[i]).values(), default=""), i)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(i: str, depth: int) -> None:
        if i in seen:
            return
        seen.add(i)
        out.append({**nodes[i], "_depth": depth})
        for k in sorted(kids.get(i, []), key=created):
            walk(k, depth + 1)
    for i in sorted((i for i in nodes if i not in has_parent), key=created):
        walk(i, 0)
    for i in sorted(nodes, key=created):  # anything a cycle hid
        walk(i, 0)
    return out


def analyze(slug: str, m: dict[str, Any] | None = None) -> dict[str, Any]:
    """One map: every node's distinct peers and counts, the gaps and the collision risks."""
    m = m or project.get(slug).read()
    peers = by_node(m)
    rows = []
    for n in _order(m):
        mine = peers.get(n["id"], [])
        rows.append({"id": n["id"], "title": n["title"], "type": n["type"], "status": n.get("status") or "todo",
                     "depth": n["_depth"], **stats(n, mine),
                     "peers": [{k: p.get(k) for k in ("id", "ids", "title", "type", "relation", "date", "entity", "aliases")}
                               for p in mine]})
    return {"slug": slug, "title": m["project"].get("title") or slug, "line": m["project"].get("line"), "nodes": rows,
            "gaps": [r for r in rows if r["gap"]], "risks": [r for r in rows if r["risk"]]}


_KEY_RANK = (re.compile(r"^paper:\d{4}\.\d{4,5}$"), re.compile(r"^doi:"), re.compile(r"^openreview:"), re.compile(r"^acl:"),
             re.compile(r"^scholar:openalex:"))


def best_key(keys: set[str]) -> str:
    """The id that names a peer best: an arXiv id, then a DOI, OpenReview, ACL key, an OpenAlex id, else any."""
    rank = lambda k: (next((i for i, rx in enumerate(_KEY_RANK) if rx.match(k)), len(_KEY_RANK)), k.startswith("note:"), k)  # noqa: E731
    return min(keys, key=rank)


def _overview() -> dict[str, Any]:
    maps, cells = [], []
    for slug in project.slugs():
        m = project.get(slug).read()
        if m["project"].get("archived"):
            continue
        a = analyze(slug, m)
        live = [r for r in a["nodes"] if r["status"] not in model.FADED]  # refuted and dropped nodes are history
        rows = [r for r in live if r["n"] or r["type"] in GAP_TYPES]
        for r in rows:
            cells += [(slug, r, p) for p in r["peers"]]
        maps.append({"slug": slug, "title": a["title"], "line": a["line"], "rows": rows,
                     "gaps": len(a["gaps"]), "risks": len(a["risks"]), "peers": sum(r["n"] for r in rows)})
    columns = []
    for g in _groups(cells, lambda c: c[2]["aliases"]):
        keys = set().union(*(c[2]["aliases"] for c in g))
        col = {"key": best_key(keys), "title": g[0][2]["title"], "type": g[0][2]["type"],
               "date": max((c[2]["date"] for c in g if c[2].get("date")), default=None),
               "projects": sorted({c[0] for c in g}), "aliases": sorted(keys)}
        seen = set()
        for slug, r, p in g:
            cell = r.setdefault("cells", {})
            if RELATION_RANK.get(p.get("relation"), 0) > RELATION_RANK.get(cell.get(col["key"], ""), 0):
                cell[col["key"]] = p.get("relation")
            seen.add((slug, r["id"]))
        col["nodes"] = len(seen)
        columns.append(col)
    for mp in maps:
        for r in mp["rows"]:
            r.setdefault("cells", {})
    columns.sort(key=lambda c: (-len(c["projects"]), -c["nodes"], c["date"] or "", c["title"] or ""))
    gaps = [{"slug": mp["slug"], "project": mp["title"], **{k: r[k] for k in ("id", "title", "type", "status")}}
            for mp in maps for r in mp["rows"] if r["gap"]]
    risks = [{"slug": mp["slug"], "project": mp["title"], **{k: r[k] for k in ("id", "title", "type", "status", "n", "same", "competing", "builds_on")},
              "peers": [p["title"] for p in r["peers"] if p.get("relation") in RISK_RELATIONS][:6]}
             for mp in maps for r in mp["rows"] if r["risk"]]
    return {"maps": maps, "columns": columns, "gaps": gaps, "risks": risks,
            "rule": {"dense": DENSE, "same_dense": SAME_DENSE, "gap_types": list(GAP_TYPES)}}


def overview() -> dict[str, Any]:
    """Every map: the rows of the 分支 × 同行 heat table (open branches and every live node with peers), the
    peers as columns (one per distinct peer across projects, most widespread first), the gaps and collision
    risks. Refuted and dropped nodes are left out, their peers too."""
    stamp = project.stamp_of(RMAP_DIR)
    return {**_cached(("overview",), stamp, _overview), "stamp": stamp}


# ---- attaching without duplicates ----

TITLE_LIMIT = {"peer": 300, "evidence": 200}


def clip_title(title: str | None, kind: str) -> str | None:
    """A derived title (a paper's, from a feed or a note) cut to what the kind accepts."""
    if not title:
        return title
    t = re.sub(r"\s+", " ", title).strip()
    lim = TITLE_LIMIT[kind]
    return t if len(t) <= lim else t[:lim - 1].rstrip() + "…"


def attach(slug: str, node: str, kind: str, fields: dict[str, Any], *, who: str = "user") -> dict[str, Any]:
    """Hang a peer or an evidence item on a node, unless the same paper / scholar / lab (any alias, the paper
    note's own ids included) already hangs there as that kind: then nothing is added, and a peer takes the
    new relation. Returns {state: added | updated | exists, id}."""
    if kind not in TITLE_LIMIT:
        raise ValueError(i18n.t("rmap_peers.err.kind"))
    fields = {k: v for k, v in (fields or {}).items() if v not in (None, "")}
    if fields.get("title"):
        fields["title"] = clip_title(fields["title"], kind)
    m = project.get(slug).read()
    if not any(n["id"] == node for n in m["nodes"]):
        raise KeyError(node)
    mine = known_aliases(fields, kind)
    for rec in m["attachments"].get(kind, []):
        if rec["node"] == node and mine & known_aliases(rec, kind):
            if kind == "peer" and fields.get("relation") and fields["relation"] != rec.get("relation"):
                ops.apply(slug, [{"op": "attach.update", "kind": kind, "id": rec["id"], "set": {"relation": fields["relation"]}}], who=who)
                return {"state": "updated", "id": rec["id"]}
            return {"state": "exists", "id": rec["id"]}
    if not fields.get("entity") and (keys := {k for k in mine if not k.startswith("note:")}):
        fields["entity"] = best_key(keys)
    r = ops.apply(slug, [{"op": "attach.add", "ref": "$a", "kind": kind, "node": node, "set": fields}], who=who)
    return {"state": "added", "id": r["refs"]["$a"]}


def fresh_peer_ops(slug: str, batch: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """The peer additions of a batch minus those already on their node (or earlier in the batch); and the
    titles left out."""
    m = project.get(slug).read()
    have: dict[str, set[str]] = {}
    for p in m["attachments"].get("peer", []):
        have.setdefault(p["node"], set()).update(known_aliases(p))
    keep, skipped = [], []
    for op in batch:
        if op.get("op") == "attach.add" and op.get("kind") == "peer" and isinstance(op.get("set"), dict):
            keys = known_aliases(op["set"])
            seen = have.setdefault(str(op.get("node")), set())
            if keys & seen:
                skipped.append(op["set"].get("title") or "")
                continue
            seen |= keys
        keep.append(op)
    return keep, skipped


# ---- suggestions: papers the app already knows about, matched to likely nodes ----

STOP = set("""a an the of for to in on and or with without by from as at is are be via using use based towards toward
into over under than that this these those its their our we new can do does how what when why which not more less
study analysis approach method model learning paper task data large small simple improve better effective
efficient framework system vs it if so no all any one two per up out also only both each between about after
before while where who whose first make made get need show work result""".split())
FEED_DAYS = 60
SCHOLAR_DAYS = 90
RUNS = 2  # weekly tracker runs to look at
MIN_SCORE = 5.0
MAX_SUGGESTIONS = 40


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z0-9@]+", " ", "".join(c for c in s if not unicodedata.combining(c)).lower()).strip()


def _stem(tok: str) -> str:
    """A light stemmer, the same for both sides: verifier / verifying / verify, rerankers / reranker, learned / learn."""
    if len(tok) > 4 and tok.endswith("ies"):
        tok = tok[:-3] + "y"
    elif len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        tok = tok[:-1]
    for suf, least in (("ing", 6), ("ed", 5), ("er", 6)):
        if len(tok) >= least and tok.endswith(suf):
            tok = tok[:-len(suf)]
            if len(tok) > 3 and tok[-1] == tok[-2] and tok[-1] not in "lsz":  # flagged -> flag
                tok = tok[:-1]
            break
    return tok[:-1] + "i" if len(tok) > 3 and tok.endswith("y") else tok


STOP_STEMS = {_stem(w) for w in STOP}  # "results", "works", "improves", "uses" are stop words too


def terms(text: str) -> set[str]:
    """Words (stemmed, stop words out) and the pairs of neighbours ("sparse attention")."""
    keep = []
    for w in _fold(text).split():
        s = _stem(w)
        keep.append(s if len(w) >= 2 and not w.isdigit() and w not in STOP and s not in STOP_STEMS else None)
    out = {w for w in keep if w}
    out |= {f"{a} {b}" for a, b in zip(keep, keep[1:]) if a and b}
    return out


def _arxiv_url(aid: str) -> str:
    return f"https://arxiv.org/abs/{aid}"


def _cand(source: str, via: str | None, *, title: str, arxiv: str | None = None, ext: str | None = None,
          doi: str | None = None, url: str | None = None, authors: list[str] | None = None, date_: str | None = None,
          abstract: str = "", paper: str | None = None, venue: str | None = None) -> dict[str, Any] | None:
    if not title:
        return None
    arxiv = re.sub(r"v\d+$", "", arxiv) if arxiv else None
    key = entity_for("paper", title=title, arxiv=arxiv, ext=ext, doi=doi)
    if not key:
        return None
    if not url:
        if arxiv:
            url = _arxiv_url(arxiv)
        elif ext and ext.startswith("doi:"):
            url = "https://doi.org/" + ext[4:].replace("|", "/")
    title = re.sub(r"\s+", " ", title).strip()
    keys = {key} | ids_in(url) | ({f"paper:{arxiv}"} if arxiv else set()) | ({ext} if ext and registry.ENTITY.match(ext) else set())
    keys |= {sources.ext_key("doi", doi)} if doi else set()
    keys |= {k for k in (title_key(title),) if k} | ({f"note:{paper}"} if paper else set())
    return {"key": key, "title": title, "arxiv": arxiv, "ext": ext, "url": url,
            "authors": [a for a in (authors or []) if isinstance(a, str)][:6], "date": (date_ or "")[:10] or None,
            "abstract": (abstract or "")[:900], "paper": paper, "venue": venue, "sources": [source],
            "via": [via] if via else [], "aliases": keys}


def _candidates(today: date) -> list[dict[str, Any]]:
    from .. import library, reading, scholars, tracking
    out: list[dict[str, Any] | None] = []
    try:
        idx, ext_idx = library.arxiv_index(), library.ext_index()
    except OSError:
        idx, ext_idx = {}, {}
    for r in tracking.runs()[:RUNS]:
        try:
            run = tracking.run(r["run"])
        except (OSError, ValueError):
            continue
        for p in run["papers"]:
            out.append(_cand("tracker", None, title=p.get("title") or "", arxiv=p.get("arxiv"), authors=p.get("authors"),
                             date_=p.get("published"), abstract=p.get("abstract") or "", paper=p.get("note")))
    feed_cut = (today - timedelta(days=FEED_DAYS)).isoformat()
    for f in tracking.follows():
        for suffix in ("json", "openreview.json"):
            for p in tracking._read_cache(tracking.FEED_CACHE / f"{f['id']}.{suffix}"):
                if not isinstance(p, dict) or (p.get("published") or "") < feed_cut:
                    continue
                aid = p.get("arxiv")
                out.append(_cand("follow", f.get("value"), title=p.get("title") or "", arxiv=aid, ext=p.get("ext"),
                                 url=p.get("url"), authors=p.get("authors"), date_=p.get("published"),
                                 abstract=p.get("summary") or "", venue=p.get("venue"),
                                 paper=idx.get(aid) if aid else ext_idx.get(p.get("ext") or "")))
    sch_cut = (today - timedelta(days=SCHOLAR_DAYS)).isoformat()
    for s in scholars.scholars():
        hit = scholars._cache_get(f"author-{s['openalex']}") if s.get("openalex") else None
        for w in (hit[0].get("works", []) + hit[0].get("arxiv", [])) if hit else []:
            if (w.get("date") or "") < sch_cut or (w.get("source") == "OpenAlex" and (w.get("date") or "").endswith("-01-01")):
                continue
            aid = w.get("arxiv")
            out.append(_cand("scholar", s.get("name"), title=w.get("title") or "", arxiv=aid, doi=w.get("doi"),
                             authors=[a.get("name") for a in w.get("authors", []) if isinstance(a, dict)],
                             date_=w.get("date"), abstract=w.get("abstract") or "", venue=w.get("venue"),
                             paper=idx.get(aid) if aid else None))
    for it in reading.load():
        if it.get("status") not in ("inbox", "queued", "reading"):
            continue
        title = it.get("title") or it.get("paper") or ""
        out.append(_cand("reading", None, title=title, arxiv=it.get("arxiv"), ext=it.get("ext"), url=it.get("url"),
                         date_=(it.get("pushed_at") or "")[:10], abstract=it.get("reason") or "", paper=it.get("paper")))
    found = [c for c in out if c]
    for c in found:  # a paper note knows its other ids
        if c.get("paper"):
            c["aliases"] |= _note_keys(c["paper"])
    result = []
    for g in _groups(found, lambda c: c["aliases"]):  # the same paper from several sources (any shared id) is one
        m = dict(g[0])
        for c in g[1:]:
            for f in ("arxiv", "ext", "url", "paper", "venue", "date"):
                m[f] = m.get(f) or c.get(f)
            if len(c["abstract"]) > len(m["abstract"]):
                m["abstract"] = c["abstract"]
            m["authors"] = m["authors"] or c["authors"]
            m["sources"] = list(dict.fromkeys(m["sources"] + c["sources"]))
            m["via"] = list(dict.fromkeys(m["via"] + c["via"]))
            m["aliases"] = m["aliases"] | c["aliases"]
        m["key"] = best_key({k for k in m["aliases"] if not k.startswith("note:")} or {m["key"]})
        result.append(m)
    return result


def candidates(today: date | None = None) -> list[dict[str, Any]]:
    """New papers from the tracking page, the followed feeds and scholars, and the reading list (all from
    disk: cached answers only, nothing is fetched)."""
    today = today or date.today()
    return _cached(("candidates", today.isoformat()), _sources_stamp(), lambda: _candidates(today))


def _store(slug: str) -> FieldStore:
    p = project.get(slug)
    return FieldStore(p.dir / "peer_suggestions.yaml", ("dismissed",), HEADER, name=f"rmap/{slug}.peer_suggestions")


def dismissed(slug: str) -> set[str]:
    """Every key (and alias) of the papers dismissed for this map."""
    out: set[str] = set()
    for r in _store(slug).load()["dismissed"]:
        if own_live(r) and r.get("key"):
            out.add(r["key"])
            out.update(a for a in r.get("aliases") or [] if isinstance(a, str))
    return out


def dismiss(key: str, slugs: list[str], aliases_: list[str] | None = None) -> dict[str, Any]:
    """Stop suggesting this paper for these maps (on both Macs), whatever id it comes with next time."""
    if not isinstance(key, str) or not registry.ENTITY.match(key):
        raise ValueError(i18n.t("rmap_peers.err.key"))
    extra = sorted({a for a in aliases_ or [] if isinstance(a, str) and 0 < len(a) <= 300 and a != key})[:20]
    done = []
    for slug in slugs:
        st = _store(slug)
        clock = Clock()
        with st.editing(skip_unchanged=True) as data:
            for r in data["dismissed"]:
                clock.observe(max((r.get("clock") or {}).values(), default=""))
            if any(r.get("key") == key and own_live(r) for r in data["dismissed"]):
                continue
            stamp = clock.tick()
            rec = {"id": "sd" + secrets.token_hex(4), "created_at": now(), "clock": {}}
            set_field(rec, "key", key, stamp)
            if extra:
                set_field(rec, "aliases", extra, stamp)
            data["dismissed"].append(rec)
        clock.save()
        done.append(slug)
    return {"key": key, "slugs": done}


def _node_text(n: dict[str, Any], hung: list[dict[str, Any]]) -> tuple[set[str], set[str], set[str]]:
    """The node's own words (title and tags; its description) and the titles of what is hung on it."""
    head = terms(n.get("title", "") + " " + " ".join(n.get("tags") or []))
    body = terms((n.get("body") or "")[:800]) - head
    near = terms(" ".join(a.get("title") or a.get("target") or "" for a in hung)) - head - body
    return head, body, near


def _targets(slug: str | None = None) -> list[dict[str, Any]]:
    """Every node a paper may be suggested for (only this map's, given a slug), with its words, the aliases of
    everything hung on it, and the map's dismissed papers."""
    try:
        from ..scholars import line_terms
        line_kw = line_terms()
    except Exception:  # noqa: BLE001 - no keywords is fine
        line_kw = {}
    out = []
    for s in ([slug] if slug else project.slugs()):
        m = project.get(s).read()
        if m["project"].get("archived"):
            continue
        hung: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for kind, recs in m["attachments"].items():
            for a in recs:
                hung.setdefault(a["node"], []).append((kind, a))
        skip = dismissed(s)
        line = m["project"].get("line")
        for n in m["nodes"]:
            if n.get("type") not in MATCH_TYPES or n.get("status") in model.FADED:
                continue
            here = hung.get(n["id"], [])
            head, body, near = _node_text(n, [a for _, a in here])
            keys = set().union(*(known_aliases(a, kind) for kind, a in here)) if here else set()
            out.append({"slug": s, "project": m["project"].get("title") or s, "node": n, "head": head, "body": body,
                        "near": near, "keys": keys, "skip": skip,
                        "line_terms": [t for t in line_kw.get(line, []) if t] if line else []})
    return out


def _line_hit(term_: str, text: str) -> bool:
    """A research-line keyword ("a + b" needs both) in the text, as whole words (app/sources.py has_terms)."""
    parts = [p.strip() for p in term_.split(" + ") if p.strip()]
    return sources.has_terms(text, parts)


def match(cands: list[dict[str, Any]], targets: list[dict[str, Any]] | None = None, *, slug: str | None = None,
          node: str | None = None, min_score: float = MIN_SCORE, per_paper: int = 2) -> list[dict[str, Any]]:
    """Rank nodes for each paper by shared words (rare words weigh more; a word in the paper's title and the
    node's title counts most, the titles of what is already hung there count a little, and at least one word
    must be the node's own; the project's research-line keywords add a little). A paper already hung on a
    node (any of its ids, or its paper note) and a paper dismissed for a map are left out there."""
    targets = _targets(slug) if targets is None else [x for x in targets if not slug or x["slug"] == slug]
    if node:
        targets = [x for x in targets if x["node"]["id"] == node]
    if not targets or not cands:
        return []
    docs = [terms(c["title"]) | terms(c["abstract"][:600]) for c in cands] + [x["head"] | x["body"] | x["near"] for x in targets]
    df: dict[str, int] = {}
    for d in docs:
        for w in d:
            df[w] = df.get(w, 0) + 1
    big = len(docs)
    idf = lambda w: math.log((big + 1) / (df.get(w, 0) + 1)) + 0.5  # noqa: E731
    out = []
    for c in cands:
        ct, ca = terms(c["title"]), terms(c["abstract"][:600])
        ca -= ct
        text = c["title"] + " " + c["abstract"][:600]
        keys = set(c.get("aliases") or ()) | {c["key"]} | ({f"note:{c['paper']}"} if c.get("paper") else set())
        hits = []
        for x in targets:
            if keys & x["keys"] or keys & x["skip"]:
                continue
            score, shared = 0.0, []
            own = (ct | ca) & (x["head"] | x["body"])
            if not own:
                continue
            for w in own | ((ct | ca) & x["near"]):
                wt = (1.0 if w in ct else 0.5) * (1.0 if w in x["head"] else 0.6 if w in x["body"] else 0.3) * (1.6 if " " in w else 1.0)
                score += idf(w) * wt
                shared.append((idf(w) * wt, w))
            if sum(v for v, w in shared if w in own) < 0.4 * min_score:  # mostly words of what is hung there
                continue
            singles = {w for _, w in shared if " " not in w}
            pairs = len(shared) - len(singles)
            if not (len(singles) >= 3 or (len(singles) >= 2 and pairs) or (len(singles) >= 2 and score >= 1.6 * min_score)):
                continue
            if any(_line_hit(t, text) for t in x["line_terms"]):
                score += 1.5
            score *= MATCH_TYPES[x["node"]["type"]]
            if score >= min_score:
                n = x["node"]
                hits.append({"slug": x["slug"], "project": x["project"], "node": n["id"], "title": n["title"],
                             "type": n["type"], "status": n.get("status") or "todo", "score": round(score, 1),
                             "terms": [w for _, w in sorted(shared, reverse=True) if " " not in w][:4]})
        if not hits:
            continue
        hits.sort(key=lambda h: -h["score"])
        best = hits[0]["score"]
        keep = [h for h in hits if h["score"] >= best * 0.7][:per_paper]
        out.append({**{k: c.get(k) for k in ("key", "title", "arxiv", "ext", "url", "authors", "date", "paper", "venue",
                                             "sources", "via")},
                    "aliases": sorted(k for k in keys if not k.startswith("note:")),
                    "abstract": c["abstract"][:300], "matches": keep, "score": best})
    out.sort(key=lambda s: (-s["score"], s["date"] or ""))
    return out


def suggestions(slug: str | None = None, node: str | None = None, limit: int = MAX_SUGGESTIONS) -> list[dict[str, Any]]:
    """Papers likely to belong on a node, for the user to confirm (never attached by themselves). Cached until
    the map(s) or a source (tracker runs, feeds, scholars, reading list, paper notes) change."""
    stamp = (project.get(slug).stamp() if slug else project.stamp_of(RMAP_DIR)) + _sources_stamp()
    rows = _cached(("suggestions", slug), stamp, lambda: match(candidates(), slug=slug))
    return [s for s in rows if not node or any(m["node"] == node for m in s["matches"])][:limit]


def match_text(title: str, text: str = "", *, key: str | None = None, note: str | None = None,
               keys: set[str] | None = None, limit: int = 6) -> list[dict[str, Any]]:
    """Nodes that one paper (the reader's open paper) most likely belongs to, best first; nodes it already
    hangs on (by any of its ids or its note) are left out."""
    cand = {"key": key or entity_for("paper", title=title) or "paper:x", "title": title, "abstract": text or "",
            "paper": note, "aliases": set(keys or ())}
    hits = match([cand], min_score=2.0, per_paper=limit)
    return hits[0]["matches"] if hits else []


def paper_info(stem: str) -> dict[str, Any]:
    """What the reader's open paper looks like as a peer or as evidence: title, ids, entity, aliases, words."""
    from .. import library, reading
    info = library.get_paper(stem)
    item = reading.find(reading.load(), stem)
    if item and info.get("external") and item.get("title"):  # no note yet: the reading list knows the title
        info["title"] = item["title"]
    text = re.sub(r"^---\n.*?\n---\n", "", info.get("note_markdown") or "", flags=re.S)
    if item and item.get("reason"):
        text = item["reason"] + "\n" + text
    ext = stem if library.ext_key(stem) else (sources.ext_key("doi", info["doi"]) if info.get("doi") else None)
    arxiv = info.get("arxiv") or None
    note = None if info.get("external") else stem
    title = info.get("title") or stem
    keys = {k for k in (f"paper:{arxiv}" if arxiv else None, ext, title_key(title), f"note:{note}" if note else None) if k}
    if note:
        keys |= _note_keys(note)
    return {"stem": stem, "title": title, "arxiv": arxiv, "ext": ext, "note": note, "year": info.get("year"),
            "tags": info.get("tags") or [], "entity": entity_for("paper", title=title, arxiv=arxiv, ext=ext),
            "url": _arxiv_url(arxiv) if arxiv else None, "aliases": keys, "text": text[:1500]}


def attached(keys: set[str]) -> list[dict[str, Any]]:
    """Where a paper is already hung, as a peer or as evidence, across maps: any alias matches, and evidence
    hung before #47 (no entity) is recognised by its paper note's own ids."""
    out = []
    for s in project.slugs():
        m = project.get(s).read()
        titles = {n["id"]: n["title"] for n in m["nodes"]}
        for kind in ("peer", "evidence"):
            for a in m["attachments"].get(kind, []):
                if keys & known_aliases(a, kind):
                    out.append({"slug": s, "project": m["project"].get("title") or s, "kind": kind, "id": a["id"],
                                "node": a["node"], "title": titles.get(a["node"])})
    return out


# ---- cache ----

_cache: dict[tuple, tuple[str, Any]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
    _note_index.clear()


def _cached(key: tuple, stamp: str, compute):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
    value = compute()
    with _cache_lock:
        if len(_cache) > 64:
            _cache.clear()
        _cache[key] = (stamp, value)
    return value


def _sources_stamp() -> str:
    """Changes when anything suggestions read changes: tracker runs, follow feeds, scholar caches and list,
    the reading list, the plan (follows), the paper notes, and the date (the windows move)."""
    from .. import scholars, tracking
    h = hashlib.sha1(date.today().isoformat().encode())
    files: list[Path] = [READING_LIST, PLAN_FILE, scholars.SCHOLARS_FILE, PAPERS_DIR]
    runs = tracking._runs_dir()
    if runs:
        files += sorted(runs.glob("*/candidates.json"))
    for folder, pattern in ((tracking.FEED_CACHE, "*.json"), (scholars.CACHE, "author-*.json")):
        if folder.is_dir():
            files += sorted(folder.glob(pattern))
    for f in files:
        try:
            st = f.stat()
            h.update(f"{f}|{st.st_mtime_ns}|{st.st_size}\n".encode())
        except OSError:
            h.update(f"{f}|-\n".encode())
    return h.hexdigest()[:16]


__all__ = ["PEER", "TYPES", "RELATIONS", "SOURCES", "GAP_TYPES", "DENSE", "SAME_DENSE", "entity_for", "aliases",
           "known_aliases", "merged", "stats", "analyze", "overview", "attach", "fresh_peer_ops", "candidates", "match",
           "suggestions", "match_text", "paper_info", "dismiss", "dismissed", "attached"]

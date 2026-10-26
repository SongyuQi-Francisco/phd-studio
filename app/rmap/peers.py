"""Peers (同行, #47): the papers, scholars and labs working near a node, and what the map shows about them.

A peer is an attachment kind (`peers.yaml` in each project folder), so it syncs field by field, is logged
and goes with its node like evidence does:

    {node, type: paper|scholar|lab, title, relation: same|competing|builds_on, url, paper, authors, venue,
     date, note, source, entity}

relation: 同一做法 (same approach) / 竞争做法 (a competing approach to the same end) / 在其基础上 (the node
builds on it). `entity` names the peer across projects (paper:<arXiv id>, doi:…, openreview:…, acl:…,
scholar:openalex:A…, lab:<slug>; see `entity_for`), so one paper hung on two projects is one peer.

Analysis (web/rmap/peerlib.js applies the same rule; the thresholds reach it through /meta):
- 空白 (gap): an open branch (hypothesis or sub-line, not closed) with no peer.
- 撞车风险 (collision risk): a node in progress with at least DENSE peers doing the same or a competing
  approach, or SAME_DENSE doing the same one.

Suggestions: papers the app already has on disk (the latest weekly runs, the cached follow feeds, the
followed scholars' cached works, the reading list) matched to likely nodes by keywords. Nothing is attached
by itself: the user confirms each one (as a peer or as evidence) or dismisses it. Dismissals are kept per
map in `<project>/peer_suggestions.yaml` (field-merged like the map, so both Macs stop showing them).
Reading these sources never goes to the network.
"""
from __future__ import annotations

import math
import re
import secrets
import unicodedata
from datetime import date, timedelta
from typing import Any

from .. import i18n
from ..config import RMAP_DIR
from ..store import now
from . import model, project, registry
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


# ---- entities: the same peer in every project ----

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
            from ..sources import ext_key
            return ext_key("doi", doi)
    elif kind == "scholar" and openalex:
        return f"scholar:openalex:{openalex.strip()}"
    elif kind == "lab" and lab:
        return f"lab:{key_text(lab)}" if key_text(lab) else None
    k = key_text(title or "")
    return f"{'paper' if kind == 'paper' else kind}:{k}" if k else None


# ---- analysis ----

def by_node(m: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for p in m["attachments"].get("peer", []):
        out.setdefault(p["node"], []).append(p)
    return out


def stats(n: dict[str, Any], peers: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts by relation, and whether the node is a gap or at risk of a collision."""
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


def peer_key(p: dict[str, Any]) -> str:
    return p.get("entity") or f"{p.get('type')}:{key_text(p.get('title', ''))}"


def analyze(slug: str, m: dict[str, Any] | None = None) -> dict[str, Any]:
    """One map: every node's peer counts, the gaps and the collision risks."""
    m = m or project.get(slug).read()
    peers = by_node(m)
    rows = []
    for n in _order(m):
        s = stats(n, peers.get(n["id"], []))
        rows.append({"id": n["id"], "title": n["title"], "type": n["type"], "status": n.get("status") or "todo",
                     "depth": n["_depth"], **s,
                     "peers": [{k: p.get(k) for k in ("id", "title", "type", "relation", "date", "entity")} for p in peers.get(n["id"], [])]})
    return {"slug": slug, "title": m["project"].get("title") or slug, "line": m["project"].get("line"), "nodes": rows,
            "gaps": [r for r in rows if r["gap"]], "risks": [r for r in rows if r["risk"]]}


RELATION_RANK = {"same": 3, "competing": 2, "builds_on": 1}


def overview() -> dict[str, Any]:
    """Every map: the rows of the 分支 × 同行 heat table (branches and every node with peers), the peers as
    columns (one per entity, across projects, most widespread first), and the gaps and collision risks."""
    maps, columns = [], {}
    for slug in project.slugs():
        m = project.get(slug).read()
        if m["project"].get("archived"):
            continue
        a = analyze(slug, m)
        rows = [r for r in a["nodes"] if r["n"] or (r["type"] in GAP_TYPES and r["status"] not in model.FADED)]
        for r in rows:
            cells: dict[str, str] = {}
            for p in r["peers"]:
                k = peer_key(p)
                if RELATION_RANK.get(p.get("relation"), 0) > RELATION_RANK.get(cells.get(k, ""), 0):
                    cells[k] = p.get("relation")
                col = columns.setdefault(k, {"key": k, "title": p.get("title"), "type": p.get("type"), "date": p.get("date"),
                                             "nodes": 0, "projects": set()})
                col["date"] = max(filter(None, (col["date"], p.get("date"))), default=None)
            for k in cells:
                columns[k]["nodes"] += 1
                columns[k]["projects"].add(slug)
            r["cells"] = cells
        maps.append({"slug": slug, "title": a["title"], "line": a["line"], "rows": rows,
                     "gaps": len(a["gaps"]), "risks": len(a["risks"]),
                     "peers": sum(r["n"] for r in a["nodes"])})
    cols = sorted(columns.values(), key=lambda c: (-len(c["projects"]), -c["nodes"], c["date"] or "", c["title"] or ""))
    for c in cols:
        c["projects"] = sorted(c["projects"])
    gaps = [{"slug": mp["slug"], "project": mp["title"], **{k: r[k] for k in ("id", "title", "type", "status")}}
            for mp in maps for r in mp["rows"] if r["gap"]]
    risks = [{"slug": mp["slug"], "project": mp["title"], **{k: r[k] for k in ("id", "title", "type", "status", "n", "same", "competing", "builds_on")},
              "peers": [p["title"] for p in r["peers"] if p.get("relation") in RISK_RELATIONS][:6]}
             for mp in maps for r in mp["rows"] if r["risk"]]
    return {"maps": maps, "columns": cols, "gaps": gaps, "risks": risks, "stamp": project.stamp_of(RMAP_DIR),
            "rule": {"dense": DENSE, "same_dense": SAME_DENSE, "gap_types": list(GAP_TYPES)}}


# ---- suggestions: papers the app already knows about, matched to likely nodes ----

STOP = set("""a an the of for to in on and or with without by from as at is are be via using use based towards toward
into over under than that this these those its their our we new can do does how what when why which not more less
study analysis approach approaches method methods model models learning paper task tasks data large small simple
improving improve improved better effective efficient framework system systems toward via vs it if so no all any
one two per up out also only both each between about after before while where who whose first make made get need
show work result""".split())
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


def terms(text: str) -> set[str]:
    """Words (plurals folded, stop words out) and the pairs of neighbours ("sparse attention")."""
    keep = [_stem(w) if len(w) >= 2 and w not in STOP and not w.isdigit() else None for w in _fold(text).split()]
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
    return {"key": key, "title": re.sub(r"\s+", " ", title).strip(), "arxiv": arxiv, "ext": ext, "url": url,
            "authors": [a for a in (authors or []) if isinstance(a, str)][:6], "date": (date_ or "")[:10] or None,
            "abstract": (abstract or "")[:900], "paper": paper, "venue": venue, "sources": [source],
            "via": [via] if via else []}


def candidates(today: date | None = None) -> list[dict[str, Any]]:
    """New papers from the tracking page, the followed feeds and scholars, and the reading list (all from
    disk: cached answers only, nothing is fetched)."""
    from .. import library, reading, scholars, tracking
    today = today or date.today()
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
    merged: dict[str, dict[str, Any]] = {}
    by_title: dict[str, str] = {}
    for c in filter(None, out):
        tk = key_text(c["title"])
        k = c["key"] if c["key"] in merged else by_title.get(tk, c["key"])
        if k in merged:
            m = merged[k]
            for f in ("arxiv", "ext", "url", "paper", "venue", "date"):
                m[f] = m.get(f) or c.get(f)
            if len(c["abstract"]) > len(m["abstract"]):
                m["abstract"] = c["abstract"]
            m["authors"] = m["authors"] or c["authors"]
            m["sources"] = list(dict.fromkeys(m["sources"] + c["sources"]))
            m["via"] = list(dict.fromkeys(m["via"] + c["via"]))
        else:
            merged[k] = c
            by_title.setdefault(tk, k)
    return list(merged.values())


def _store(slug: str) -> FieldStore:
    p = project.get(slug)
    return FieldStore(p.dir / "peer_suggestions.yaml", ("dismissed",), HEADER, name=f"rmap/{slug}.peer_suggestions")


def dismissed(slug: str) -> set[str]:
    return {r["key"] for r in _store(slug).load()["dismissed"] if own_live(r) and r.get("key")}


def dismiss(key: str, slugs: list[str]) -> dict[str, Any]:
    """Stop suggesting this paper for these maps (on both Macs)."""
    if not isinstance(key, str) or not registry.ENTITY.match(key):
        raise ValueError(i18n.t("rmap_peers.err.key"))
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


def _targets() -> list[dict[str, Any]]:
    """Every node a paper may be suggested for, with its words and what is already hung on it."""
    out = []
    try:
        from ..scholars import line_terms
        line_kw = line_terms()
    except Exception:  # noqa: BLE001 - no keywords is fine
        line_kw = {}
    for slug in project.slugs():
        m = project.get(slug).read()
        if m["project"].get("archived"):
            continue
        hung: dict[str, list[dict[str, Any]]] = {}
        for recs in m["attachments"].values():
            for a in recs:
                hung.setdefault(a["node"], []).append(a)
        skip = dismissed(slug)
        line = m["project"].get("line")
        for n in m["nodes"]:
            if n.get("type") not in MATCH_TYPES or n.get("status") in model.FADED:
                continue
            head, body, near = _node_text(n, hung.get(n["id"], []))
            keys = {a.get("entity") for a in hung.get(n["id"], []) if a.get("entity")}
            papers = {a.get("target") for a in hung.get(n["id"], []) if a.get("type") == "paper"}
            papers |= {a.get("paper") for a in hung.get(n["id"], []) if a.get("paper")}
            out.append({"slug": slug, "project": m["project"].get("title") or slug, "node": n, "head": head, "body": body, "near": near,
                        "keys": keys, "papers": papers - {None}, "skip": skip,
                        "line_terms": [t for t in line_kw.get(line, []) if t] if line else []})
    return out


def _line_hit(term_: str, text: str) -> bool:
    return all(re.search(r"(?<![a-z0-9])" + re.escape(_fold(p)), text) for p in term_.split(" + ") if _fold(p))


def match(cands: list[dict[str, Any]], targets: list[dict[str, Any]] | None = None, *, slug: str | None = None,
          node: str | None = None, min_score: float = MIN_SCORE, per_paper: int = 2) -> list[dict[str, Any]]:
    """Rank nodes for each paper by shared words (rare words weigh more; a word in the paper's title and the
    node's title counts most, the titles of what is already hung there count a little, and at least one word
    must be the node's own; the project's research-line keywords add a little). Papers already hung on a
    node (same entity, or the same paper note) and papers dismissed for a map are left out there."""
    targets = _targets() if targets is None else targets
    if slug:
        targets = [x for x in targets if x["slug"] == slug]
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
        flat = _fold(c["title"] + " " + c["abstract"][:600])
        hits = []
        for x in targets:
            if c["key"] in x["keys"] or c["key"] in x["skip"] or (c.get("paper") and c["paper"] in x["papers"]):
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
            if any(_line_hit(t, flat) for t in x["line_terms"]):
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
                    "abstract": c["abstract"][:300], "matches": keep, "score": best})
    out.sort(key=lambda s: (-s["score"], s["date"] or ""))
    return out


def suggestions(slug: str | None = None, node: str | None = None, limit: int = MAX_SUGGESTIONS) -> list[dict[str, Any]]:
    """Papers likely to belong on a node, for the user to confirm (never attached by themselves)."""
    return match(candidates(), slug=slug, node=node)[:limit]


def match_text(title: str, text: str = "", *, key: str | None = None, limit: int = 6) -> list[dict[str, Any]]:
    """Nodes that one paper (the reader's open paper) most likely belongs to, best first."""
    cand = {"key": key or entity_for("paper", title=title) or "paper:x", "title": title, "abstract": text or "", "paper": None}
    hits = match([cand], min_score=2.0, per_paper=limit)
    return hits[0]["matches"] if hits else []


def paper_info(stem: str) -> dict[str, Any]:
    """What the reader's open paper looks like as a peer or as evidence: title, ids, entity, words to match."""
    from .. import library, reading, sources
    info = library.get_paper(stem)
    item = reading.find(reading.load(), stem)
    if item and info.get("external") and item.get("title"):  # no note yet: the reading list knows the title
        info["title"] = item["title"]
    text = re.sub(r"^---\n.*?\n---\n", "", info.get("note_markdown") or "", flags=re.S)
    if item and item.get("reason"):
        text = item["reason"] + "\n" + text
    ext = stem if library.ext_key(stem) else (sources.ext_key("doi", info["doi"]) if info.get("doi") else None)
    arxiv = info.get("arxiv") or None
    return {"stem": stem, "title": info.get("title") or stem, "arxiv": arxiv, "ext": ext,
            "note": None if info.get("external") else stem, "year": info.get("year"), "tags": info.get("tags") or [],
            "entity": entity_for("paper", title=info.get("title") or stem, arxiv=arxiv, ext=ext),
            "url": _arxiv_url(arxiv) if arxiv else None, "text": text[:1500]}


def attached(entity: str) -> list[dict[str, Any]]:
    """Where a paper is already hung (as a peer or as evidence), across maps."""
    return [h for h in project.entities(entity).get(entity, []) if h["kind"] in ("peer", "evidence")]


__all__ = ["PEER", "TYPES", "RELATIONS", "SOURCES", "GAP_TYPES", "DENSE", "SAME_DENSE", "entity_for", "stats",
           "analyze", "overview", "candidates", "match", "suggestions", "match_text", "paper_info", "dismiss",
           "dismissed", "attached"]

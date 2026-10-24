"""The followed feed over arXiv, OpenReview and the ACL Anthology: labels, per-source status, caching."""
from __future__ import annotations

import copy
import re
from datetime import date, datetime, time as dtime, timedelta, timezone

import pytest

from app import reading, sources, tracking

from .fakenet import fixture, fixture_json, net  # noqa: F401  (net is a fixture)

TODAY = date(2026, 10, 25)
ATOM_TITLE = "AtomRec: Evolving Atomic Memory for Agentic Recommendation"
VOL = "aclanthology.org/volumes/2026.acl-long.bib"


def ms(days_ago: int) -> int:
    return int(datetime.combine(TODAY - timedelta(days=days_ago), dtime(12), tzinfo=timezone.utc).timestamp() * 1000)


def atom(*days_ago: int) -> str:
    """The recorded arXiv answer with its entries re-dated to `days_ago` before TODAY, newest first."""
    stamps = iter(f"{(TODAY - timedelta(days=d)).isoformat()}T08:00:00Z" for d in days_ago)
    return re.sub(r"<published>[^<]*", lambda m: f"<published>{next(stamps)}", fixture("arxiv_search.xml"))


def or_notes(**days_ago: int) -> dict:
    """The recorded OpenReview answer, with each note id re-dated; ids not named are dropped."""
    notes = []
    for n in fixture_json("openreview_search.json")["notes"]:
        if n["id"] in days_ago:
            n = copy.deepcopy(n)
            n["cdate"], n["pdate"] = ms(days_ago[n["id"]]), None
            notes.append(n)
    return {"notes": notes, "count": len(notes)}


@pytest.fixture(autouse=True)
def fill_now(monkeypatch):
    monkeypatch.setattr(sources, "_spawn_fill", sources.acl_fill)  # the volumes download in the background; here, at once


def routes(net, *, arxiv_days=(5, 20, 60), openreview=None, acl=True):
    net.add("export.arxiv.org/api/query", atom(*arxiv_days))
    net.add("api2.openreview.net/notes/search", openreview if openreview is not None else or_notes(ljXCQmOXqj=10, mmIAp3cVS0=200))
    if acl:
        net.add(VOL, fixture("acl_volume.bib"))
    net.add("aclanthology.org/volumes/", "404", status=404)


def follow(kind="keyword", value="memory"):
    return tracking.add_follow(kind, value)


def feed(**kw):
    return tracking.feed(today=TODAY, **{"refresh": True, **kw})


def by_source(f):
    out: dict[str, list] = {}
    for p in f["papers"]:
        out.setdefault(p["source"], []).append(p)
    return out


# ---------- the three sources ----------

def test_every_paper_says_where_it_came_from(net):
    routes(net)
    follow()
    [f] = feed()
    papers = by_source(f)
    assert set(papers) == {"arxiv", "openreview", "acl"}
    assert [p["title"] for p in papers["arxiv"]][0] == ATOM_TITLE and papers["arxiv"][0]["arxiv"]
    assert papers["openreview"][0]["title"].startswith("Belief Memory") and papers["openreview"][0]["ext"] == "openreview:ljXCQmOXqj"
    assert papers["openreview"][0]["venue"] == "ICLR 2027 Conference Submission"
    assert papers["openreview"][0]["url"] == "https://openreview.net/forum?id=ljXCQmOXqj"
    assert {p["ext"] for p in papers["acl"]} == {"acl:2026.acl-long.7", "acl:2025.emnlp-main.42"}
    assert [(s["id"], s["ok"], s["error"]) for s in f["sources"]] == [("arxiv", True, None), ("openreview", True, None), ("acl", True, None)]
    assert f["error"] is None


def test_openreview_drops_old_submissions_and_notes_that_miss_a_keyword(net):
    routes(net, openreview=or_notes(ljXCQmOXqj=10, mmIAp3cVS0=200, AhFsKmuaCb=3, A5opaPyKt8=2))
    follow("keyword", "agent memory")
    [f] = feed()
    # 200 days old: out of the window; the drug-discovery and cyberattack notes are recent but are not about memory
    assert [p["ext"] for p in by_source(f)["openreview"]] == ["openreview:ljXCQmOXqj"]


def test_openreview_skips_dblp_imports(net):
    notes = or_notes(ljXCQmOXqj=10, mmIAp3cVS0=12)
    notes["notes"][1]["content"]["venueid"] = {"value": "dblp.org/journals/CORR/2026"}  # an arXiv mirror, dated by its import
    routes(net, openreview=notes)
    follow("keyword", "memory")
    [f] = feed()
    assert [p["ext"] for p in by_source(f)["openreview"]] == ["openreview:ljXCQmOXqj"]


def test_openreview_is_asked_for_the_followed_words(net):
    routes(net)
    follow("keyword", 'agent "memory consolidation"')
    feed()
    q = net.calls_to("api2.openreview.net/notes/search")[0]
    assert q["term"] == 'agent "memory consolidation"' and q["source"] == "forum" and q["limit"] == 100


def test_acl_papers_are_dated_by_the_month_of_their_event(net):
    routes(net)
    follow()
    [f] = feed()
    acl = {p["ext"]: p for p in by_source(f)["acl"]}
    p = acl["acl:2026.acl-long.7"]
    assert p["published"] == "2026-07-01" and p["month_only"] and p["url"] == "https://aclanthology.org/2026.acl-long.7/"
    assert p["authors"] == ["Ada Example", "Ben Sample"] and p["venue"] == "ACL 2026"
    assert "acl:2024.acl-long.99" not in acl  # the 2024 paper in the same recorded volume is outside the year-long window


def test_acl_matches_the_title_and_the_marquez_style_names(net):
    routes(net)
    follow("keyword", "E-commerce script")
    [f] = feed()
    assert [p["title"] for p in by_source(f)["acl"]] == ["EcomScriptBench: A Benchmark for E-commerce Script Planning"]
    assert by_source(f)["acl"][0]["authors"][1] == "Lluís Màrquez"


def test_acl_ignores_the_days_window_but_arxiv_does_not(net):
    routes(net, arxiv_days=(5, 20, 60))
    follow()
    [f] = feed(days=7)
    papers = by_source(f)
    assert len(papers["arxiv"]) == 1 and len(papers["acl"]) == 2


def test_a_person_matches_authors_on_openreview_and_acl(net):
    routes(net, openreview=or_notes(mmIAp3cVS0=3, ljXCQmOXqj=3))
    follow("person", "Ada Example")
    [f] = feed()
    papers = by_source(f)
    assert [p["ext"] for p in papers["openreview"]] == ["openreview:mmIAp3cVS0"]  # the note with hidden authors is not guessed at
    assert {p["ext"] for p in papers["acl"]} == {"acl:2026.acl-long.7", "acl:2025.emnlp-main.42"}  # "Ada Example" and "A. Example"
    assert net.calls_to("api2.openreview.net/notes/search")[0]["term"] == '"Ada Example"'


# ---------- one source never takes the others down ----------

def test_openreview_asking_for_a_human_check_leaves_the_others_standing(net):
    routes(net)
    net.routes.insert(0, ("api2.openreview.net/notes/search", {}, fixture_json("openreview_challenge.json"), 403))
    follow()
    [f] = feed()
    st = {s["id"]: s for s in f["sources"]}
    assert not st["openreview"]["ok"] and "人机验证" in st["openreview"]["error"] and st["openreview"]["n"] == 0
    assert st["arxiv"]["ok"] and st["acl"]["ok"]
    assert set(by_source(f)) == {"arxiv", "acl"} and "OpenReview" in f["error"]


def test_arxiv_down_does_not_hide_openreview_and_acl(net):
    routes(net)
    net.routes = [r for r in net.routes if "arxiv" not in r[0]]
    follow()
    [f] = feed()
    st = {s["id"]: s for s in f["sources"]}
    assert not st["arxiv"]["ok"] and "arXiv" in st["arxiv"]["error"] and st["openreview"]["ok"]
    assert set(by_source(f)) == {"openreview", "acl"}


def test_a_malformed_answer_is_just_that_sources_status(net):
    routes(net)
    net.routes.insert(0, ("api2.openreview.net/notes/search", {}, "<html>oops</html>", 200))
    follow()
    [f] = feed()
    assert not {s["id"]: s for s in f["sources"]}["openreview"]["ok"] and "arxiv" in by_source(f)


def test_a_failed_refresh_serves_the_last_answer_marked_stale(net):
    routes(net)
    follow()
    feed()
    net.routes.insert(0, ("api2.openreview.net/notes/search", {}, {}, 500))
    [f] = feed()
    st = {s["id"]: s for s in f["sources"]}["openreview"]
    assert not st["ok"] and st["stale"] and "500" in st["error"]
    assert [p["ext"] for p in by_source(f)["openreview"]] == ["openreview:ljXCQmOXqj"]


def test_a_failure_with_nothing_cached_is_not_stale(net):
    routes(net)
    net.routes.insert(0, ("api2.openreview.net/notes/search", {}, {}, 500))
    follow()
    [f] = feed()
    assert not {s["id"]: s for s in f["sources"]}["openreview"]["stale"]


# ---------- caching and pacing ----------

def test_each_source_is_cached_and_refresh_asks_again(net):
    routes(net)
    follow()
    feed()
    n = len(net.calls)
    tracking.feed(today=TODAY)  # within the cache window: no arXiv, no OpenReview, no volume downloads
    assert len(net.calls) == n
    feed(refresh=True)
    assert len(net.calls_to("export.arxiv.org")) == 2 and len(net.calls_to("api2.openreview.net")) == 2


def test_the_old_arxiv_cache_file_name_is_kept(net):
    routes(net)
    f = follow()
    feed()
    assert (tracking.FEED_CACHE / f"{f['id']}.json").exists() and (tracking.FEED_CACHE / f"{f['id']}.openreview.json").exists()


def test_volumes_are_fetched_once_for_all_follows(net):
    routes(net)
    follow("keyword", "memory")
    follow("person", "Ada Example")
    feed()
    assert len(net.calls_to(VOL)) == 1


def test_acl_volumes_download_in_the_background_and_the_feed_says_so(net, monkeypatch):
    routes(net)
    asked = []
    monkeypatch.setattr(sources, "_spawn_fill", lambda vols: asked.append(vols))
    follow()
    [f] = feed()
    st = {s["id"]: s for s in f["sources"]}["acl"]
    total = len(sources.acl_volumes(TODAY))
    assert st["ok"] and st["loading"] and st["progress"] == {"done": 0, "total": total} and "acl" not in by_source(f)
    assert len(asked[0]) == total and net.calls_to("aclanthology.org") == []
    sources.acl_fill(asked[0])  # the background thread finishes
    [f] = feed()
    st = {s["id"]: s for s in f["sources"]}["acl"]
    assert not st["loading"] and len(by_source(f)["acl"]) == 2


def test_volumes_not_yet_published_are_remembered_for_a_few_days(net):
    routes(net)
    follow()
    feed()
    first = len(net.calls_to("aclanthology.org/volumes/"))
    feed(refresh=True)
    assert len(net.calls_to("aclanthology.org/volumes/")) == first  # 404s are cached, downloaded volumes too


def test_a_volume_that_changes_on_disk_is_read_again(net):
    net.add(VOL, fixture("acl_volume.bib"))
    sources.fetch_volume("2026.acl-long")
    assert len(sources._read_volume("2026.acl-long")["papers"]) == 4
    net.routes.clear()
    net.add(VOL, "@inproceedings{x,\n    title = \"Only One\",\n    month = jan,\n    year = \"2026\",\n    url = \"https://aclanthology.org/2026.acl-long.1/\"\n}\n")
    sources.fetch_volume("2026.acl-long")
    assert [p["title"] for p in sources._read_volume("2026.acl-long")["papers"]] == ["Only One"]


def test_only_events_inside_the_window_are_downloaded():
    ids = [v for v, _ in sources.acl_volumes(TODAY)]
    assert "2026.acl-long" in ids and "2025.emnlp-main" in ids and "2026.findings-eacl" in ids
    assert "2025.acl-long" not in ids and "2025.naacl-long" not in ids  # July and May 2025 are older than a year
    assert dict(sources.acl_volumes(TODAY))["2026.findings-acl"] == "ACL 2026 Findings"


# ---------- merging and marking ----------

def test_the_same_paper_on_arxiv_and_openreview_shows_once(net):
    routes(net)
    note = copy.deepcopy(fixture_json("openreview_search.json")["notes"][1])  # Belief Memory ...
    note["content"]["title"]["value"] = ATOM_TITLE
    note["cdate"], note["pdate"] = ms(2), None
    net.routes.insert(0, ("api2.openreview.net/notes/search", {}, {"notes": [note]}, 200))
    follow()
    [f] = feed()
    atom_rec = [p for p in f["papers"] if p["title"] == ATOM_TITLE]
    assert len(atom_rec) == 1 and atom_rec[0]["source"] == "arxiv"
    assert atom_rec[0]["also"] == [{"source": "openreview", "ext": "openreview:ljXCQmOXqj", "venue": "ICLR 2027 Conference Submission",
                                    "url": "https://openreview.net/forum?id=ljXCQmOXqj"}]


def test_papers_are_newest_first_and_each_source_is_capped_on_its_own(net):
    routes(net)
    follow()
    [f] = feed(per_follow=1)
    assert {s: len(ps) for s, ps in by_source(f).items()} == {"arxiv": 1, "openreview": 1, "acl": 1}
    [f] = feed(per_follow=8)
    dates = [p["published"] for p in f["papers"]]
    assert len(dates) == 5 and dates == sorted(dates, reverse=True)
    assert {s["id"]: s["n"] for s in f["sources"]} == {"arxiv": 2, "openreview": 1, "acl": 2}


def test_a_source_status_counts_what_was_in_the_window_not_what_fits(net):
    routes(net)
    follow()
    [f] = feed(per_follow=1)
    assert {s["id"]: s["n"] for s in f["sources"]} == {"arxiv": 2, "openreview": 1, "acl": 2}


def test_feed_marks_what_is_already_in_the_reading_list_or_the_vault(net, make_note):
    routes(net)
    reading.push(ext="acl:2026.acl-long.7", title="Trustworthy Memory Consolidation", reason="x")
    make_note("Belief", source="https://openreview.net/forum?id=ljXCQmOXqj")
    reading.push(arxiv="2609.04882", title=ATOM_TITLE, reason="x")
    follow()
    [f] = feed()
    got = {p.get("ext") or p["arxiv"]: p for p in f["papers"]}
    assert got["acl:2026.acl-long.7"]["listed"] == "inbox" and got["acl:2025.emnlp-main.42"]["listed"] is None
    assert got["openreview:ljXCQmOXqj"]["note"] == "Belief"
    assert got["2609.04882"]["listed"] == "inbox"


def test_without_follows_nothing_is_asked(net):
    assert feed() == [] and net.calls == []

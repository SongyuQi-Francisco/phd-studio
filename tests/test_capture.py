"""The capture box: links and ids go straight into the reading inbox, titles come back as matches to pick."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import reading, track_api, tracking

from .fakenet import fixture, fixture_json, net  # noqa: F401  (net is a fixture)

DOI = "10.1145/3626772.3657833"


@pytest.fixture(autouse=True)
def arxiv_titles(monkeypatch):
    monkeypatch.setattr(reading, "fetch_arxiv_title", lambda aid: f"Title of {aid}")


def route_doi(net, doi=DOI):
    net.add(f"api.crossref.org/works/{doi}", fixture_json("crossref_work.json"))
    net.add(f"api.openalex.org/works/doi:{doi}", fixture_json("openalex_doi_plain.json"))


def only_item():
    items = reading.load()
    assert len(items) == 1
    return items[0]


# ---------- identifiers ----------

def test_arxiv_link_still_goes_straight_in():
    r = tracking.capture("https://arxiv.org/abs/2606.17328v2", "worth a read")
    assert r["ok"] and r["result"] == "added"
    item = only_item()
    assert item["arxiv"] == "2606.17328" and item["reason"] == "worth a read" and item["pushed_by"] == "追踪 · 捕捉"
    assert item["source"] == "self" and item["status"] == "inbox"


def test_a_bare_doi_is_not_mistaken_for_an_arxiv_id(net):
    route_doi(net)
    r = tracking.capture(DOI, "")
    assert r["ok"]
    item = only_item()
    assert item.get("arxiv") is None and item["ext"] == "doi:10.1145|3626772.3657833"
    assert item["title"].startswith("M 2 -RAAP") and item["url"] == "https://doi.org/" + DOI
    assert item["reason"] == "手动捕捉" and reading.key_of(item) == item["ext"]
    assert r["resolved"]["year"] == 2024


def test_doi_link_and_bare_doi_dedupe(net):
    route_doi(net)
    tracking.capture(f"https://doi.org/{DOI}", "first")
    r = tracking.capture(f"doi:{DOI}", "second")
    assert "already listed" in r["result"] and len(reading.load()) == 1
    assert reading.load()[0]["reason"] == "first | second"


def test_doi_of_a_paper_with_a_preprint_dedupes_against_the_arxiv_item(net):
    reading.push(arxiv="2609.29973", title="Learning Better Reasoning for Generative Recommendation with Examples", reason="seen")
    net.add("api.crossref.org/works/10.9999/example.2025.7", fixture_json("crossref_work.json"))
    net.add("api.openalex.org/works/doi:10.9999/example.2025.7", fixture_json("openalex_doi_arxiv.json"))
    r = tracking.capture("10.9999/example.2025.7", "via the journal")
    assert "already listed" in r["result"]
    item = only_item()
    assert item["arxiv"] == "2609.29973" and item["ext"] == "doi:10.9999|example.2025.7"  # one item, now known by both


def test_unknown_doi_is_reported_and_nothing_is_added(net):
    net.add("api.crossref.org/works/", {}, status=404)
    net.add("https://doi.org/", "no such doi", status=404)
    r = tracking.capture("10.1234/typo", "x")
    assert not r["ok"] and "找不到" in r["error"] and reading.load() == []


def test_openreview_link_is_read_and_listed(net):
    net.add("api2.openreview.net/notes", fixture_json("openreview_note.json"))
    r = tracking.capture("https://openreview.net/forum?id=mmIAp3cVS0", "memory for agents", "T2", ["Project A"])
    assert r["ok"] and r["resolved"]["venue"] == "NeurIPS 2025 spotlight"
    item = only_item()
    assert item["ext"] == "openreview:mmIAp3cVS0" and item["title"].startswith("G-Memory")
    assert item["url"] == "https://openreview.net/forum?id=mmIAp3cVS0" and item["lines"] == ["Project A"]
    assert item["tier"] == "T2" and item["status"] == "queued"
    again = tracking.capture("https://openreview.net/pdf?id=mmIAp3cVS0", "again")
    assert "already listed" in again["result"] and len(reading.load()) == 1


def test_openreview_behind_a_human_check_is_listed_by_id_with_a_warning(net):
    net.add("api2.openreview.net/notes", fixture_json("openreview_challenge.json"), status=403)
    r = tracking.capture("https://openreview.net/forum?id=mmIAp3cVS0", "later")
    assert r["ok"] and "人机验证" in r["warning"]
    item = only_item()
    assert item["ext"] == "openreview:mmIAp3cVS0" and not item.get("title")
    items = reading.load()
    summary = reading.summary(items)  # fills in what the list shows for an item without a title
    assert summary["inbox"] == 1 and items[0]["title"] == "openreview:mmIAp3cVS0" and items[0]["key"] == items[0]["ext"]


def test_openreview_unknown_id_is_not_listed(net):
    net.add("api2.openreview.net/notes", {"notes": []})
    r = tracking.capture("https://openreview.net/forum?id=nope12345", "x")
    assert not r["ok"] and "OpenReview" in r["error"] and reading.load() == []


def test_acl_anthology_link_and_id(net):
    net.add("aclanthology.org/2024.acl-long.1.bib", fixture("acl_paper.bib"))
    r = tracking.capture("https://aclanthology.org/2024.acl-long.1/", "ACL paper")
    assert r["ok"]
    item = only_item()
    assert item["ext"] == "acl:2024.acl-long.1" and item["title"].startswith("Quantized Side Tuning")
    assert "already listed" in tracking.capture("2024.acl-long.1", "again")["result"]


def test_acl_doi_is_the_anthology_paper(net):
    net.add("aclanthology.org/2024.acl-long.1.bib", fixture("acl_paper.bib"))
    tracking.capture("https://doi.org/10.18653/v1/2024.acl-long.1", "x")
    assert only_item()["ext"] == "acl:2024.acl-long.1"


def test_a_source_that_is_down_still_lists_a_link_by_its_id(net):
    r = tracking.capture("2024.acl-long.1", "x")  # nothing routed: connection error
    assert r["ok"] and "ACL Anthology" in r["warning"]
    assert only_item()["ext"] == "acl:2024.acl-long.1"


# ---------- titles ----------

def route_search(net):
    net.add("export.arxiv.org/api/query", fixture("arxiv_search.xml"))
    net.add("api.crossref.org/works", fixture_json("crossref_search.json"))
    net.add("api.openalex.org/works", fixture_json("openalex_search.json"))


def test_a_title_comes_back_as_matches_and_nothing_is_listed(net):
    route_search(net)
    r = tracking.capture("Generative Recommendation with Semantic IDs: A Practitioner's Handbook", "x")
    assert not r["ok"] and not r.get("unresolved")
    top = r["candidates"][0]
    assert top["year"] == 2025 and top["venue"] and top["key"] == "doi:10.1145|3746252.3761612"
    assert {s["id"] for s in r["sources"]} == {"arxiv", "crossref", "openalex"}
    assert reading.load() == []


def test_picking_a_match_lists_it_with_the_reason(net):
    route_search(net)
    cand = tracking.capture("Generative Recommendation with Semantic IDs: A Practitioner's Handbook", "")["candidates"][0]
    r = tracking.capture_pick(cand, "the benchmark I need", "T1", ["Project B"])
    assert r["ok"] and r["result"] == "added"
    item = only_item()
    assert item["ext"] == "doi:10.1145|3746252.3761612" and item["title"] == cand["title"]
    assert item["reason"] == "the benchmark I need" and item["tier"] == "T1" and item["lines"] == ["Project B"]


def test_picking_an_arxiv_match_uses_the_arxiv_key_without_asking_arxiv_again(net, monkeypatch):
    monkeypatch.setattr(reading, "fetch_arxiv_title", lambda aid: pytest.fail("title is already known"))
    route_search(net)
    cand = tracking.capture("Unleash the Potential of Long Semantic IDs for Generative Recommendation", "")["candidates"][0]
    tracking.capture_pick(cand, "x")
    item = only_item()
    assert item["arxiv"] == "2602.13573" and item["title"].startswith("Unleash")


def test_the_same_paper_through_another_door_is_one_item(net):
    reading.push(arxiv="2602.13573", title="Unleash the Potential of Long Semantic IDs for Generative Recommendation", reason="a")
    r = tracking.capture_pick({"doi": "10.1234/journal.5", "title": "Unleash the Potential of Long Semantic IDs for Generative Recommendation"}, "b")
    assert "already listed" in r["result"] and len(reading.load()) == 1


def test_a_title_with_every_source_down_says_so(net):
    r = tracking.capture("Generative Recommendation with Semantic IDs", "x")
    assert not r["ok"] and "都没连上" in r["error"] and len(r["sources"]) == 3


def test_a_title_nobody_knows_goes_to_the_assistant(net):
    route_search(net)
    assert tracking.capture("quantum chromodynamics on a lattice", "x")["unresolved"]


@pytest.mark.parametrize("text", ["", "   ", "short", "https://example.com/some/paper.pdf"])
def test_non_titles_go_to_the_assistant_without_searching(net, text):
    assert tracking.capture(text, "x") == {"ok": False, "unresolved": True}
    assert net.calls == []


# ---------- the pick route ----------

@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(track_api.router)
    return TestClient(app)


def test_a_feed_card_names_itself_by_key(client):
    r = client.post("/api/track/capture/pick", json={"candidate": {"key": "acl:2026.acl-long.7", "title": "Trustworthy Memory",
                                                                    "url": "https://aclanthology.org/2026.acl-long.7/"}, "reason": "feed"})
    assert r.status_code == 200 and only_item()["ext"] == "acl:2026.acl-long.7"
    r = client.post("/api/track/capture/pick", json={"candidate": {"key": "arxiv:2609.04882", "title": "AtomRec"}, "reason": "feed"})
    assert r.status_code == 200 and reading.load()[1]["arxiv"] == "2609.04882"
    assert client.post("/api/track/capture/pick", json={"candidate": {"key": "doi:10.1145/3626772.3657833"}}).status_code == 400


def test_pick_route_lists_the_candidate(client):
    r = client.post("/api/track/capture/pick", json={"candidate": {"openreview": "abcdef12", "title": "A Paper", "venue": "ICLR 2027",
                                                                    "url": "https://openreview.net/forum?id=abcdef12"},
                                                      "reason": "from the feed", "lines": ["Project A"]})
    assert r.status_code == 200 and r.json()["item"]["ext"] == "openreview:abcdef12"
    assert only_item()["url"] == "https://openreview.net/forum?id=abcdef12"


@pytest.mark.parametrize("body", [{}, {"candidate": "x"}, {"candidate": {}}, {"candidate": {"arxiv": "../../x"}},
                                  {"candidate": {"doi": "../x", "title": "t"}}, {"candidate": {"openreview": "a/b"}}])
def test_pick_route_refuses_what_is_not_a_paper_key(client, body):
    assert client.post("/api/track/capture/pick", json=body).status_code == 400
    assert reading.load() == []


def test_pick_route_only_keeps_https_links(client):
    client.post("/api/track/capture/pick", json={"candidate": {"acl": "2024.acl-long.1", "url": "javascript:alert(1)"}})
    assert not only_item().get("url")


# ---------- the same paper through two doors, in either order ----------

OR_TITLE = "G-Memory: Tracing Hierarchical Memory for Multi-Agent Systems"


def test_openreview_first_then_the_arxiv_version_is_one_item(net, monkeypatch):
    net.add("api2.openreview.net/notes", fixture_json("openreview_note.json"))
    tracking.capture("https://openreview.net/forum?id=mmIAp3cVS0", "from OpenReview")
    monkeypatch.setattr(reading, "fetch_arxiv_title", lambda aid: OR_TITLE)
    r = tracking.capture("https://arxiv.org/abs/2506.07398", "from arXiv")
    assert "already listed" in r["result"] and len(reading.load()) == 1
    assert reading.load()[0]["reason"] == "from OpenReview | from arXiv"


def test_the_arxiv_version_added_from_a_feed_card_is_recognised_too(net, monkeypatch):
    net.add("api2.openreview.net/notes", fixture_json("openreview_note.json"))
    tracking.capture("https://openreview.net/forum?id=mmIAp3cVS0", "x")
    monkeypatch.setattr(reading, "fetch_arxiv_title", lambda aid: OR_TITLE.upper())
    _, what = reading.push(arxiv="2506.07398", reason="card")  # what POST /api/reading does for an arXiv card
    assert "already listed" in what and len(reading.load()) == 1


def test_arxiv_first_then_openreview_is_one_item_as_before(net, monkeypatch):
    monkeypatch.setattr(reading, "fetch_arxiv_title", lambda aid: OR_TITLE)
    tracking.capture("2506.07398", "x")
    net.add("api2.openreview.net/notes", fixture_json("openreview_note.json"))
    r = tracking.capture("https://openreview.net/forum?id=mmIAp3cVS0", "y")
    assert "already listed" in r["result"] and len(reading.load()) == 1


def test_a_paper_already_listed_does_not_ask_arxiv_for_its_title(monkeypatch):
    reading.push(arxiv="2506.07398", title="Known", reason="x")
    monkeypatch.setattr(reading, "fetch_arxiv_title", lambda aid: pytest.fail("no need to ask"))
    assert "already listed" in reading.push(arxiv="2506.07398", reason="again")[1]


def test_the_title_is_asked_before_the_list_is_locked(monkeypatch):
    import fcntl

    def ask(aid):
        with reading._LOCK.open("w") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if push holds the lock across the network call
            fcntl.flock(f, fcntl.LOCK_UN)
        return "A Title Long Enough To Compare"
    monkeypatch.setattr(reading, "fetch_arxiv_title", ask)
    assert reading.push(arxiv="2506.07398", reason="x")[1] == "added" and reading.load()[0]["title"] == "A Title Long Enough To Compare"


def test_feed_cards_of_a_listed_paper_show_it_as_listed_under_either_key(net, monkeypatch):
    # an arXiv card for a paper that is in the list under its OpenReview key
    net.add("api2.openreview.net/notes", fixture_json("openreview_note.json"))
    tracking.capture("https://openreview.net/forum?id=mmIAp3cVS0", "x")
    from . import test_follow_sources as tf
    net.add("export.arxiv.org/api/query", tf.atom(5, 20, 60).replace("AtomRec: Evolving Atomic Memory for Agentic Recommendation", OR_TITLE))
    net.add("api2.openreview.net/notes/search", {"notes": []})
    net.add("aclanthology.org/volumes/", "404", status=404)
    tracking.add_follow("keyword", "memory")
    [f] = tracking.feed(today=tf.TODAY, refresh=True)
    card = [p for p in f["papers"] if p["title"] == OR_TITLE][0]
    assert card["source"] == "arxiv" and card["listed"] == "inbox"


# ---------- a DOI that resolves to an arXiv paper still finds its note ----------

def write_note(stem, front):
    from app import config, library
    (config.PAPERS_DIR / f"{stem}.md").write_text(f"---\ntype: paper\n{front}\n---\n- Title: A note\n", encoding="utf-8")
    library._ext_index.cache_clear()
    library._arxiv_index.cache_clear()


def route_preprint_doi(net):
    net.add("api.crossref.org/works/10.9999/example.2025.7", fixture_json("crossref_work.json"))
    net.add("api.openalex.org/works/doi:10.9999/example.2025.7", fixture_json("openalex_doi_arxiv.json"))


def test_a_doi_with_a_preprint_links_to_the_note_that_has_that_doi(net):
    write_note("Gamma", "doi: 10.9999/example.2025.7")
    route_preprint_doi(net)
    tracking.capture("10.9999/example.2025.7", "x")
    item = only_item()
    assert item["paper"] == "Gamma" and item["arxiv"] == "2609.29973" and item["ext"] == "doi:10.9999|example.2025.7"
    assert reading.key_of(item) == "Gamma"


def test_a_picked_match_with_an_arxiv_id_and_a_doi_links_to_the_note_too(net):
    write_note("Gamma", "doi: 10.9999/example.2025.7")
    tracking.capture_pick({"arxiv": "2609.29973", "doi": "10.9999/example.2025.7", "title": "X Paper"}, "x")
    assert only_item()["paper"] == "Gamma"


def test_a_doi_item_and_later_the_same_paper_by_arxiv_and_doi_are_one_item(net):
    route_preprint_doi(net)
    net.routes.insert(0, ("api.openalex.org/works/doi:10.9999/example.2025.7", {}, {}, 500))  # no preprint known yet
    tracking.capture("10.9999/example.2025.7", "first")
    assert only_item()["ext"] == "doi:10.9999|example.2025.7" and not only_item().get("arxiv")
    r = tracking.capture_pick({"arxiv": "2609.29973", "doi": "10.9999/example.2025.7", "title": "Same Paper Again"}, "second")
    assert "already listed" in r["result"] and len(reading.load()) == 1


def test_push_keeps_the_ext_key_on_an_existing_arxiv_item():
    reading.push(arxiv="2609.29973", title="Some Title Long Enough", reason="a")
    reading.push(arxiv="2609.29973", ext="doi:10.9999|example.2025.7", reason="b")
    item = only_item()
    assert item["ext"] == "doi:10.9999|example.2025.7" and reading.key_of(item) == "arxiv:2609.29973"


# ---------- a DOI added from a card is classified like a pasted one ----------

def test_a_card_doi_of_an_acl_paper_is_listed_as_the_acl_paper():
    tracking.capture_pick({"doi": "10.18653/v1/2024.acl-long.1", "title": "Quantized Side Tuning", "url": "https://doi.org/10.18653/v1/2024.acl-long.1"}, "x")
    assert only_item()["ext"] == "acl:2024.acl-long.1"


def test_a_card_doi_of_an_arxiv_doi_is_listed_as_the_arxiv_paper():
    tracking.capture_pick({"doi": "10.48550/arxiv.2406.12345", "title": "An arXiv Paper"}, "x")
    item = only_item()
    assert item["arxiv"] == "2406.12345" and not item.get("ext")


def test_a_card_doi_with_a_preprint_copy_is_listed_by_the_arxiv_key(net):
    net.add("api.openalex.org/works/doi:10.9999/example.2025.7", fixture_json("openalex_doi_arxiv.json"))
    tracking.capture_pick({"doi": "10.9999/example.2025.7", "title": "A Journal Paper"}, "x")
    item = only_item()
    assert item["arxiv"] == "2609.29973" and item["ext"] == "doi:10.9999|example.2025.7"


def test_a_card_doi_stays_a_doi_when_openalex_cannot_be_asked(net):
    tracking.capture_pick({"doi": "10.9999/example.2025.7", "title": "A Journal Paper"}, "x")
    assert only_item()["ext"] == "doi:10.9999|example.2025.7"

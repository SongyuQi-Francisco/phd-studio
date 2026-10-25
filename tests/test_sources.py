"""Identifier parsing, BibTeX, matching and the source adapters (recorded responses, no network)."""
from __future__ import annotations

import httpx
import pytest

from app import sources

from .fakenet import fixture, fixture_json, net  # noqa: F401  (net is a fixture)

# ---------- classify: what a pasted string names ----------

CLASSIFY = [
    ("https://openreview.net/forum?id=mmIAp3cVS0", ("openreview", "mmIAp3cVS0")),
    ("https://openreview.net/forum?id=mmIAp3cVS0&noteId=abc123", ("openreview", "mmIAp3cVS0")),
    ("https://openreview.net/pdf?id=-wcm_3WJt4", ("openreview", "-wcm_3WJt4")),
    ("https://aclanthology.org/2024.acl-long.1/", ("acl", "2024.acl-long.1")),
    ("https://aclanthology.org/2024.acl-long.1.pdf", ("acl", "2024.acl-long.1")),
    ("https://aclanthology.org/P19-1001.pdf", ("acl", "P19-1001")),
    ("2024.findings-acl.5", ("acl", "2024.findings-acl.5")),
    ("ACL: 2020.emnlp-main.5", ("acl", "2020.emnlp-main.5")),
    ("p19-1001", ("acl", "P19-1001")),
    ("https://doi.org/10.1145/3626772.3657833", ("doi", "10.1145/3626772.3657833")),
    ("https://dl.acm.org/doi/10.1145/3626772.3657833", ("doi", "10.1145/3626772.3657833")),
    ("10.1145/3626772.3657833", ("doi", "10.1145/3626772.3657833")),
    ("doi:10.1145/3626772.3657833.", ("doi", "10.1145/3626772.3657833")),
    ("(DOI: 10.1007/978-3-031-56027-9_1)", ("doi", "10.1007/978-3-031-56027-9_1")),
    ("https://doi.org/10.1145%2F3626772.3657833", ("doi", "10.1145/3626772.3657833")),
    ("10.48550/arXiv.2406.12345", ("arxiv", "2406.12345")),                  # an arXiv DOI is the arXiv paper
    ("doi:10.18653/v1/2024.acl-long.1", ("acl", "2024.acl-long.1")),         # so is an ACL Anthology DOI
    ("https://arxiv.org/abs/2406.12345v2", ("arxiv", "2406.12345")),
    ("arXiv:2406.12345", ("arxiv", "2406.12345")),
    ("2406.12345", ("arxiv", "2406.12345")),
]


@pytest.mark.parametrize("text, expected", CLASSIFY)
def test_classify_names_what_was_pasted(text, expected):
    assert sources.classify(text) == expected


@pytest.mark.parametrize("text", [
    "Attention Is All You Need", "Generative recommendation with semantic IDs", "https://example.com/paper.pdf",
    "2024.05.123", "", "   ", "version 1234.5 of the thing",
])
def test_classify_leaves_titles_and_unknown_links_alone(text):
    assert sources.classify(text) is None


def test_the_digits_of_a_doi_are_not_an_arxiv_id():
    assert sources.classify("10.1145/3626772.3657833") == ("doi", "10.1145/3626772.3657833")
    assert sources.classify("https://dl.acm.org/doi/10.1145/3626772.3657833")[0] == "doi"


def test_a_doi_with_characters_the_keys_cannot_carry_is_not_taken():
    assert sources.classify("10.1002/(SICI)1097-4571(199806)49:8<693::AID-ASI4>3.0.CO;2-O") is None


def test_looks_like_url():
    assert sources.looks_like_url(" https://example.com/x") and sources.looks_like_url("www.example.com")
    assert not sources.looks_like_url("Attention is all you need")


# ---------- external keys ----------

def test_doi_keys_carry_no_slash_so_they_fit_in_a_url_path():
    key = sources.ext_key("doi", "https://doi.org/10.1145/3626772.3657833")
    assert key == "doi:10.1145|3626772.3657833" and "/" not in key
    assert sources.ext_parts(key) == ("doi", "10.1145/3626772.3657833")


@pytest.mark.parametrize("key, expected", [
    ("openreview:mmIAp3cVS0", ("openreview", "mmIAp3cVS0")),
    ("openreview:-wcm_3WJt4", ("openreview", "-wcm_3WJt4")),
    ("acl:2024.acl-long.1", ("acl", "2024.acl-long.1")),
    ("acl:P19-1001", ("acl", "P19-1001")),
    ("doi:10.1145|3626772.3657833", ("doi", "10.1145/3626772.3657833")),
])
def test_ext_parts_accepts_the_three_shapes(key, expected):
    assert sources.ext_parts(key) == expected


@pytest.mark.parametrize("key", [
    "", "doi:", "doi:10.1145/3626772", "doi:../x", "doi:10.1145|../../x ", "openreview:", "openreview:a/b", "openreview:ab",
    "acl:2024", "acl:../x", "acl:2024.acl-long", "arxiv:2606.17328", "OPENREVIEW:abcdef", "x:openreview:abcdef", "openreview:abcdef\n",
])
def test_ext_parts_refuses_everything_else(key):
    assert sources.ext_parts(key) is None


def test_ext_from_url_reads_the_links_a_note_keeps_in_its_source():
    assert sources.ext_from_url("https://openreview.net/forum?id=abc123XYZ") == "openreview:abc123XYZ"
    assert sources.ext_from_url("https://aclanthology.org/2024.acl-long.1/") == "acl:2024.acl-long.1"
    assert sources.ext_from_url("https://doi.org/10.1145/3626772.3657833") == "doi:10.1145|3626772.3657833"
    assert sources.ext_from_url("https://example.com/x") is None and sources.ext_from_url("") is None


def test_candidate_key_prefers_arxiv_then_openreview_acl_doi_and_rejects_bad_ids():
    assert sources.candidate_key({"arxiv": "2406.12345", "doi": "10.1/x"}) == "arxiv:2406.12345"
    assert sources.candidate_key({"openreview": "abcdef", "doi": "10.1145/3626772"}) == "openreview:abcdef"
    assert sources.candidate_key({"doi": "10.1145/3626772.3657833"}) == "doi:10.1145|3626772.3657833"
    assert sources.candidate_key({"arxiv": "../../etc"}) is None
    assert sources.candidate_key({"doi": "../x"}) is None and sources.candidate_key({}) is None


# ---------- BibTeX ----------

def test_parse_bib_reads_entries_fields_and_macros():
    rec = sources.parse_bib(fixture("acl_paper.bib"))
    assert len(rec) == 1 and rec[0]["_type"] == "inproceedings"
    assert rec[0]["month"] == "aug" and rec[0]["year"] == "2024" and rec[0]["doi"] == "10.18653/v1/2024.acl-long.1"


def test_parse_bib_volume_has_a_proceedings_entry_and_latex_names():
    rec = sources.parse_bib(fixture("acl_volume.bib"))
    assert [r["_type"] for r in rec] == ["inproceedings", "proceedings", "inproceedings", "inproceedings", "inproceedings", "inproceedings"]
    assert sources._bib_authors(rec[2]["author"]) == ["Cleo Fixture", "Maía Fixtùre", "Dana Üller"]
    assert sources.unlatex(rec[2]["title"]) == "EcomScriptBench: A Benchmark for E-commerce Script Planning"


# ---------- matching ----------

def test_terms_keep_quoted_phrases_whole():
    assert sources.terms('agent "memory consolidation" LLM') == ["agent", "memory consolidation", "llm"]


def test_has_terms_needs_every_term_and_ignores_case_and_accents():
    assert sources.has_terms("Trustworthy Memory Consolidation for Agents", ["memory", "agents"])
    assert not sources.has_terms("Trustworthy Memory Consolidation", ["memory", "recommendation"])
    assert sources.has_terms("Fixtùre on memory", ["fixture"]) and not sources.has_terms("anything", [])


@pytest.mark.parametrize("text, wanted, hit", [
    ("Leveraging Retrieval for QA", ["rag"], False), ("Average Treatment Effects", ["rag"], False), ("Coverage of Benchmarks", ["rag"], False),
    ("RAG for Question Answering", ["rag"], True), ("Their First Result", ["ir"], False), ("IR Evaluation Revisited", ["ir"], True),
    ("World Models", ["rl"], False), ("RL for Agents", ["rl"], True), ("Recent Advances", ["rec"], False), ("Rec Sys at Scale", ["rec"], True),
    ("Agents and their Memories", ["agent", "memory"], True), ("Agent Systems", ["agent", "memory"], False),
    ("Memory Consolidation for Agents", ["memory consolidation"], True), ("Consolidation of Memory", ["memory consolidation"], False),
    ("A Memory of Consolidation", ["memory consolidation"], False), ("Multi-Agent Memory", ["multi agent"], True),
    ("Memory in Agents", sources.terms("agent memory"), True), ("Memory in Agents", sources.terms('"agent memory"'), False),
    ("Business Process", ["bus"], False), ("Class Imbalance", ["clas"], False),
])
def test_has_terms_matches_whole_words_not_substrings(text, wanted, hit):
    assert sources.has_terms(text, wanted) is hit


@pytest.mark.parametrize("followed, author, same", [
    ("Ada Example", "Ada Example", True), ("Ada Example", "ada  EXAMPLE", True), ("Ada Example", "A. Example", True),
    ("A. Example", "Ada Example", True), ("Ada Example", "Ben Example", False), ("Ada Example", "Ada Sample", False),
    ("Maía Fixtùre", "Maia Fixture", True), ("Example", "Ada Example", False), ("Example", "Example", True),
])
def test_same_person(followed, author, same):
    assert sources.same_person(followed, author) is same


# ---------- caching ----------

def test_cached_serves_a_stale_copy_when_the_source_fails():
    calls = []

    def load():
        calls.append(1)
        if len(calls) > 1:
            raise sources.SourceError("down")
        return {"v": 1}
    assert sources.cached("k", 0, load) == ({"v": 1}, False)
    assert sources.cached("k", 0, load) == ({"v": 1}, True)  # ttl 0: asks again, fails, serves the old copy


def test_cached_without_an_old_copy_lets_the_error_through():
    with pytest.raises(sources.SourceError):
        sources.cached("nothing", 60, lambda: (_ for _ in ()).throw(sources.SourceError("down")))


def test_cached_does_not_ask_again_within_the_ttl():
    calls = []
    sources.cached("k2", 3600, lambda: calls.append(1) or {"v": 1})
    sources.cached("k2", 3600, lambda: calls.append(1) or {"v": 2})
    assert len(calls) == 1


# ---------- adapters ----------

def test_openreview_meta_reads_the_note(net):
    net.add("api2.openreview.net/notes", fixture_json("openreview_note.json"))
    c = sources.resolve("openreview", "mmIAp3cVS0")
    assert c["title"].startswith("G-Memory") and c["openreview"] == "mmIAp3cVS0"
    assert c["venue"] == "NeurIPS 2025 spotlight" and c["year"] == 2025 and c["key"] == "openreview:mmIAp3cVS0"
    assert c["authors"] == ["Ada Example", "Ben Sample", "Cleo Fixture"]
    assert net.calls_to("api2.openreview.net/notes")[0] == {"id": "mmIAp3cVS0"}


def test_openreview_human_check_is_reported_not_worked_around(net):
    net.add("api2.openreview.net/notes", fixture_json("openreview_challenge.json"), status=403)
    with pytest.raises(sources.SourceError, match="人机验证"):
        sources.resolve("openreview", "mmIAp3cVS0")
    assert len(net.calls) == 1  # asked once; no retry with other headers


def test_openreview_empty_answer_is_not_found(net):
    net.add("api2.openreview.net/notes", {"notes": []})
    with pytest.raises(sources.NotFound):
        sources.resolve("openreview", "doesnotexist")


def test_resolved_papers_are_cached(net):
    net.add("api2.openreview.net/notes", fixture_json("openreview_note.json"))
    sources.resolve("openreview", "mmIAp3cVS0")
    sources.resolve("openreview", "mmIAp3cVS0")
    assert len(net.calls) == 1


def test_acl_meta_reads_the_bib(net):
    net.add("aclanthology.org/2024.acl-long.1.bib", fixture("acl_paper.bib"))
    c = sources.resolve("acl", "2024.acl-long.1")
    assert c["title"].startswith("Quantized Side Tuning") and c["authors"] == ["Ada Example", "Ben Sample", "Cleo Fixture"]
    assert c["year"] == 2024 and "Annual Meeting" in c["venue"] and c["doi"] == "10.18653/v1/2024.acl-long.1"
    assert c["key"] == "acl:2024.acl-long.1" and c["url"] == "https://aclanthology.org/2024.acl-long.1/"


def test_acl_front_matter_is_not_a_paper(net):
    net.add("aclanthology.org/2026.acl-long.0.bib", "@proceedings{x,\n    title = \"Proceedings\",\n    year = \"2026\"\n}\n")
    with pytest.raises(sources.NotFound):
        sources.resolve("acl", "2026.acl-long.0")


def test_doi_meta_reads_crossref(net):
    net.add("api.crossref.org/works/10.1145/3626772.3657833", fixture_json("crossref_work.json"))
    net.add("api.openalex.org/works/doi:10.1145/3626772.3657833", fixture_json("openalex_doi_plain.json"))
    c = sources.resolve("doi", "10.1145/3626772.3657833")
    assert c["title"].startswith("M 2 -RAAP") and c["year"] == 2024 and "SIGIR" in c["venue"]
    assert c["key"] == "doi:10.1145|3626772.3657833" and c["arxiv"] is None and c["authors"][0] == "Ada Example"


def test_a_doi_with_a_preprint_copy_gets_the_arxiv_key(net):
    net.add("api.crossref.org/works/10.9999/example.2025.7", fixture_json("crossref_work.json"))
    net.add("api.openalex.org/works/doi:10.9999/example.2025.7", fixture_json("openalex_doi_arxiv.json"))
    c = sources.resolve("doi", "10.9999/example.2025.7")
    assert c["arxiv"] == "2609.29973" and c["key"] == "arxiv:2609.29973"


def test_doi_meta_survives_openalex_being_down(net):
    net.add("api.crossref.org/works/10.1145/3626772.3657833", fixture_json("crossref_work.json"))
    assert sources.resolve("doi", "10.1145/3626772.3657833")["key"] == "doi:10.1145|3626772.3657833"


def test_doi_unknown_to_crossref_falls_back_to_content_negotiation(net):
    net.add("api.crossref.org/works/", {"message": "Resource not found."}, status=404)
    net.add("https://doi.org/10.5281/zenodo.123456", {"title": "A Dataset Paper", "author": [{"given": "Eve", "family": "Mockwood"}],
                                                      "issued": {"date-parts": [[2025, 3]]}, "container-title": "Zenodo"})
    c = sources.resolve("doi", "10.5281/zenodo.123456")
    assert (c["title"], c["year"], c["venue"], c["authors"]) == ("A Dataset Paper", 2025, "Zenodo", ["Eve Mockwood"])


def test_doi_unknown_everywhere_is_not_found(net):
    net.add("api.crossref.org/works/", {}, status=404)
    net.add("https://doi.org/", "not found", status=404)
    with pytest.raises(sources.NotFound):
        sources.resolve("doi", "10.1234/typo")


def test_doi_pdf_urls_come_from_openalex(net):
    net.add("api.openalex.org/works/doi:10.9999/example.2025.7", fixture_json("openalex_doi_arxiv.json"))
    assert sources.doi_pdf_urls("10.9999/example.2025.7") == ["https://arxiv.org/pdf/2609.29973"]


def test_doi_pdf_urls_is_empty_when_openalex_is_down(net):
    assert sources.doi_pdf_urls("10.9999/example.2025.7") == []


def test_rate_limit_message_has_no_url(net):
    net.add("api.crossref.org", {}, status=429)
    with pytest.raises(sources.SourceError) as e:
        sources.resolve("doi", "10.1145/3626772.3657833")
    assert "http" not in str(e.value) and "额度" in str(e.value)


# ---------- title search ----------

def _search_net(net):
    net.add("export.arxiv.org/api/query", fixture("arxiv_search.xml"))
    net.add("api.crossref.org/works", fixture_json("crossref_search.json"))
    net.add("api.openalex.org/works", fixture_json("openalex_search.json"))


def test_title_search_merges_the_same_paper_from_two_sources(net):
    _search_net(net)
    r = sources.search_title("Generative Recommendation with Semantic IDs: A Practitioner's Handbook")
    top = r["candidates"][0]
    assert top["title"].startswith("Generative Recommendation with Semantic IDs") and top["year"] == 2025
    assert top["doi"] == "10.1145/3746252.3761612" and top["key"] == "doi:10.1145|3746252.3761612"
    assert set(top["sources"]) == {"Crossref", "OpenAlex"} and "Knowledge Management" in top["venue"]
    assert [s["id"] for s in r["sources"]] == ["arxiv", "crossref", "openalex"] and all(s["ok"] for s in r["sources"])


def test_title_search_prefers_the_arxiv_key_when_a_preprint_exists(net):
    _search_net(net)
    r = sources.search_title("Unleash the Potential of Long Semantic IDs for Generative Recommendation")
    assert r["candidates"][0]["key"] == "arxiv:2602.13573" and r["candidates"][0]["url"] == "https://arxiv.org/abs/2602.13573"


def test_title_search_drops_peer_reviews_and_poor_matches(net):
    _search_net(net)
    r = sources.search_title("Generative Recommendation with Semantic IDs")
    assert all(c["doi"] != "10.5555/review.1" for c in r["candidates"])
    assert all(sources._score("Generative Recommendation with Semantic IDs", c) >= 0.35 for c in r["candidates"])
    assert not sources.search_title("quantum chromodynamics lattice")["candidates"]


def test_title_search_reports_the_source_that_failed_and_still_answers(net):
    net.add("api.crossref.org/works", {}, status=429)
    net.add("api.openalex.org/works", fixture_json("openalex_search.json"))  # arXiv is not routed: connection error
    r = sources.search_title("Generative Recommendation with Semantic IDs")
    by = {s["id"]: s for s in r["sources"]}
    assert not by["arxiv"]["ok"] and "arXiv" in by["arxiv"]["error"]
    assert not by["crossref"]["ok"] and "额度" in by["crossref"]["error"]
    assert by["openalex"]["ok"] and r["candidates"]


def test_title_search_with_every_source_down_says_so(net):
    r = sources.search_title("Generative Recommendation with Semantic IDs")
    assert r["candidates"] == [] and not any(s["ok"] for s in r["sources"])


def test_a_malformed_answer_is_one_failed_source_not_a_crash(net):
    _search_net(net)
    net.routes.insert(0, ("api.crossref.org/works", {}, "<html>maintenance</html>", 200))
    r = sources.search_title("Generative Recommendation with Semantic IDs")
    assert not {s["id"]: s for s in r["sources"]}["crossref"]["ok"] and r["candidates"]


def test_arxiv_title_search_drops_stopwords_and_retries_with_the_first_words(net):
    empty = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    net.add("export.arxiv.org/api/query", fixture("arxiv_search.xml"),
            search_query="all:Agentic AND all:Recommender AND all:Systems AND all:Memory")
    net.add("export.arxiv.org/api/query", empty)
    got = sources.search_arxiv("Agentic Recommender Systems with Memory and a Typo in the Wordss", 3)
    queries = [c["search_query"] for c in net.calls_to("export.arxiv.org")]
    assert queries == ["all:Agentic AND all:Recommender AND all:Systems AND all:Memory AND all:Typo AND all:Wordss",
                       "all:Agentic AND all:Recommender AND all:Systems AND all:Memory"]
    assert len(got) == 3 and got[0]["arxiv"] == "2609.04882"


def test_arxiv_title_search_of_only_stopwords_asks_nothing(net):
    assert sources.search_arxiv("a of the to", 3) == [] and net.calls == []


def test_arxiv_requests_share_one_pace(net):
    net.add("export.arxiv.org/api/query", fixture("arxiv_search.xml"))
    sources.arxiv_query("all:agent", 3)
    assert net.calls_to("export.arxiv.org")[0]["sortBy"] == "submittedDate"
    assert isinstance(sources.ARXIV_PACE, sources.Pace) and sources.ARXIV_PACE.gap == 3.0


def test_arxiv_query_still_raises_http_errors(net):
    with pytest.raises(httpx.HTTPError):
        sources.arxiv_query("all:agent", 3)


# ---------- links from remote answers ----------

@pytest.mark.parametrize("url, kept", [("https://example.org/p", True), ("http://example.org/p", True), ("javascript:alert(1)", False),
                                       ("data:text/html,x", False), ("//example.org/p", False), ("", False), (None, False)])
def test_candidates_only_keep_http_links(url, kept):
    assert (sources.candidate(title="t", url=url)["url"] != "") is kept


def test_a_javascript_landing_page_from_openalex_or_crossref_is_dropped(net):
    d = fixture_json("openalex_search.json")
    d["results"][0]["primary_location"]["landing_page_url"] = "javascript:alert(1)"
    net.add("api.openalex.org/works", d)
    c = sources.search_openalex("Generative Recommendation with Semantic IDs", 4)[0]
    assert c["url"] == "https://doi.org/10.1145/3746252.3761612"  # the DOI link stands in
    cw = fixture_json("crossref_work.json")
    cw["message"]["URL"] = "javascript:alert(1)"
    net.add("api.crossref.org/works/10.1145/3626772.3657833", cw)
    assert sources.crossref_work("10.1145/3626772.3657833")["url"] == "https://doi.org/10.1145/3626772.3657833"


# ---------- canonical: one classification for every way in ----------

def test_canonical_turns_an_acl_or_arxiv_doi_into_that_paper():
    assert sources.canonical({"doi": "10.18653/v1/2024.acl-long.1"})["acl"] == "2024.acl-long.1"
    assert sources.canonical({"doi": "10.48550/arXiv.2406.12345"})["arxiv"] == "2406.12345"


def test_canonical_looks_for_a_preprint_of_any_other_doi(net):
    net.add("api.openalex.org/works/doi:10.9999/example.2025.7", fixture_json("openalex_doi_arxiv.json"))
    assert sources.canonical({"doi": "10.9999/example.2025.7"})["arxiv"] == "2609.29973"


def test_canonical_keeps_the_doi_when_openalex_is_down_or_the_doi_is_not_one(net):
    assert sources.canonical({"doi": "10.9999/example.2025.7"}) == {"doi": "10.9999/example.2025.7"}
    assert sources.canonical({"doi": "../x"}) == {"doi": "../x"}


def test_canonical_leaves_a_candidate_that_already_has_a_key():
    c = {"openreview": "abcdef12", "doi": "10.1145/3626772.3657833"}
    assert sources.canonical(c) == c


def test_backoff_grows_and_stops_at_half_an_hour():
    assert [sources.backoff(n) for n in (1, 2, 3, 4, 5, 6, 7, 20)] == [60, 120, 240, 480, 960, 1800, 1800, 1800]

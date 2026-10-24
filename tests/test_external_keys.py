"""Papers without a note or an arXiv id (doi:, openreview:, acl: keys): dedupe, notes, PDF lookup."""
from __future__ import annotations

import pytest

from app import config, library, reading

from .fakenet import fixture_json, net  # noqa: F401  (net is a fixture)

DOI_KEY = "doi:10.1145|3626772.3657833"


# ---------- library ----------

def test_validate_accepts_external_keys_and_refuses_the_rest():
    for key in (DOI_KEY, "openreview:mmIAp3cVS0", "acl:2024.acl-long.1", "arxiv:2606.17328"):
        library.validate(key)
    for key in ("doi:10.1145/3626772.3657833", "doi:../../x", "openreview:", "acl:nope", "openreview:a/../b"):
        with pytest.raises(FileNotFoundError):
            library.validate(key)


def test_an_external_paper_reads_like_an_arxiv_only_paper():
    p = library.get_paper(DOI_KEY)
    assert p["external"] and p["note_rel"] is None and p["arxiv"] is None and p["pdf"] == "remote"
    assert library.get_paper("arxiv:2606.17328")["external"] and library.get_paper("arxiv:2606.17328")["arxiv"] == "2606.17328"


def test_safe_name_of_an_external_key_stays_a_single_file_name():
    for key in (DOI_KEY, "openreview:-wcm_3WJt4", "acl:P19-1001"):
        assert "/" not in library.safe_name(key) and ":" not in library.safe_name(key)


def test_ext_index_maps_dois_and_source_links_to_notes(make_note):
    make_note("Alpha", source="https://openreview.net/forum?id=abc123XYZ")
    make_note("Beta", source="https://aclanthology.org/2024.acl-long.1/")
    make_note("Gamma", source="https://doi.org/10.1145/3626772.3657833")
    (config.PAPERS_DIR / "Delta.md").write_text("---\ntype: paper\ndoi: 10.5555/ABC.1\n---\n", encoding="utf-8")
    library._ext_index.cache_clear()
    idx = library.ext_index()
    assert idx == {"openreview:abc123XYZ": "Alpha", "acl:2024.acl-long.1": "Beta", DOI_KEY: "Gamma", "doi:10.5555|abc.1": "Delta"}


@pytest.mark.parametrize("info, urls", [
    ({"openreview": "abc123XYZ"}, ["https://openreview.net/pdf?id=abc123XYZ"]),
    ({"acl": "2024.acl-long.1"}, ["https://aclanthology.org/2024.acl-long.1.pdf"]),
    ({"doi": "10.18653/v1/2024.acl-long.1"}, ["https://aclanthology.org/2024.acl-long.1.pdf"]),
    ({"doi": "10.48550/arxiv.2406.12345"}, ["https://arxiv.org/pdf/2406.12345"]),
    ({"doi": "10.1145/3626772.3657833"}, ["https://dl.acm.org/doi/pdf/10.1145/3626772.3657833"]),
    ({"doi": "10.5281/zenodo.1"}, []),
    ({"arxiv": "2606.17328"}, ["https://arxiv.org/pdf/2606.17328"]),
])
def test_pdf_urls_for_each_kind_of_key(info, urls):
    assert library.pdf_urls(info) == urls


def test_pdf_urls_still_read_a_notes_source_link(make_note):
    make_note("Alpha", source="https://openreview.net/forum?id=abc123XYZ")
    assert library.pdf_urls(library._parse_note(config.PAPERS_DIR / "Alpha.md")) == ["https://openreview.net/pdf?id=abc123XYZ"]


def test_ensure_pdf_downloads_an_openreview_paper_into_the_cache(net):
    net.add("openreview.net/pdf", "%PDF-1.4 fake")
    p = library.ensure_pdf("openreview:abc123XYZ")
    assert p.parent == config.PDF_CACHE_DIR and p.name == "openreview_abc123XYZ.pdf" and p.read_bytes().startswith(b"%PDF-")
    assert len(net.calls_to("openreview.net/pdf")) == 1
    assert library.ensure_pdf("openreview:abc123XYZ") == p and len(net.calls) == 1  # cached


def test_ensure_pdf_for_a_plain_doi_asks_openalex_for_an_open_copy(net):
    net.add("api.openalex.org/works/doi:10.9999/example.2025.7", fixture_json("openalex_doi_arxiv.json"))
    net.add("arxiv.org/pdf/2609.29973", "%PDF-1.5 fake")
    p = library.ensure_pdf("doi:10.9999|example.2025.7")
    assert p.name == "doi_10.9999_example.2025.7.pdf" and p.read_bytes().startswith(b"%PDF-")


def test_ensure_pdf_without_any_copy_says_what_failed(net):
    net.add("api.openalex.org/works/doi:10.1145/3626772.3657833", fixture_json("openalex_doi_plain.json"))
    net.add("dl.acm.org", "forbidden", status=403)
    with pytest.raises(FileNotFoundError, match="403"):
        library.ensure_pdf(DOI_KEY)


# ---------- reading ----------

def test_push_by_ext_adds_dedupes_and_finds_by_key():
    item, what = reading.push(ext="openreview:abc123XYZ", title="A Paper", url="https://openreview.net/forum?id=abc123XYZ",
                              reason="why", lines=["Project A"])
    assert what == "added" and item["ext"] == "openreview:abc123XYZ" and not item.get("arxiv")
    assert reading.key_of(item) == "openreview:abc123XYZ"
    again, what = reading.push(ext="openreview:abc123XYZ", reason="and again", lines=["Project B"])
    assert "already listed" in what and again["lines"] == ["Project A", "Project B"] and len(reading.load()) == 1
    assert reading.find(reading.load(), "openreview:abc123XYZ")["id"] == item["id"]
    assert reading.update("openreview:abc123XYZ", {"tier": "T1"})["status"] == "queued"
    assert reading.mark_opened("openreview:abc123XYZ")["status"] == "reading"


def test_push_by_ext_refuses_malformed_keys_and_missing_ids():
    with pytest.raises(reading.ReadingError, match="ext must look like"):
        reading.push(ext="doi:10.1145/3626772.3657833", reason="x")
    with pytest.raises(reading.ReadingError, match="give --paper"):
        reading.push(reason="x")
    assert reading.load() == []


def test_a_dismissed_external_paper_is_not_brought_back():
    item, _ = reading.push(ext=DOI_KEY, reason="x")
    reading.update(item["id"], {"status": "dismissed"})
    _, what = reading.push(ext=DOI_KEY, reason="again")
    assert "dismissed earlier" in what


def test_push_by_ext_uses_the_note_that_already_exists(make_note):
    make_note("Alpha", source="https://openreview.net/forum?id=abc123XYZ")
    library._ext_index.cache_clear()
    item, _ = reading.push(ext="openreview:abc123XYZ", reason="x")
    assert item["paper"] == "Alpha" and reading.key_of(item) == "Alpha"


def test_an_external_item_is_attached_to_its_note_once_the_note_appears(make_note):
    reading.push(ext=DOI_KEY, title="X", reason="x")
    assert "paper" not in reading.load()[0]
    make_note("Gamma", source="https://doi.org/10.1145/3626772.3657833")
    library._ext_index.cache_clear()
    items = reading.load()
    assert items[0]["paper"] == "Gamma" and reading.key_of(items[0]) == "Gamma" and items[0]["ext"] == DOI_KEY


def test_the_ext_key_survives_a_save_and_a_load():
    reading.push(ext="acl:2024.acl-long.1", title="Quantized", url="https://aclanthology.org/2024.acl-long.1/", reason="x")
    assert "ext: acl:2024.acl-long.1" in config.READING_LIST.read_text(encoding="utf-8")
    assert reading.load()[0]["ext"] == "acl:2024.acl-long.1"


def test_the_generated_reading_list_links_external_items_to_their_page():
    reading.push(ext="acl:2024.acl-long.1", title="Quantized Tuning", url="https://aclanthology.org/2024.acl-long.1/", reason="why")
    reading.push(ext="openreview:abc123XYZ", reason="no title yet")
    view = reading.READING_VIEW.read_text(encoding="utf-8")
    assert "[Quantized Tuning](https://aclanthology.org/2024.acl-long.1/) — why" in view and "- openreview:abc123XYZ — no title yet" in view


def test_sync_conflict_copies_merge_external_items(tmp_path):
    reading.push(ext=DOI_KEY, title="Mine", reason="x")
    conflict = config.READING_LIST.with_name("reading.sync-conflict-20260101-000000-ABCDEFG.yaml")
    conflict.write_text("items:\n- id: rzzzzzz\n  ext: openreview:abc123XYZ\n  title: Theirs\n  status: inbox\n  reason: y\n"
                        "  updated_at: '2026-01-01T00:00:00'\n", encoding="utf-8")
    keys = {reading.key_of(i) for i in reading.load()}
    assert keys == {DOI_KEY, "openreview:abc123XYZ"}


def test_an_item_an_older_app_version_saved_without_ids_does_not_crash_the_key():
    assert reading.key_of({"id": "r123456"}) == "r123456"

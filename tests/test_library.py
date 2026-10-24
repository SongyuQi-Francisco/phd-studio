"""app/library.py: paper keys, safe file names, path validation, note parsing, PDF link rules."""
from __future__ import annotations

import pytest

from app import config, library

from .conftest import ROOT


# ---------- arxiv_key ----------


@pytest.mark.parametrize("key,expected", [
    ("arxiv:2606.17328", "2606.17328"),
    ("arxiv:2606.1732", "2606.1732"),      # older 4-digit sequence numbers
    ("arxiv:0704.0001", "0704.0001"),
    ("Attention 2017", None),
    ("", None),
    ("arxiv:", None),
    ("arxiv:2606.173289", None),            # too many digits
    ("arxiv:2606.173", None),               # too few
    ("arxiv:2606.17328v2", None),           # versions are normalized before they get here
    ("arXiv:2606.17328", None),             # the prefix is case-sensitive
    ("arxiv:2606.17328/../x", None),
    ("xarxiv:2606.17328", None),
    (" arxiv:2606.17328", None),
    ("arxiv: 2606.17328", None),
])
def test_arxiv_key(key, expected):
    assert library.arxiv_key(key) == expected


# ---------- safe_name ----------


@pytest.mark.parametrize("key,expected", [
    ("arxiv:2606.17328", "arxiv_2606.17328"),
    ("Attention 2017", "Attention 2017"),
    ("a/b", "a_b"),
    ("../../etc/passwd", ".._.._etc_passwd"),
    ("a:b/c:d", "a_b_c_d"),
    ("", ""),
])
def test_safe_name_never_contains_path_separators_or_colons(key, expected):
    assert library.safe_name(key) == expected
    assert "/" not in library.safe_name(key) and ":" not in library.safe_name(key)


def test_cache_paths_built_from_safe_names_stay_in_the_cache():
    p = library.PDF_CACHE_DIR / f"{library.safe_name('../../evil')}.pdf"
    assert p.resolve().parent == library.PDF_CACHE_DIR.resolve()


# ---------- note_path / validate ----------


def test_note_path_resolves_a_note_in_the_papers_folder(make_note):
    p = make_note("Attention 2017")
    assert library.note_path("Attention 2017") == p.resolve()


@pytest.mark.parametrize("stem", ["Missing", "", ".", "..", "../Attention 2017", "sub/Attention 2017", "Attention 2017/"])
def test_note_path_refuses_missing_or_non_direct_children(make_note, stem):
    make_note("Attention 2017")
    sub = config.PAPERS_DIR / "sub"
    sub.mkdir()
    (sub / "Attention 2017.md").write_text("nested", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        library.note_path(stem)


def test_note_path_refuses_traversal_to_other_vault_notes(write_vault_file):
    write_vault_file("Atlas/Roadmap.md", "# private-ish")
    write_vault_file("secret.md", "x")
    for stem in ("../Atlas/Roadmap", "../secret", "../../secret", "../Papers/../secret"):
        with pytest.raises(FileNotFoundError):
            library.note_path(stem)


def test_note_path_refuses_absolute_paths_and_outside_files():
    (ROOT / "outside.md").write_text("secret", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        library.note_path(str(ROOT / "outside"))
    with pytest.raises(FileNotFoundError):
        library.note_path("/etc/hosts")


def test_note_path_refuses_a_symlinked_note_that_points_elsewhere():
    (ROOT / "outside.md").write_text("secret", encoding="utf-8")
    (config.PAPERS_DIR / "Linked.md").symlink_to(ROOT / "outside.md")
    with pytest.raises(FileNotFoundError):
        library.note_path("Linked")


def test_validate_accepts_notes_and_arxiv_keys(make_note):
    make_note("Attention 2017")
    library.validate("Attention 2017")
    library.validate("arxiv:2606.17328")  # no note needed for an arXiv-only paper


@pytest.mark.parametrize("key", ["Missing", "../Atlas/Roadmap", "arxiv:../../x", "arxiv:2606.17328v2", "arXiv:2606.17328", "/etc/passwd", ""])
def test_validate_refuses_everything_else(write_vault_file, key):
    write_vault_file("Atlas/Roadmap.md")
    with pytest.raises(FileNotFoundError):
        library.validate(key)


# ---------- note parsing ----------


def test_parse_note_reads_frontmatter_title_and_links(make_note):
    p = make_note("Attention 2017", arxiv="1706.03762", source="https://arxiv.org/abs/1706.03762",
                  zotero="zotero://select/library/items/ABCD1234/", title="Attention Is All You Need",
                  tags=["paper", "transformer", "nlp"], line="TrustMem", year=2017)
    info = library._parse_note(p)
    assert info["stem"] == "Attention 2017"
    assert info["title"] == "Attention Is All You Need"
    assert info["year"] == 2017
    assert info["arxiv"] == "1706.03762"
    assert info["zotero_key"] == "ABCD1234"
    assert info["tags"] == ["transformer", "nlp"]       # the generic "paper" tag is dropped
    assert info["research_line"] == ["TrustMem"]
    assert info["status"] == "" and info["source"] == "https://arxiv.org/abs/1706.03762"


def test_parse_note_defaults_for_a_bare_note(make_note):
    info = library._parse_note(make_note("Bare"))
    assert (info["title"], info["arxiv"], info["zotero_key"], info["tags"], info["research_line"]) == ("Bare", "", "", [], [])


def test_parse_note_survives_broken_frontmatter():
    p = config.PAPERS_DIR / "Broken.md"
    p.write_text("---\ntags: [unclosed\n---\n- Title: Still Readable\n", encoding="utf-8")
    info = library._parse_note(p)
    assert info["title"] == "Still Readable" and info["tags"] == []


# ---------- arxiv index ----------


def test_arxiv_index_maps_ids_from_frontmatter_and_source_urls(make_note):
    make_note("From Frontmatter", arxiv="2606.00001v2")
    make_note("From Source", source="https://arxiv.org/pdf/2606.00002v3")
    make_note("No Id", source="https://example.com/x")
    idx = library.arxiv_index()
    assert idx == {"2606.00001": "From Frontmatter", "2606.00002": "From Source"}


def test_arxiv_index_follows_new_notes(make_note):
    assert library.arxiv_index() == {}
    make_note("New", arxiv="2606.00009")
    assert library.arxiv_index() == {"2606.00009": "New"}


# ---------- reading queue note ----------


def test_reading_queue_lists_wikilinks_in_order_once_with_their_section():
    config.READING_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    config.READING_QUEUE.write_text(
        "# Queue\n[[Ignored Before Section]]\n## First\n- [[A]] and [[B|alias]]\n- [[A]] again\n## Second\n1. [[C#Method]]\n",
        encoding="utf-8")
    assert library.reading_queue() == [{"stem": "Ignored Before Section", "section": ""}, {"stem": "A", "section": "First"},
                                       {"stem": "B", "section": "First"}, {"stem": "C", "section": "Second"}]


def test_reading_queue_without_a_note_is_empty():
    assert library.reading_queue() == []


# ---------- PDF links and files ----------


@pytest.mark.parametrize("info,urls", [
    ({"arxiv": "2606.00001"}, ["https://arxiv.org/pdf/2606.00001"]),
    ({"source": "https://arxiv.org/abs/2606.00001v2"}, ["https://arxiv.org/pdf/2606.00001"]),
    ({"arxiv": "2606.00001", "source": "https://arxiv.org/abs/2606.00001"}, ["https://arxiv.org/pdf/2606.00001"]),  # deduped
    ({"source": "https://openreview.net/forum?id=AbC-123"}, ["https://openreview.net/pdf?id=AbC-123"]),
    ({"source": "https://aclanthology.org/2024.acl-long.1/"}, ["https://aclanthology.org/2024.acl-long.1.pdf"]),
    ({"source": "https://dl.acm.org/doi/10.1145/3624918.3625339"}, ["https://dl.acm.org/doi/pdf/10.1145/3624918.3625339"]),
    ({"source": "https://example.com/paper"}, []),
    ({}, []),
])
def test_pdf_urls(info, urls):
    assert library.pdf_urls(info) == urls


def test_local_pdf_prefers_the_vault_copy_over_the_cache():
    assert library.local_pdf("Attention 2017") is None
    cached = library.PDF_CACHE_DIR / "Attention 2017.pdf"
    cached.write_bytes(b"%PDF-1.4 cache")
    assert library.local_pdf("Attention 2017") == cached
    vault = config.VAULT_PDFS_DIR / "Attention 2017.pdf"
    vault.write_bytes(b"%PDF-1.4 vault")
    assert library.local_pdf("Attention 2017") == vault


def test_arxiv_only_papers_are_cached_under_a_safe_name():
    cached = library.PDF_CACHE_DIR / "arxiv_2606.17328.pdf"
    cached.write_bytes(b"%PDF-1.4")
    assert library.local_pdf("arxiv:2606.17328") == cached
    assert library.ensure_pdf("arxiv:2606.17328") == cached  # no download when cached


def test_ensure_pdf_downloads_into_the_cache_not_the_vault(monkeypatch):
    class Resp:
        status_code = 200
        content = b"%PDF-1.7 fake"

    seen: list[str] = []
    monkeypatch.setattr("httpx.get", lambda url, **kw: seen.append(url) or Resp())
    p = library.ensure_pdf("arxiv:2606.17328")
    assert seen == ["https://arxiv.org/pdf/2606.17328"]
    assert p == library.PDF_CACHE_DIR / "arxiv_2606.17328.pdf" and p.read_bytes() == b"%PDF-1.7 fake"
    assert list(config.VAULT_DIR.rglob("*.pdf")) == []


def test_ensure_pdf_rejects_non_pdf_responses(monkeypatch):
    class Resp:
        status_code = 200
        content = b"<html>login</html>"

    monkeypatch.setattr("httpx.get", lambda url, **kw: Resp())
    with pytest.raises(FileNotFoundError, match="HTTP 200"):
        library.ensure_pdf("arxiv:2606.17328")
    assert list(library.PDF_CACHE_DIR.iterdir()) == []


def test_ensure_pdf_for_an_unknown_note_is_an_error():
    with pytest.raises(FileNotFoundError):
        library.ensure_pdf("../../etc/passwd")


def test_get_paper_for_arxiv_only_keys_and_notes(make_note):
    a = library.get_paper("arxiv:2606.17328")
    assert a["arxiv"] == "2606.17328" and a["note_rel"] is None and a["pdf"] == "remote"
    make_note("Attention 2017", title="Attention", body="text")
    n = library.get_paper("Attention 2017")
    assert n["note_rel"] == "Papers/Attention 2017.md" and n["pdf"] == "none"
    assert n["obsidian_url"].endswith("file=Papers/Attention%202017.md")
    with pytest.raises(FileNotFoundError):
        library.get_paper("../secret")


def test_list_papers_orders_info_by_queue_rank(make_note):
    make_note("A")
    make_note("B", arxiv="2606.00001")
    config.READING_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    config.READING_QUEUE.write_text("## S\n- [[B]]\n", encoding="utf-8")
    by = {p["stem"]: p for p in library.list_papers()}
    assert by["B"]["queue_rank"] == 0 and by["A"]["queue_rank"] is None
    assert by["B"]["pdf"] == "remote" and by["A"]["pdf"] == "none"

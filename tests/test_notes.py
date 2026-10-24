"""app/notes.py: safe vault paths, frontmatter, sections, link stripping, wikilink lookup."""
from __future__ import annotations

import pytest

from app import config, notes

from .conftest import ROOT


# ---------- resolve: nothing outside the vault ----------


def test_resolve_returns_the_absolute_path_of_a_vault_note(write_vault_file):
    p = write_vault_file("Atlas/Roadmap.md", "# hi")
    assert notes.resolve("Atlas/Roadmap.md") == p.resolve()


def test_resolve_allows_dotdot_that_stays_inside_the_vault(write_vault_file):
    p = write_vault_file("Atlas/Roadmap.md")
    write_vault_file("Other/x.md")
    assert notes.resolve("Other/../Atlas/Roadmap.md") == p.resolve()


@pytest.mark.parametrize("rel", ["../outside.md", "../../outside.md", "Atlas/../../outside.md", "./../outside.md"])
def test_resolve_refuses_parent_traversal(write_vault_file, rel):
    (ROOT / "outside.md").write_text("secret", encoding="utf-8")
    write_vault_file("Atlas/Roadmap.md")
    with pytest.raises(FileNotFoundError):
        notes.resolve(rel)


def test_resolve_refuses_absolute_paths_outside_the_vault():
    outside = ROOT / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        notes.resolve(str(outside))


def test_resolve_refuses_a_sibling_folder_that_shares_the_vault_name_prefix():
    evil = ROOT / (config.VAULT_DIR.name + "-evil")
    evil.mkdir()
    (evil / "secret.md").write_text("secret", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        notes.resolve(f"../{evil.name}/secret.md")


def test_resolve_refuses_symlinks_that_lead_out_of_the_vault(write_vault_file):
    (ROOT / "outside.md").write_text("secret", encoding="utf-8")
    link = config.VAULT_DIR / "link.md"
    link.symlink_to(ROOT / "outside.md")
    with pytest.raises(FileNotFoundError):
        notes.resolve("link.md")
    (ROOT / "outside_dir").mkdir()
    (ROOT / "outside_dir" / "n.md").write_text("secret", encoding="utf-8")
    (config.VAULT_DIR / "dirlink").symlink_to(ROOT / "outside_dir")
    with pytest.raises(FileNotFoundError):
        notes.resolve("dirlink/n.md")


@pytest.mark.parametrize("rel", ["notes.txt", "plan.yaml", "Atlas/Roadmap", "Atlas/Roadmap.md.bak", "Atlas", ""])
def test_resolve_only_serves_existing_markdown_files(write_vault_file, rel):
    write_vault_file("notes.txt")
    write_vault_file("plan.yaml")
    write_vault_file("Atlas/Roadmap.md.bak")
    with pytest.raises(FileNotFoundError):
        notes.resolve(rel)


def test_resolve_missing_note():
    with pytest.raises(FileNotFoundError):
        notes.resolve("Nope.md")


def test_rel_and_obsidian_url(write_vault_file):
    p = write_vault_file("Folder A/Note 中.md")
    assert notes.rel(p) == "Folder A/Note 中.md"
    assert notes.obsidian_url(p) == f"obsidian://open?vault={config.VAULT_NAME}&file=Folder%20A/Note%20%E4%B8%AD.md"


# ---------- frontmatter ----------


def test_split_reads_frontmatter_and_body():
    fm, body = notes.split("---\ntype: paper\ntags: [a, b]\nyear: 2025\n---\n# Title\ntext")
    assert fm == {"type": "paper", "tags": ["a", "b"], "year": 2025}
    assert body == "# Title\ntext"


def test_split_without_frontmatter_returns_the_text_untouched():
    text = "# Title\n\n---\nnot frontmatter\n---\n"
    assert notes.split(text) == ({}, text)


def test_split_frontmatter_closing_at_end_of_file():
    fm, body = notes.split("---\na: 1\n---")
    assert fm == {"a": 1} and body == ""


@pytest.mark.parametrize("text", ["---\na: [unclosed\n---\nbody", "---\n- just\n- a list\n---\nbody", "---\n---\nbody"])
def test_split_bad_or_non_mapping_frontmatter_gives_an_empty_dict(text):
    fm, body = notes.split(text)
    assert fm == {}
    assert body.endswith("body")


def test_read_returns_a_plain_json_ready_record(write_vault_file):
    p = write_vault_file("Papers/X.md", "---\nupdated: 2026-10-24\ntitle: X\n---\n## Summary\nshort\n## Notes\n- a\n")
    n = notes.read(p)
    assert n["path"] == "Papers/X.md" and n["name"] == "X"
    assert n["frontmatter"] == {"updated": "2026-10-24", "title": "X"}  # dates as text
    assert n["sections"] == {"Summary": "short", "Notes": "- a"}
    assert n["obsidian_url"].startswith("obsidian://open?vault=")
    assert n["mtime"] == p.stat().st_mtime
    assert n["body"].startswith("## Summary")


# ---------- sections ----------


def test_sections_splits_on_level_two_headings_only():
    body = "intro line\n\n## One\nfirst\n\n### Sub\nstill one\n\n## Two  \ntext\n# Top\nafter top\n"
    secs = notes.sections(body)
    assert list(secs) == ["One", "Two"]
    assert secs["One"] == "first\n\n### Sub\nstill one"
    assert secs["Two"] == "text\n# Top\nafter top"


def test_sections_first_occurrence_wins_and_empty_sections_are_kept():
    secs = notes.sections("## A\none\n## A\ntwo\n## Empty\n## Last\nz")
    assert secs == {"A": "one", "Empty": "", "Last": "z"}


def test_sections_of_a_text_without_headings_is_empty():
    assert notes.sections("just text\n# only h1\n### only h3") == {}


def test_sections_ignores_text_before_the_first_heading():
    assert "intro" not in "".join(notes.sections("intro\n## A\nbody").values())


def test_sections_handles_heading_at_the_very_start_and_end():
    assert notes.sections("## A\nx") == {"A": "x"}
    assert notes.sections("## A") == {"A": ""}


def test_find_section_matches_prefix_case_insensitively_in_the_order_given():
    secs = {"Summary of results": "S", "Method": "M", "method details": "D"}
    assert notes.find_section(secs, "summary") == "S"
    assert notes.find_section(secs, "METHOD") == "M"
    assert notes.find_section(secs, "missing", "method") == "M"
    assert notes.find_section(secs, "method", "summary") == "M"
    assert notes.find_section(secs, "nothing") == ""
    assert notes.find_section({}, "x") == ""


# ---------- paragraphs, bullets ----------


def test_first_paragraph_skips_tables_headings_and_comments():
    text = "<!-- c -->\n\n| a | b |\n|---|---|\n\n# Heading\n\nThe real\nparagraph  here.\n\nSecond."
    assert notes.first_paragraph(text) == "The real paragraph here."
    assert notes.first_paragraph("") == ""
    assert notes.first_paragraph("# only heading") == ""


def test_bullets_strips_markers_and_keeps_order():
    text = "intro\n- one\n* two\n  - nested\n1. three\n10. ten\nnot a bullet\n-no space"
    assert notes.bullets(text) == ["one", "two", "nested", "three", "ten"]


# ---------- strip_links ----------


@pytest.mark.parametrize("raw,plain", [
    ("see [[Attention 2017]] now", "see Attention 2017 now"),
    ("see [[Attention 2017|the transformer]]", "see the transformer"),
    ("see [[Attention 2017#Method]]", "see Attention 2017"),
    ("see [[Attention 2017#Method|how it works]]", "see how it works"),
    ("**bold** and `code` and **a** `b`", "bold and code and a b"),
    ("- first\n- second\n* third", "first\nsecond\nthird"),
    ("  - indented item", "indented item"),
    ("  padded  ", "padded"),
    ("keep-this - dash and 2 * 3", "keep-this - dash and 2 * 3"),
    ("**[[Alpha]]** and `[[Beta|b]]`", "Alpha and b"),
    ("", ""),
])
def test_strip_links(raw, plain):
    assert notes.strip_links(raw) == plain


# ---------- find_by_name ----------


def test_find_by_name_finds_a_note_anywhere_in_the_vault(write_vault_file):
    p = write_vault_file("Deep/Down/Folder/Idea Radar.md")
    assert notes.find_by_name("Idea Radar") == p
    assert notes.find_by_name("  Idea Radar  ") == p


def test_find_by_name_prefers_the_shallowest_match(write_vault_file):
    write_vault_file("A/B/C/Roadmap.md")
    shallow = write_vault_file("Atlas/Roadmap.md")
    write_vault_file("A/B/Roadmap.md")
    assert notes.find_by_name("Roadmap") == shallow


def test_find_by_name_ignores_obsidian_config_and_trash(write_vault_file):
    write_vault_file(".obsidian/Plugin Note.md")
    write_vault_file(".trash/Deleted.md")
    live = write_vault_file("Notes/Deleted.md")
    assert notes.find_by_name("Plugin Note") is None
    assert notes.find_by_name("Deleted") == live


def test_find_by_name_missing_returns_none(write_vault_file):
    write_vault_file("Atlas/Roadmap.md")
    assert notes.find_by_name("Nope") is None
    assert notes.find_by_name("") is None
    assert notes.find_by_name("Roadmap.md") is None  # a wikilink target has no extension


def test_find_by_name_treats_glob_characters_literally(write_vault_file):
    star = write_vault_file("Notes/Q&A [draft].md")
    write_vault_file("Notes/Q&A d.md")
    assert notes.find_by_name("Q&A [draft]") == star
    assert notes.find_by_name("*") is None
    assert notes.find_by_name("Q&A ?") is None


def test_find_by_name_accepts_folder_qualified_links(write_vault_file):
    p = write_vault_file("Ideas/Research Line - X.md")
    write_vault_file("Other/Research Line - X.md")
    assert notes.find_by_name("Ideas/Research Line - X") == p


def test_find_by_name_folder_path_link_resolves_from_the_vault_root(write_vault_file):
    p = write_vault_file("02 Ideas/Idea A.md")
    write_vault_file("Archive/02 Ideas/Idea A.md")
    assert notes.find_by_name("02 Ideas/Idea A") == p


def test_find_by_name_keeps_dots_in_the_name(write_vault_file):
    p = write_vault_file("Papers/Smith et al. 2024.md")
    v = write_vault_file("Notes/v1.2.md")
    u = write_vault_file("Ideas/注意力 v2.0.md")
    assert notes.find_by_name("Smith et al. 2024") == p
    assert notes.find_by_name("v1.2") == v
    assert notes.find_by_name("注意力 v2.0") == u


def test_find_by_name_never_leaves_the_vault():
    (ROOT / "outside.md").write_text("secret", encoding="utf-8")
    assert notes.find_by_name("../outside") is None


@pytest.mark.parametrize("name", ["Atlas/../../outside", "../../outside", "..", "Atlas/.."])
def test_find_by_name_refuses_dotdot_segments(write_vault_file, name):
    (ROOT / "outside.md").write_text("secret", encoding="utf-8")
    write_vault_file("Atlas/Roadmap.md")
    assert notes.find_by_name(name) is None


def test_find_by_name_refuses_absolute_paths(write_vault_file):
    outside = ROOT / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    inside = write_vault_file("Atlas/Roadmap.md")
    assert notes.find_by_name(str(outside.with_suffix(""))) is None
    assert notes.find_by_name(str(inside.with_suffix(""))) is None


def test_find_by_name_skips_a_symlink_that_leads_out_of_the_vault(write_vault_file):
    (ROOT / "outside.md").write_text("secret", encoding="utf-8")
    (config.VAULT_DIR / "Leak.md").symlink_to(ROOT / "outside.md")
    assert notes.find_by_name("Leak") is None

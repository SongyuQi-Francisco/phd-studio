"""app/excerpt.py: one-line excerpts appended to a paper note, and a byte-exact undo."""
from __future__ import annotations

import os

import pytest

if "phd-studio-test-" not in os.environ.get("STUDIO_CONFIG", ""):  # never against the real vault
    pytest.skip("needs the sandboxed vault from tests/conftest.py", allow_module_level=True)

from app import config, excerpt  # noqa: E402

TEMPLATE = """---
type: paper
---

# Paper

## Problem

What it solves.

## Evidence

- Table 2: +3.1 NDCG.

## Links

- Ideas:

## Claim Gate

- Verified source:
"""


@pytest.fixture
def note():
    """Write raw bytes as the note `P 2026` (no newline translation) and return a reader for it."""
    path = config.PAPERS_DIR / "P 2026.md"

    def write(text: str = TEMPLATE) -> bytes:
        path.write_bytes(text.encode("utf-8"))
        return path.read_bytes()

    write.read = lambda: path.read_bytes().decode("utf-8")
    write.path = path
    return write


def roundtrip(note, text: str, section: str, quote: str = "a quoted passage", page: int | None = 3, comment=None):
    """Append then undo; return (note text after append, append result, undo result) and check the bytes come back."""
    before = note(text)
    os.utime(note.path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))  # an old, distinctive mtime
    mtime_ns = note.path.stat().st_mtime_ns
    r = excerpt.append("P 2026", quote, page, section, comment)
    after = note.read()
    u = excerpt.undo("P 2026", r["line"], r["inserted"], r["section"], r["created"], r["mtime_ns"])
    assert u == {"ok": True, "exact": True}
    assert note.path.read_bytes() == before
    assert note.path.stat().st_mtime_ns == mtime_ns
    return after, r, u


# ---------- where the line goes ----------


def test_appends_to_an_existing_list_section_and_undoes_exactly(note):
    after, r, _ = roundtrip(note, TEMPLATE, "Evidence")
    assert '- Table 2: +3.1 NDCG.\n- "a quoted passage" [p.3]\n\n## Links' in after
    assert r["created"] is False


def test_a_paragraph_section_gets_a_blank_line_before_the_item(note):
    after, _, _ = roundtrip(note, TEMPLATE, "Problem")
    assert 'What it solves.\n\n- "a quoted passage" [p.3]\n\n## Evidence' in after


def test_new_excerpts_section_goes_before_links(note):
    after, r, _ = roundtrip(note, TEMPLATE, "Excerpts", comment="对照我们的指标")
    assert '## Excerpts\n\n- "a quoted passage" [p.3] — 对照我们的指标\n\n## Links' in after
    assert r["created"] is True
    assert excerpt.headings(after) == ["Problem", "Evidence", "Excerpts", "Links", "Claim Gate"]


def test_last_line_with_trailing_spaces_keeps_them_and_undo_still_works(note):
    text = "## Evidence\n\nfoo bar  \n\n## Links\n"
    after, _, _ = roundtrip(note, text, "Evidence")
    assert 'foo bar  \n\n- "a quoted passage" [p.3]\n' in after


@pytest.mark.parametrize("text", [
    "## Evidence\n\n## Links\n",                          # empty section
    "## Evidence\nfoo\n## Links\n",                       # paragraph directly followed by a heading
    "## Evidence\n\nfoo",                                 # no trailing newline
    "## Evidence\r\n\r\nfoo\r\n\r\n## Links\r\n",         # CRLF note
    "## Evidence\n\n- a\n- b\n\n\n\n## Links\n",          # extra blank lines after a list
], ids=["empty", "para-heading", "no-final-newline", "crlf", "blank-run"])
def test_undo_is_byte_exact_in_awkward_layouts(note, text):
    roundtrip(note, text, "Evidence")


@pytest.mark.parametrize("text", ["## Problem\n\nfoo\n", "## Problem\n\nfoo", "## Problem\n\nfoo\n\n", ""],
                         ids=["eof-newline", "eof-no-newline", "eof-blank", "empty-file"])
def test_new_excerpts_section_at_end_of_file_undoes_exactly(note, text):
    after, r, _ = roundtrip(note, text, "Excerpts")
    assert r["created"] and after.rstrip("\n").endswith('## Excerpts\n\n- "a quoted passage" [p.3]')


def test_crlf_notes_stay_crlf(note):
    note("## Evidence\r\n\r\nfoo\r\n\r\n## Links\r\n")
    excerpt.append("P 2026", "q", 1, "Evidence")
    raw = note.path.read_bytes()
    assert b'foo\r\n\r\n- "q" [p.1]\r\n\r\n## Links' in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")


# ---------- headings: front matter and fenced code are not structure ----------


def test_headings_skip_fenced_code_blocks(note):
    text = "## Method\n\n```python\n## train loop\nx = 1\n```\n\n~~~\n## not this\n~~~\n\n## Links\n"
    assert excerpt.headings(text) == ["Method", "Links"]
    after, _, _ = roundtrip(note, text, "Method")
    assert '~~~\n\n- "a quoted passage" [p.3]\n\n## Links' in after


def test_a_heading_only_inside_code_is_not_a_section(note):
    note("## Method\n\n```\n## train loop\n```\n")
    with pytest.raises(KeyError):
        excerpt.append("P 2026", "q", 1, "train loop")


def test_front_matter_is_not_searched_for_headings():
    text = "---\ntitle: x\n## odd: but inside the front matter\n---\n\n## Real\n"
    assert excerpt.headings(text) == ["Real"]


# ---------- escaping ----------


@pytest.mark.parametrize("quote, written", [
    ("the <s> token", r"the \<s\> token"),
    ("<img src=x onerror=alert(1)>", r"\<img src=x onerror=alert(1)\>"),
    ("costs $5 and $6", r"costs \$5 and \$6"),
    ("A & B", r"A \& B"),
    (r"a \alpha b", r"a \\alpha b"),
    ('he said "no"', "he said 'no'"),
    ("multi\nline   text", "multi line text"),
])
def test_quoted_text_is_escaped_so_it_stays_text(quote, written):
    assert excerpt.line_for(quote, 2) == f'- "{written}" [p.2]'


def test_comment_is_kept_as_markdown():
    assert excerpt.line_for("q", None, "see [[Idea A]]") == '- "q" — see [[Idea A]]'


# ---------- duplicates, errors, fallback undo ----------


def test_the_same_excerpt_twice_is_a_duplicate_even_with_trailing_spaces(note):
    note(TEMPLATE)
    r = excerpt.append("P 2026", "q", 1, "Evidence")
    note(note.read().replace(r["line"], r["line"] + "   "))
    again = excerpt.append("P 2026", "q", 1, "Evidence")
    assert again["duplicate"] is True and again["inserted"] is None


def test_unknown_section_and_empty_text_are_refused(note):
    note(TEMPLATE)
    with pytest.raises(KeyError):
        excerpt.append("P 2026", "q", 1, "Nope")
    with pytest.raises(ValueError):
        excerpt.append("P 2026", "   ", 1, "Evidence")


def test_missing_note_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        excerpt.append("No Such 2026", "q", 1, "Evidence")


def test_undo_after_the_note_changed_removes_just_the_line(note):
    note(TEMPLATE)
    r = excerpt.append("P 2026", "q", 1, "Evidence")
    edited = note.read().replace("What it solves.", "What it solves, edited in Obsidian.")
    edited = edited.replace(r["line"], "  " + r["line"] + "  ")  # and the line itself re-indented
    note(edited)
    assert excerpt.undo("P 2026", r["line"], r["inserted"], r["section"], r["created"]) == {"ok": True, "exact": False}
    assert r["line"] not in note.read()
    assert "edited in Obsidian" in note.read()


def test_fallback_undo_removes_a_created_section_that_is_now_empty(note):
    note(TEMPLATE)
    r = excerpt.append("P 2026", "q", 1, "Excerpts")
    note(note.read().replace("## Excerpts\n\n", "## Excerpts\n\n\n"))  # breaks the exact match
    assert excerpt.undo("P 2026", r["line"], r["inserted"], r["section"], r["created"])["ok"] is True
    assert "## Excerpts" not in note.read()


def test_undo_reports_failure_when_the_line_is_gone(note):
    note(TEMPLATE)
    r = excerpt.append("P 2026", "q", 1, "Evidence")
    note(TEMPLATE)
    assert excerpt.undo("P 2026", r["line"], r["inserted"], r["section"], r["created"]) == {"ok": False, "exact": False}


def test_a_longer_line_sharing_the_prefix_is_not_undone(note):
    note(TEMPLATE)
    r = excerpt.append("P 2026", "q", 1, "Evidence")
    note(note.read().replace(r["line"], r["line"] + " — later comment"))
    u = excerpt.undo("P 2026", r["line"], r["inserted"], r["section"], r["created"])
    assert u["ok"] is False
    assert "later comment" in note.read()


# ---------- the file itself ----------


def test_writes_in_place_keeping_the_inode(note):
    note(TEMPLATE)
    ino = os.stat(note.path).st_ino
    r = excerpt.append("P 2026", "q", 1, "Evidence")
    excerpt.undo("P 2026", r["line"], r["inserted"], r["section"], r["created"])
    assert os.stat(note.path).st_ino == ino
    assert not list(config.PAPERS_DIR.glob(".*")), "no temp files left next to the note"

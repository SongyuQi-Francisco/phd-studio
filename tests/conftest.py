"""Test setup: a throwaway vault, data dir and state dir, wired in before `app.config` loads.

`app.config` reads its paths when first imported (and exits without a config file), so the
test config is written and STUDIO_CONFIG pointed at it here, at conftest import. Every test
then starts from an empty fake vault. Nothing touches the real vault, `studio.toml`,
`~/Library/Application Support/PhD Studio` or `state/`.
"""
from __future__ import annotations

import atexit
import csv
import io
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

ROOT = Path(tempfile.mkdtemp(prefix="phd-studio-test-")).resolve()
atexit.register(shutil.rmtree, ROOT, ignore_errors=True)

VAULT = ROOT / "vault"
DATA = ROOT / "data"
STATE = ROOT / "state"
PROJECT = Path(__file__).resolve().parent.parent

CONFIG_TEXT = f"""\
[paths]
vault = "{VAULT}"
papers = "Papers"
pdfs = "PDFs"
reading_queue = "Reading Queue/Reading Queue.md"
reading_list = "Reading Queue/reading.yaml"
plan = "Atlas/plan.yaml"
ideas = "Ideas/ideas.yaml"
rules = "AGENTS.md"
zotero = "{ROOT / 'zotero'}"
secrets = "{ROOT / 'secrets'}"
data = "{DATA}"
state = "{STATE}"
ideas_dir = "Ideas"
extra_read_dirs = []

[profile]
about = "Test student."
lines = ["Project A", "Project B"]
venues = ["KDD", "ACL", "NoSuchVenue"]
"""

CONFIG_FILE = ROOT / "studio.toml"
CONFIG_FILE.write_text(CONFIG_TEXT, encoding="utf-8")
os.environ["STUDIO_CONFIG"] = str(CONFIG_FILE)
os.environ["STUDIO_DEVICE"] = "test"

if "app.config" in sys.modules:  # imported by something before this file ran: paths may be the real ones
    pytest.exit("app.config was imported before tests/conftest.py; refusing to run against it", returncode=3)

from app import config, library, plan, reading, venues  # noqa: E402

for _name in ("VAULT_DIR", "DATA_DIR", "STATE_DIR", "ZOTERO_DIR", "SECRETS_DIR"):
    _path = getattr(config, _name)
    if not _path.is_relative_to(ROOT):
        pytest.exit(f"config.{_name} = {_path} is outside the test sandbox {ROOT}", returncode=3)


def _fresh_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)


@pytest.fixture(autouse=True)
def sandbox(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty vault + data dir for every test; no network; module caches cleared."""
    for d in (VAULT, DATA, STATE):
        _fresh_dir(d)
    for d in (config.PAPERS_DIR, config.VAULT_PDFS_DIR, config.READING_LIST.parent, config.PLAN_FILE.parent,
              config.IDEAS_FILE.parent, config.STATE_DIR, config.CHATS_DIR, config.PDF_CACHE_DIR,
              config.BILINGUAL_DIR, config.TEXT_DIR, config.CROPS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    library._arxiv_index.cache_clear()
    library._ext_index.cache_clear()
    library._zotero_pdf_urls.cache_clear()
    venues._catalog.cache_clear()
    monkeypatch.setattr(venues, "DETAILS_FILE", ROOT / "venue_details.json")

    def no_network(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(f"network access attempted in a test: {args[:1]}")

    monkeypatch.setattr("httpx.get", no_network)
    monkeypatch.setattr("httpx.post", no_network)
    return VAULT


@pytest.fixture
def make_note() -> Callable[..., Path]:
    """Write a paper note into the fake Papers folder."""
    def make(stem: str, *, arxiv: str | None = None, source: str | None = None, zotero: str | None = None,
             title: str | None = None, tags: list[str] | None = None, line: str | None = None,
             year: int | None = None, body: str = "") -> Path:
        fm = ["---", "type: paper"]
        if arxiv:
            fm.append(f'arxiv: "{arxiv}"')
        if source:
            fm.append(f"source: {source}")
        if zotero:
            fm.append(f"zotero: {zotero}")
        if year:
            fm.append(f"year: {year}")
        if tags:
            fm.append("tags: [" + ", ".join(tags) + "]")
        if line:
            fm.append(f'research_line: "[[{line}]]"')
        fm.append("---")
        text = "\n".join(fm) + "\n" + (f"- Title: {title}\n" if title else "") + body
        p = config.PAPERS_DIR / f"{stem}.md"
        p.write_text(text, encoding="utf-8")
        library._arxiv_index.cache_clear()
        library._ext_index.cache_clear()
        return p
    return make


@pytest.fixture
def write_vault_file() -> Callable[[str, str], Path]:
    def write(rel: str, text: str = "") -> Path:
        p = VAULT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p
    return write


# ---------- venue catalog fixture (no network) ----------


def _wall(utc: datetime, offset_hours: int = 0) -> str:
    """The wall-clock string ccfddl would write for a UTC instant in a UTC+offset timezone."""
    return (utc + timedelta(hours=offset_hours)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def venue_data() -> SimpleNamespace:
    """A small ccfddl + CORE catalog on disk, with deadlines relative to now.

    KDD (CORE A*) is listed as SIGKDD in ccfddl, CCF A, next abstract in 20 days and paper in 27,
    written in UTC+8 wall-clock time. ACL has only past or TBD deadlines. CIKM is CCF B, due in 90 days.
    FAKECONF exists only in ccfddl.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    kdd_abs, kdd_paper = now + timedelta(days=20), now + timedelta(days=27)
    cikm_paper = now + timedelta(days=90)
    past = now - timedelta(days=200)

    buf = io.StringIO()
    w = csv.writer(buf)
    for row in (
        (101, "ACM SIGKDD Conference on Knowledge Discovery and Data Mining", "KDD", "ICORE2026", "A*"),
        (102, "Pacific-Asia Conference on Knowledge Discovery and Data Mining", "PAKDD", "ICORE2026", "B"),
        (103, "Annual Meeting of the Association for Computational Linguistics", "ACL", "ICORE2026", "A*"),
        (104, "Mini Conference on Data Mining", "KDDM", "ICORE2026", "C"),
        (105, "ACM International Conference on Information and Knowledge Management", "CIKM", "ICORE2026", "A"),
    ):
        w.writerow([*row, "Yes", "4602", "", ""])  # the portal export has no header row
    (config.DATA_DIR / "venues").mkdir(parents=True, exist_ok=True)
    venues.CORE_CSV.write_text(buf.getvalue(), encoding="utf-8")

    conf = venues.CCFDDL_DIR / "conference"
    (conf / "DB").mkdir(parents=True)
    (conf / "AI").mkdir(parents=True)
    (conf / "DB" / "sigkdd.yml").write_text(f"""\
- title: SIGKDD
  description: ACM SIGKDD Conference on Knowledge Discovery and Data Mining
  sub: DB
  rank: {{ccf: A, core: A*}}
  dblp: kdd
  confs:
    - year: {past.year}
      link: https://kdd.example/old
      timeline:
        - deadline: '{_wall(past, 8)}'
      timezone: UTC+8
      date: 'Aug 1-5'
      place: Somewhere
    - year: {kdd_paper.year + 1}
      link: https://kdd.example/next
      timeline:
        - abstract_deadline: '{_wall(kdd_abs, 8)}'
          deadline: '{_wall(kdd_paper, 8)}'
          comment: Research Track
      timezone: UTC+8
      date: 'Aug 9-13'
      place: Elsewhere
- title: CIKM
  description: ACM International Conference on Information and Knowledge Management
  sub: DB
  rank: {{ccf: B, core: A}}
  confs:
    - year: {cikm_paper.year}
      link: https://cikm.example
      timeline:
        - deadline: '{_wall(cikm_paper, -12)}'
      timezone: AoE
""", encoding="utf-8")
    (conf / "AI" / "acl.yml").write_text(f"""\
- title: ACL
  description: Annual Meeting of the Association for Computational Linguistics
  sub: AI
  rank: {{ccf: A, core: A*}}
  confs:
    - year: {past.year}
      timeline:
        - deadline: '{_wall(past, 0)}'
      timezone: UTC
    - year: {now.year + 1}
      timeline:
        - abstract_deadline: TBD
          deadline: TBD
      timezone: AoE
- title: FAKECONF
  description: A conference only ccfddl knows
  sub: SE
  rank: {{ccf: C, core: N}}
  confs: []
""", encoding="utf-8")
    rates = venues.CCFDDL_DIR / "accept_rates" / "DB"
    rates.mkdir(parents=True)
    (rates / "sigkdd.yml").write_text("""\
- title: SIGKDD
  accept_rates:
    - {year: 2023, rate: 0.19, submitted: 1000, accepted: 190, source: example}
    - {year: 2024, rate: 0.22, submitted: 1000, accepted: 220, source: example}
    - {year: 2025, rate: null}
""", encoding="utf-8")
    venues.STAMP.write_text(now.isoformat())
    venues._catalog.cache_clear()
    return SimpleNamespace(now=now, kdd_abstract=kdd_abs, kdd_paper=kdd_paper, cikm_paper=cikm_paper, past=past)

"""Ranking and filter rules for the top-scholar list (app/scholar_rank.py, pure functions)."""
from __future__ import annotations

from datetime import date

import pytest

from app import scholar_rank as R

MODEL = R.compile_model({
    "domain_gate": {"patterns": ["recommend", "recsys", "collaborative filtering"]},
    "topics": [
        {"id": "recsys", "patterns": ["recommend", "recsys"]},
        {"id": "llm", "patterns": ["large language model", r"\bllms?\b"]},
        {"id": "agent", "patterns": [r"\bagent"]},
    ],
})
GROUPS = [["recsys"], ["llm", "agent"]]

SNAPSHOT = """# Ranking fetched 2026-10-25
1. Noisy Physicist · AAAAAAAAAAAA · 319k · — · NOISE: collaboration papers
2. Ada Example · BBBBBBBBBBBB · 124k · Example University
3. Late Professor · CCCCCCCCCCCC · 86k · Old University · deceased 2013
4. No Affiliation · DDDDDDDDDDDD · 27k
5. Small Count · EEEEEEEEEEEE · 1,234 · Somewhere

## Extra people
- Grace Sample · 76k · Sample Institute
"""


def person(name, cites, sid=None, **kw):
    return {"name": name, "citations": cites, "scholar_id": sid, **kw}


# ---------- snapshots ----------

def test_parse_snapshot_rows_flags_and_extra_people():
    snap = R.parse_snapshot_md(SNAPSHOT)
    assert snap["date"] == "2026-10-25"
    by = {e["name"]: e for e in snap["entries"]}
    assert len(by) == 6
    assert by["Ada Example"]["citations"] == 124_000 and by["Ada Example"]["scholar_id"] == "BBBBBBBBBBBB"
    assert by["Noisy Physicist"]["noise"] and by["Noisy Physicist"]["affiliation"] == ""
    assert by["Late Professor"]["deceased"]
    assert by["No Affiliation"]["citations"] == 27_000 and by["No Affiliation"]["affiliation"] == ""
    assert by["Small Count"]["citations"] == 1234
    assert by["Grace Sample"] == {**by["Grace Sample"], "scholar_id": None, "citations": 76_000, "via": "name"}


def test_merge_by_scholar_id_keeps_highest_count_and_labels():
    a = [person("Ada Example", 120_000, "BBBBBBBBBBBB", labels=["label:recommender_systems"])]
    b = [person("Ada  Example", 124_000, "BBBBBBBBBBBB", labels=["label:recommendation"]), person("Grace Sample", 76_000)]
    merged = R.merge_entries(a, b)
    assert [m["name"] for m in merged] == ["Ada Example", "Grace Sample"]
    assert merged[0]["citations"] == 124_000
    assert merged[0]["labels"] == ["label:recommendation", "label:recommender_systems"]


def test_merge_by_name_when_ids_missing():
    merged = R.merge_entries([person("Grace Sample", 70_000)], [person("grace sample", 76_000, affiliation="X")])
    assert len(merged) == 1 and merged[0]["citations"] == 76_000 and merged[0]["affiliation"] == "X"


# ---------- filters ----------

def test_field_papers_needs_every_group_and_skips_workshops_and_duplicates():
    works = [
        {"title": "Large Language Models for Recommendation", "date": "2026-01-01"},
        {"title": "Large language models for recommendation", "date": "2025-06-01"},  # same paper, other version
        {"title": "An Agent that Recommends Movies", "date": "2025-03-01"},
        {"title": "Collaborative filtering at scale", "date": "2025-03-01"},  # no LLM / agent
        {"title": "First Workshop on LLMs for Recommendation", "date": "2025-03-01"},
        {"title": "LLM recommender", "abstract": "", "date": "2020-01-01"},  # outside the window
        {"title": "Reasoning", "abstract": "we use LLMs to recommend items", "date": "2025-09-01"},
    ]
    got = R.field_papers(works, MODEL, GROUPS, since="2024-10-25")
    assert got == ["Large Language Models for Recommendation", "An Agent that Recommends Movies", "Reasoning"]


def test_noise_from_mega_collaborations():
    top = [{"title": "Observation of a new particle", "cites": 9000, "team": 180},
           {"title": "A recommender", "cites": 1000, "team": 2}]
    assert "超大合作" in R.noise_verdict(top, MODEL)


def test_noise_from_out_of_field_citations():
    top = [{"title": "A deep learning library", "cites": 9500, "team": 8},
           {"title": "Recommending with graphs", "cites": 500, "team": 3}]
    assert "领域外" in R.noise_verdict(top, MODEL)


def test_field_scholar_is_not_noise():
    top = [{"title": "Neural collaborative filtering", "cites": 6000, "team": 5},
           {"title": "Visual question answering", "cites": 5000, "team": 6}]
    assert R.noise_verdict(top, MODEL) is None
    assert R.noise_verdict([], MODEL) is None


def test_inactive_verdict():
    today = date(2026, 10, 25)
    assert R.inactive_verdict("2026-05-01", today) is None
    assert "3 年内没有" in R.inactive_verdict("2019-01-01", today)
    assert R.inactive_verdict(None, today)


# ---------- ranking ----------

def checks(table):
    calls = []

    def check(c):
        calls.append(c["name"])
        return {"field_papers": table.get(c["name"], 0)}
    return check, calls


def test_rank_orders_by_citations_and_filters():
    cands = [person("A", 100, "A" * 12), person("B", 300, "B" * 12), person("C", 200, "C" * 12),
             person("D", 250, "D" * 12, noise=True), person("E", 280, "E" * 12, deceased=True)]
    check, calls = checks({"A": 5, "B": 1, "C": 3})
    out = R.rank(cands, check, top_n=2, alternates=0, min_papers=3)
    assert [c["name"] for c in out["list"]] == ["C", "A"]
    assert [c["rank"] for c in out["list"]] == [1, 2]
    reasons = {c["name"]: c["reason"] for c in out["skipped"]}
    assert "已故" in reasons["E"] and "大合作" in reasons["D"] and "1 篇" in reasons["B"]
    assert "D" not in calls and "E" not in calls  # flagged people never cost a lookup


def test_rank_is_lazy_and_fills_alternates():
    cands = [person(n, c) for n, c in [("A", 900), ("B", 800), ("C", 700), ("D", 600), ("E", 500), ("F", 400)]]
    check, calls = checks({n: 3 for n in "ABCDEF"})
    out = R.rank(cands, check, top_n=2, alternates=2, min_papers=3)
    assert [c["name"] for c in out["list"]] == ["A", "B"]
    assert [c["name"] for c in out["alternates"]] == ["C", "D"]
    assert calls == ["A", "B", "C", "D"]  # E and F are never looked up


def test_pinned_stays_even_when_failing_and_takes_a_slot():
    cands = [person("A", 900, "A" * 12), person("B", 800, "B" * 12), person("P", 10, "P" * 12)]
    check, _ = checks({"A": 9, "B": 9, "P": 0})
    out = R.rank(cands, check, top_n=2, alternates=1, min_papers=3, pinned=[{"name": "P", "scholar_id": "P" * 12}])
    assert [c["name"] for c in out["list"]] == ["A", "P"]
    assert out["list"][1]["pinned"] is True
    assert [c["name"] for c in out["alternates"]] == ["B"]


def test_excluded_matches_by_name_when_ids_differ():
    cands = [person("Ada Example", 900, "B" * 12), person("Grace Sample", 800, "G" * 12)]
    check, calls = checks({"Ada Example": 9, "Grace Sample": 9})
    out = R.rank(cands, check, top_n=2, alternates=0, excluded=[{"name": "ada example"}])
    assert [c["name"] for c in out["list"]] == ["Grace Sample"]
    assert out["skipped"][0]["reason"] == "在排除名单里" and "Ada Example" not in calls


def test_unavailable_lookup_stops_the_ranking():
    def check(c):
        raise R.CheckUnavailable("quota")
    with pytest.raises(R.CheckUnavailable):
        R.rank([person("A", 1)], check)


# ---------- suggestion ----------

def test_diff_enter_leave_moves_and_pins_never_leave():
    current = [{"name": "A", "scholar_id": "A" * 12, "rank": 1}, {"name": "B", "scholar_id": "B" * 12, "rank": 2},
               {"name": "P", "scholar_id": "P" * 12, "rank": 3, "pinned": True}]
    proposed = [{"name": "C", "scholar_id": "C" * 12, "rank": 1}, {"name": "A", "scholar_id": "A" * 12, "rank": 2}]
    d = R.diff(current, proposed)
    assert [c["name"] for c in d["enter"]] == ["C"]
    assert [c["name"] for c in d["leave"]] == ["B"]
    assert d["moves"] == [{"name": "A", "from": 1, "to": 2}]

"""Experiment results on the research map (#48): the records, reading CSV / JSON, column mappings, imports as one
atomic batch, watched files, `studio map ...`, the assistant's "record result" proposal, and the routes."""
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import cli, config
from app.rmap import ops, project, proposals, registry
from app.rmap.results import importer, mapping, model, refs, tabular

from .conftest import CONFIG_FILE, PROJECT
from .test_rmap import as_other_mac, conflict, raw


@pytest.fixture(autouse=True)
def results_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Files may only be read from `paths.results_roots`: here, the test's own temp folder."""
    monkeypatch.setattr(config, "RESULTS_ROOTS", [str(tmp_path.resolve())])
    return tmp_path.resolve()


@pytest.fixture
def slug() -> str:
    return ops.create_project("Project Atlas", line="Project A")["slug"]


@pytest.fixture
def exp(slug: str) -> dict[str, str]:
    """A small map: question -> hypothesis -> two experiments."""
    r = ops.apply(slug, [
        {"op": "node.add", "ref": "$q", "set": {"type": "question", "title": "Can agents recommend?"}},
        {"op": "node.add", "ref": "$h", "set": {"type": "hypothesis", "title": "Tool use helps", "status": "active"}, "parent": "$q"},
        {"op": "node.add", "ref": "$x", "set": {"type": "experiment", "title": "Ablation A", "status": "active"}, "parent": "$h"},
        {"op": "node.add", "ref": "$y", "set": {"type": "experiment", "title": "Ablation B"}, "parent": "$h"},
    ])
    return r["refs"]


def results(slug: str, node: str | None = None) -> list[dict]:
    rows = project.get(slug).read()["attachments"]["results"]
    return [r for r in rows if not node or r["node"] == node]


WIDE = "method,split,recall@10,ndcg@10,seed\nBM25,MS MARCO,0.380,0.310,1\nOurs,MS MARCO,0.412,0.350,1\nOurs,MS MARCO,0.420,0.356,2\n"
LONG = "config,dataset,metric,value,date\nOurs,NQ,Recall@10,0.55,2026-10-20\nOurs,NQ,Recall@10,0.57,2026-10-22\nBM25,NQ,Recall@10,0.49,2026-10-20\n"


# ---------- numbers, dates, tables ----------


@pytest.mark.parametrize("cell,want", [("0.412", 0.412), ("41.2%", 41.2), ("1,234.5", 1234.5), ("0,412", 0.412), ("1,234", 1234.0),
                                       ("0.412 ± 0.003", 0.412), ("12 ms", 12.0), (" -3 ", -3.0), (7, 7.0), ("1e-3", 0.001)])
def test_numbers_come_out_of_cells(cell, want):
    assert model.parse_number(cell) == want


@pytest.mark.parametrize("cell", ["n/a", "", None, True, "nan", "inf", "12 apples", float("nan"), "abc"])
def test_what_is_not_a_number_is_none(cell):
    assert model.parse_number(cell) is None


def test_dates_come_out_of_common_spellings():
    assert model.parse_date("2026-10-20") == model.parse_date("2026/10/20") == model.parse_date("20261020") == "2026-10-20"
    assert model.parse_date("2026-10-20T09:30:00") == "2026-10-20"
    assert model.parse_date(1790000000).startswith("2026-")
    assert model.parse_date("yesterday") is None and model.parse_date("") is None


def test_direction_is_guessed_from_the_metric_name_unless_the_record_says():
    assert model.lower_is_better("val_loss") and model.lower_is_better("Latency (ms)") and model.lower_is_better("WER")
    assert not model.lower_is_better("Recall@10") and not model.lower_is_better("accuracy")
    assert model.direction({"metric": "Recall@10", "better": "lower"}) == "lower"
    assert model.direction({"metric": "loss"}) == "lower"


def test_csv_tsv_markdown_json_and_jsonl_all_become_columns_and_rows():
    csv_ = tabular.parse(WIDE)
    assert csv_.format == "csv" and csv_.columns == ["method", "split", "recall@10", "ndcg@10", "seed"] and len(csv_.rows) == 3
    tsv = tabular.parse(WIDE.replace(",", "\t"))  # cells copied from a spreadsheet
    assert tsv.format == "tsv" and tsv.rows[1]["method"] == "Ours"
    assert tabular.parse("a;b\n1;2\n").rows == [{"a": "1", "b": "2"}]  # a semicolon, as some locales export
    md = tabular.parse("| method | recall@10 |\n|---|---:|\n| BM25 | 0.38 |\n| **Ours** | 0.41 |\n")
    assert md.format == "md" and md.rows[1] == {"method": "Ours", "recall@10": "0.41"}
    js = tabular.parse('[{"config": "A", "metrics": {"recall@10": 0.4, "ndcg": {"@10": 0.3}}}, {"config": "B", "metrics": {"recall@10": 0.5}}]')
    assert js.format == "json" and js.columns == ["config", "metrics.recall@10", "metrics.ndcg.@10"] and js.rows[0]["metrics.recall@10"] == 0.4
    named = tabular.parse('{"BM25": {"recall@10": 0.38}, "Ours": {"recall@10": 0.41}}')  # an object of name -> metrics
    assert named.columns == ["name", "recall@10"] and [r["name"] for r in named.rows] == ["BM25", "Ours"]
    nested = tabular.parse('{"runs": [{"config": "A", "value": 1}], "meta": {"x": 1}}')
    assert nested.rows == [{"config": "A", "value": 1}]
    lines = tabular.parse('{"config": "A", "value": 1}\n{"config": "B", "value": 2}\n')
    assert lines.format == "jsonl" and len(lines.rows) == 2
    dup = tabular.parse("x,x\n1,2\n")
    assert dup.columns == ["x", "x (2)"]


@pytest.mark.parametrize("text", ["", "   \n", "only,a,header\n", "{not json", "[1, 2, 3]", '"str"'])
def test_unreadable_tables_are_refused_with_a_reason(text):
    with pytest.raises(ValueError):
        tabular.parse(text)


# ---------- mapping ----------


def test_the_mapping_is_guessed_from_the_header_long_form_and_wide_form():
    wide = mapping.guess(tabular.parse(WIDE))
    assert wide["config"] == {"col": "method"} and wide["dataset"] == {"col": "split"} and wide["run"] == {"col": "seed"}
    assert wide["metrics"] == ["recall@10", "ndcg@10"] and "metric" not in wide
    long = mapping.guess(tabular.parse(LONG))
    assert long["metric"] == {"col": "metric"} and long["value"] == {"col": "value"} and long["date"] == {"col": "date"} and "metrics" not in long


def test_records_in_wide_form_with_baselines_dates_and_direction():
    table = tabular.parse("model,dataset,recall@10,latency_ms,when\nBM25,A,0.38,12,2026-10-20\nOurs,A,0.41,48,2026-10-21\nOurs,A,,50,2026-10-21\nOurs,A,n/a,51,\n")
    mp = {**mapping.guess(table), "baselines": ["bm25"], "lower": ["latency_ms"]}
    recs, skipped = mapping.records(table, mp, default_date="2026-10-26", source="results.csv")
    by = {(r["config"], r["metric"], r["date"]): r for r in recs}
    assert by[("BM25", "recall@10", "2026-10-20")]["baseline"] is True and "baseline" not in by[("Ours", "recall@10", "2026-10-21")]
    assert by[("Ours", "latency_ms", "2026-10-21")]["better"] == "lower" and by[("Ours", "latency_ms", "2026-10-26")]["value"] == 51.0  # no date: the default
    assert all(r["source"] == "results.csv" for r in recs)
    assert [(s["row"], s["metric"]) for s in skipped] == [(5, "recall@10")]  # "n/a" is not a number; an empty cell is just missing


def test_fixed_values_stand_in_for_columns_and_a_mapping_must_be_complete():
    table = tabular.parse("method,score\nA,0.5\nB,0.6\n")
    mp = mapping.clean({"config": {"col": "method"}, "metric": {"const": "Accuracy"}, "value": {"col": "score"}, "dataset": {"const": "Dev"}})
    recs, _ = mapping.records(table, mp, default_date="2026-10-26")
    assert [(r["config"], r["metric"], r["dataset"], r["value"]) for r in recs] == [("A", "Accuracy", "Dev", 0.5), ("B", "Accuracy", "Dev", 0.6)]
    with pytest.raises(ValueError, match="配置"):
        mapping.records(table, {"metric": {"const": "x"}, "value": {"col": "score"}}, default_date="2026-10-26")
    with pytest.raises(ValueError, match="指标"):
        mapping.records(table, {"config": {"col": "method"}}, default_date="2026-10-26")
    with pytest.raises(ValueError, match="没有这一列"):
        mapping.records(table, {"config": {"col": "nope"}, "metrics": ["score"]}, default_date="2026-10-26")
    with pytest.raises(ValueError):
        mapping.clean({"metrics": "recall"})


# ---------- the records ----------


def test_results_hang_on_experiment_and_result_nodes_only(slug: str, exp):
    assert {"results", "result_sources"} <= set(registry.kinds())
    ops.apply(slug, [{"op": "attach.add", "kind": "results", "node": exp["$x"],
                      "set": {"config": "Ours", "metric": "Recall@10", "value": "41.2%", "dataset": "MS MARCO", "date": "2026-10-20"}}])
    [r] = results(slug)
    assert r["value"] == 41.2 and r["node"] == exp["$x"] and r["metric"] == "Recall@10" and r["dataset"] == "MS MARCO"
    with pytest.raises(ValueError, match="只能挂在"):
        ops.apply(slug, [{"op": "attach.add", "kind": "results", "node": exp["$h"], "set": {"config": "A", "metric": "m", "value": 1}}])
    for bad in ({"config": "A", "metric": "m"}, {"config": "A", "metric": "m", "value": "x"}, {"config": "A", "metric": "m", "value": 1, "date": "soon"},
                {"config": "A", "metric": "m", "value": 1, "better": "bigger"}, {"config": "A" * 200, "metric": "m", "value": 1},
                {"config": "A", "metric": "m", "value": 1, "extra": 1}):
        with pytest.raises(ValueError):
            ops.apply(slug, [{"op": "attach.add", "kind": "results", "node": exp["$x"], "set": bad}])
    assert len(results(slug)) == 1


def test_bulk_results_are_one_log_event_and_go_with_their_node(slug: str, exp):
    rows = [{"op": "attach.add", "kind": "results", "node": exp["$x"], "set": {"config": f"c{i}", "metric": "acc", "value": i}} for i in range(30)]
    r = ops.apply(slug, rows, who="import", summary="imported results.csv")
    bulk = [e for e in r["events"] if e["kind"] == "results"]
    assert len(bulk) == 1 and bulk[0]["count"] == 30 and bulk[0]["nodes"] == [exp["$x"]]
    assert [e["summary"] for e in r["events"] if e["kind"] == "log"] == ["imported results.csv"]
    ops.apply(slug, [{"op": "node.delete", "id": exp["$x"]}])
    assert results(slug) == []  # the node took its results along


def test_results_merge_field_by_field_between_two_macs(slug: str, exp):
    ops.apply(slug, [{"op": "attach.add", "kind": "results", "node": exp["$x"], "set": {"config": "Ours", "metric": "acc", "value": 0.5}}])
    f = config.RMAP_DIR / slug / "results" / f"{exp['$x']}.yaml"  # one file per node
    theirs = raw(f)
    as_other_mac(theirs["results"][0], value=0.9)
    conflict(f).write_text(yaml.safe_dump(theirs), encoding="utf-8")
    ops.apply(slug, [{"op": "attach.update", "kind": "results", "id": results(slug)[0]["id"], "set": {"baseline": True}}])  # this Mac: another field
    [r] = results(slug)
    assert r["value"] == 0.9 and r["baseline"] is True and not conflict(f).exists()


# ---------- importing ----------


def test_preview_says_what_it_sees_and_proposes_a_mapping(slug: str, exp):
    p = importer.preview(slug, text=WIDE)
    assert p["format"] == "csv" and p["total"] == 3 and p["mapping"]["metrics"] == ["recall@10", "ndcg@10"] and not p["remembered"]
    assert p["configs"] == ["BM25", "Ours"] and len(p["rows"]) == 3


def test_a_pasted_table_imports_as_one_atomic_batch_and_importing_it_again_changes_nothing(slug: str, exp):
    r = importer.run_import(slug, exp["$x"], text=WIDE, mapping={**mapping.guess(tabular.parse(WIDE)), "baselines": ["BM25"]})
    assert (r["added"], r["updated"], r["unchanged"]) == (6, 0, 0) and r["source"] == "paste"
    rows = results(slug, exp["$x"])
    assert len(rows) == 6 and {x["config"] for x in rows if x.get("baseline")} == {"BM25"} and {x["run"] for x in rows} == {"1", "2"}
    assert all(x["date"] == importer.today() for x in rows)
    log = [e for e in project.get(slug).events(limit=50)["events"] if e["kind"] == "results"]
    assert len(log) == 1 and log[0]["count"] == 6 and log[0]["who"] == "import"
    again = importer.run_import(slug, exp["$x"], text=WIDE, mapping=r["mapping"])
    assert (again["added"], again["updated"], again["unchanged"]) == (0, 0, 6)
    assert len(results(slug)) == 6 and len([e for e in project.get(slug).events(limit=50)["events"] if e["kind"] == "results"]) == 1  # no empty events
    changed = importer.run_import(slug, exp["$x"], text=WIDE.replace("0.412", "0.4125"), mapping=r["mapping"])
    assert (changed["added"], changed["updated"], changed["unchanged"]) == (0, 1, 5)


def test_the_same_measurement_on_another_date_is_a_new_point_for_the_trend(slug: str, exp):
    importer.run_import(slug, exp["$x"], text=LONG)
    assert len(results(slug)) == 3
    importer.run_import(slug, exp["$x"], text=LONG.replace("2026-10-22", "2026-10-25"))
    assert {r["date"] for r in results(slug) if r["config"] == "Ours"} == {"2026-10-20", "2026-10-22", "2026-10-25"}


def test_a_dry_run_writes_nothing_and_a_bad_table_leaves_the_map_as_it_was(slug: str, exp):
    before = raw(config.RMAP_DIR / slug / "map.yaml")
    r = importer.run_import(slug, exp["$x"], text=WIDE, dry_run=True)
    assert r["dry_run"] and r["added"] == 6 and r["sample"][0].startswith("BM25 · MS MARCO")
    assert not (config.RMAP_DIR / slug / "results").exists() and raw(config.RMAP_DIR / slug / "map.yaml") == before
    with pytest.raises(ValueError, match="没有读到"):
        importer.run_import(slug, exp["$x"], text="method,recall\nA,n/a\n", mapping={"config": {"col": "method"}, "metrics": ["recall"]})
    with pytest.raises(KeyError):
        importer.run_import(slug, exp["$h"], text=WIDE)  # a hypothesis has no results
    assert results(slug) == []


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    f = tmp_path / "exp" / "results.csv"
    f.parent.mkdir()
    f.write_text(LONG, encoding="utf-8")
    return f


def test_a_file_import_remembers_the_file_and_its_mapping(slug: str, exp, csv_file: Path):
    r = importer.run_import(slug, exp["$x"], path=str(csv_file), mapping={**mapping.guess(tabular.parse(LONG)), "baselines": ["BM25"]})
    assert r["added"] == 3 and r["source"] == str(csv_file) and r["source_id"]
    [src] = project.get(slug).read()["attachments"]["result_sources"]
    assert src["path"] == str(csv_file) and src["node"] == exp["$x"] and src["mapping"]["baselines"] == ["BM25"] and src["columns"][0] == "config"
    assert not src.get("watch") and all(x["source"] == str(csv_file) for x in results(slug))
    # the next import of the file needs no mapping: it is the one used before (and a file with the same header finds it too)
    t = tabular.read_file(str(csv_file))
    assert mapping.remembered(t, [src], src["path"])["baselines"] == ["BM25"]
    other = importer.preview(slug, path=str(csv_file))
    assert other["remembered"] and other["mapping"]["baselines"] == ["BM25"]
    csv_file.write_text(LONG + "Ours,NQ,Recall@10,0.60,2026-10-26\n", encoding="utf-8")
    again = importer.run_import(slug, exp["$x"], path=str(csv_file))
    assert (again["added"], again["unchanged"]) == (1, 3) and len(project.get(slug).read()["attachments"]["result_sources"]) == 1
    assert {x["config"] for x in results(slug) if x.get("baseline")} == {"BM25"}


def test_files_dated_by_their_own_time_when_they_have_no_date_column(slug: str, exp, tmp_path: Path):
    f = tmp_path / "out.csv"
    f.write_text("method,acc\nA,0.5\n", encoding="utf-8")
    os.utime(f, (1790000000, 1790000000))  # an old run
    importer.run_import(slug, exp["$x"], path=str(f))
    assert results(slug)[0]["date"] == model.parse_date(1790000000)


def test_only_files_inside_a_results_root_are_read(slug: str, exp, tmp_path: Path, monkeypatch):
    for path, why in (("results.csv", "完整路径"), (str(tmp_path / "missing.csv"), "没有这个文件"), (str(tmp_path), "没有这个文件")):
        with pytest.raises(ValueError, match=why):
            tabular.resolve(path)
    for name in ("notes.txt", "run.py", "key.pem", "auth.yaml"):  # an allowlist of extensions: these never
        f = tmp_path / name
        f.write_text("a,b\n1,2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="不支持"):
            tabular.resolve(str(f))
    good = tmp_path / "ok.json"
    good.write_text('[{"config": "A", "value": 1}]', encoding="utf-8")
    assert tabular.resolve(str(good)) == good.resolve()
    # outside every root: a .json that is someone's credentials is not readable just because of its extension
    other = tmp_path.parent / f"{tmp_path.name}-outside"
    other.mkdir()
    (other / "auth.json").write_text('[{"token": "secret"}]', encoding="utf-8")
    with pytest.raises(ValueError, match="results_roots"):
        tabular.resolve(str(other / "auth.json"))
    with pytest.raises(ValueError, match="results_roots"):
        importer.preview(slug, path=str(other / "auth.json"))  # and the preview does not echo its first rows
    # no roots at all: paste only
    monkeypatch.setattr(config, "RESULTS_ROOTS", [])
    with pytest.raises(ValueError, match="results_roots"):
        tabular.resolve(str(good))
    monkeypatch.setattr(config, "RESULTS_ROOTS", [str(tmp_path)])
    # a link inside the root to a file outside it is the file outside
    link = tmp_path / "link.json"
    link.symlink_to(other / "auth.json")
    with pytest.raises(ValueError, match="results_roots"):
        tabular.resolve(str(link))
    dirlink = tmp_path / "dirlink"
    dirlink.symlink_to(other)
    with pytest.raises(ValueError, match="results_roots"):
        tabular.resolve(str(dirlink / "auth.json"))
    # the app's own folders and secrets stay closed even inside a root
    for d in (config.SECRETS_DIR, config.DATA_DIR):
        d.mkdir(parents=True, exist_ok=True)
        f = d / "x.csv"
        f.write_text("a,b\n1,2\n", encoding="utf-8")
        monkeypatch.setattr(config, "RESULTS_ROOTS", [str(d)])
        with pytest.raises(ValueError, match="不能读"):
            tabular.resolve(str(f))
    monkeypatch.setattr(config, "RESULTS_ROOTS", [str(tmp_path)])
    big = tmp_path / "big.csv"
    big.write_text("a,b\n" + "1,2\n" * (tabular.MAX_BYTES // 4 + 10), encoding="utf-8")
    with pytest.raises(ValueError, match="太大"):
        tabular.resolve(str(big))
    assert not (config.RMAP_DIR / slug / "results").exists()


def test_the_command_line_may_name_any_csv_but_a_watch_needs_a_root(capsys, slug: str, exp, tmp_path: Path):
    outside = tmp_path.parent / f"{tmp_path.name}-cli"
    outside.mkdir()
    f = outside / "results.csv"
    f.write_text(LONG, encoding="utf-8")
    code, out, _ = run(capsys, "map", "result", "import", "atlas", "Ablation A", str(f))  # the person at the shell named it
    assert code == 0 and "3 added" in out
    code, _, err = run(capsys, "map", "result", "import", "atlas", "Ablation A", str(f), "--watch")
    assert code == 2 and "results_roots" in err and not importer.watched(slug)
    code, _, err = run(capsys, "map", "result", "import", "atlas", "Ablation A", str(outside / "a.txt"))
    assert code == 2  # the extension allowlist holds for the command line too


def test_a_watched_file_is_read_again_when_it_changes_and_only_by_the_mac_that_watches_it(slug: str, exp, csv_file: Path):
    importer.run_import(slug, exp["$x"], path=str(csv_file), watch=True)
    [w] = importer.watched(slug)
    assert w["node"] == exp["$x"] and project.get(slug).read()["attachments"]["result_sources"][0]["device"] == config.DEVICE
    assert importer.sync(slug) == []  # unchanged since the import
    csv_file.write_text(LONG + "Ours,NQ,Recall@10,0.60,2026-10-26\nBM25,NQ,Recall@10,0.50,2026-10-26\n", encoding="utf-8")
    [done] = importer.sync(slug)
    assert (done["added"], done["updated"]) == (2, 0) and len(results(slug)) == 5
    assert importer.sync(slug) == []
    f = config.RMAP_DIR / slug / "result_sources.yaml"  # the other Mac set it up: this Mac leaves the file alone
    data = raw(f)
    as_other_mac(data["result_sources"][0], device="othermac")
    f.write_text(yaml.safe_dump(data), encoding="utf-8")
    csv_file.write_text(LONG, encoding="utf-8")
    assert importer.watched(slug) == [] and importer.sync(slug) == []


def test_a_watched_file_that_goes_missing_or_breaks_says_so_once(slug: str, exp, csv_file: Path):
    importer.run_import(slug, exp["$x"], path=str(csv_file), watch=True)
    sid = importer.watched(slug)[0]["id"]
    csv_file.write_text("config,dataset,metric,value,date\nOurs,NQ,Recall@10,n/a,2026-10-20\n", encoding="utf-8")
    [bad] = importer.sync(slug)
    assert "没有读到" in bad["error"] and importer.source_states(slug)[sid]["error"]
    assert importer.sync(slug) == []  # not again until the file changes
    csv_file.write_text(LONG, encoding="utf-8")
    [ok] = importer.sync(slug)
    assert "error" not in ok and not importer.source_states(slug)
    csv_file.unlink()
    [gone] = importer.sync(slug)
    assert "没有这个文件" in gone["error"] and len(results(slug)) == 3  # what was imported stays
    assert importer.sync(slug) == []


# ---------- reporting from a script, the command line ----------


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_a_script_reports_results_and_moves_the_status_in_one_write(capsys, slug: str, exp):
    code, out, _ = run(capsys, "map", "result", "add", "Project Atlas", "Ablation A", "recall@10=0.412", "ndcg@10=0.35",
                       "--config", "Ours k=8", "--dataset", "MS MARCO", "--status", "verified", "--source", "run_42.sh")
    assert code == 0 and "2 added" in out and "status -> verified" in out
    rows = results(slug, exp["$x"])
    assert {(r["metric"], r["value"], r["source"], r["date"]) for r in rows} == {("recall@10", 0.412, "run_42.sh", importer.today()),
                                                                                   ("ndcg@10", 0.35, "run_42.sh", importer.today())}
    n = {n["title"]: n for n in project.get(slug).read()["nodes"]}["Ablation A"]
    assert n["status"] == "verified"
    mine = [e for e in project.get(slug).events(limit=50)["events"] if e["who"] == "cli"]
    assert {e["kind"] for e in mine} == {"results", "node", "log"}
    assert len({e.get("group") for e in mine}) == 1 and mine[0].get("group")  # one batch: the numbers, the status and the log line
    assert [e["summary"] for e in mine if e["kind"] == "log"][0].startswith("「Ablation A」的结果：Ours k=8 · MS MARCO · RECALL@10 = 0.412")
    code, out, _ = run(capsys, "map", "result", "add", "atlas", exp["$x"], "recall@10=0.412", "--config", "Ours k=8", "--dataset", "MS MARCO",
                       "--source", "run_42.sh")  # the same number again: nothing to write
    assert code == 0 and out.startswith("unchanged") and len(results(slug)) == 2
    code, out, _ = run(capsys, "map", "result", "add", "atlas", "ablation a", "recall@10=0.43", "--config", "Ours k=8", "--dataset", "MS MARCO",
                       "--source", "run_42.sh")  # a better number on the same day: updated
    assert code == 0 and "1 updated" in out and {r["value"] for r in results(slug) if r["metric"] == "recall@10"} == {0.43}


def test_the_cli_lists_shows_and_updates_status(capsys, slug: str, exp):
    code, out, _ = run(capsys, "map", "list")
    assert code == 0 and slug in out and "Project A" in out and "4 nodes" in out
    code, out, _ = run(capsys, "map", "show", "atlas")
    assert code == 0 and exp["$x"] in out and "Ablation A" in out and "Can agents recommend?" in out
    code, out, _ = run(capsys, "map", "status", slug, "Ablation B", "blocked", "--note", "GPU queue is full")
    assert code == 0 and "todo -> blocked" in out
    assert {n["title"]: n["status"] for n in project.get(slug).read()["nodes"]}["Ablation B"] == "blocked"
    assert any(e["kind"] == "log" and e["summary"] == "GPU queue is full" for e in project.get(slug).events(limit=10)["events"])
    code, out, _ = run(capsys, "map", "status", slug, "Ablation B", "blocked")
    assert code == 0 and out.startswith("unchanged")
    code, out, _ = run(capsys, "map", "note", slug, "ran the ablation, tool use did not help", "--node", "Ablation A")
    assert code == 0
    ev = [e for e in project.get(slug).events(node=exp["$x"], limit=10)["events"] if e["kind"] == "log"]
    assert ev and ev[0]["summary"].startswith("ran the ablation")
    code, out, _ = run(capsys, "map", "show", "atlas", "--json")
    assert json.loads(out)["nodes"][2]["title"] == "Ablation A"


def test_the_cli_says_what_is_wrong(capsys, slug: str, exp):
    for argv, why in ((("map", "show", "nope"), "没有这个课题"), (("map", "status", "atlas", "nope", "verified"), "没有这个实验或结果节点"),
                      (("map", "result", "add", "atlas", "Ablation A", "recall=abc", "--config", "A"), "数值不对"),
                      (("map", "result", "add", "atlas", "Ablation A", "recall0.4", "--config", "A"), "METRIC=VALUE"),
                      (("map", "result", "add", "atlas", "Tool use helps", "acc=1", "--config", "A"), "没有这个实验或结果节点")):
        code, out, err = run(capsys, *argv)
        assert code == 2 and why in err and not out, (argv, err)
    assert results(slug) == []
    ops.create_project("Project Atlas Two")  # "atlas" now matches two projects
    code, _, err = run(capsys, "map", "show", "atlas")
    assert code == 2 and "多个课题" in err


def test_the_cli_imports_a_file_and_stdin(capsys, monkeypatch, slug: str, exp, csv_file: Path):
    code, out, _ = run(capsys, "map", "result", "import", "atlas", "Ablation A", str(csv_file), "--baseline", "BM25", "--dry-run")
    assert code == 0 and out.startswith("would import: 3 added") and results(slug) == []
    code, out, _ = run(capsys, "map", "result", "import", "atlas", "Ablation A", str(csv_file), "--baseline", "BM25", "--watch")
    assert code == 0 and "3 added" in out and "[watching]" in out
    assert {r["config"] for r in results(slug) if r.get("baseline")} == {"BM25"} and importer.watched(slug)
    code, out, _ = run(capsys, "map", "result", "import", "atlas", "Ablation A", str(csv_file))  # the mapping and baselines are remembered
    assert code == 0 and "0 added" in out and "3 unchanged" in out
    monkeypatch.setattr(sys, "stdin", io.StringIO(WIDE))
    code, out, _ = run(capsys, "map", "result", "import", "atlas", "Ablation B", "-", "--col", "dataset=@Dev", "--lower", "ndcg@10")
    assert code == 0 and "6 added" in out and "from stdin" in out
    rows = results(slug, exp["$y"])
    assert {r["dataset"] for r in rows} == {"Dev"} and {r["better"] for r in rows if r["metric"] == "ndcg@10"} == {"lower"}
    code, out, _ = run(capsys, "map", "result", "list", "atlas", "Ablation B", "--metric", "recall@10")
    assert code == 0 and out.count("RECALL@10") == 3
    code, out, _ = run(capsys, "map", "result", "list", "atlas", "--json")
    assert len(json.loads(out)) == 9
    csv_file.write_text(LONG + "Ours,NQ,Recall@10,0.9,2026-10-26\n", encoding="utf-8")
    code, out, _ = run(capsys, "map", "result", "sync")
    assert code == 0 and "1 added" in out
    code, out, _ = run(capsys, "map", "result", "sync")
    assert code == 0 and out.startswith("nothing changed")


def test_the_cli_works_as_a_real_process_with_the_app_stopped(slug: str, exp):
    """`./studio map ...` in its own process, as a Claude session or a training script would run it."""
    env = {**os.environ, "STUDIO_CONFIG": str(CONFIG_FILE), "STUDIO_DEVICE": "script-mac"}
    base = [sys.executable, "-m", "app.cli"]
    r = subprocess.run([*base, "map", "result", "add", "atlas", "Ablation A", "acc=0.9", "--config", "Ours", "--status", "verified"],
                       cwd=PROJECT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    [row] = results(slug, exp["$x"])
    assert row["value"] == 0.9
    assert {e["device"] for e in project.get(slug).events(limit=20)["events"] if e["kind"] == "results"} == {"script-mac"}


# ---------- refs ----------


def test_projects_and_nodes_are_found_by_what_people_type(slug: str, exp):
    assert refs.find_project("project atlas") == refs.find_project("Project A") == refs.find_project("atlas") == slug
    m = project.get(slug).read()
    assert refs.find_node(m, "ablation a")["id"] == exp["$x"] and refs.find_node(m, exp["$x"][:6])["id"] == exp["$x"]
    assert refs.find_node(m, "Tool use", types=("hypothesis",))["id"] == exp["$h"]
    with pytest.raises(KeyError, match="多个"):
        refs.find_node(m, "Ablation")
    with pytest.raises(KeyError, match="没有这个实验或结果节点"):
        refs.find_node(m, "Tool use", types=("experiment", "result"))


# ---------- the assistant ----------


def call(tool: str, **args):
    from app import studio_tools
    seen: list[dict] = []

    async def emit(ev):
        seen.append(ev)

    tools = {t.name: t for t in studio_tools._tools(emit)}
    out = asyncio.run(tools[tool].handler(args))
    return json.loads(out["content"][0]["text"]) if not out.get("is_error") else out["content"][0]["text"], seen


def test_the_assistant_proposes_results_and_nothing_is_written_until_the_user_confirms(slug: str, exp):
    out, seen = call("map_record_result", project="Project Atlas", node="Ablation A", status="verified",
                     results=[{"config": "Ours", "metric": "Recall@10", "value": 0.412, "dataset": "MS MARCO"},
                              {"config": "BM25", "metric": "Recall@10", "value": "0.38", "dataset": "MS MARCO", "baseline": True}])
    assert out["changes"] == ["Ours · MS MARCO · RECALL@10 = 0.412", "BM25 · MS MARCO · RECALL@10 = 0.38"] and "不能应用" in out["note"]
    assert results(slug) == [] and seen[0]["type"] == "studio_changed" and seen[0]["area"] == "rmap"
    [p] = proposals.pending(slug)
    assert p["summary"].startswith("「Ablation A」的结果：Ours · MS MARCO") and {n["title"]: n["status"] for n in project.get(slug).read()["nodes"]}["Ablation A"] == "active"
    done = proposals.apply(p["id"])
    assert done["slug"] == slug and len(results(slug)) == 2 and {r["source"] for r in results(slug)} == {"assistant"}
    assert {n["title"]: n["status"] for n in project.get(slug).read()["nodes"]}["Ablation A"] == "verified"
    assert all(e["who"] == "assistant" for e in project.get(slug).events(limit=20)["events"] if e["kind"] in ("results", "log"))
    again, _ = call("map_record_result", project="atlas", node="Ablation A", results=[{"config": "Ours", "metric": "Recall@10", "value": 0.412, "dataset": "MS MARCO", "source": "assistant"}])
    assert "已经记录过" in again["note"] and not proposals.pending(slug)


def test_the_assistants_own_sentence_still_lists_the_numbers_in_the_proposal(slug: str, exp):
    call("map_record_result", project="atlas", node="Ablation A", summary="ablation done, rewrite matters less",
         results=[{"config": "Ours", "metric": "Recall@10", "value": 0.4}])
    [p] = proposals.pending(slug)
    assert p["summary"] == "ablation done, rewrite matters less (Ours · RECALL@10 = 0.4)"
    importer.report(slug, "Ablation A", [{"config": "Ours", "metric": "Recall@10", "value": 0.4}], summary="my own sentence")
    assert [e["summary"] for e in project.get(slug).events(kind="log", limit=5)["events"]][0] == "my own sentence"  # the CLI keeps what it was told


def test_the_assistant_gets_clear_errors_and_sees_results_on_the_map(slug: str, exp):
    for args, why in (({"project": "nope", "node": "Ablation A", "results": [{"config": "A", "metric": "m", "value": 1}]}, "没有这个课题"),
                      ({"project": "atlas", "node": "Tool use helps", "results": [{"config": "A", "metric": "m", "value": 1}]}, "没有这个实验或结果节点"),
                      ({"project": "atlas", "node": "Ablation A", "results": [{"config": "A", "metric": "m", "value": "lots"}]}, "数值不对"),
                      ({"project": "atlas", "node": "Ablation A", "results": []}, "没有要汇报"),
                      ({"project": "atlas", "node": "Ablation A", "results": [{"config": "A", "metric": "m", "value": 1}], "status": "great"}, "status")):
        out, _ = call("map_record_result", **args)
        assert isinstance(out, str) and why in out, args
    assert not proposals.pending(slug)
    importer.report(slug, "Ablation A", [{"config": "Ours", "metric": "acc", "value": 0.7, "baseline": True}])
    brief, _ = call("map_read", project="Project Atlas")
    [node] = [n for n in brief["nodes"] if n["title"] == "Ablation A"]
    assert node["results"] == [f"Ours · ACC = 0.7 ({importer.today()}, baseline)"]


# ---------- routes ----------


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


def test_routes_preview_import_sync_and_status(client: TestClient, slug: str, exp, csv_file: Path):
    base = f"/api/rmap/projects/{slug}/results"
    meta = client.get("/api/rmap/meta").json()
    assert {"results", "result_sources"} <= {k["name"] for k in meta["kinds"]}
    p = client.post(f"{base}/preview", json={"text": WIDE}).json()
    assert p["mapping"]["config"] == {"col": "method"} and p["total"] == 3
    assert client.post(f"{base}/preview", json={"text": ""}).status_code == 400
    r = client.post(f"{base}/import", json={"node": exp["$x"], "text": WIDE, "mapping": p["mapping"], "dry_run": True}).json()
    assert r["dry_run"] and r["added"] == 6 and results(slug) == []
    r = client.post(f"{base}/import", json={"node": exp["$x"], "text": WIDE, "mapping": p["mapping"]}).json()
    assert r["added"] == 6 and len(results(slug)) == 6
    assert client.post(f"{base}/import", json={"node": "nope", "text": WIDE}).status_code == 404
    assert client.post(f"{base}/import", json={"node": exp["$x"], "path": "relative.csv"}).status_code == 400
    r = client.post(f"{base}/import", json={"node": exp["$y"], "path": str(csv_file), "watch": True}).json()
    assert r["added"] == 3 and r["source_id"]
    st = client.get(f"{base}/status").json()
    assert st["watching"] == [r["source_id"]] and st["running"] and st["device"] == config.DEVICE and st["errors"] == {}
    csv_file.write_text(LONG + "Ours,NQ,Recall@10,0.9,2026-10-26\n", encoding="utf-8")
    assert client.post(f"{base}/sync").json()["synced"][0]["added"] == 1
    assert client.post(f"{base}/sync", json={"force": True}).json()["synced"][0]["added"] == 0
    from app.rmap.results import watcher
    watcher.stop()
    assert client.get("/api/rmap/projects/nope/results/status").status_code == 404
    listed = client.get(f"/api/rmap/projects/{slug}/attachments/results", params={"node": exp["$y"]}).json()["results"]
    assert len(listed) == 4  # the generic route works for the kind too


def test_file_suggestions_come_from_the_results_roots_in_studio_toml(client: TestClient, tmp_path: Path, monkeypatch):
    code = tmp_path / "code"
    (code / "run1").mkdir(parents=True)
    (code / "run1" / "metrics.json").write_text("[]", encoding="utf-8")
    (code / "run1" / "notes.txt").write_text("x", encoding="utf-8")
    (code / ".git").mkdir()
    (code / ".git" / "hidden.csv").write_text("a,b\n", encoding="utf-8")
    (code / "node_modules").mkdir()
    (code / "node_modules" / "x.csv").write_text("a,b\n", encoding="utf-8")
    (code / "results.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(config, "RESULTS_ROOTS", [])
    none = client.get("/api/rmap/results/files").json()
    assert none["files"] == [] and "paths.results_roots" in none["message"]
    monkeypatch.setattr(config, "RESULTS_ROOTS", [str(code)])
    got = client.get("/api/rmap/results/files").json()
    assert {Path(f["path"]).name for f in got["files"]} == {"metrics.json", "results.csv"} and got["message"] is None
    assert [Path(f["path"]).name for f in client.get("/api/rmap/results/files", params={"q": "metrics"}).json()["files"]] == ["metrics.json"]


# ---------- fixes from the review of PR #60 ----------


def test_a_table_without_dates_is_the_same_measurements_on_every_day(slug: str, exp, monkeypatch):
    importer.run_import(slug, exp["$x"], text=WIDE)
    n = len(results(slug))
    monkeypatch.setattr(importer, "today", lambda: "2026-12-01")  # another day, the same table pasted again
    again = importer.run_import(slug, exp["$x"], text=WIDE)
    assert (again["added"], again["updated"], again["unchanged"]) == (0, 0, n) and len(results(slug)) == n
    changed = importer.run_import(slug, exp["$x"], text=WIDE.replace("0.380", "0.390"))  # a new number is the measurement now
    assert (changed["added"], changed["updated"]) == (0, 1)
    [bm25] = [r for r in results(slug) if r["config"] == "BM25" and r["metric"] == "recall@10"]
    assert bm25["value"] == 0.39 and bm25["date"] == "2026-12-01" and bm25["date_inferred"] is True


def test_a_watched_file_without_dates_only_adds_the_rows_that_are_new(slug: str, exp, tmp_path: Path):
    f = tmp_path / "out.csv"
    f.write_text("config,dataset,metric,value\nOurs,NQ,recall,0.5\nBM25,NQ,recall,0.4\n", encoding="utf-8")
    os.utime(f, (1790000000, 1790000000))
    importer.run_import(slug, exp["$x"], path=str(f), watch=True)
    assert len(results(slug)) == 2
    f.write_text(f.read_text(encoding="utf-8") + "Ours,MS,recall,0.6\n", encoding="utf-8")
    os.utime(f, (1790000000 + 86400 * 3, 1790000000 + 86400 * 3))  # three days later
    [done] = importer.sync(slug)
    assert (done["added"], done["updated"], done["unchanged"]) == (1, 0, 2) and len(results(slug)) == 3  # not 2 + 3
    dates = {r["dataset"]: r["date"] for r in results(slug) if r["config"] == "Ours"}
    assert dates["NQ"] == model.parse_date(1790000000) and dates["MS"] == model.parse_date(1790000000 + 86400 * 3)


def test_a_date_in_the_data_still_makes_a_new_point_for_each_date(slug: str, exp):
    importer.run_import(slug, exp["$x"], text=LONG)
    importer.run_import(slug, exp["$x"], text=LONG.replace("2026-10-22", "2026-10-25"))
    assert {r["date"] for r in results(slug) if r["config"] == "Ours"} == {"2026-10-20", "2026-10-22", "2026-10-25"}
    assert not any(r.get("date_inferred") for r in results(slug))


def propose_result(config_: str, value: float, **extra) -> dict:
    out, _ = call("map_record_result", project="atlas", node="Ablation A", results=[{"config": config_, "metric": "acc", "value": value, "date": "2026-10-20", **extra}])
    return out


def test_two_proposals_on_one_value_do_not_silently_overwrite_each_other(slug: str, exp):
    importer.report(slug, "Ablation A", [{"config": "Ours", "metric": "acc", "value": 0.5, "date": "2026-10-20"}], source="assistant")
    propose_result("Ours", 0.6)
    propose_result("Ours", 0.7)
    first, second = sorted(proposals.pending(slug), key=lambda p: p["created_at"] + p["id"])
    assert first["base"] and second["base"]  # the record and the field it changes, as they were
    older, newer = (first, second) if first["ops"][0]["set"]["value"] == 0.6 else (second, first)
    proposals.apply(older["id"])
    assert results(slug)[0]["value"] == older["ops"][0]["set"]["value"]
    with pytest.raises(ValueError, match="之后改过"):
        proposals.apply(newer["id"])
    assert results(slug)[0]["value"] == older["ops"][0]["set"]["value"]  # the confirmed one stands
    assert [p["id"] for p in proposals.pending(slug)] == [newer["id"]]  # the other stays, to be discarded and asked again


def test_a_proposal_does_not_mind_changes_to_other_fields_of_the_record(slug: str, exp):
    importer.report(slug, "Ablation A", [{"config": "Ours", "metric": "acc", "value": 0.5, "date": "2026-10-20"}], source="assistant")
    propose_result("Ours", 0.6)
    ops.apply(slug, [{"op": "attach.update", "kind": "results", "id": results(slug)[0]["id"], "set": {"baseline": True}}])
    [p] = proposals.pending(slug)
    proposals.apply(p["id"])
    assert results(slug)[0]["value"] == 0.6 and results(slug)[0]["baseline"] is True


def test_two_proposals_that_add_the_same_measurement_do_not_write_it_twice(slug: str, exp):
    propose_result("Ours", 0.6)
    propose_result("Ours", 0.6)
    a, b = proposals.pending(slug)
    assert a["absent"] and a["absent"][0]["kind"] == "results"
    proposals.apply(a["id"])
    with pytest.raises(ValueError, match="已经有同样的"):
        proposals.apply(b["id"])
    assert len(results(slug)) == 1
    proposals.discard(b["id"])  # (what the user does with it)
    propose_result("Ours", 0.9, run="2")  # a different measurement (another run) is no duplicate
    [c] = proposals.pending(slug)
    proposals.apply(c["id"])
    assert len(results(slug)) == 2


def test_a_proposal_to_delete_a_result_is_stale_once_the_record_changed(slug: str, exp):
    importer.report(slug, "Ablation A", [{"config": "Ours", "metric": "acc", "value": 0.5}])
    rid = results(slug)[0]["id"]
    prop = proposals.propose(ops_=[{"op": "attach.delete", "kind": "results", "id": rid}], summary="drop it", slug=slug)
    ops.apply(slug, [{"op": "attach.update", "kind": "results", "id": rid, "set": {"value": 0.8}}])
    with pytest.raises(ValueError, match="之后改过"):
        proposals.apply(prop["id"])
    assert len(results(slug)) == 1


def test_a_manual_import_keeps_the_watch_the_page_did_not_set(slug: str, exp, csv_file: Path):
    importer.run_import(slug, exp["$x"], path=str(csv_file), watch=True)
    pre = importer.preview(slug, path=str(csv_file), node=exp["$x"])
    assert pre["existing"] == {"id": pre["existing"]["id"], "watch": True, "device": config.DEVICE}
    importer.run_import(slug, exp["$x"], path=str(csv_file))  # what the page sends when the box was not touched: no `watch`
    assert [w["node"] for w in importer.watched(slug)] == [exp["$x"]]
    f = config.RMAP_DIR / slug / "result_sources.yaml"  # the other Mac watches it: this Mac's manual import leaves that alone
    data = raw(f)
    as_other_mac(data["result_sources"][0], device="othermac")
    f.write_text(yaml.safe_dump(data), encoding="utf-8")
    importer.run_import(slug, exp["$x"], path=str(csv_file))
    [src] = project.get(slug).read()["attachments"]["result_sources"]
    assert src["watch"] is True and src["device"] == "othermac"
    importer.run_import(slug, exp["$x"], path=str(csv_file), watch=False)  # only an explicit choice turns it off
    [src] = project.get(slug).read()["attachments"]["result_sources"]
    assert not src.get("watch") and not src.get("device")
    assert importer.preview(slug, text=LONG, node=exp["$x"])["existing"] is None


def test_two_processes_importing_the_same_table_write_it_once(slug: str, exp):
    env = {**os.environ, "STUDIO_CONFIG": str(CONFIG_FILE), "STUDIO_DEVICE": "test"}
    procs = [subprocess.Popen([sys.executable, "-m", "app.cli", "map", "result", "import", "atlas", "Ablation A", "-"], cwd=PROJECT, env=env,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    for p in procs:
        p.stdin.write(WIDE)
        p.stdin.close()
    out = [(p.wait(timeout=120), p.stderr.read()) for p in procs]
    assert all(code == 0 for code, _ in out), out
    assert len(results(slug)) == 6  # not 12, 18 or 24: each saw what the one before had written


def test_the_watch_state_survives_many_writers(slug: str):
    import threading
    errors: list[Exception] = []

    def work(i: int) -> None:
        try:
            for j in range(10):
                importer._remember_state(slug, f"rf{i:02d}{j}", {"mtime_ns": i, "size": j})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and len([k for k in importer._state() if k.startswith(slug)]) == 80
    assert not list(importer.STATE_FILE.parent.glob("results-watch.json.*.tmp"))


def test_the_decimal_mark_is_decided_per_file_not_per_cell():
    semi = tabular.parse("config;metric;value\nA;acc;1,234\nB;acc;0,5\n")
    assert semi.delimiter == ";" and tabular.decimal_mark(semi) == ","
    recs, _ = mapping.records(semi, mapping.guess(semi), default_date="2026-10-26")
    assert [r["value"] for r in recs] == [1.234, 0.5]  # not 1234
    euro = tabular.parse("config;metric;value\nA;acc;1.234,5\nB;acc;2.000,25\n")
    assert [r["value"] for r in mapping.records(euro, mapping.guess(euro), default_date="2026-10-26")[0]] == [1234.5, 2000.25]
    point = tabular.parse('config,metric,value\nA,acc,"1,234.5"\nB,acc,0.5\n')
    assert tabular.decimal_mark(point) == "." and [r["value"] for r in mapping.records(point, mapping.guess(point), default_date="2026-10-26")[0]] == [1234.5, 0.5]
    thousands = tabular.parse('config,metric,value\nA,acc,"1,234"\nB,acc,"2,500"\n')  # no evidence, a comma file: thousands
    assert [r["value"] for r in mapping.records(thousands, mapping.guess(thousands), default_date="2026-10-26")[0]] == [1234.0, 2500.0]
    # the user can say it
    forced = {**mapping.guess(thousands), "decimal": ","}
    assert [r["value"] for r in mapping.records(thousands, forced, default_date="2026-10-26")[0]] == [1.234, 2.5]
    assert mapping.clean({"config": {"col": "c"}, "decimal": ","})["decimal"] == ","
    with pytest.raises(ValueError, match="decimal"):
        mapping.clean({"decimal": ";"})
    assert model.parse_number("1.234,5", ",") == 1234.5 and model.parse_number("1,234.5", ".") == 1234.5 and model.parse_number("0,5 ± 0,1", ",") == 0.5


def test_a_semicolon_csv_imports_with_its_own_decimal_comma(slug: str, exp):
    r = importer.run_import(slug, exp["$x"], text="method;recall;ndcg\nA;0,41;0,33\nB;1.234,5;0,2\n")
    assert r["added"] == 4 and r["mapping"]["decimal"] == ","
    assert {x["value"] for x in results(slug)} == {0.41, 0.33, 1234.5, 0.2}


def test_the_limit_counts_records_not_rows(slug: str, exp):
    wide = "config,m1,m2\n" + "".join(f"c{i},{i},{i}\n" for i in range(ops.MAX_IMPORT_OPS // 2 + 1))  # half the cap in rows, all of it in records
    assert len(wide) < tabular.MAX_BYTES
    with pytest.raises(ValueError, match="最多导入"):
        importer.run_import(slug, exp["$x"], text=wide)
    with pytest.raises(ValueError, match="最多导入"):
        importer.run_import(slug, exp["$x"], text=wide, dry_run=True)  # the preview says so too, before anything is written
    assert not (config.RMAP_DIR / slug / "results").exists()
    ok = "config,m1,m2\n" + "".join(f"c{i},{i},{i}\n" for i in range(50))
    assert importer.run_import(slug, exp["$x"], text=ok)["added"] == 100


def test_flagging_a_config_with_more_than_a_batch_of_records_still_works(client: TestClient, slug: str, exp):
    rows = "config,dataset,metric,value\n" + "".join(f"BM25,d{i},acc,{i / 1000}\n" for i in range(1000)) + "Ours,d0,acc,0.9\n"
    importer.run_import(slug, exp["$x"], text=rows)
    base = f"/api/rmap/projects/{slug}/results/set"
    r = client.post(base, json={"node": exp["$x"], "config": "BM25", "set": {"baseline": True}})
    assert r.status_code == 200 and r.json() == {"updated": 1000}
    assert {x["config"] for x in results(slug) if x.get("baseline")} == {"BM25"} and sum(1 for x in results(slug) if x.get("baseline")) == 1000
    assert client.post(base, json={"node": exp["$x"], "config": "BM25", "set": {"baseline": True}}).json() == {"updated": 0}  # nothing left to do
    assert client.post(base, json={"node": exp["$x"], "config": "BM25", "set": {"baseline": None}}).json() == {"updated": 1000}
    assert not any(x.get("baseline") for x in results(slug))
    assert client.post(base, json={"node": exp["$x"], "metric": "ACC", "set": {"better": "lower"}}).json() == {"updated": 1001}
    assert {x["better"] for x in results(slug)} == {"lower"}
    assert client.post(base, json={"set": {"value": 1}}).status_code == 400 and client.post(base, json={"set": {"better": "bigger"}}).status_code == 400
    assert client.post(f"/api/rmap/projects/{slug}/results/set", json={"node": "nope", "set": {"baseline": True}}).status_code == 404


def test_nan_and_infinity_are_empty_cells_not_crashes(client: TestClient, slug: str, exp):
    text = '[{"config": NaN, "metric": "acc", "value": 0.5}, {"config": "A", "dataset": Infinity, "metric": "acc", "value": NaN},' \
           ' {"config": "B", "dataset": -Infinity, "metric": "acc", "value": 0.7}, {"config": 1e999, "metric": "acc", "value": 0.2}]'
    t = tabular.parse(text)
    assert t.rows[0]["config"] is None and t.rows[2]["dataset"] is None
    r = importer.run_import(slug, exp["$x"], text=text)
    assert [(x["config"], x["value"]) for x in results(slug)] == [("B", 0.7)] and r["skipped"] >= 1
    # over HTTP: an answer, never a 500
    base = f"/api/rmap/projects/{slug}/results"
    for body in ({"text": text}, {"text": '{"config": NaN, "value": NaN}'}):
        assert client.post(f"{base}/preview", json=body).status_code in (200, 400)
    assert client.post(f"{base}/import", json={"node": exp["$x"], "text": text, "dry_run": True}).status_code == 200


def test_one_broken_watched_file_does_not_stop_the_next(slug: str, exp, tmp_path: Path, monkeypatch):
    files = {}
    for node in ("$x", "$y"):
        f = tmp_path / f"{node[1:]}.csv"
        f.write_text(LONG, encoding="utf-8")
        importer.run_import(slug, exp[node], path=str(f), watch=True)
        f.write_text(LONG + "Ours,NQ,Recall@10,0.9,2026-10-26\n", encoding="utf-8")
        files[node] = f
    real = importer.run_import

    def flaky(slug_, node, **kw):
        if node == exp["$x"]:
            raise OverflowError("cannot convert float infinity to integer")
        return real(slug_, node, **kw)

    monkeypatch.setattr(importer, "run_import", flaky)
    done = {d["path"]: d for d in importer.sync(slug)}
    assert "infinity" in done[str(files["$x"])]["error"] and done[str(files["$y"])]["added"] == 1
    assert len(results(slug, exp["$y"])) == 4


def test_results_live_one_file_per_node_and_a_write_leaves_the_others_alone(slug: str, exp):
    importer.run_import(slug, exp["$x"], text=WIDE)
    importer.run_import(slug, exp["$y"], text=WIDE)
    folder = config.RMAP_DIR / slug / "results"
    assert sorted(p.name for p in folder.glob("*.yaml")) == sorted([f"{exp['$x']}.yaml", f"{exp['$y']}.yaml"])
    other = folder / f"{exp['$y']}.yaml"
    before = (other.read_bytes(), other.stat().st_mtime_ns)
    importer.run_import(slug, exp["$x"], text=WIDE.replace("0.412", "0.5"))
    ops.apply(slug, [{"op": "attach.update", "kind": "results", "id": results(slug, exp["$x"])[0]["id"], "set": {"baseline": True}}])
    assert (other.read_bytes(), other.stat().st_mtime_ns) == before  # not even rewritten
    assert {r["node"] for r in results(slug)} == {exp["$x"], exp["$y"]} and len(results(slug, exp["$y"])) == 6
    one = project.get(slug).kind_store(model.RESULTS).load_node(exp["$y"])
    assert len(one) == 6 and project.get(slug).kind_store(model.RESULTS).load_node("nope") == []
    with pytest.raises(ValueError):
        project.get(slug).kind_store(model.RESULTS).shard("../evil")
    ops.apply(slug, [{"op": "node.delete", "id": exp["$x"]}])  # the node's results go with it, the other node's stay
    assert results(slug, exp["$x"]) == [] and len(results(slug, exp["$y"])) == 6
    assert all(r.get("deleted") for r in raw(folder / f"{exp['$x']}.yaml")["results"])


def test_listings_do_not_read_the_results(slug: str, exp, monkeypatch, capsys):
    importer.run_import(slug, exp["$x"], text=WIDE)
    from app.rmap.records import ShardedStore

    def boom(*a, **k):
        raise AssertionError("results were read for a listing")

    monkeypatch.setattr(ShardedStore, "load", boom)
    monkeypatch.setattr(ShardedStore, "load_node", boom)
    [row] = project.listing()
    assert row["slug"] == slug and row["nodes"] == 4
    assert project.get(slug).read(attachments=False)["attachments"] == {}
    assert refs.find_project("atlas") == slug
    code, out, _ = run(capsys, "map", "list")
    assert code == 0 and slug in out


def test_a_csv_line_starting_with_a_hash_is_data_not_a_comment(slug: str, exp):
    t = tabular.parse("config,metric,value\n#3,acc,0.5\n#4,acc,0.6\n")
    assert [r["config"] for r in t.rows] == ["#3", "#4"]
    importer.run_import(slug, exp["$x"], text="config,metric,value\n#3,acc,0.5\n")
    assert results(slug)[0]["config"] == "#3"


def test_the_assistants_proposal_toast_follows_the_interface_language(slug: str, exp):
    _, seen = call("map_record_result", project="atlas", node="Ablation A", results=[{"config": "Ours", "metric": "acc", "value": 1}])
    assert seen[0]["summary"].startswith("研究地图：Project Atlas 的实验结果待你确认")
    config.save_settings({"ui_language": "en"})
    _, seen = call("map_record_result", project="atlas", node="Ablation A", results=[{"config": "Ours", "metric": "acc", "value": 2}])
    assert seen[0]["summary"].startswith("Research map: results for Project Atlas to confirm")


# ---------- the page's number crunching in a JavaScript engine ----------

JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")


@pytest.mark.parametrize("engine", ["node", "jsc"])
def test_results_data_in_a_javascript_engine(engine, tmp_path):
    exe = shutil.which("node") if engine == "node" else (str(JSC) if JSC.exists() else None)
    if not exe:
        pytest.skip(f"{engine} is not installed")
    shutil.copy(PROJECT / "web" / "rmap" / "results" / "data.js", tmp_path / "data.mjs")
    shutil.copy(PROJECT / "tests" / "js" / "rmap_results.test.mjs", tmp_path / "test.mjs")
    args = [exe, "-m", "test.mjs"] if engine == "jsc" else [exe, "test.mjs"]
    r = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr


def test_the_js_and_python_agree_on_which_metrics_are_lower_is_better():
    js = (PROJECT / "web" / "rmap" / "results" / "data.js").read_text(encoding="utf-8")
    pattern = next(line for line in js.splitlines() if line.startswith("const LOWER = "))
    assert pattern.split("/")[1] == model._LOWER.pattern

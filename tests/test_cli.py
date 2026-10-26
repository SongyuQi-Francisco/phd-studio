"""app/cli.py (`studio`): reading list, milestones, ideas, deadlines. Runs in-process and as a real subprocess."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import date, timedelta

import pytest
import yaml

from app import cli, config, plan, reading

from .conftest import CONFIG_FILE, PROJECT


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def in_days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


# ---------- reading list ----------


def test_push_list_done_roundtrip(capsys, make_note):
    make_note("Attention 2017")
    code, out, _ = run(capsys, "push", "--paper", "Attention 2017", "--tier", "T1", "--line", "Project A",
                       "--reason", "look at the metric", "--by", "planning session", "--source", "claude")
    assert code == 0 and out.startswith("added: r") and "Attention 2017 [inbox]" in out
    code, out, _ = run(capsys, "list")
    assert code == 0 and "Attention 2017" in out and "Project A" in out and "look at the metric" in out
    iid = out.split()[0]
    code, out, _ = run(capsys, "done", "Attention 2017", "--depth", "skim", "--verdict", "key", "--takeaway", "uses X")
    assert code == 0 and out == f"done: {iid} Attention 2017\n"
    [item] = reading.load()
    assert (item["status"], item["depth"], item["verdict"], item["takeaway"]) == ("done", "skim", "key", "uses X")


def test_push_without_a_reason_is_refused(capsys, make_note):
    make_note("Attention 2017")
    with pytest.raises(SystemExit) as e:
        cli.main(["push", "--paper", "Attention 2017"])
    assert e.value.code == 2
    code, out, err = run(capsys, "push", "--paper", "Attention 2017", "--reason", "  ")
    assert code == 2 and "reason is required" in err and out == ""
    assert not config.READING_LIST.exists()


def test_push_twice_and_force(capsys, make_note):
    make_note("Attention 2017")
    run(capsys, "push", "--paper", "Attention 2017", "--reason", "r1")
    code, out, _ = run(capsys, "push", "--paper", "Attention 2017", "--reason", "r2")
    assert code == 0 and "already listed (inbox); merged" in out
    run(capsys, "done", "Attention 2017")
    code, out, _ = run(capsys, "push", "--paper", "Attention 2017", "--reason", "r3")
    assert code == 0 and "already read on" in out
    code, out, _ = run(capsys, "push", "--paper", "Attention 2017", "--reason", "r3", "--force")
    assert code == 0 and "[inbox]" in out
    assert len(reading.load()) == 1


def test_push_arxiv_with_a_title(capsys):
    code, out, _ = run(capsys, "push", "--arxiv", "2606.17328", "--title", "A Paper", "--reason", "cited", "--source", "tracker")
    assert code == 0 and "arxiv:2606.17328 [inbox]" in out
    assert reading.load()[0]["source"] == "tracker"


def test_push_rejects_paper_and_arxiv_together_and_unknown_notes(capsys):
    with pytest.raises(SystemExit):
        cli.main(["push", "--paper", "A", "--arxiv", "2606.00001", "--reason", "x"])
    code, _, err = run(capsys, "push", "--paper", "Missing", "--reason", "x")
    assert code == 2 and "no paper note named 'Missing'" in err


def test_list_filters_and_json(capsys, make_note):
    make_note("Alpha")
    make_note("Beta")
    run(capsys, "push", "--paper", "Alpha", "--reason", "a", "--line", "Project A")
    run(capsys, "push", "--paper", "Beta", "--reason", "b", "--line", "Project B")
    run(capsys, "done", "Beta")
    assert "Alpha" in run(capsys, "list", "--status", "inbox")[1] and "Beta" not in run(capsys, "list", "--status", "inbox")[1]
    assert "Beta" in run(capsys, "list", "--status", "done")[1]
    out = run(capsys, "list", "--line", "Project A")[1]
    assert "Alpha" in out and "Beta" not in out
    rows = json.loads(run(capsys, "list", "--json")[1])
    assert {r["key"] for r in rows} == {"Alpha", "Beta"} and all("age_days" in r for r in rows)
    assert run(capsys, "list", "--status", "snoozed")[1] == "(empty)\n"


def test_done_unknown_paper(capsys):
    code, _, err = run(capsys, "done", "Nope")
    assert code == 2 and "not on the reading list: Nope" in err


# ---------- milestones ----------


def test_milestone_add_keeps_the_old_shorthand_and_has_an_explicit_form(capsys):
    code, out, _ = run(capsys, "milestone", "Draft experiments", "--date", "2026-11-15", "--line", "Project A", "--kind", "gate",
                       "--notes", "n")
    assert code == 0 and out.startswith("added: m") and out.endswith("2026-11-15 Draft experiments\n")
    code, out, _ = run(capsys, "milestone", "add", "Submit", "--date", "2026-12-01")
    assert code == 0 and out.endswith("2026-12-01 Submit\n")
    code, out, _ = run(capsys, "milestone", "--date", "2026-12-02", "Options first")
    assert code == 0 and out.endswith("2026-12-02 Options first\n")
    ms = {m["title"]: m for m in plan.milestones()}
    assert ms["Draft experiments"]["kind"] == "gate" and ms["Draft experiments"]["line"] == "Project A"
    assert ms["Draft experiments"]["notes"] == "n" and ms["Submit"]["status"] == "todo"
    assert set(ms) == {"Draft experiments", "Submit", "Options first"}


def test_milestone_titled_like_a_subcommand_needs_the_explicit_form(capsys):
    code, out, _ = run(capsys, "milestone", "add", "list", "--date", "2026-12-01")
    assert code == 0 and out.endswith("2026-12-01 list\n")
    code, out, _ = run(capsys, "milestone", "list")
    assert code == 0 and "list" in out


def test_milestone_add_validates(capsys):
    code, _, err = run(capsys, "milestone", "X", "--date", "not-a-date")
    assert code == 2 and err.startswith("error:")
    with pytest.raises(SystemExit):
        cli.main(["milestone", "X"])  # --date is required
    with pytest.raises(SystemExit):
        cli.main(["milestone", "X", "--date", "2026-12-01", "--kind", "party"])
    assert plan.milestones() == []


def test_milestone_list_sorted_by_date_with_filters(capsys):
    plan.add_milestone("Late", "2026-12-01", line="Project B")
    plan.add_milestone("Early", "2026-11-01", line="Project A", kind="gate")
    plan.add_milestone("Middle", "2026-11-15", line="Project A", status="doing")
    code, out, _ = run(capsys, "milestone", "list")
    assert code == 0
    rows = out.splitlines()
    assert [r.split()[-1] for r in rows] == ["Early", "Middle", "Late"]
    assert rows[0].split()[1:4] == ["2026-11-01", "todo", "gate"]
    assert [r.split()[-1] for r in run(capsys, "milestone", "list", "--line", "Project A")[1].splitlines()] == ["Early", "Middle"]
    assert [r.split()[-1] for r in run(capsys, "milestone", "list", "--status", "doing")[1].splitlines()] == ["Middle"]
    assert run(capsys, "milestone", "list", "--status", "done")[1] == "(empty)\n"
    rows = json.loads(run(capsys, "milestone", "list", "--json")[1])
    assert [r["title"] for r in rows] == ["Early", "Middle", "Late"] and rows[0]["id"].startswith("m")


def test_milestone_list_when_empty_and_bad_status(capsys):
    assert run(capsys, "milestone", "list") == (0, "(empty)\n", "")
    with pytest.raises(SystemExit):
        cli.main(["milestone", "list", "--status", "finished"])


def test_milestone_done_by_id_title_or_part_of_title(capsys):
    a = plan.add_milestone("Draft experiments", "2026-11-15")
    b = plan.add_milestone("Write related work", "2026-11-20")
    c = plan.add_milestone("Submit to ACL", "2026-12-01")
    code, out, _ = run(capsys, "milestone", "done", a["id"])
    assert (code, out) == (0, f"done: {a['id']} 2026-11-15 Draft experiments\n")
    code, out, _ = run(capsys, "milestone", "done", "write RELATED work")
    assert (code, out) == (0, f"done: {b['id']} 2026-11-20 Write related work\n")
    code, out, _ = run(capsys, "milestone", "done", "submit")
    assert (code, out) == (0, f"done: {c['id']} 2026-12-01 Submit to ACL\n")
    assert {m["title"]: m["status"] for m in plan.milestones()} == {
        "Draft experiments": "done", "Write related work": "done", "Submit to ACL": "done"}


def test_milestone_done_is_written_through_the_plan_store(capsys):
    m = plan.add_milestone("Gate 1", "2026-11-15", line="Project A", kind="gate", notes="keep")
    with plan.plan_store.editing() as data:
        data["milestones"][0]["updated_at"] = "2000-01-01T00:00:00"
    run(capsys, "milestone", "done", "Gate 1")
    raw = yaml.safe_load(config.PLAN_FILE.read_text(encoding="utf-8"))["milestones"][0]
    assert (raw["id"], raw["status"], raw["kind"], raw["line"], raw["notes"]) == (m["id"], "done", "gate", "Project A", "keep")
    assert raw["updated_at"] > "2000-01-01T00:00:00"      # stamped, so it wins a sync-conflict merge
    assert list(config.VAULT_DIR.rglob("*.tmp")) == []


def test_milestone_done_twice_says_so_and_does_not_rewrite(capsys):
    plan.add_milestone("Gate 1", "2026-11-15")
    run(capsys, "milestone", "done", "Gate 1")
    with plan.plan_store.editing() as data:
        data["milestones"][0]["updated_at"] = "2000-01-01T00:00:00"
    code, out, _ = run(capsys, "milestone", "done", "Gate 1")
    assert code == 0 and out.startswith("already done: m")
    assert plan.milestones()[0]["updated_at"] == "2000-01-01T00:00:00"


def test_milestone_done_prefers_the_open_one_among_same_titles(capsys):
    old = plan.add_milestone("Weekly sync", "2026-10-01", status="done")
    new = plan.add_milestone("Weekly sync", "2026-11-01")
    code, out, _ = run(capsys, "milestone", "done", "Weekly sync")
    assert code == 0 and out.startswith(f"done: {new['id']}")
    assert {m["id"]: m["status"] for m in plan.milestones()} == {old["id"]: "done", new["id"]: "done"}


def test_milestone_done_ambiguous_or_unknown_is_an_error_and_changes_nothing(capsys):
    plan.add_milestone("Draft paper", "2026-11-15")
    plan.add_milestone("Draft slides", "2026-11-16")
    plan.add_milestone("Plan A", "2026-11-17")
    plan.add_milestone("Plan A", "2026-11-18")
    code, out, err = run(capsys, "milestone", "done", "draft")
    assert code == 2 and out == "" and "matches 2 milestones, use the id" in err and "Draft paper" in err and "Draft slides" in err
    code, _, err = run(capsys, "milestone", "done", "Plan A")
    assert code == 2 and "matches 2 milestones" in err
    code, _, err = run(capsys, "milestone", "done", "nothing like this")
    assert code == 2 and "no milestone matches 'nothing like this'" in err
    code, _, err = run(capsys, "milestone", "done", "  ")
    assert code == 2 and "required" in err
    assert {m["status"] for m in plan.milestones()} == {"todo"}


def test_deleted_milestones_cannot_be_completed(capsys):
    m = plan.add_milestone("Gone", "2026-11-15")
    plan.delete("milestones", m["id"])
    code, _, err = run(capsys, "milestone", "done", "Gone")
    assert code == 2 and "no milestone matches" in err


def test_milestone_help_lists_the_actions(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["milestone", "--help"])
    assert e.value.code == 0
    text = capsys.readouterr().out
    assert "add" in text and "list" in text and "done" in text
    with pytest.raises(SystemExit) as e:
        cli.main(["milestone"])
    assert e.value.code == 2


# ---------- ideas ----------


def test_idea_add_shorthand_and_explicit_form(capsys):
    code, out, _ = run(capsys, "idea", "use X as a verifier for Y", "--line", "Project A")
    assert code == 0 and out.startswith("added: i") and "use X as a verifier for Y" in out
    code, out, _ = run(capsys, "idea", "add", "second idea")
    assert code == 0
    ideas = plan.ideas()
    assert {i["text"]: i["source"] for i in ideas} == {"use X as a verifier for Y": "claude", "second idea": "claude"}
    assert {i["text"]: i.get("line") for i in ideas}["use X as a verifier for Y"] == "Project A"


def test_idea_add_empty_text_is_an_error(capsys):
    code, _, err = run(capsys, "idea", "   ")
    assert code == 2 and "empty" in err


def test_idea_list_newest_first_with_filters(capsys):
    a = plan.add_idea("oldest", line="Project A")
    b = plan.add_idea("middle", line="Project B")
    c = plan.add_idea("newest", line="Project A")
    with plan.idea_store.editing() as data:
        for rec, ts in ((a, "2026-10-01T10:00:00"), (b, "2026-10-02T10:00:00"), (c, "2026-10-03T10:00:00")):
            next(r for r in data["ideas"] if r["id"] == rec["id"])["created_at"] = ts
    plan.idea_store.update("ideas", b["id"], {"status": "kept"})
    code, out, _ = run(capsys, "idea", "list")
    assert code == 0
    assert [r.split()[-1] for r in out.splitlines()] == ["newest", "middle", "oldest"]
    assert out.splitlines()[0].split()[:2] == [c["id"], "new"]
    assert [r.split()[-1] for r in run(capsys, "idea", "list", "--status", "new")[1].splitlines()] == ["newest", "oldest"]
    assert [r.split()[-1] for r in run(capsys, "idea", "list", "--status", "kept")[1].splitlines()] == ["middle"]
    assert [r.split()[-1] for r in run(capsys, "idea", "list", "--line", "Project A")[1].splitlines()] == ["newest", "oldest"]
    assert run(capsys, "idea", "list", "--status", "promoted")[1] == "(empty)\n"
    rows = json.loads(run(capsys, "idea", "list", "--json")[1])
    assert [r["text"] for r in rows] == ["newest", "middle", "oldest"]


def test_idea_list_when_empty_and_bad_status(capsys):
    assert run(capsys, "idea", "list") == (0, "(empty)\n", "")
    with pytest.raises(SystemExit):
        cli.main(["idea", "list", "--status", "maybe"])


def test_idea_list_hides_deleted(capsys):
    i = plan.add_idea("gone")
    plan.add_idea("kept")
    plan.idea_store.delete("ideas", i["id"])
    assert [r.split()[-1] for r in run(capsys, "idea", "list")[1].splitlines()] == ["kept"]


# ---------- deadlines ----------


def test_deadlines_lists_venue_target_and_plan_events(capsys, venue_data):
    plan.add_target("Right Rec", "KDD")
    plan.watch("CIKM")
    plan.add_milestone("Draft", in_days(10))
    plan.add_milestone("Overdue", in_days(-5))
    code, out, _ = run(capsys, "deadlines", "--days", "60")
    assert code == 0
    lines = out.splitlines()
    assert any("TARGET" in ln and "KDD" in ln and "Abstract deadline" in ln for ln in lines)
    assert any("plan" in ln and ln.endswith("Draft") for ln in lines)
    assert not any("CIKM" in ln for ln in lines)          # 90 days away
    assert not any("Overdue" in ln for ln in lines)       # the list only shows what is still ahead
    assert all(" UTC " in ln for ln in lines)
    assert lines == sorted(lines, key=lambda ln: ln[:16])
    assert any("CIKM" in ln and "venue" in ln for ln in run(capsys, "deadlines", "--days", "120")[1].splitlines())


# ---------- as a real process ----------


def studio_env() -> dict[str, str]:
    env = {**os.environ}
    assert env["STUDIO_CONFIG"] == str(CONFIG_FILE), "subprocess would not use the test sandbox"
    return env


def test_cli_runs_as_a_module_and_shares_the_stores_with_this_process(make_note):
    make_note("Alpha")
    base = [sys.executable, "-m", "app.cli"]
    run_ = lambda *a: subprocess.run([*base, *a], cwd=PROJECT, env=studio_env(), capture_output=True, text=True, timeout=60)  # noqa: E731
    r = run_("push", "--paper", "Alpha", "--reason", "from another process")
    assert r.returncode == 0, r.stderr
    assert [i["paper"] for i in reading.load()] == ["Alpha"]
    r = run_("milestone", "Gate", "--date", "2026-12-01")
    assert r.returncode == 0, r.stderr
    assert [m["title"] for m in plan.milestones()] == ["Gate"]
    r = run_("milestone", "done", "gate")
    assert r.returncode == 0 and r.stdout.startswith("done: m"), r.stderr
    assert plan.milestones()[0]["status"] == "done"
    r = run_("idea", "an idea")
    assert r.returncode == 0, r.stderr
    r = run_("idea", "list")
    assert r.returncode == 0 and "an idea" in r.stdout
    r = run_("milestone", "done", "nothing")
    assert r.returncode == 2 and "no milestone matches" in r.stderr


@pytest.mark.skipif(not shutil.which("uv"), reason="uv not installed")
def test_studio_wrapper_script_works(make_note):
    wrapper = PROJECT / "studio"
    assert os.access(wrapper, os.X_OK)
    r = subprocess.run([str(wrapper), "milestone", "list"], cwd=PROJECT, env=studio_env(), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "(empty)\n"

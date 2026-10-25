"""app/bilingual_queue.py (and the job guard in app/bilingual.py): bilingual PDFs made ahead of time.

pdf2zh is never run: bilingual._command is swapped for a tiny fake that prints progress and writes the
.dual/.mono files (or fails, or hangs until it is terminated), and logs every start.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

if "phd-studio-test-" not in os.environ.get("STUDIO_CONFIG", ""):
    pytest.skip("needs the sandbox from tests/conftest.py", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from app import bilingual, bilingual_queue as bq, config, library, reading  # noqa: E402

from .conftest import ROOT  # noqa: E402

FAKE = ROOT / "fake_pdf2zh.py"
FAKE.write_text("""\
import sys, time, pathlib
pdf, out, mode, log = sys.argv[1:5]
with open(log, "a") as f:
    f.write(pathlib.Path(pdf).stem + "\\n")
print("translating 10%", flush=True)
if mode == "fail":
    print("Error: the API said no", flush=True)
    sys.exit(2)
if mode == "slow":
    time.sleep(30)
print("translating 60%", flush=True)
name = pathlib.Path(pdf).stem
(pathlib.Path(out) / f"{name}.zh.dual.pdf").write_bytes(b"%PDF-dual")
(pathlib.Path(out) / f"{name}.zh.mono.pdf").write_bytes(b"%PDF-mono")
""", encoding="utf-8")


class Fake:
    def __init__(self) -> None:
        self.mode = "ok"
        self.log = ROOT / "fake_pdf2zh.log"
        self.log.write_text("", encoding="utf-8")
        self.engines: list[list[str]] = []

    def command(self, pdf: Path, out: Path, engine_args: list[str]) -> list[str]:
        self.engines.append(engine_args)
        return [sys.executable, str(FAKE), str(pdf), str(out), self.mode, str(self.log)]

    def starts(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines()


@pytest.fixture
def fake(sandbox, monkeypatch):
    f = Fake()
    monkeypatch.setattr(bilingual, "_command", f.command)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    (config.SECRETS_DIR / "deepseek.env").unlink(missing_ok=True)
    bilingual._jobs.clear()
    bilingual.QUEUED.clear()
    bilingual._stopping.clear()
    bq._stop.clear()
    config.save_settings({"bilingual_prefetch": "T1T2"})  # off by default; see test_off_by_default
    yield f
    for job in bilingual._jobs.values():  # nothing outlives a test
        if (p := job.get("proc")) and p.poll() is None:
            p.kill()
            p.wait()
    bilingual._jobs.clear()
    bilingual.QUEUED.clear()
    bilingual._stopping.clear()
    bq._stop.clear()


def paper(make_note, stem: str) -> str:
    make_note(stem, title=stem)
    (config.VAULT_PDFS_DIR / f"{stem}.pdf").write_bytes(b"%PDF-1.4")
    return stem


def wait(stem: str, timeout: float = 15) -> str:
    end = time.time() + timeout
    while bilingual._jobs.get(stem, {}).get("state") == "running":
        if time.time() > end:
            raise AssertionError(f"{stem} still running")
        time.sleep(0.05)
    return bilingual._jobs[stem]["state"]


def until(cond, timeout: float = 10) -> None:
    end = time.time() + timeout
    while not cond():
        if time.time() > end:
            raise AssertionError("timed out")
        time.sleep(0.02)


def queued() -> list[str]:
    return [j["key"] for j in bq.snapshot()["queued"]]


def jobs() -> dict[str, dict]:
    return {j["key"]: j for j in json.loads(bq.QUEUE_FILE.read_text())["jobs"]}


def tier(stem: str, t: str | None, status: str | None = None) -> None:
    item = reading.find(reading.load(), stem)
    reading.update(item["id"], {"tier": t, **({"status": status} if status else {})})


# ---------- what gets queued ----------


def test_first_look_only_records_then_new_t1_t2_are_queued(fake, make_note):
    a, b, c = (paper(make_note, s) for s in ("A", "B", "C"))
    reading.push(paper=a, reason="r", source="self", tier="T1")
    for s in (b, c):
        reading.push(paper=s, reason="r", source="tracker")
    bq.set_paused(True)
    bq.tick()
    assert queued() == []  # A was already T1 before the queue first looked
    tier(b, "T2")
    tier(c, "T3")
    bq.tick()
    assert queued() == ["B"]
    assert bq.snapshot()["queued"][0]["reason"] == "进入 T2"
    assert bq.snapshot()["backfill"] == 1  # A could still be back-filled
    assert fake.starts() == []  # paused


def test_mode_decides_which_tiers_count(fake, make_note):
    for s in ("A", "B"):
        reading.push(paper=paper(make_note, s), reason="r", source="tracker")
    config.save_settings({"bilingual_prefetch": "T1"})
    bq.set_paused(True)
    bq.tick()
    tier("A", "T1")
    tier("B", "T2")
    bq.tick()
    assert queued() == ["A"]
    config.save_settings({"bilingual_prefetch": "off"})
    tier("B", "T1")
    bq.tick()
    assert queued() == ["A"]


def test_arxiv_only_items_are_queued_by_their_key(fake):
    reading.push(arxiv="2610.11920", title="Loose", reason="r", source="tracker")
    bq.set_paused(True)
    bq.tick()
    tier("arxiv:2610.11920", "T1")
    bq.tick()
    assert bq.snapshot()["queued"][0] | {"added": None} == {
        "key": "arxiv:2610.11920", "title": "Loose", "reason": "进入 T1", "added": None, "state": "queued"}


def test_papers_with_a_bilingual_pdf_are_skipped(fake, make_note):
    reading.push(paper=paper(make_note, "Done"), reason="r", source="tracker")
    reading.push(paper=paper(make_note, "Later"), reason="r", source="tracker")
    bq.set_paused(True)
    bq.tick()
    out = config.BILINGUAL_DIR / "Done"
    out.mkdir(parents=True)
    (out / "Done.zh.dual.pdf").write_bytes(b"%PDF")
    tier("Done", "T1")
    tier("Later", "T1")
    bq.tick()
    assert queued() == ["Later"]
    out = config.BILINGUAL_DIR / "Later"  # made some other way while waiting
    out.mkdir(parents=True)
    (out / "Later.zh.dual.pdf").write_bytes(b"%PDF")
    bq.set_paused(False)
    bq.tick()
    assert jobs()["Later"]["state"] == "skipped" and fake.starts() == []


def test_backfill_queues_current_t1_t2_once(fake, make_note):
    for s, t in (("A", "T1"), ("B", "T2"), ("C", "T3")):
        reading.push(paper=paper(make_note, s), reason="r", source="self", tier=t)
    bq.set_paused(True)
    bq.tick()
    assert bq.backfill() == 2 and queued() == ["A", "B"]
    assert bq.backfill() == 0 and bq.snapshot()["backfill"] == 0


# ---------- running ----------


def test_one_job_at_a_time_in_queue_order(fake, make_note):
    for s in ("A", "B"):
        reading.push(paper=paper(make_note, s), reason="r", source="tracker")
    bq.set_paused(True)
    bq.tick()
    tier("A", "T1")
    tier("B", "T2")
    bq.tick()
    fake.mode = "slow"
    bq.set_paused(False)
    bq.tick()
    assert bilingual.running() == ["A"] and jobs()["A"]["state"] == "running"
    bq.tick()
    assert bilingual.running() == ["A"] and jobs()["B"]["state"] == "queued"  # max one at a time
    assert bilingual.status("B") == {"state": "queued", "position": 1, "paused": False}
    snap = bq.snapshot()
    assert snap["running"]["key"] == "A" and snap["running"]["progress"] >= 0

    until(lambda: bilingual._jobs["A"]["progress"] >= 10)  # the fake is up and has logged its start
    bilingual._jobs["A"]["proc"].kill()
    assert wait("A") == "error"
    fake.mode = "ok"
    bq.tick()  # A is settled, B starts
    assert jobs()["A"]["state"] == "error" and jobs()["A"]["error"]
    assert bilingual.running() == ["B"]
    assert wait("B") == "done"
    bq.tick()
    assert jobs()["B"]["state"] == "done" and bilingual.output("B", "dual")
    assert bilingual.status("B")["state"] == "done"
    assert fake.starts() == ["A", "B"]


def test_a_job_started_from_the_reader_holds_the_queue(fake, make_note):
    for s in ("Manual", "Waiting"):
        reading.push(paper=paper(make_note, s), reason="r", source="tracker")
    bq.tick()
    fake.mode = "slow"
    bilingual.start("Manual")
    tier("Waiting", "T1")
    bq.tick()
    assert queued() == ["Waiting"] and bilingual.running() == ["Manual"]
    assert bq.snapshot()["busy"] == ["Manual"]


def test_reader_and_queue_never_run_one_paper_twice(fake, make_note, monkeypatch):
    reading.push(paper=paper(make_note, "Same"), reason="r", source="tracker")
    bq.tick()
    real = library.ensure_pdf

    def slow_pdf(stem):  # the download window, where both used to pass the "running?" check
        time.sleep(0.3)
        return real(stem)
    monkeypatch.setattr(library, "ensure_pdf", slow_pdf)
    tier("Same", "T1")
    t = threading.Thread(target=bq.tick)
    t.start()
    time.sleep(0.05)
    bilingual.start("Same")  # the reader's 双语 button while the queue is starting it
    t.join()
    assert wait("Same") == "done"
    assert fake.starts() == ["Same"]
    bq.tick()
    assert jobs()["Same"]["state"] == "done"


def test_a_paper_that_left_t1_t2_is_dropped(fake, make_note):
    reading.push(paper=paper(make_note, "Gone"), reason="r", source="tracker")
    bq.set_paused(True)
    bq.tick()
    tier("Gone", "T1")
    bq.tick()
    tier("Gone", None, status="dismissed")
    bq.set_paused(False)
    bq.tick()
    assert jobs()["Gone"]["state"] == "skipped" and fake.starts() == []


def test_failures_are_kept_and_can_be_retried_or_removed(fake, make_note):
    reading.push(paper=paper(make_note, "Bad"), reason="r", source="tracker")
    bq.tick()
    fake.mode = "fail"
    tier("Bad", "T1")
    bq.tick()
    assert wait("Bad") == "error"
    bq.tick()
    rec = bq.snapshot()["recent"][0]
    assert rec["state"] == "error" and "the API said no" in rec["error"]
    fake.mode = "ok"
    assert bq.retry("Bad") and queued() == ["Bad"]
    assert not bq.retry("Bad")  # already waiting
    assert bq.remove("Bad") and queued() == []
    assert not bq.remove("Bad")


def test_missing_pdf_is_an_error_not_a_crash(fake, make_note):
    make_note("NoPdf", title="NoPdf")
    reading.push(paper="NoPdf", reason="r", source="tracker")
    bq.tick()
    tier("NoPdf", "T1")
    bq.tick()  # ensure_pdf finds no link: the sandbox has no network and the note no source
    assert jobs()["NoPdf"]["state"] == "error" and "PDF" in jobs()["NoPdf"]["error"]
    assert bilingual.running() == []


def test_pause_holds_and_resume_starts(fake, make_note):
    reading.push(paper=paper(make_note, "P"), reason="r", source="tracker")
    bq.tick()
    bq.set_paused(True)
    tier("P", "T1")
    bq.tick()
    assert fake.starts() == [] and bilingual.status("P") == {"state": "queued", "position": 1, "paused": True}
    bq.set_paused(False)
    bq.tick()
    assert wait("P") == "done"


# ---------- restarts and shutdown ----------


def test_quitting_mid_job_redoes_it_after_the_next_start(fake, make_note):
    for s in ("First", "Second"):
        reading.push(paper=paper(make_note, s), reason="r", source="tracker")
    bq.set_paused(True)
    bq.tick()
    tier("First", "T1")
    tier("Second", "T1")
    bq.tick()
    fake.mode = "slow"
    bq.set_paused(False)
    bq.tick()
    until(lambda: bilingual._jobs["First"]["progress"] >= 10)
    proc = bilingual._jobs["First"]["proc"]
    bq.stop_worker()  # app shutdown: the queue's own pdf2zh goes, nothing is marked failed
    proc.wait(timeout=5)
    assert proc.returncode is not None
    bq.tick()
    assert jobs()["First"]["state"] == "running"  # as saved on disk

    # Next start: a fresh process (no in-memory jobs), the cut job is first in line again.
    bilingual._jobs.clear()
    bilingual.QUEUED.clear()
    bq.start_worker()
    bq.stop_worker()
    bq._stop.clear()
    assert queued() == ["First", "Second"]
    assert bilingual.status("First")["state"] == "queued"


def test_stop_all_blocks_new_processes(fake, make_note):
    reading.push(paper=paper(make_note, "Late"), reason="r", source="tracker")
    bilingual.stop_all()
    bilingual.start("Late")
    assert wait("Late") == "error" and fake.starts() == []


# ---------- engine ----------


def test_engine_follows_the_setting_and_the_key(fake, make_note, monkeypatch):
    assert bilingual.engine() == ("Google 翻译", ["--google"])  # DeepSeek chosen, but no key
    (config.SECRETS_DIR).mkdir(parents=True, exist_ok=True)
    (config.SECRETS_DIR / "deepseek.env").write_text("DEEPSEEK_API_KEY=sk-test\n", encoding="utf-8")
    label, args = bilingual.engine()
    assert label == "DeepSeek" and args == ["--deepseek", "--deepseek-model", "deepseek-chat"]
    # the key goes to pdf2zh in its environment, never on the command line (visible in ps)
    assert bilingual._engine()[2] == {"PDF2ZH_DEEPSEEK_API_KEY": "sk-test"} and "sk-test" not in " ".join(args)
    assert bilingual._child_env(bilingual._engine()[2])["PDF2ZH_DEEPSEEK_API_KEY"] == "sk-test"
    config.save_settings({"translate_engine": "google"})
    assert bilingual.engine()[0] == "Google 翻译"
    snap = bq.snapshot()
    assert (snap["engine"], snap["deepseek_key"], snap["engine_setting"]) == ("Google 翻译", True, "google")
    (config.SECRETS_DIR / "deepseek.env").unlink()


# ---------- routes ----------


def test_routes(fake, make_note):
    from app.main import app
    client = TestClient(app)  # no lifespan: no worker thread
    for s, t in (("A", "T1"), ("B", "T2")):
        reading.push(paper=paper(make_note, s), reason="r", source="self", tier=t)
    assert client.post("/api/bilingual-queue/pause", json={"paused": True}).json()["paused"] is True
    r = client.post("/api/bilingual-queue/backfill").json()
    assert r["added"] == 2 and [j["key"] for j in r["queued"]] == ["A", "B"]
    assert client.get("/api/bilingual/A").json() == {"state": "queued", "position": 1, "paused": True}
    assert client.get("/api/papers/B").json()["bilingual"]["position"] == 2
    assert [j["key"] for j in client.delete("/api/bilingual-queue/A").json()["queued"]] == ["B"]
    assert client.delete("/api/bilingual-queue/A").status_code == 404
    assert client.post("/api/bilingual-queue/B/retry").status_code == 409
    assert client.get("/api/bilingual-queue").json()["mode"] == "T1T2"


def test_queue_file_lives_in_the_data_dir():
    assert bq.QUEUE_FILE.is_relative_to(ROOT) and bq.QUEUE_FILE.parent == config.DATA_DIR
    assert not bq.QUEUE_FILE.is_relative_to(config.VAULT_DIR)


# ---------- review fixes ----------


def test_off_by_default(fake, make_note):
    assert config.DEFAULT_SETTINGS["bilingual_prefetch"] == "off"
    config.SETTINGS_FILE.unlink()
    assert bq.mode() == "off" and bq.snapshot()["mode"] == "off"
    reading.push(paper=paper(make_note, "A"), reason="r", source="tracker")
    bq.tick()
    tier("A", "T1")
    bq.tick()
    assert queued() == [] and fake.starts() == []


def _make_output(key: str) -> Path:
    out = config.BILINGUAL_DIR / library.safe_name(key)
    out.mkdir(parents=True, exist_ok=True)
    (out / "x.zh.dual.pdf").write_bytes(b"%PDF-dual")
    return out


def test_a_pdf_made_before_the_note_is_found_after_it(fake, make_note):
    _make_output("arxiv:2610.11920")
    make_note("Later Note", arxiv="2610.11920", title="Later Note")
    (config.VAULT_PDFS_DIR / "Later Note.pdf").write_bytes(b"%PDF-1.4")
    assert bilingual.aliases("Later Note") == ["Later Note", "arxiv:2610.11920"]
    assert bilingual.aliases("arxiv:2610.11920") == ["arxiv:2610.11920", "Later Note"]
    assert bilingual.output("Later Note", "dual") and bilingual.status("Later Note")["state"] == "done"
    assert bilingual.start("Later Note")["state"] == "done"  # never translated (and paid for) again
    assert fake.starts() == []
    _make_output("Other Note")
    make_note("Other Note", arxiv="2610.22222", title="Other Note")
    assert bilingual.status("arxiv:2610.22222")["state"] == "done"


def test_a_job_under_the_old_key_is_seen_under_the_new_one(fake, make_note):
    (config.PDF_CACHE_DIR / "arxiv_2610.11920.pdf").write_bytes(b"%PDF-1.4")
    fake.mode = "slow"
    bilingual.start("arxiv:2610.11920")
    make_note("New Note", arxiv="2610.11920", title="New Note")
    (config.VAULT_PDFS_DIR / "New Note.pdf").write_bytes(b"%PDF-1.4")
    assert bilingual.status("New Note")["state"] == "running"
    assert bilingual.start("New Note")["state"] == "running"
    until(lambda: fake.starts())
    assert fake.starts() == ["arxiv_2610.11920"] and bilingual.running() == ["arxiv:2610.11920"]


@pytest.mark.parametrize("relinked", [False, True])
def test_a_waiting_arxiv_item_follows_its_new_note(fake, make_note, relinked):
    reading.push(arxiv="2610.11920", title="Loose", reason="r", source="tracker")
    bq.set_paused(True)
    bq.tick()
    tier("arxiv:2610.11920", "T1")
    bq.tick()
    assert queued() == ["arxiv:2610.11920"]
    make_note("Got A Note", arxiv="2610.11920", title="Got A Note")
    (config.VAULT_PDFS_DIR / "Got A Note.pdf").write_bytes(b"%PDF-1.4")
    if relinked:
        reading.load()  # the reading list itself now says paper: Got A Note
    assert queued() == ["Got A Note"]  # followed, not dropped as "no longer T1"
    assert bilingual.status("Got A Note") == {"state": "queued", "position": 1, "paused": True}
    bq.set_paused(False)
    bq.tick()
    assert wait("Got A Note") == "done"
    bq.tick()
    assert jobs()["Got A Note"]["state"] == "done" and fake.starts() == ["Got A Note"]


def test_a_job_started_before_the_rekey_still_settles(fake, make_note):
    reading.push(arxiv="2610.11920", title="Loose", reason="r", source="tracker")
    (config.PDF_CACHE_DIR / "arxiv_2610.11920.pdf").write_bytes(b"%PDF-1.4")
    bq.tick()
    tier("arxiv:2610.11920", "T1")
    bq.tick()  # starts under arxiv:2610.11920
    make_note("Mid Note", arxiv="2610.11920", title="Mid Note")
    assert wait("arxiv:2610.11920") == "done"
    bq.tick()
    assert jobs()["Mid Note"]["state"] == "done" and jobs()["Mid Note"]["job"] == "arxiv:2610.11920"
    assert bilingual.status("Mid Note")["state"] == "done"


@pytest.mark.parametrize("gap", ["missing", "emptied"])
def test_a_list_that_disappears_for_a_while_is_not_news(fake, make_note, gap):
    for s in ("A", "B"):
        reading.push(paper=paper(make_note, s), reason="r", source="self", tier="T1")
    bq.set_paused(True)
    bq.tick()  # both already T1: recorded, not queued
    saved = config.READING_LIST.read_bytes()
    if gap == "missing":
        config.READING_LIST.unlink()
    else:
        config.READING_LIST.write_text("items: []\n", encoding="utf-8")
    bq.tick()
    config.READING_LIST.write_bytes(saved)
    t = time.time() + 5
    os.utime(config.READING_LIST, (t, t))
    bq.tick()
    assert queued() == []  # neither A nor B is "new" after the gap


def test_child_process_does_not_get_the_launchers_python_settings(fake, make_note, monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "/nonexistent-stub-home")  # the desktop stub's; breaks any other Python
    monkeypatch.setenv("PYTHONNOUSERSITE", "1")
    assert "PYTHONHOME" not in bilingual._child_env() and "PYTHONNOUSERSITE" not in bilingual._child_env()
    reading.push(paper=paper(make_note, "Env"), reason="r", source="tracker")
    bilingual.start("Env")
    assert wait("Env") == "done"


def test_shutdown_during_a_download_starts_no_process(fake, make_note, monkeypatch):
    reading.push(paper=paper(make_note, "Slow"), reason="r", source="tracker")
    bq.tick()
    real = library.ensure_pdf

    def slow_pdf(stem):
        time.sleep(0.4)
        return real(stem)
    monkeypatch.setattr(library, "ensure_pdf", slow_pdf)
    tier("Slow", "T1")
    t = threading.Thread(target=bq.tick)
    t.start()
    time.sleep(0.15)  # the worker is inside the download
    bq.stop_worker()
    t.join()
    assert wait("Slow") == "error" and bilingual._jobs["Slow"]["error"] == "工作台正在关闭"
    assert fake.starts() == []


def _folder(name: str, days_old: float, size: int = 1000) -> Path:
    d = config.BILINGUAL_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / "x.zh.dual.pdf"
    f.write_bytes(b"x" * size)
    t = time.time() - days_old * 86400
    os.utime(f, (t, t))
    return d


def test_disk_use_and_cleanup(fake, make_note):
    reading.push(paper=paper(make_note, "Read"), reason="r", source="self", tier="T1")
    reading.update(reading.find(reading.load(), "Read")["id"], {"status": "done"})
    reading.push(paper=paper(make_note, "Active"), reason="r", source="self", tier="T2")
    make_note("Noted", arxiv="2601.00001", title="Noted")
    reading.push(paper="Noted", reason="r", source="tracker")  # in the inbox
    _folder("Read", 100)
    _folder("Active", 200)
    _folder("Recent", 2)
    _folder("arxiv_2601.00009", 40)  # never on the list
    _folder("arxiv_2601.00001", 200)  # the inbox paper's, under its old key
    d = bq.disk()
    assert (d["bytes"], d["papers"]) == (5000, 5)
    assert d["cleanable"] == {"30": {"papers": 2, "bytes": 2000}, "90": {"papers": 1, "bytes": 1000},
                              "180": {"papers": 0, "bytes": 0}}
    r = bq.cleanup(90)
    assert (r["removed"], r["bytes"]) == (1, 1000) and not (config.BILINGUAL_DIR / "Read").exists()
    assert sorted(p.name for p in config.BILINGUAL_DIR.iterdir()) == ["Active", "Recent", "arxiv_2601.00001", "arxiv_2601.00009"]
    with pytest.raises(ValueError):
        bq.cleanup(7)


def test_cleanup_route(fake):
    from app.main import app
    client = TestClient(app)
    _folder("arxiv_2601.00009", 40)
    assert client.post("/api/bilingual-queue/cleanup", json={"days": 5}).status_code == 400
    r = client.post("/api/bilingual-queue/cleanup", json={"days": 30}).json()
    assert r["removed"] == 1 and r["disk"]["papers"] == 0


# ---------- papers known by a DOI, OpenReview or ACL key ----------

DOI_KEY = "doi:10.1145|3626772.3657833"


def note_with(stem: str, front: str) -> None:
    (config.PAPERS_DIR / f"{stem}.md").write_text(f"---\ntype: paper\n{front}\n---\n- Title: {stem}\n", encoding="utf-8")
    (config.VAULT_PDFS_DIR / f"{stem}.pdf").write_bytes(b"%PDF-1.4")
    library._arxiv_index.cache_clear()
    library._ext_index.cache_clear()


@pytest.mark.parametrize("key, front", [
    (DOI_KEY, "doi: 10.1145/3626772.3657833"),
    ("openreview:abc123XYZ", "source: https://openreview.net/forum?id=abc123XYZ"),
    ("acl:2024.acl-long.1", "source: https://aclanthology.org/2024.acl-long.1/"),
])
def test_a_pdf_made_before_the_note_is_found_after_it_for_every_kind_of_key(fake, key, front):
    _make_output(key)
    note_with("Later Note", front)
    assert bilingual.aliases("Later Note") == ["Later Note", key]
    assert bilingual.aliases(key) == [key, "Later Note"]
    assert bilingual.status("Later Note")["state"] == "done" and bilingual.start("Later Note")["state"] == "done"
    assert fake.starts() == []  # never translated (and paid for) again


def test_an_item_with_an_arxiv_key_and_a_doi_joins_them_with_its_note(fake):
    reading.push(arxiv="2610.11920", ext=DOI_KEY, title="Both", reason="r", source="tracker")
    _make_output("arxiv:2610.11920")
    note_with("Found By Doi", "doi: 10.1145/3626772.3657833")  # the note knows the DOI, not the arXiv id
    reading.load()  # the item is now attached to the note
    assert reading.key_of(reading.load()[0]) == "Found By Doi"
    assert set(bilingual.aliases("Found By Doi")) == {"Found By Doi", "arxiv:2610.11920", DOI_KEY}
    assert bilingual.aliases("arxiv:2610.11920")[1] == "Found By Doi"
    assert bilingual.status("Found By Doi")["state"] == "done"


def test_a_job_under_the_old_doi_key_is_seen_under_the_new_one(fake):
    (config.PDF_CACHE_DIR / f"{library.safe_name(DOI_KEY)}.pdf").write_bytes(b"%PDF-1.4")
    fake.mode = "slow"
    bilingual.start(DOI_KEY)
    note_with("New Note", "doi: 10.1145/3626772.3657833")
    assert bilingual.status("New Note")["state"] == "running" and bilingual.start("New Note")["state"] == "running"
    until(lambda: fake.starts())
    assert fake.starts() == [library.safe_name(DOI_KEY)] and bilingual.running() == [DOI_KEY]


def test_doi_items_are_queued_by_their_key(fake):
    reading.push(ext=DOI_KEY, title="Loose", reason="r", source="tracker")
    bq.set_paused(True)
    bq.tick()
    tier(DOI_KEY, "T1")
    bq.tick()
    assert queued() == [DOI_KEY]


@pytest.mark.parametrize("relinked", [False, True])
@pytest.mark.parametrize("key, front, push", [
    (DOI_KEY, "doi: 10.1145/3626772.3657833", {"ext": DOI_KEY}),
    ("openreview:abc123XYZ", "source: https://openreview.net/forum?id=abc123XYZ", {"ext": "openreview:abc123XYZ"}),
])
def test_a_waiting_external_item_follows_its_new_note(fake, key, front, push, relinked):
    reading.push(title="Loose", reason="r", source="tracker", **push)
    bq.set_paused(True)
    bq.tick()
    tier(key, "T1")
    bq.tick()
    assert queued() == [key]
    note_with("Got A Note", front)
    if relinked:
        reading.load()  # the reading list itself now says paper: Got A Note
    assert queued() == ["Got A Note"]  # followed, not dropped as "no longer T1"
    assert bilingual.status("Got A Note") == {"state": "queued", "position": 1, "paused": True}
    bq.set_paused(False)
    bq.tick()
    assert wait("Got A Note") == "done"
    bq.tick()
    assert jobs()["Got A Note"]["state"] == "done" and fake.starts() == ["Got A Note"]


def test_the_cleanup_keeps_the_folders_of_unread_external_papers(fake):
    out = _make_output(DOI_KEY)
    reading.push(ext=DOI_KEY, title="Unread", reason="r", source="tracker")
    assert library.safe_name(DOI_KEY) in bq._keep() and out.exists()

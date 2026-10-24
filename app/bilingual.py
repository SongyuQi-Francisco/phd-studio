"""Full-paper bilingual PDFs via pdf2zh-next (BabelDOC), run in its own isolated env.

pdf2zh-next keeps formulas, figures and layout and writes a side-by-side dual PDF
plus a Chinese-only PDF. Output lives in the data dir, never in the vault.
Jobs start from the reader (one click) or from the background queue in bilingual_queue.py.

A paper can have two keys over its life: arxiv:<id> while it is only on the reading list, then its
note stem once a note exists. Outputs and running jobs are looked up under both (aliases), so a PDF
made before the note was written is still found, and never translated (and paid for) twice.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import library
from .config import BILINGUAL_DIR, load_settings, secret

UVX = shutil.which("uvx") or str(Path.home() / ".local" / "bin" / "uvx")
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()  # claiming a paper, starting a process and stopping them all
_stopping = threading.Event()  # set on shutdown: no new pdf2zh process after that
_PCT_RE = re.compile(r"(\d{1,3})%")
# Set by the desktop launcher stub for its own Python; uvx's Python must not inherit them.
_ENV_DROP = ("PYTHONHOME", "PYTHONNOUSERSITE", "PYTHONPATH", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__")
# Papers waiting in the background queue, kept current by bilingual_queue: {key: {"position", "paused"}}.
QUEUED: dict[str, dict[str, Any]] = {}


def aliases(stem: str) -> list[str]:
    """The paper's keys: the one asked for, then its other name (note stem <-> arxiv:<id>)."""
    try:
        by_arxiv = library.arxiv_index()
    except OSError:  # no Papers folder
        return [stem]
    if aid := library.arxiv_key(stem):
        note = by_arxiv.get(aid)
        return [stem, note] if note else [stem]
    return [stem] + [f"arxiv:{a}" for a, s in by_arxiv.items() if s == stem]


def _dir(stem: str) -> Path:
    return BILINGUAL_DIR / library.safe_name(stem)


def output(stem: str, variant: str) -> Path | None:
    kind = {"dual": "dual", "mono": "mono"}.get(variant)
    if not kind:
        return None
    for key in aliases(stem):
        if _dir(key).exists() and (hits := sorted(_dir(key).glob(f"*.{kind}.pdf"))):
            return hits[0]
    return None


def running() -> list[str]:
    return [stem for stem, job in _jobs.items() if job["state"] == "running"]


def _running_job(stem: str) -> dict[str, Any] | None:
    return next((_jobs[k] for k in aliases(stem) if _jobs.get(k, {}).get("state") == "running"), None)


def status(stem: str) -> dict[str, Any]:
    if job := _running_job(stem):
        return {k: job[k] for k in ("state", "progress", "engine", "started")}
    if output(stem, "dual"):
        return {"state": "done", "variants": [v for v in ("dual", "mono") if output(stem, v)]}
    if q := next((QUEUED[k] for k in aliases(stem) if k in QUEUED), None):
        return {"state": "queued", **q}
    if job := _jobs.get(stem):
        return {k: job.get(k) for k in ("state", "error", "engine")}
    return {"state": "none"}


def engine() -> tuple[str, list[str]]:
    """The translate engine setting, falling back to Google when there is no DeepSeek key."""
    key = secret("DEEPSEEK_API_KEY", "deepseek")
    if load_settings().get("translate_engine") == "deepseek" and key:
        return "DeepSeek", ["--deepseek", "--deepseek-model", "deepseek-chat", "--deepseek-api-key", key]
    return "Google 翻译", ["--google"]


def _command(pdf: Path, out: Path, engine_args: list[str]) -> list[str]:
    return [
        UVX, "--python", "3.12", "--with", "bitstring<5", "--from", "pdf2zh-next", "pdf2zh_next",
        str(pdf), "--output", str(out), "--lang-out", "zh-CN",
        "--watermark-output-mode", "no_watermark", *engine_args,
    ]


def _child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _ENV_DROP}


def start(stem: str, source: str = "reader") -> dict[str, Any]:
    label, engine_args = engine()
    with _lock:
        if _running_job(stem) or output(stem, "dual"):  # already running, or already made (maybe under its other key)
            return status(stem)
        # Claimed before the (possibly slow) PDF download, so a second start waits its turn.
        job = {"state": "running", "progress": 0, "engine": label, "started": time.time(), "source": source}
        _jobs[stem] = job
    try:
        pdf = library.ensure_pdf(stem)
    except Exception as e:
        job.update(state="error", error=f"找不到 PDF：{e}")
        raise
    out = _dir(stem)
    out.mkdir(parents=True, exist_ok=True)
    cmd = _command(pdf, out, engine_args)

    def run() -> None:
        log = out / "pdf2zh.log"
        try:
            with log.open("w", encoding="utf-8") as f:
                with _lock:  # either stop_all sees this process, or it was stopped before it could start
                    if _stopping.is_set():
                        job.update(state="error", error="工作台正在关闭")
                        return
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                            bufsize=1, env=_child_env())
                    job["proc"] = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    f.write(line)
                    if m := _PCT_RE.findall(line):
                        job["progress"] = max(job["progress"], min(99, int(m[-1])))
                proc.wait()
        except OSError as e:
            job.update(state="error", error=f"pdf2zh 启动失败：{e}")
            return
        if proc.returncode == 0 and output(stem, "dual"):
            job.update(state="done", progress=100)
        else:
            tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
            job.update(state="error", error=" / ".join(tail) or f"exit {proc.returncode}")

    threading.Thread(target=run, daemon=True).start()
    return status(stem)


def stop_all(source: str | None = None) -> None:
    """Called on shutdown: no new pdf2zh process from now on, and running ones (all, or one source's) end."""
    with _lock:
        _stopping.set()
        procs = [j.get("proc") for j in _jobs.values() if source is None or j.get("source") == source]
    for proc in procs:
        if proc and proc.poll() is None:
            proc.terminate()

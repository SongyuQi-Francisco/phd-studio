"""Full-paper bilingual PDFs via pdf2zh-next (BabelDOC), run in its own isolated env.

pdf2zh-next keeps formulas, figures and layout and writes a side-by-side dual PDF
plus a Chinese-only PDF. Output lives in the data dir, never in the vault.
Jobs start from the reader (one click) or from the background queue in bilingual_queue.py.
"""
from __future__ import annotations

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
_lock = threading.Lock()  # check-and-claim a paper, so it never gets two jobs at once
_stopping = threading.Event()  # set on shutdown: no new pdf2zh process after that
_PCT_RE = re.compile(r"(\d{1,3})%")
# Papers waiting in the background queue, kept current by bilingual_queue: {stem: {"position", "paused"}}.
QUEUED: dict[str, dict[str, Any]] = {}


def _dir(stem: str) -> Path:
    return BILINGUAL_DIR / library.safe_name(stem)


def output(stem: str, variant: str) -> Path | None:
    kind = {"dual": "dual", "mono": "mono"}.get(variant)
    if not kind or not _dir(stem).exists():
        return None
    hits = sorted(_dir(stem).glob(f"*.{kind}.pdf"))
    return hits[0] if hits else None


def running() -> list[str]:
    return [stem for stem, job in _jobs.items() if job["state"] == "running"]


def status(stem: str) -> dict[str, Any]:
    job = _jobs.get(stem)
    if job and job["state"] == "running":
        return {k: job[k] for k in ("state", "progress", "engine", "started")}
    if output(stem, "dual"):
        return {"state": "done", "variants": [v for v in ("dual", "mono") if output(stem, v)]}
    if q := QUEUED.get(stem):
        return {"state": "queued", **q}
    if job:
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


def start(stem: str, source: str = "reader") -> dict[str, Any]:
    label, engine_args = engine()
    with _lock:
        if _jobs.get(stem, {}).get("state") == "running":
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
        if _stopping.is_set():
            job.update(state="error", error="工作台正在关闭")
            return
        try:
            with log.open("w", encoding="utf-8") as f:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        bufsize=1)
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


def stop_all() -> None:
    """Called on shutdown so no translation keeps running in the background."""
    _stopping.set()
    for job in _jobs.values():
        proc = job.get("proc")
        if proc and proc.poll() is None:
            proc.terminate()

"""Full-paper bilingual PDFs via pdf2zh-next (BabelDOC), run in its own isolated env.

pdf2zh-next keeps formulas, figures and layout and writes a side-by-side dual PDF
plus a Chinese-only PDF. Output lives in the data dir, never in the vault.
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
_PCT_RE = re.compile(r"(\d{1,3})%")


def _dir(stem: str) -> Path:
    return BILINGUAL_DIR / library.safe_name(stem)


def output(stem: str, variant: str) -> Path | None:
    kind = {"dual": "dual", "mono": "mono"}.get(variant)
    if not kind or not _dir(stem).exists():
        return None
    hits = sorted(_dir(stem).glob(f"*.{kind}.pdf"))
    return hits[0] if hits else None


def status(stem: str) -> dict[str, Any]:
    job = _jobs.get(stem)
    if job and job["state"] == "running":
        return {k: job[k] for k in ("state", "progress", "engine", "started")}
    if output(stem, "dual"):
        return {"state": "done", "variants": [v for v in ("dual", "mono") if output(stem, v)]}
    if job:
        return {k: job.get(k) for k in ("state", "error", "engine")}
    return {"state": "none"}


def start(stem: str) -> dict[str, Any]:
    if _jobs.get(stem, {}).get("state") == "running":
        return status(stem)
    pdf = library.ensure_pdf(stem)
    out = _dir(stem)
    out.mkdir(parents=True, exist_ok=True)
    key = secret("DEEPSEEK_API_KEY", "deepseek")
    use_deepseek = load_settings().get("translate_engine") == "deepseek" and key
    engine = ["--deepseek", "--deepseek-model", "deepseek-chat", "--deepseek-api-key", key] if use_deepseek else ["--google"]
    cmd = [
        UVX, "--python", "3.12", "--with", "bitstring<5", "--from", "pdf2zh-next", "pdf2zh_next",
        str(pdf), "--output", str(out), "--lang-out", "zh-CN",
        "--watermark-output-mode", "no_watermark", *engine,
    ]
    job = {"state": "running", "progress": 0, "engine": "DeepSeek" if use_deepseek else "Google 翻译",
           "started": time.time()}
    _jobs[stem] = job

    def run() -> None:
        log = out / "pdf2zh.log"
        with log.open("w", encoding="utf-8") as f:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    bufsize=1)
            assert proc.stdout is not None
            for line in proc.stdout:
                f.write(line)
                if m := _PCT_RE.findall(line):
                    job["progress"] = max(job["progress"], min(99, int(m[-1])))
            proc.wait()
        if proc.returncode == 0 and output(stem, "dual"):
            job.update(state="done", progress=100)
        else:
            tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
            job.update(state="error", error=" / ".join(tail) or f"exit {proc.returncode}")

    threading.Thread(target=run, daemon=True).start()
    return status(stem)

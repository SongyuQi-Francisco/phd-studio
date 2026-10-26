"""scripts/demo/make_demo.py builds the fictional demo workspace used for screenshots and development. It touches every
part of the app (vault notes, plan, ideas, research maps with peers, results and a watched CSV, tracking data), so
this builds one end to end in a temp folder, in its own process, with the network cut and without PDFs."""
from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import config
from app.rmap.results import tabular

from .conftest import PROJECT

WATCHED = ["ACL", "NAACL", "EMNLP", "ICML", "NeurIPS", "ICLR", "CVPR", "IJCAI"]  # what scripts/demo/make_demo.py watches

# The generator in its own process (it points the app at the demo's studio.toml when it imports it), with the network
# cut: anything that would download something raises instead.
RUN = """
import runpy, socket, sys
import httpx

def blocked(*a, **k):
    raise httpx.ConnectError("the network is blocked in tests")

for name in ("get", "post", "put", "patch", "delete", "head", "request", "stream"):
    setattr(httpx, name, blocked)
httpx.Client.send = blocked
httpx.AsyncClient.send = blocked

def no_connect(self, *a, **k):
    raise OSError("the network is blocked in tests")

socket.socket.connect = no_connect
script = sys.argv[1]
sys.argv = [script, *sys.argv[2:]]
runpy.run_path(script, run_name="__main__")
"""


def clean_env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("STUDIO_")}
    return {**env, "HOME": str(home)}


def fake_catalog(folder: Path) -> Path:
    """A venue catalog (CORE + ccfddl files, as the app downloads them) holding every venue the demo watches."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = io.StringIO()
    w = csv.writer(rows)
    conf = folder / "ccfddl" / "conference" / "AI"
    conf.mkdir(parents=True)
    for n, name in enumerate(WATCHED):
        w.writerow([100 + n, f"The {name} Conference", name, "ICORE2026", "A*", "Yes", "4602", "", ""])
        due = now + timedelta(days=30 + n)
        (conf / f"{name.lower()}.yml").write_text(f"""\
- title: {name}
  description: The {name} Conference
  sub: AI
  rank: {{ccf: A, core: A*}}
  confs:
    - year: {now.year + 1}
      link: https://{name.lower()}.example
      timeline:
        - abstract_deadline: '{(due - timedelta(days=7)):%Y-%m-%d %H:%M:%S}'
          deadline: '{due:%Y-%m-%d %H:%M:%S}'
      timezone: AoE
      date: 'Jul 1-5'
      place: Somewhere
""", encoding="utf-8")
    (folder / "icore.csv").write_text(rows.getvalue(), encoding="utf-8")
    (folder / "fetched_at").write_text(now.isoformat(), encoding="utf-8")
    return folder


def test_the_demo_generator_builds_a_workspace_in_one_command(tmp_path: Path):
    catalog = fake_catalog(tmp_path / "venues")  # stands in for the download
    # where the generator puts the demo by default on a Mac, under ~/Library/Application Support: the results folder
    # lives there too, and the results import must be allowed to read it (#66)
    home = tmp_path / "home"
    out = home / "Library" / "Application Support" / "PhD Studio Demo"
    out.parent.mkdir(parents=True)
    r = subprocess.run([sys.executable, "-c", RUN, str(PROJECT / "scripts" / "demo" / "make_demo.py"), "--out", str(out), "--no-pdfs",
                        "--venues-from", str(catalog)],
                       cwd=PROJECT, env=clean_env(home), capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-3000:]
    assert "Demo ready" in r.stdout

    toml = (out / "studio.toml").read_text(encoding="utf-8")
    assert f'results_roots = ["{out / "experiments"}"]' in toml
    maps = sorted((out / "DemoVault" / "Research Maps").iterdir())
    assert len(maps) == 2 and all((m / "map.yaml").exists() for m in maps)
    atlas = next(m for m in maps if m.name.startswith("project-atlas"))
    beacon = next(m for m in maps if m.name.startswith("project-beacon"))
    assert list((atlas / "results").glob("*.yaml")) and (atlas / "result_sources.yaml").read_text(encoding="utf-8").count("watch: true") == 1
    assert (out / "experiments" / "atlas" / "ablation.csv").exists()
    assert (beacon / "peers.yaml").exists()  # the peers of #62 too

    # and the demo works as a workspace: its config loads and its results can be listed with the CLI
    env = {**clean_env(home), "STUDIO_CONFIG": str(out / "studio.toml"), "STUDIO_DEVICE": "demo"}
    r = subprocess.run([sys.executable, "-m", "app.cli", "map", "result", "list", "atlas"], cwd=PROJECT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "RECALL@10" in r.stdout and "LATENCY (MS)" in r.stdout, r.stdout + r.stderr


def test_a_results_root_inside_library_application_support_is_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The regression behind #66: that folder was closed as a whole, which shut out the default demo workspace."""
    home = tmp_path / "home"
    root = home / "Library" / "Application Support" / "PhD Studio Demo" / "experiments"
    root.mkdir(parents=True)
    f = root / "results.csv"
    f.write_text("config,metric,value\nA,acc,1\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config, "RESULTS_ROOTS", [str(root)])
    assert tabular.resolve(str(f)) == f.resolve()
    assert tabular.collapse(f.resolve()).startswith("~/Library/Application Support/")
    # what stays closed: credentials and this app's own folders, inside a root or not
    (home / ".ssh").mkdir()
    (home / ".ssh" / "keys.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(config, "RESULTS_ROOTS", [str(home)])
    with pytest.raises(ValueError, match="不能读"):
        tabular.resolve(str(home / ".ssh" / "keys.json"))

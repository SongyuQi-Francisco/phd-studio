# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.47", "pillow>=10", "imageio-ffmpeg>=0.5"]
# ///
"""README images: the hero banners and small copies of the demo screenshots in docs/assets/.

    uv run scripts/demo/readme_assets.py --shots <dir written by screenshots.py>

The banners are drawn from launcher/icon.svg and a dark screenshot of the demo, rendered with the
system Chrome. Screenshots are scaled to 1600 px wide WebP; the clips are re-encoded as small GIFs.
"""
from __future__ import annotations

import argparse
import base64
import subprocess
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

PROJECT = Path(__file__).resolve().parents[2]
ASSETS = PROJECT / "docs" / "assets"

# README image -> screenshot it is made from, or (screenshot, crop box in its pixels)
SIDE = (1990, 0, 2880, 1800)  # the chat panel of a 2880×1800 reader shot
PICKS = {
    "overview": "page-overview-dark.png",
    "reader-crop": "live-crop-answer-dark.png",
    "claim-gate": "live-claim-gate-diff-dark.png",
    "co-reader": ("live-explain-dark.png", SIDE),
    "note-links": "closeup-note-links-dark.png",
    "bilingual": "reader-bilingual-dark.png",
    "bilingual-mono": "reader-mono-dark.png",
    "reading-list": "page-reading-list-dark.png",
    "timeline": "page-timeline-plan-dark.png",
    "track-scholars": "page-track-scholars-dark.png",
    "track-radar": "page-track-radar-dark.png",
    "direction": "page-direction-dark.png",
    "ideas": "page-ideas-dark.png",
    "model-picker": "closeup-model-picker-dark.png",
}
CLIPS = {"clip-translate": "clip-select-translate-dark.mp4", "clip-assistant": "clip-assistant-dark.mp4"}

BANNER = """<!doctype html><html><head><meta charset="utf-8"><style>
:root { --bg: #0A0E14; --blue: #3987e5; --aqua: #1baf7a; --orange: #eb6834; }
* { box-sizing: border-box; margin: 0; }
html, body { width: 1280px; height: 420px; overflow: hidden; background: var(--bg); }
body { position: relative; font-family: -apple-system, "SF Pro Display", "PingFang SC", "Helvetica Neue", sans-serif; color: #f5f7fb; }
.grid { position: absolute; inset: 0;
  background-image: linear-gradient(rgba(57,135,229,.075) 1px, transparent 1px), linear-gradient(90deg, rgba(57,135,229,.075) 1px, transparent 1px);
  background-size: 40px 40px; background-position: -1px -1px;
  mask-image: radial-gradient(ellipse 85% 95% at 30% 45%, #000 40%, transparent 100%); }
.glow { position: absolute; width: 560px; height: 560px; left: -110px; top: -120px; border-radius: 50%;
  background: radial-gradient(circle, rgba(57,135,229,.22), rgba(57,135,229,0) 65%); }
.glow2 { position: absolute; width: 520px; height: 520px; right: -40px; bottom: -260px; border-radius: 50%;
  background: radial-gradient(circle, rgba(27,175,122,.14), rgba(27,175,122,0) 65%); }
.left { position: absolute; left: 72px; top: 0; bottom: 0; width: 600px; display: flex; flex-direction: column; justify-content: center; }
.brand { display: flex; align-items: center; gap: 22px; }
.logo { width: 92px; height: 92px; filter: drop-shadow(0 0 22px rgba(57,135,229,.45)); }
h1 { font-size: 60px; font-weight: 700; letter-spacing: -.02em; }
.tag { margin-top: 22px; font-size: 25px; font-weight: 500; color: #dfe6f1; letter-spacing: -.005em; line-height: 1.35; }
.sub { margin-top: 12px; font-size: 17px; color: #8b97a8; letter-spacing: .01em; }
.pills { margin-top: 26px; display: flex; gap: 10px; flex-wrap: wrap; }
.pill { font-size: 14px; padding: 7px 13px; border-radius: 999px; color: #cfe0f7; border: 1px solid rgba(57,135,229,.38);
  background: rgba(57,135,229,.09); }
.pill b { color: var(--aqua); font-weight: 600; }
.meta { position: absolute; left: 72px; bottom: 26px; font: 500 13px "SF Mono", ui-monospace, Menlo, monospace; color: #5d6a7c; letter-spacing: .04em; }
.meta i { color: var(--orange); font-style: normal; }
.shot { position: absolute; left: 720px; top: 58px; width: 760px; border-radius: 16px; overflow: hidden;
  border: 1px solid rgba(255,255,255,.10); box-shadow: 0 30px 80px rgba(0,0,0,.55), 0 0 0 1px rgba(57,135,229,.18), 0 0 60px rgba(57,135,229,.16); }
.shot img { display: block; width: 100%; }
.shot::after { content: ""; position: absolute; inset: 0; background: linear-gradient(90deg, rgba(10,14,20,0) 55%, rgba(10,14,20,.92)),
  linear-gradient(180deg, rgba(10,14,20,0) 62%, rgba(10,14,20,.95)); }
.bar { height: 26px; background: rgba(255,255,255,.04); border-bottom: 1px solid rgba(255,255,255,.06); display: flex; gap: 7px; align-items: center; padding-left: 12px; }
.bar span { width: 10px; height: 10px; border-radius: 50%; background: rgba(255,255,255,.14); }
</style></head><body>
<div class="grid"></div><div class="glow"></div><div class="glow2"></div>
<div class="left">
  <div class="brand"><img class="logo" src="{logo}"><h1>PhD Studio</h1></div>
  <div class="tag">{tag}</div>
  <div class="sub">{sub}</div>
  <div class="pills">{pills}</div>
</div>
<div class="meta">{meta}</div>
<div class="shot"><div class="bar"><span></span><span></span><span></span></div><img src="{shot}"></div>
</body></html>"""

TEXT = {
    "en": {"tag": "The local-first AI research studio<br>for PhD students.",
           "sub": "Your vault and Zotero stay the source of truth. Claude edits only after you approve the diff.",
           "pills": ["Read with a co-reader", "Track the field", "Meet deadlines", "Grow ideas"],
           "meta": "macOS · Python 3.12 · MIT <i>·</i> runs on 127.0.0.1"},
    "zh-CN": {"tag": "为 PhD 打造的本地 AI 科研工作台",
              "sub": "读论文 · 追前沿 · 赶截稿 · 理想法 —— 数据只在你的电脑上",
              "pills": ["Claude 伴读", "同行追踪", "截稿倒计时", "想法与方向"],
              "meta": "macOS · Python 3.12 · MIT <i>·</i> 只在本机 127.0.0.1 运行"},
}


def data_url(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def banners(shots: Path) -> None:
    logo = data_url(PROJECT / "launcher" / "icon.svg", "image/svg+xml")
    shot = data_url(shots / "page-overview-dark.png", "image/png")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome")
        page = b.new_page(viewport={"width": 1280, "height": 420}, device_scale_factor=2)
        for lang, t in TEXT.items():
            pills = "".join(f'<span class="pill">{p}</span>' for p in t["pills"])
            page.set_content(BANNER.replace("{logo}", logo).replace("{shot}", shot).replace("{tag}", t["tag"])
                             .replace("{sub}", t["sub"]).replace("{pills}", pills).replace("{meta}", t["meta"]))
            page.wait_for_timeout(400)
            out = ASSETS / ("banner.png" if lang == "en" else f"banner.{lang}.png")
            page.screenshot(path=str(out))
            Image.open(out).convert("RGB").save(out.with_suffix(".jpg"), quality=88, optimize=True, progressive=True)
            out.unlink()
            print(f"  {out.with_suffix('.jpg').name}")
        b.close()


def screenshots(shots: Path) -> None:
    for name, src in PICKS.items():
        src, box = src if isinstance(src, tuple) else (src, None)
        f = shots / src
        if not f.exists():
            print(f"  ! missing {src}")
            continue
        im = Image.open(f).convert("RGB")
        if box:
            im = im.crop(box)
        if im.width > 1600:
            im = im.resize((1600, round(im.height * 1600 / im.width)), Image.LANCZOS)
        out = ASSETS / f"{name}.webp"
        im.save(out, "WEBP", quality=82, method=6)
        print(f"  {out.name} {out.stat().st_size // 1024} KB")


def clips(shots: Path) -> None:
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    for name, src in CLIPS.items():
        f = shots / src
        if not f.exists():
            print(f"  ! missing {src}")
            continue
        out = ASSETS / f"{name}.gif"
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(f), "-vf",
                        "fps=10,scale=880:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
                        "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle", str(out)], check=True)
        print(f"  {out.name} {out.stat().st_size // 1024} KB")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shots", type=Path, required=True, help="the folder screenshots.py wrote")
    a = ap.parse_args()
    ASSETS.mkdir(parents=True, exist_ok=True)
    banners(a.shots)
    screenshots(a.shots)
    clips(a.shots)
    total = sum(p.stat().st_size for p in ASSETS.iterdir() if p.is_file())
    print(f"docs/assets: {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

"""List interface text still written in Chinese in the frontend code (#49), to track the extraction.

    uv run python scripts/i18n_scan.py                 # every line, grouped by file
    uv run python scripts/i18n_scan.py --summary       # counts per file only
    uv run python scripts/i18n_scan.py web/plan.js --strict   # exit 1 if anything is left

Reports each line of web/**/*.js (not web/vendor) with Chinese characters outside comments.
A line ending in `// i18n-ignore` is skipped (e.g. a regex that matches Chinese note text).
A file whose first lines contain `// i18n: done` must have none left: the scan then exits 1,
which tests/test_i18n.py checks in CI. See docs/i18n.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")
DONE_MARK = "// i18n: done"
IGNORE_MARK = "i18n-ignore"
# Before these, a "/" starts a regex literal rather than a division.
REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^") | {""}


def strip_comments(src: str) -> str:
    """The source with comments blanked out (newlines kept), strings and regexes left as they are."""
    out: list[str] = []
    stack = ["code"]  # "code" | "tpl"; each code frame inside a template counts its own braces
    depth = [0]
    i, n = 0, len(src)
    last = ""  # last significant code character, to tell a regex from a division
    while i < n:
        c = src[i]
        mode = stack[-1]
        if mode == "tpl":
            if c == "\\":
                out.append(src[i:i + 2]); i += 2; continue
            if c == "`":
                stack.pop(); depth.pop(); out.append(c); i += 1; last = "`"; continue
            if src.startswith("${", i):
                stack.append("code"); depth.append(0); out.append("${"); i += 2; last = "{"; continue
            out.append(c); i += 1; continue
        # code
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j; continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", src[i:j])); i = j; continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1]); i = j + 1; last = c; continue
        if c == "`":
            stack.append("tpl"); depth.append(0); out.append(c); i += 1; continue
        if c == "/" and last in REGEX_BEFORE:
            j, cls = i + 1, False
            while j < n and src[j] != "\n" and (cls or src[j] != "/"):
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    cls = True
                elif src[j] == "]":
                    cls = False
                j += 1
            out.append(src[i:j + 1]); i = j + 1; last = "/r"; continue
        if c == "{":
            depth[-1] += 1
        elif c == "}":
            if len(stack) > 1 and depth[-1] == 0:
                stack.pop(); depth.pop(); out.append(c); i += 1; continue
            depth[-1] -= 1
        if not c.isspace():
            last = c if not (c.isalnum() or c in "_$") else "w"
        out.append(c); i += 1
    return "".join(out)


def scan_file(path: Path) -> list[tuple[int, str]]:
    src = path.read_text(encoding="utf-8")
    raw = src.splitlines()
    hits = []
    for no, line in enumerate(strip_comments(src).splitlines(), 1):
        if CJK.search(line) and IGNORE_MARK not in raw[no - 1]:
            hits.append((no, raw[no - 1].strip()))
    return hits


def is_done(path: Path) -> bool:
    return any(DONE_MARK in line for line in path.read_text(encoding="utf-8").splitlines()[:12])


def files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths or [str(WEB)]):
        p = p if p.is_absolute() else (Path.cwd() / p)
        if p.is_dir():
            out += sorted(f for f in p.rglob("*.js") if "vendor" not in f.relative_to(p).parts)
        else:
            out.append(p)
    return out


def scan(paths: list[str] | None = None) -> dict[str, dict]:
    """{file: {"done": bool, "lines": [(line, text), ...]}} for files that are marked done or have hits."""
    result = {}
    for f in files(paths or []):
        hits, done = scan_file(f), is_done(f)
        if hits or done:
            rel = str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f)
            result[rel] = {"done": done, "lines": hits}
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="files or folders (default: web/)")
    ap.add_argument("--summary", action="store_true", help="counts per file only")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any Chinese text is left")
    args = ap.parse_args()
    result = scan(args.paths)
    broken = [f for f, r in result.items() if r["done"] and r["lines"]]
    total = sum(len(r["lines"]) for r in result.values())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for f, r in sorted(result.items(), key=lambda kv: -len(kv[1]["lines"])):
            mark = " (marked done!)" if r["done"] and r["lines"] else " (done)" if r["done"] else ""
            print(f"{f}: {len(r['lines'])}{mark}")
            if not args.summary:
                for no, text in r["lines"]:
                    print(f"  {f}:{no}  {text[:140]}")
        left = sum(1 for r in result.values() if r["lines"])
        print(f"\n{total} lines with Chinese text in {left} files; done: {sum(r['done'] for r in result.values())}")
    for f in broken:
        print(f"error: {f} is marked '{DONE_MARK}' but still has Chinese text", file=sys.stderr)
    return 1 if broken or (args.strict and total) else 0


if __name__ == "__main__":
    sys.exit(main())

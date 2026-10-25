"""Track the frontend's move to t() (#49): Chinese text and hard-coded locales still in the code,
and mistakes the tests should catch.

    uv run python scripts/i18n_scan.py                 # every line, grouped by file
    uv run python scripts/i18n_scan.py --summary       # counts per file only
    uv run python scripts/i18n_scan.py web/plan.js --strict   # exit 1 if anything is left

Left to extract, in web/**/*.js (not web/vendor), outside comments:
  text    Chinese characters
  locale  a hard-coded or missing locale: toLocaleDateString("zh-CN"), Intl.NumberFormat(), "en-US"
          (use locale() / formatDate() / formatNumber() from /i18n.js)
A line ending in `// i18n-ignore` is skipped (e.g. a regex that matches Chinese note text).

Errors (exit 1, checked by tests/test_i18n.py):
  - in a file that imports a t (from /i18n.js, or /rmap/strings.js until the research map moves over):
    a variable, parameter or destructured name `t` (it hides t() and breaks only when that code runs)
  and in files that import t from /i18n.js:
  - a key that is in neither web/locales/zh nor web/locales/en
  - a key built at runtime ("zotero.auto." + v, a variable) without the keys it can take listed in an
    `// i18n-keys: zotero.auto.{off,triage,all}` comment (same file for a prefix; same or previous line otherwise)
  - Chinese text or a locale left in a file marked `// i18n: done` (in its first lines)
See docs/i18n.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
LOCALES = WEB / "locales"
CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")
DONE_MARK = "// i18n: done"
IGNORE_MARK = "i18n-ignore"
# Before these, a "/" starts a regex literal rather than a division.
REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^") | {""}
LOCALE_PATTERNS = [
    re.compile(r"\.toLocale(?:Date|Time)?String\(\s*(?!locale\(\))"),
    re.compile(r"\bIntl\.(?:DateTimeFormat|NumberFormat|RelativeTimeFormat|ListFormat|PluralRules|DisplayNames|Collator)"
               r"\(\s*(?!locale\(\))(?!\)\s*\.resolvedOptions\(\)\s*\.timeZone)"),
    re.compile(r"[\"'](?:zh|en)(?:-[A-Za-z]{2,4})+[\"']"),
    re.compile(r"\blocaleCompare\([^()]*,\s*[\"']"),
]
IMPORTS_T = re.compile(r"import\s*\{[^}]*(?<![\w$])t(?![\w$])[^}]*\}\s*from\s*[\"'][^\"']+[\"']")
IMPORTS_I18N = re.compile(r"from\s*[\"']/i18n\.js[\"']")
KEY = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+")
PREFIX = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*\.")
T_CALL = re.compile(r"(?<![\w$.])tx?\(")
LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'|`([^`]*)`')
DECLARES_KEYS = re.compile(r"i18n-keys:\s*(.+?)\s*(?:\*/)?$")


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


def _line_of(code: str, pos: int) -> int:
    return code.count("\n", 0, pos) + 1


def _names_t(group: str) -> bool:
    """Does a parameter list or destructuring pattern declare a name t?"""
    for m in re.finditer(r"(?<![\w$])t(?![\w$])", group):
        before, after = group[:m.start()], group[m.end():].lstrip()
        if before.endswith(".") and not before.endswith("..."):
            continue  # obj.t
        if after[:1] in (":", "("):
            continue  # { t: other } renames; t(...) is a call in a default value
        return True
    return False


SHADOW_PATTERNS = [
    (re.compile(r"(?<![\w$.])(?:const|let|var)\s+t(?![\w$])"), None),
    (re.compile(r"(?<![\w$.])(?:const|let|var)\s*([\[{][^=;]*[\]}])\s*="), 1),
    (re.compile(r"\(([^()]*)\)\s*=>"), 1),
    (re.compile(r"(?<![\w$.])t\s*=>"), None),
    (re.compile(r"\bfunction\b\s*[\w$]*\s*\(([^()]*)\)"), 1),
    (re.compile(r"\bfunction\s+t\s*\("), None),
    (re.compile(r"\bcatch\s*\(\s*t\s*\)"), None),
]


def shadowed_t(code: str) -> list[int]:
    """Lines (of comment-free code) that declare a variable, parameter or destructured name t."""
    lines = set()
    for pat, group in SHADOW_PATTERNS:
        for m in pat.finditer(code):
            if group is None or _names_t(m.group(group)):
                lines.add(_line_of(code, m.start()))
    return sorted(lines)


def _first_arg(code: str, start: int) -> str:
    """The text of a call's first argument, from just after its "("."""
    depth, i, n = 0, start, len(code)
    while i < n:
        c = code[i]
        if c in "\"'`":
            j = i + 1
            while j < n and code[j] != c:
                j += 2 if code[j] == "\\" else 1
            i = j + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif c == "," and depth == 0:
            break
        i += 1
    return code[start:i]


def _expand(spec: str) -> list[str]:
    m = re.fullmatch(r"(.*)\{([^{}]*)\}(.*)", spec)
    if not m:
        return [spec]
    return [k for part in m.group(2).split(",") for k in _expand(m.group(1) + part.strip() + m.group(3))]


def declared_keys(src: str) -> dict[int, list[str]]:
    """{line: keys} from `// i18n-keys: a.b.{c,d} e.f` comments."""
    out = {}
    for no, line in enumerate(src.splitlines(), 1):
        if m := DECLARES_KEYS.search(line):
            out[no] = [k for spec in re.findall(r"[^\s,{}]*\{[^{}]*\}[^\s,{}]*|[^\s,]+", m.group(1)) for k in _expand(spec)]
    return out


def key_uses(src: str, code: str | None = None) -> list[tuple[int, str, str]]:
    """Every key a t() / tx() call can ask for: (line, "key" | "prefix" | "dynamic", text)."""
    code = strip_comments(src) if code is None else code
    out = []
    for m in T_CALL.finditer(code):
        arg = _first_arg(code, m.end())
        line = _line_of(code, m.start())
        full, prefixes = [], []
        for lm in LITERAL.finditer(arg):
            text = next(g for g in lm.groups() if g is not None)
            if lm.group(3) is not None and "${" in text:
                text = text.split("${", 1)[0]
            if KEY.fullmatch(text):
                full.append(text)
            elif PREFIX.fullmatch(text):
                prefixes.append(text)
        if prefixes:
            out += [(line, "prefix", p) for p in prefixes]
        elif full:
            out += [(line, "key", k) for k in full]
        else:
            out.append((line, "dynamic", arg.strip()))
    return out


def web_tables() -> dict[str, dict[str, object]]:
    tables: dict[str, dict[str, object]] = {}
    for lang in ("zh", "en"):
        tables[lang] = {f"{f.stem}.{k}": v for f in sorted((LOCALES / lang).glob("*.json"))
                        for k, v in json.loads(f.read_text(encoding="utf-8")).items()}
    return tables


def scan_file(path: Path, tables: dict[str, dict[str, object]] | None = None) -> dict:
    """{"lines": [(line, "text" | "locale", source)], "errors": [(line, message)]}"""
    src = path.read_text(encoding="utf-8")
    raw = src.splitlines()
    code = strip_comments(src)
    lines, errors = [], []
    for no, line in enumerate(code.splitlines(), 1):
        if IGNORE_MARK in raw[no - 1]:
            continue
        if CJK.search(line):
            lines.append((no, "text", raw[no - 1].strip()))
        if any(p.search(line) for p in LOCALE_PATTERNS):
            lines.append((no, "locale", raw[no - 1].strip()))
    if IMPORTS_T.search(code):
        errors += [(no, "a local name t hides the imported t(); rename it") for no in shadowed_t(code)]
    if IMPORTS_T.search(code) and IMPORTS_I18N.search(code):
        tables = tables or web_tables()
        known = set(tables["zh"]) & set(tables["en"])
        declared = declared_keys(src)
        all_declared = [k for ks in declared.values() for k in ks]
        for no, ks in declared.items():
            errors += [(no, f"i18n-keys lists {k!r}, which is not in both zh and en") for k in ks if k not in known]
        for no, kind, text in key_uses(src, code):
            if kind == "key" and text not in known:
                errors.append((no, f"key {text!r} is not in both zh and en"))
            elif kind == "prefix" and not any(k.startswith(text) for k in all_declared):
                errors.append((no, f"key {text!r} + ... is built at runtime: list its keys in an // i18n-keys: comment"))
            elif kind == "dynamic" and not (declared.get(no) or declared.get(no - 1)):
                errors.append((no, f"key {text[:40]!r} is a variable: list its keys in an // i18n-keys: comment on this or the previous line"))
    return {"lines": lines, "errors": errors}


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
    """{file: {"done", "lines", "errors"}} for files that are marked done or have something to report."""
    result, tables = {}, web_tables()
    for f in files(paths or []):
        r, done = scan_file(f, tables), is_done(f)
        if done and r["lines"]:
            r["errors"] += [(no, f"marked '{DONE_MARK}' but still has {kind}") for no, kind, _ in r["lines"]]
        if r["lines"] or r["errors"] or done:
            rel = str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f)
            result[rel] = {"done": done, **r}
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="files or folders (default: web/)")
    ap.add_argument("--summary", action="store_true", help="counts per file only")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--strict", action="store_true", help="exit 1 if anything is left to extract")
    args = ap.parse_args()
    result = scan(args.paths)
    count = lambda kind: sum(1 for r in result.values() for _, k, _ in r["lines"] if k == kind)  # noqa: E731
    errors = sum(len(r["errors"]) for r in result.values())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for f, r in sorted(result.items(), key=lambda kv: (-len(kv[1]["errors"]), -len(kv[1]["lines"]))):
            text = sum(1 for _, k, _ in r["lines"] if k == "text")
            mark = " (done)" if r["done"] else ""
            print(f"{f}: {text} text, {len(r['lines']) - text} locale{mark}"
                  + (f", {len(r['errors'])} errors" if r["errors"] else ""))
            for no, msg in r["errors"]:
                print(f"  {f}:{no}  error: {msg}")
            if not args.summary:
                for no, kind, src in r["lines"]:
                    print(f"  {f}:{no}  [{kind}] {src[:140]}")
        left = sum(1 for r in result.values() if r["lines"])
        print(f"\n{count('text')} lines with Chinese text and {count('locale')} with a hard-coded locale in {left} files;"
              f" done: {sum(r['done'] for r in result.values())}; errors: {errors}")
    return 1 if errors or (args.strict and (count("text") or count("locale"))) else 0


if __name__ == "__main__":
    sys.exit(main())

"""Interface language (#49): message files, app/i18n.py, /api/i18n and settings, web/i18n.js, the CJK scan."""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app import config, i18n

ROOT = Path(__file__).resolve().parent.parent
TREES = {"web": ROOT / "web" / "locales", "app": ROOT / "app" / "locales"}
PLURAL_FORMS = {"zero", "one", "two", "few", "many", "other"}
PH = re.compile(r"\{(\w+)\}")
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")

sys.path.insert(0, str(ROOT / "scripts"))
import i18n_scan  # noqa: E402


def modules(tree: Path) -> list[str]:
    return sorted({f.stem for lang in i18n.LANGS for f in (tree / lang).glob("*.json")})


def forms(v: object) -> list[str]:
    return list(v.values()) if isinstance(v, dict) else [v]  # type: ignore[list-item]


# ---------- the message files ----------


@pytest.mark.parametrize("tree", TREES.values(), ids=TREES.keys())
def test_every_module_exists_in_both_languages_with_the_same_keys(tree: Path):
    for mod in modules(tree):
        tables = {}
        for lang in i18n.LANGS:
            f = tree / lang / f"{mod}.json"
            assert f.exists(), f"{f.relative_to(ROOT)} is missing"
            tables[lang] = json.loads(f.read_text(encoding="utf-8"))
        zh, en = set(tables["zh"]), set(tables["en"])
        assert zh == en, f"{mod}: only in zh {sorted(zh - en)}, only in en {sorted(en - zh)}"


@pytest.mark.parametrize("tree", TREES.values(), ids=TREES.keys())
def test_values_are_text_or_plural_forms_with_matching_placeholders(tree: Path):
    tables = i18n.load_tree(tree)
    for key, zh in tables["zh"].items():
        en = tables["en"][key]
        for lang, v in (("zh", zh), ("en", en)):
            if isinstance(v, dict):
                assert "other" in v and set(v) <= PLURAL_FORMS, f"{lang} {key}: plural forms are {sorted(v)}"
            assert all(isinstance(s, str) and s.strip() for s in forms(v)), f"{lang} {key}: empty or not text"
        ph = {lang: {p for s in forms(v) for p in PH.findall(s)} for lang, v in (("zh", zh), ("en", en))}
        assert ph["zh"] == ph["en"], f"{key}: placeholders differ {ph}"
        if any(isinstance(v, dict) for v in (zh, en)):
            assert "n" in ph["en"], f"{key}: plural forms are picked by {{n}}"


def test_keys_are_snake_case_with_dots():
    for tree in TREES.values():
        for key in i18n.load_tree(tree)["zh"]:
            assert re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)+", key), key


def test_the_frontend_scan_finds_no_errors():
    """Unknown keys, undeclared dynamic keys, a local t, leftovers in files marked done: see scripts/i18n_scan.py."""
    errors = {f: r["errors"] for f, r in i18n_scan.scan().items() if r["errors"]}
    assert not errors, errors


def py_modules() -> list[tuple[Path, ast.Module, str]]:
    return [(f, ast.parse(f.read_text(encoding="utf-8")), f.read_text(encoding="utf-8")) for f in sorted((ROOT / "app").rglob("*.py"))]


def imported_from_i18n(tree: ast.Module) -> set[str]:
    return {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "i18n"
            for a in n.names}


def key_args(tree: ast.Module) -> list[tuple[int, ast.expr]]:
    """The key argument of every t() / tl() / tv() call, direct or as i18n.t()."""
    names = imported_from_i18n(tree) & {"t", "tl", "tv"}
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = (f.id if isinstance(f, ast.Name) and f.id in names else
                f.attr if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "i18n" else None)
        if name in ("t", "tl", "tv"):
            idx = 1 if name == "tl" else 0
            if len(n.args) > idx:
                out.append((n.lineno, n.args[idx]))
    return out


def test_every_key_the_backend_asks_for_exists():
    tables = i18n.load_tree(i18n.APP_LOCALES)
    known = set(tables["zh"]) & set(tables["en"])
    problems, used = [], 0
    for f, tree, src in py_modules():
        declared = i18n_scan.declared_keys(src)
        problems += [(f.name, no, k) for no, ks in declared.items() for k in ks if k not in known]
        for no, arg in key_args(tree):
            used += 1
            consts = [arg] if isinstance(arg, ast.Constant) else [arg.body, arg.orelse] if isinstance(arg, ast.IfExp) else []
            if consts and all(isinstance(c, ast.Constant) and isinstance(c.value, str) for c in consts):
                problems += [(f.name, no, c.value) for c in consts if c.value not in known]
            elif not (declared.get(no) or declared.get(no - 1)):
                problems.append((f.name, no, "a key built at runtime needs an `# i18n-keys:` comment"))
    assert used and not problems, problems


def test_no_local_name_hides_t_in_python():
    found = []
    for f, tree, _ in py_modules():
        if "t" not in imported_from_i18n(tree):
            continue
        for n in ast.walk(tree):
            if (isinstance(n, ast.Name) and n.id == "t" and isinstance(n.ctx, ast.Store)) or (isinstance(n, ast.arg) and n.arg == "t") \
                    or (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "t") \
                    or (isinstance(n, ast.ExceptHandler) and n.name == "t"):
                found.append((f.name, n.lineno))
    assert not found, found


# ---------- app/i18n.py ----------


def test_backend_interpolation_plurals_and_fallback(tmp_path, monkeypatch):
    for lang, data in {"zh": {"hello": "你好，{name}", "count": "{n} 篇", "only_zh": "只有中文"},
                       "en": {"hello": "Hello, {name}", "count": {"zero": "none", "one": "{n} paper", "other": "{n} papers"}}}.items():
        (tmp_path / lang).mkdir()
        (tmp_path / lang / "m.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(i18n, "APP_LOCALES", tmp_path)
    assert i18n.tl("en", "m.hello", name="Ada") == "Hello, Ada"
    assert i18n.tl("zh", "m.hello", name="Ada") == "你好，Ada"
    assert i18n.tl("en", "m.hello") == "Hello, {name}"
    assert [i18n.tl("en", "m.count", n=n) for n in (0, 1, 2)] == ["none", "1 paper", "2 papers"]
    assert i18n.tl("zh", "m.count", n=1) == "1 篇"
    assert i18n.tl("en", "m.only_zh") == "只有中文"
    assert i18n.tl("en", "m.nope") == "m.nope"


def test_ui_language_follows_settings_then_the_system(monkeypatch):
    assert i18n.ui_lang() == "zh"  # conftest: STUDIO_LANG=zh stands in for the OS language
    config.save_settings({"ui_language": "en"})
    assert i18n.ui_lang() == "en"
    assert i18n.t("reading.view.reading") == "Reading"
    config.save_settings({"ui_language": "system"})
    monkeypatch.setenv("STUDIO_LANG", "en_US.UTF-8")
    i18n.system_lang.cache_clear()
    try:
        assert i18n.ui_lang() == "en"
    finally:
        i18n.system_lang.cache_clear()


def test_system_language_from_the_locale_when_not_on_a_mac(monkeypatch):
    monkeypatch.delenv("STUDIO_LANG")
    monkeypatch.setattr(i18n.sys, "platform", "linux")
    for env, want in (({"LANG": "zh_CN.UTF-8"}, "zh"), ({"LANG": "en_GB.UTF-8"}, "en"), ({"LANG": "C"}, "en")):
        for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            monkeypatch.delenv(var, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        i18n.system_lang.cache_clear()
        assert i18n.system_lang() == want, env
    i18n.system_lang.cache_clear()


def test_answer_language_is_its_own_setting(monkeypatch):
    monkeypatch.setitem(config.PROFILE, "answer_language", "中文")
    assert i18n.answer_language() == "中文"  # "": as in studio.toml
    config.save_settings({"answer_language": "en"})
    assert i18n.answer_language() == "English" and not i18n.answers_in_chinese()
    config.save_settings({"answer_language": "ui", "ui_language": "zh"})
    assert i18n.answer_language() == "中文" and i18n.answers_in_chinese()
    config.save_settings({"ui_language": "en"})
    assert i18n.answer_language() == "English"


def test_vault_language_is_fixed_by_studio_toml(monkeypatch):
    assert i18n.vault_lang() == "zh"  # default, whatever this Mac's interface language
    config.save_settings({"ui_language": "en"})
    assert i18n.vault_lang() == "zh" and i18n.tv("reading.view.title") == "阅读清单"
    monkeypatch.setitem(config.PROFILE, "vault_language", "en")
    assert i18n.vault_lang() == "en"
    monkeypatch.setitem(config.PROFILE, "vault_language", "ui")
    assert i18n.vault_lang() == "en"
    config.save_settings({"ui_language": "zh"})
    assert i18n.vault_lang() == "zh"


def test_takeaway_length_suits_the_answer_language():
    from app import claude_chat
    assert "不超过 60 字" in claude_chat.takeaway_prompt()
    config.save_settings({"answer_language": "en"})
    prompt = claude_chat.takeaway_prompt("full text")
    assert "English" in prompt and "25 个英文单词" in prompt and "60 字" not in prompt and prompt.endswith("full text")


def test_prompts_use_the_answer_language(monkeypatch):
    from app import claude_chat
    config.save_settings({"answer_language": "en"})
    prompt = claude_chat._studio_prompt()
    assert "一律用English回答" in prompt


# ---------- the API ----------


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def test_api_sends_both_languages_and_the_setting(client):
    r = client.get("/api/i18n").json()
    assert r["setting"] == "system" and r["lang"] == "zh" and r["system"] == "zh"
    assert r["messages"]["zh"]["settings.title"] == "设置"
    assert r["messages"]["en"]["settings.title"] == "Settings"


def test_api_marks_a_development_server(client):
    from fastapi.testclient import TestClient

    from app.main import app
    assert client.get("/api/i18n").json()["dev"] is False  # the test client has no port
    assert TestClient(app, base_url="http://127.0.0.1:8770").get("/api/i18n").json()["dev"] is False
    assert TestClient(app, base_url="http://127.0.0.1:8795").get("/api/i18n").json()["dev"] is True


def test_settings_save_and_check_the_languages(client):
    r = client.post("/api/settings", json={"ui_language": "en", "answer_language": "zh"})
    assert r.status_code == 200 and r.json()["ui_language"] == "en" and r.json()["answer_language"] == "zh"
    assert client.get("/api/i18n").json()["lang"] == "en"
    bad = client.post("/api/settings", json={"ui_language": "fr"})
    assert bad.status_code == 400 and "can't be fr" in bad.json()["detail"]  # the error is in the new language
    config.save_settings({"ui_language": "zh"})
    assert "不能设为" in client.post("/api/settings", json={"answer_language": "fr"}).json()["detail"]


# ---------- web/i18n.js ----------


@pytest.mark.parametrize("engine", ["node", "jsc"])
def test_web_i18n_in_a_javascript_engine(engine, tmp_path):
    exe = shutil.which("node") if engine == "node" else (str(JSC) if JSC.exists() else None)
    if not exe:
        pytest.skip(f"{engine} is not installed")
    shutil.copy(ROOT / "web" / "i18n.js", tmp_path / "i18n.mjs")  # .mjs: a module in every engine
    shutil.copy(ROOT / "tests" / "js" / "i18n.test.mjs", tmp_path / "test.mjs")
    args = [exe, "-m", "test.mjs"] if engine == "jsc" else [exe, "test.mjs"]
    r = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr


# ---------- scripts/i18n_scan.py ----------


def test_scan_skips_comments_and_ignored_lines_but_finds_strings_and_markup(tmp_path):
    f = tmp_path / "x.js"
    f.write_text("\n".join([
        "// 注释",
        "const a = \"文字\"; // 尾注释",
        "/* 块",
        "   注释 */ const b = 1;",
        "const url = \"https://x.y\"; const c = `模板 ${d ? html`<b>粗</b>` : \"否\"}`;",
        "const re = /第(\\d+)页/; // i18n-ignore",
        "const e = x / 2; // 除法",
        "return html`<p>",
        "  段落</p>`;",
    ]), encoding="utf-8")
    assert [n for n, kind, _ in i18n_scan.scan_file(f)["lines"] if kind == "text"] == [2, 5, 9]


def test_scan_finds_a_local_t_in_its_usual_forms():
    code = i18n_scan.strip_comments("\n".join([
        "const t = setInterval(f, 1000);",       # 1
        "const toggle = (t) => x[t];",           # 2
        "items.map((t, i) => t.id);",            # 3
        "const { a, t } = obj;",                 # 4
        "function show(a, t) {}",                # 5
        "list.forEach(t => go(t));",             # 6
        "try {} catch (t) {}",                   # 7
        "const [t] = pair;",                     # 8
        "html`${t(\"k.a\")} ${o.t} ${fmt(t(\"k.b\"))}`;",  # 9: calls and properties are fine
        "const { t: other } = obj;",             # 10: renamed, fine
        "const cb = ({ s }) => t(\"k.c\");",    # 11: fine
        "const tt = 1, at = 2;",                 # 12: fine
    ]))
    assert i18n_scan.shadowed_t(code) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_scan_flags_hard_coded_locales(tmp_path):
    f = tmp_path / "x.js"
    f.write_text("\n".join([
        "d.toLocaleDateString(\"zh-CN\", { month: \"long\" });",  # 1
        "n.toLocaleString();",                                          # 2: the system's locale, not the interface's
        "new Intl.NumberFormat(\"en-US\").format(n);",                # 3
        "const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;",  # 4: fine
        "d.toLocaleDateString(locale(), { day: \"numeric\" });",      # 5: fine
        "formatDate(d, { month: \"short\" });",                        # 6: fine
        "a.localeCompare(b);",                                           # 7: fine
        "d.toLocaleDateString(\"sv\"); // i18n-ignore: ISO date",      # 8: ignored
    ]), encoding="utf-8")
    assert [(n, k) for n, k, _ in i18n_scan.scan_file(f)["lines"]] == [(1, "locale"), (2, "locale"), (3, "locale")]


def test_scan_checks_static_ternary_and_dynamic_keys(tmp_path):
    f = tmp_path / "x.js"
    f.write_text("\n".join([
        'import { t } from "/i18n.js";',
        'const STATES = ["a", "b"]; // i18n-keys: settings.{title,model}',
        't("settings.title");',                                  # fine
        't(ok ? "settings.title" : "settings.nope");',            # 4: a ternary branch is missing
        't("settings." + s);',                                    # fine: declared above
        't("zotero.state." + s);',                                # 6: no declaration covers it
        't(key);',                                                # 7: a variable, undeclared
        't(key); // i18n-keys: common.on common.off',             # fine
        't(`reminders.type.${x}`);',                              # 9: template prefix, undeclared
        't("settings.nope2", { n: 1 }); tx("common.done", {});',  # 10: missing key; tx is checked too
        '// i18n-keys: common.gone',                              # 11: lists a key that does not exist
    ]), encoding="utf-8")
    errors = i18n_scan.scan_file(f)["errors"]
    assert sorted(n for n, _ in errors) == [4, 6, 7, 9, 10, 11], errors


def test_files_marked_done_have_no_chinese_text_left():
    result = i18n_scan.scan()
    done = [f for f, r in result.items() if r["done"]]
    assert "web/bilingual.js" in done and "web/reminders.js" in done and "web/models.js" in done
    left = {f: r["lines"][:3] for f, r in result.items() if r["done"] and r["lines"]}
    assert not left, f"marked '// i18n: done' but still Chinese: {left}"

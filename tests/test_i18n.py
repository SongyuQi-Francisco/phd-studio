"""Interface language (#49): message files, app/i18n.py, /api/i18n and settings, web/i18n.js, the CJK scan."""
from __future__ import annotations

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


def test_every_key_the_frontend_asks_for_exists():
    tables = i18n.web_messages()
    call = re.compile(r"(?<![\w$.])tx?\(\s*\"([a-z][a-z0-9_.]*)\"\s*[,)]")
    used = {(f.name, k) for f in (ROOT / "web").rglob("*.js") if "vendor" not in f.parts
            for k in call.findall(i18n_scan.strip_comments(f.read_text(encoding="utf-8")))}
    assert used, "no t() calls found"
    missing = sorted((f, k) for f, k in used if k not in tables["zh"] or k not in tables["en"])
    assert not missing, missing


def test_every_key_the_backend_asks_for_exists():
    tables = i18n.load_tree(i18n.APP_LOCALES)
    call = re.compile(r"(?<![\w])(?:i18n\.)?t\(\s*\"([a-z][a-z0-9_.]*)\"")
    used = {k for f in (ROOT / "app").glob("*.py") for k in call.findall(f.read_text(encoding="utf-8"))}
    assert used
    assert not sorted(k for k in used if k not in tables["zh"] or k not in tables["en"])


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


def test_settings_save_and_check_the_languages(client):
    r = client.post("/api/settings", json={"ui_language": "en", "answer_language": "zh"})
    assert r.status_code == 200 and r.json()["ui_language"] == "en" and r.json()["answer_language"] == "zh"
    assert client.get("/api/i18n").json()["lang"] == "en"
    bad = client.post("/api/settings", json={"ui_language": "fr"})
    assert bad.status_code == 400 and "can't be fr" in bad.json()["detail"]  # the error is in the new language
    config.save_settings({"ui_language": "zh"})
    assert "不能设为" in client.post("/api/settings", json={"answer_language": "fr"}).json()["detail"]


# ---------- web/i18n.js ----------


def test_web_i18n_in_a_javascript_engine():
    engine = shutil.which("node") or (str(JSC) if JSC.exists() else None)
    if not engine:
        pytest.skip("no JavaScript engine (node, or macOS's jsc)")
    args = [engine, "-m", "tests/js/i18n.test.mjs"] if engine.endswith("jsc") else [engine, "tests/js/i18n.test.mjs"]
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60)
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
    assert [n for n, _ in i18n_scan.scan_file(f)] == [2, 5, 9]


def test_files_marked_done_have_no_chinese_text_left():
    result = i18n_scan.scan()
    done = [f for f, r in result.items() if r["done"]]
    assert "web/bilingual.js" in done and "web/reminders.js" in done and "web/models.js" in done
    left = {f: r["lines"][:3] for f, r in result.items() if r["done"] and r["lines"]}
    assert not left, f"marked '// i18n: done' but still Chinese: {left}"

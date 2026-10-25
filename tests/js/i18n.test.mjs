// web/i18n.js without a browser: run by tests/test_i18n.py with node, or with JavaScriptCore's jsc
// (WebKit's engine, the one the desktop window uses). Prints "ok" or throws.
// The test copies web/i18n.js next to this file as i18n.mjs, so every engine loads it as a module.
import { t, tx, lang, loadMessages, setLangSetting, onLangChange, intlLocale } from "./i18n.mjs";

const out = typeof print === "function" ? print : console.log.bind(console);
const warnings = [];
if (typeof console === "undefined") globalThis.console = {};
console.warn = (m) => warnings.push(m);

function eq(actual, expected, what) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${what}: expected ${e}, got ${a}`);
}

loadMessages({
  setting: "system",
  system: "en",
  messages: {
    zh: { "m.hello": "你好，{name}", "m.count": "{n} 篇", "m.only_zh": "只有中文", "m.link": "打开 {link} 看看" },
    en: {
      "m.hello": "Hello, {name}",
      "m.count": { "zero": "no papers", "one": "{n} paper", "other": "{n} papers" },
      "m.only_en": "English only",
      "m.link": "Open {link} to see",
    },
  },
});

// "system" follows the OS language the server reported
eq(lang(), "en", "system language");
eq(intlLocale(), "en-US", "Intl locale");

// interpolation
eq(t("m.hello", { name: "Ada" }), "Hello, Ada", "interpolation");
eq(t("m.hello"), "Hello, {name}", "no params leaves the placeholder");
eq(t("m.hello", {}), "Hello, {name}", "missing param leaves the placeholder");
if (!warnings.some((w) => w.includes("{name}"))) throw new Error("a missing param should warn");

// plurals
eq(t("m.count", { n: 0 }), "no papers", "zero form");
eq(t("m.count", { n: 1 }), "1 paper", "one form");
eq(t("m.count", { n: 5 }), "5 papers", "other form");

// fallback: other language, then the key, each warned once
warnings.length = 0;
eq(t("m.only_zh"), "只有中文", "falls back to the other language");
eq(t("m.nope"), "m.nope", "falls back to the key");
eq(t("m.nope"), "m.nope", "still the key");
eq(warnings.filter((w) => w.includes("m.nope")).length, 1, "warns once per key");
eq(warnings.filter((w) => w.includes("m.only_zh")).length, 1, "warns about the fallback");

// nodes inside a sentence
const node = { tag: "a" };
eq(tx("m.link", { link: node }), ["Open ", node, " to see"], "tx keeps nodes");

// switching re-runs subscribers and changes the text at once
let seen = null;
const off = onLangChange((l) => { seen = l; });
setLangSetting("zh");
eq(seen, "zh", "subscriber called");
eq(lang(), "zh", "switched");
eq(t("m.hello", { name: "Ada" }), "你好，Ada", "text in the new language");
eq(t("m.count", { n: 1 }), "1 篇", "Chinese has one form");
eq(t("m.only_en"), "English only", "falls back to English");
off();
setLangSetting("system");
eq(seen, "zh", "unsubscribed");
eq(lang(), "en", "back to the system language");

out("ok");

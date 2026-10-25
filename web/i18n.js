// Interface language (#49): t(key, params) over the per-module files web/locales/<lang>/<module>.json.
// No build step and no imports: /api/i18n sends both languages, flattened to "<module>.<key>" → text,
// so switching language is a re-render, not a reload. Conventions and checks: docs/i18n.md.
//
//   t("settings.title")                       → "设置" | "Settings"
//   t("reading.count", { n: 3 })              → plural forms {"one": "...", "other": "..."} picked by n
//   tx("zotero.help.save", { file: html`<code>${f}</code>` })  → array of text and nodes, for markup inside a sentence
//   formatDate(d, { month: "short", day: "numeric" })  → dates and numbers in the interface language
//
// Call t() while rendering, never at module top level: the text is looked up again on every render,
// which is what makes a language switch take effect at once. Never name a local variable t.

export const LANGS = ["zh", "en"];

let current = "zh";
let setting = "system"; // what Settings says: "zh" | "en" | "system"
let osLang = "zh"; // the OS language, as the server sees it
let tables = { zh: {}, en: {} };
let dev = false; // a development server: a missing key shows up loudly instead of as the bare key
const subs = new Set();
const warned = new Set();

const warn = (msg) => {
  if (warned.has(msg)) return;
  warned.add(msg);
  if (typeof console !== "undefined") console.warn("[i18n] " + msg);
};

export const lang = () => current;
export const langSetting = () => setting;
export const systemLang = () => osLang;
// For Intl / toLocale*String: dates, times and numbers in the interface language.
export const locale = () => (current === "zh" ? "zh-CN" : "en-US"); // i18n-ignore
export const formatDate = (d, options) => new Date(d).toLocaleDateString(locale(), options);
export const formatTime = (d, options) => new Date(d).toLocaleTimeString(locale(), options);
export const formatDateTime = (d, options) => new Date(d).toLocaleString(locale(), options);
export const formatNumber = (n, options) => new Intl.NumberFormat(locale(), options).format(n);

let rules = null;
function pluralForm(n) {
  if (!rules || rules.lang !== current) {
    rules = typeof Intl !== "undefined" && Intl.PluralRules ? new Intl.PluralRules(locale()) : null;
    if (rules) rules.lang = current;
  }
  return rules ? rules.select(n) : n === 1 ? "one" : "other";
}

function lookup(key) {
  const v = tables[current][key];
  if (v !== undefined) return v;
  const other = current === "zh" ? "en" : "zh";
  const w = tables[other][key];
  warn(w === undefined ? `missing key "${key}"` : `"${key}" is missing in ${current}, using ${other}`);
  return w;
}

function pick(v, params, key) {
  if (typeof v === "string") return v;
  const n = params?.n;
  if (typeof n !== "number") { warn(`"${key}" has plural forms but no numeric n`); return v.other ?? ""; }
  if (n === 0 && v.zero != null) return v.zero;
  return v[pluralForm(n)] ?? v.other ?? "";
}

// The message split around its {placeholders}: [text, name, text, name, ..., text].
const PH = /\{(\w+)\}/g;

export function t(key, params) {
  const v = lookup(key);
  if (v === undefined) return dev ? `⟦${key}⟧` : key;
  const s = pick(v, params, key);
  if (!params) return s;
  return s.replace(PH, (m, name) => {
    if (params[name] === undefined || params[name] === null) { warn(`"${key}" needs {${name}}`); return m; }
    return String(params[name]);
  });
}

// Like t(), but placeholders may be nodes (a link, <code>, <b>); returns an array to render as children.
export function tx(key, params = {}) {
  const v = lookup(key);
  if (v === undefined) return [dev ? `⟦${key}⟧` : key];
  const parts = pick(v, params, key).split(PH);
  return parts.map((p, i) => (i % 2 === 0 ? p : params[p] ?? `{${p}}`)).filter((p) => p !== "");
}

// Runs on every switch; returns the unsubscribe function (fits straight into useEffect).
export function onLangChange(fn) {
  subs.add(fn);
  return () => { subs.delete(fn); };
}

const resolve = (s) => (LANGS.includes(s) ? s : osLang);

function switchTo(next) {
  if (next === current) return;
  current = next;
  if (typeof document !== "undefined") document.documentElement.lang = locale();
  subs.forEach((f) => f(current));
}

// Called after Settings saved ui_language: switches at once, no reload.
export function setLangSetting(s) {
  setting = s;
  switchTo(resolve(s));
}

// The /api/i18n payload: {setting, system, dev, messages: {zh: {...}, en: {...}}}. Exported for tests.
export function loadMessages(data) {
  tables = { zh: data.messages?.zh || {}, en: data.messages?.en || {} };
  dev = !!data.dev;
  osLang = LANGS.includes(data.system) ? data.system : "zh";
  setting = data.setting || "system";
  const next = resolve(setting);
  if (next !== current) switchTo(next);
  else if (typeof document !== "undefined") document.documentElement.lang = locale();
}

// Before the first render (main.js). If the server can't answer, keys show instead of text.
export async function initI18n() {
  try {
    const r = await fetch("/api/i18n");
    if (!r.ok) throw new Error("HTTP " + r.status);
    loadMessages(await r.json());
  } catch (e) {
    warn("could not load messages: " + (e.message || e));
  }
}

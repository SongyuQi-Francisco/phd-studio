// The research map's own text is in web/locales/<lang>/rmap.json and goes through t() from /i18n.js
// (docs/i18n.md). What is left here is for extensions, whose labels the page cannot know: label(key)
// shows a registered item's label (a layer, a tool, a view, a section), whether its key is in the
// extension's own locale module (the way to do it: web/locales/<lang>/<module>.json) or was given with
// rmap.strings({zh, en}) (still accepted, for extensions written before the locale files).
// i18n: done
import { t as i18nT, lang } from "/i18n.js";

const extra = { zh: {}, en: {} };

export function addStrings(dict) {
  for (const [l, entries] of Object.entries(dict || {})) {
    if (!extra[l]) extra[l] = {};
    Object.assign(extra[l], entries);
  }
}

export function label(key, params) {
  if (!key) return "";
  const v = extra[lang()]?.[key] ?? extra.zh[key] ?? extra.en[key];
  if (v === undefined) return i18nT(key, params);
  return params ? v.replace(/\{(\w+)\}/g, (m, k) => (params[k] ?? m)) : v;
}

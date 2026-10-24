// Shared helpers: htm binding, icons, markdown + math rendering, fetch, local prefs.
import { h } from "preact";
import htm from "htm";
import { marked } from "/vendor/marked.mjs";
import katex from "/vendor/katex/katex.mjs";

export const html = htm.bind(h);

const PATHS = {
  library: "M4 19.5V5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2.5zM8 7h6M8 11h6",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-4.3-4.3",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  fit: "M4 9V5h4M20 9V5h-4M4 15v4h4M20 15v4h-4",
  crop: "M6 2v14a2 2 0 0 0 2 2h14M18 22V8a2 2 0 0 0-2-2H2",
  outline: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  translate: "M4 5h7M7.5 3v2M5.5 5c.8 3 2.7 5.4 5.5 6.5M9.5 5c-.8 3.5-3 6.2-6 7.5M13 21l4-9 4 9M14.3 18h5.4",
  send: "M12 19V5M5 12l7-7 7 7",
  stop: "M7 7h10v10H7z",
  sparkle: "M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z",
  x: "M18 6 6 18M6 6l12 12",
  chat: "M21 12a8 8 0 0 1-11.8 7L4 20l1.1-4.6A8 8 0 1 1 21 12z",
  note: "M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8M8 17h5",
  tool: "M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z",
  file: "M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6",
  external: "M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6",
  refresh: "M3 12a9 9 0 0 1 15.5-6.3L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.3L3 16M3 21v-5h5",
  up: "M18 15l-6-6-6 6",
  down: "M6 9l6 6 6-6",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  quote: "M7 7h4v4c0 3-2 5-4 5M14 7h4v4c0 3-2 5-4 5",
  check: "M20 6 9 17l-5-5",
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z",
  calendar: "M4 5h16v16H4zM4 9h16M8 3v4M16 3v4",
  bulb: "M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.6 1 2.5h6c0-.9.2-1.7 1-2.5A6 6 0 0 0 12 3z",
  star: "M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z",
  compass: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM15.5 8.5l-2 5-5 2 2-5z",
  radar: "M12 12l6-6M19.1 7.5A9 9 0 1 1 16.5 4.2M15.5 9.5a5 5 0 1 1-2-1.3M12 12h.01",
  power: "M12 3v9M6.4 6.4a8 8 0 1 0 11.2 0",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  right: "M9 6l6 6-6 6",
  inbox: "M22 12h-6l-2 3h-4l-2-3H2M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z",
  book: "M2 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H2zM22 4h-7a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h8z",
};

export function Icon({ name, sm }) {
  return html`<svg class=${"i" + (sm ? " sm" : "")} viewBox="0 0 24 24" aria-hidden="true"><path d=${PATHS[name]} /></svg>`;
}

marked.setOptions({ breaks: true, gfm: true });

function esc(s) {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

// Page and section references as written in notes: "p. 3", "(pp. 4-5)", "page 7", "§5.4.1", "Section 3.2".
const LOOSE_REF = /\b(?:pp?\.\s?|page\s)(\d{1,3})(?:\s?[-–]\s?\d{1,3})?\b|(?:§\s?|\b(?:Section|Sec\.)\s?)(\d{1,2}(?:\.\d{1,2}){0,3})(?![\d.]*\d)/gi;

// Link loose references in text only: not inside tags, code, math (MathML or KaTeX), links or [[wikilinks]].
function linkLoose(out) {
  let skip = null, depth = 0, afterCode = false;
  return out.split(/(<[^>]+>)/).map((part) => {
    if (part.startsWith("<")) {
      const t = /^<(\/?)([a-z]+)/i.exec(part);
      afterCode = /^<\/code>/i.test(part);
      if (!t) return part;
      const name = t[2].toLowerCase();
      if (skip) { if (name === skip) depth += t[1] ? -1 : 1; if (depth === 0) skip = null; }
      else if (!t[1] && (/^(code|pre|a|math)$/.test(name) || /class="(?:wikilink|katex)/.test(part))) { skip = name; depth = 1; }
      return part;
    }
    // "`PROPOSAL.md` §2.7" points into another document, not this paper.
    if (skip || (afterCode && /^\s*§/.test(part))) return part;
    return part.replace(LOOSE_REF, (m, page, sec) => page
      ? `<a class="pagelink" data-page="${page}">${m}</a>`
      : `<a class="pagelink sec" data-sec="${sec}">${m}</a>`);
  }).join("");
}

// Markdown with $...$ / $$...$$ math, [p.5] page links and [[wikilinks]].
// loose: also link "p. 3" / "§5.4" style references (paper notes and the paper's own chat).
export function renderMarkdown(src, { loose = false } = {}) {
  const maths = [];
  const stash = (tex, display) => { maths.push({ tex, display }); return `\u0000M${maths.length - 1}\u0000`; };
  let s = (src || "")
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => stash(t, true))
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, t) => stash(t, true))
    .replace(/\\\((.+?)\\\)/g, (_, t) => stash(t, false))
    .replace(/(^|[^\\$])\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)/g, (_, pre, t) => pre + stash(t, false));
  let out = marked.parse(s);
  out = out.replace(/\u0000M(\d+)\u0000/g, (_, i) => {
    const m = maths[+i];
    try { return katex.renderToString(m.tex, { displayMode: m.display, throwOnError: false }); }
    catch { return esc(m.tex); }
  });
  out = out.replace(/\[(?:p\.|P\.|第)\s?(\d+)(?:\s?页)?\]/g, '<a class="pagelink" data-page="$1">p.$1</a>');
  out = out.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, t, a) =>
    `<span class="wikilink" data-target="${esc(t.split("#")[0].trim())}">${a || t}</span>`);
  return sanitize(loose ? linkLoose(out) : out);
}

// Notes come from many places (PDF text, the web, other tools) and marked keeps raw HTML:
// drop active elements, on* handlers and script URLs before the HTML reaches the page.
const DROP = new Set(["SCRIPT", "IFRAME", "FRAME", "FRAMESET", "OBJECT", "EMBED", "LINK", "META", "BASE", "STYLE",
  "FORM", "INPUT", "BUTTON", "TEXTAREA", "SELECT"]);
const URL_ATTR = new Set(["href", "src", "xlink:href", "action", "formaction", "srcset", "poster", "background"]);
function sanitize(markup) {
  const t = document.createElement("template"); // inert: nothing in it loads or runs
  t.innerHTML = markup;
  for (const el of [...t.content.querySelectorAll("*")]) {
    if (DROP.has(el.tagName.toUpperCase())) { el.remove(); continue; }
    for (const a of [...el.attributes]) {
      const n = a.name.toLowerCase();
      if (n.startsWith("on") || n === "srcdoc" || (URL_ATTR.has(n) && /^\s*(?:javascript|vbscript|data):/i.test(a.value) &&
          !(n === "src" && /^\s*data:image\/(?:png|jpe?g|gif|webp);/i.test(a.value)))) el.removeAttribute(a.name);
    }
  }
  return t.innerHTML;
}

// Enter that only confirms an IME candidate (WebKit fires compositionend first, then keyCode 229).
export function composing(e) {
  return e.isComposing || e.keyCode === 229;
}

export async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export const prefs = {
  get(key, fallback) {
    try { const v = localStorage.getItem("studio:" + key); return v === null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem("studio:" + key, JSON.stringify(value)); } catch {}
  },
};

// Clipboard that also works inside the desktop window (WebKit can refuse navigator.clipboard).
export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch {}
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch {}
  ta.remove();
  return ok;
}

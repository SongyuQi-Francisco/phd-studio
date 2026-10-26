// Reader toolbar extensions: items other features add to the PDF reader's toolbar without editing the reader
// (the research map's 「挂到地图」, #47, is the first). No static imports: runs outside the browser too
// (tests/js/reader_tools.test.mjs), and loading it can never fail.
//
//   import { readerTool } from "/reader-tools.js";
//   readerTool({ id: "rmap-attach", order: 50, render: ({ stem, paper, toast }) => html`<button …>` });
//
// The modules in MODULES are imported when this file loads, each on its own: one that fails to load is logged
// and left out, and the reader works as before. web/main.js renders renderReaderTools({stem, paper, toast})
// after the Zotero button; an item whose render throws is logged and skipped. Registering the same id again
// replaces the item; a module that registers late shows from the reader's next render on.
// i18n: done

export const MODULES = ["/rmap/attach.js"];
const items = [];
const logError = (...a) => { if (typeof console !== "undefined") console.error(...a); };

export function readerTool(spec) {
  if (!spec || typeof spec.id !== "string" || !spec.id || typeof spec.render !== "function") {
    throw new Error("readerTool: an id and a render function are required");
  }
  const item = { order: 100, ...spec };
  const at = items.findIndex((x) => x.id === spec.id);
  if (at >= 0) items[at] = item; else items.push(item);
  items.sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  return () => { const i = items.indexOf(item); if (i >= 0) items.splice(i, 1); };
}

export const readerTools = () => items.slice();

// [[id, rendered], ...] for the toolbar, in order; an item that throws is left out.
export function renderReaderTools(props) {
  const out = [];
  for (const it of items) {
    try { out.push([it.id, it.render(props)]); } catch (e) { logError(`reader tool ${it.id} failed`, e); }
  }
  return out;
}

let loading = null;
export function loadReaderTools(modules = MODULES, load = (src) => import(src)) {
  if (!loading) {
    loading = Promise.all(modules.map((src) => Promise.resolve().then(() => load(src))
      .catch((e) => logError(`reader tool module ${src} failed to load`, e))));
  }
  return loading;
}

if (typeof document !== "undefined") loadReaderTools();

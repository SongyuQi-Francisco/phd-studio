// Reader toolbar extensions: items other features add to the PDF reader's toolbar without editing the reader
// (the research map's 「挂到地图」, #47, is the first). No imports: runs outside the browser too
// (tests/js/reader_tools.test.mjs).
//
//   import { readerTool } from "/reader-tools.js";
//   readerTool({ id: "rmap-attach", order: 50, render: ({ stem, paper, toast }) => html`<button …>` });
//
// web/main.js imports the modules that register and renders readerTools() after the Zotero button, in order;
// registering the same id again replaces the item. render() gets the open paper's stem, its info and a toast.
// i18n: done

const items = [];

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

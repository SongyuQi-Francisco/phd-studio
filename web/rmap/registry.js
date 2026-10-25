// Extension registry of the research map page. Later phases add files under web/rmap/ext/
// i18n: done
// (listed by GET /api/rmap/ext and imported by the page shell) and register here instead of editing
// the core files. See docs/research-map.md, "Front-end extension points", for the full contract.
//
//   import { rmap } from "/rmap/registry.js";
//   rmap.layer({ id: "peers", label: "rmap.layer.peers", order: 10, node: (n, box, ctx) => svg, nodeClass, svg, legend });
//   rmap.section({ id: "results", label: "...", order: 40, when: (n, ctx) => n.type === "experiment", render: (n, ctx) => html });
//   rmap.tool({ id: "replay", label: "...", icon: "clock", order: 20, run: (ctx) => ..., render: (ctx) => html });
//   rmap.view({ id: "overview", label: "rmap.view.overview", order: 0, render: (ctx) => html });
//   rmap.kpi({ id: "depth", order: 10, render: (ctx) => html });
//   rmap.on("change", ({ slug, events }) => ...);   // also "load", "select", "view"
//   labels are i18n keys from the extension's own web/locales/<lang>/<module>.json (rmap.strings({zh, en}) still works)
import { addStrings } from "/rmap/strings.js";

const SLOTS = ["layer", "section", "tool", "view", "kpi"];
const items = Object.fromEntries(SLOTS.map((s) => [s, []]));
const listeners = new Map(); // event name -> Set(fn)
const watchers = new Set(); // re-render callbacks of mounted pages
let version = 0;

function changed() {
  version += 1;
  watchers.forEach((fn) => { try { fn(version); } catch (e) { console.error(e); } });
}

function put(slot, spec) {
  if (!spec || typeof spec.id !== "string" || !spec.id) throw new Error(`rmap.${slot}: an id is required`);
  const list = items[slot];
  const at = list.findIndex((x) => x.id === spec.id);
  const item = { order: 100, ...spec };
  if (at >= 0) list[at] = item; else list.push(item); // same id again replaces (a re-imported extension)
  list.sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  changed();
  return () => { const i = list.indexOf(item); if (i >= 0) { list.splice(i, 1); changed(); } };
}

export const rmap = {
  // Map layers: toggled from the toolbar, drawn by the map view (decorations on nodes, overlays in map coordinates).
  layer: (spec) => put("layer", spec),
  // Detail panel sections for the selected node (slots under the core description and evidence).
  section: (spec) => put("section", spec),
  // Toolbar items: a button (run) or a small component (render).
  tool: (spec) => put("tool", spec),
  // Whole-page views in the header's segmented control, next to the projects (e.g. the all-projects map).
  view: (spec) => put("view", spec),
  // KPI tiles above the map.
  kpi: (spec) => put("kpi", spec),
  strings: (dict) => { addStrings(dict); changed(); },

  list: (slot) => items[slot].slice(),
  get: (slot, id) => items[slot].find((x) => x.id === id) || null,

  on(name, fn) {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(fn);
    return () => listeners.get(name)?.delete(fn);
  },
  emit(name, payload) {
    (listeners.get(name) || []).forEach((fn) => { try { fn(payload); } catch (e) { console.error(`rmap "${name}" listener`, e); } });
  },

  // For the page shell: call fn whenever something registers, so the page re-renders.
  watch(fn) { watchers.add(fn); return () => watchers.delete(fn); },
  get version() { return version; },
};

// Extension files are imported once per page load, in name order; one that fails is logged and skipped.
let loading = null;
export function loadExtensions(api) {
  if (!loading) {
    loading = api("/api/rmap/ext").then(async ({ scripts, styles }) => {
      styles.forEach((href) => {
        if (document.querySelector(`link[href="${href}"]`)) return;
        const l = document.createElement("link");
        l.rel = "stylesheet"; l.href = href;
        document.head.appendChild(l);
      });
      for (const src of scripts) {
        try { await import(src); } catch (e) { console.error("research map extension failed to load:", src, e); }
      }
    }).catch((e) => console.error("research map extensions:", e));
  }
  return loading;
}

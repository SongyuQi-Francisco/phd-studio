// Helpers shared by the research map page and its extensions.

// Links a map may open: http(s), and the Obsidian and Zotero apps. Anything else (javascript:, data:, file:) is "".
const SAFE = /^(?:https?|obsidian|zotero):/i;
export const safeUrl = (u) => (typeof u === "string" && SAFE.test(u.trim()) ? u.trim() : "");

// The flow edges (refines, depends) a layout or a metric may walk: the server marks the ones that close a
// cycle (two Macs can make one between them) with `cycle`, and they are left out here.
export const dagEdges = (map, meta) => map.edges.filter((e) => meta.flow.includes(e.kind) && !e.cycle);

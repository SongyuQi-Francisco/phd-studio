// Helpers shared by the research map page and its extensions.
import { useEffect, useRef } from "preact/hooks";

// Links a map may open: http(s), and the Obsidian and Zotero apps. Anything else (javascript:, data:, file:) is "".
const SAFE = /^(?:https?|obsidian|zotero):/i;
export const safeUrl = (u) => (typeof u === "string" && SAFE.test(u.trim()) ? u.trim() : "");

// The flow edges (refines, depends) a layout or a metric may walk: the server marks the ones that close a
// cycle (two Macs can make one between them) with `cycle`, and they are left out here.
export const dagEdges = (map, meta) => map.edges.filter((e) => meta.flow.includes(e.kind) && !e.cycle);

// Preact's autoFocus only sets the attribute, which a browser honors on page load, not for an input that
// appears later: this ref focuses its element when it appears and whenever `key` changes (select: the text too).
export function useAutoFocus(key, { select = false } = {}) {
  const ref = useRef();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus({ preventScroll: true });
    if (select) el.select?.();
  }, [key, !!ref.current]);
  return ref;
}

// DOM helpers for the research map page.
// i18n: done
import { useEffect, useRef } from "preact/hooks";
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

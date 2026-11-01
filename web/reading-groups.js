// The reading list page's groups, in page order: pinned items first (most recently pinned on top, taken out
// of their own group), then inbox, reading, T1–T3 and the folded ones. Labels are added by web/reading.js.
// No imports: runs outside the browser too (tests/js/reading_groups.test.mjs).
// i18n: done

export const ACTIVE = ["inbox", "queued", "reading"]; // done, snoozed and dismissed items never show as pinned
export const isPinned = (i) => !!i.pinned && ACTIVE.includes(i.status);

export function readingGroups(items) {
  const pinned = items.filter(isPinned).sort((a, b) => (b.pinned_at || "").localeCompare(a.pinned_at || ""));
  const by = (st) => items.filter((i) => i.status === st && !isPinned(i));
  const order = (a, b) => (a.due || "9").localeCompare(b.due || "9") || a.age_days - b.age_days;
  const queued = by("queued").sort(order);
  return [
    { id: "pinned", items: pinned },
    { id: "inbox", items: by("inbox").filter((i) => !i.stale).sort((a, b) => a.age_days - b.age_days) },
    { id: "reading", items: by("reading") },
    { id: "T1", items: queued.filter((i) => i.tier === "T1") },
    { id: "T2", items: queued.filter((i) => i.tier === "T2") },
    { id: "T3", items: queued.filter((i) => i.tier === "T3") },
    { id: "done", items: by("done").sort((a, b) => (b.read_at || "").localeCompare(a.read_at || "")), fold: true },
    { id: "snoozed", items: by("snoozed"), fold: true },
    { id: "stale", items: by("inbox").filter((i) => i.stale), fold: true },
    { id: "dismissed", items: by("dismissed"), fold: true },
  ];
}

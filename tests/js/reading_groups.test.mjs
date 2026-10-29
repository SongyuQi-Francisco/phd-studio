// web/reading-groups.js without a browser: run by tests/test_reading.py with node or JavaScriptCore's jsc.
// The test copies reading-groups.js next to this file (as .mjs). Prints "ok" or throws.
import { readingGroups, isPinned, ACTIVE } from "./reading-groups.mjs";

const out = typeof print === "function" ? print : console.log.bind(console);
function eq(actual, expected, what) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${what}: expected ${e}, got ${a}`);
}
const item = (id, status, more = {}) => ({ id, status, age_days: 1, lines: [], ...more });
const groups = (items) => Object.fromEntries(readingGroups(items).map((g) => [g.id, g.items.map((i) => i.id)]));

eq(readingGroups([]).map((g) => g.id), ["pinned", "inbox", "reading", "T1", "T2", "T3", "done", "snoozed", "stale", "dismissed"],
  "pinned comes first, above the inbox");
eq(ACTIVE, ["inbox", "queued", "reading"], "the statuses a pin shows for");

const items = [
  item("a", "inbox"),
  item("b", "inbox", { pinned: true, pinned_at: "2026-10-20T09:00:00" }),
  item("c", "queued", { tier: "T1", pinned: true, pinned_at: "2026-10-22T09:00:00" }),
  item("d", "reading", { pinned: true, pinned_at: "2026-10-21T09:00:00" }),
  item("e", "queued", { tier: "T1" }),
  item("f", "done", { pinned: true, pinned_at: "2026-10-23T09:00:00", read_at: "2026-10-24" }),
  item("g", "dismissed", { pinned: true, pinned_at: "2026-10-23T09:00:00" }),
  item("h", "snoozed", { pinned: true, pinned_at: "2026-10-23T09:00:00" }),
  item("i", "inbox", { stale: true }),
  item("j", "inbox", { pinned: true }),  // no pinned_at (written by hand): last among the pins
  item("k", "inbox", { note: "Notes/Report A.md", pinned: true, pinned_at: "2026-10-25T09:00:00" }),
];
const g = groups(items);
eq(g.pinned, ["k", "c", "d", "b", "j"], "most recently pinned first, papers and notes alike");
eq([g.inbox, g.reading, g.T1], [["a"], [], ["e"]], "a pinned item leaves its own group");
eq([g.done, g.dismissed, g.snoozed, g.stale], [["f"], ["g"], ["h"], ["i"]], "done, dismissed and snoozed never show as pinned");
const shown = readingGroups(items).flatMap((x) => x.items.map((i) => i.id));
eq(shown.length, new Set(shown).size, "nothing shows twice");
eq(shown.length, items.length, "every item has a group");
eq([isPinned(items[1]), isPinned(items[5]), isPinned(items[0])], [true, false, false], "isPinned");

// the queued groups keep their order: due date first, then the newest
const q = groups([item("x", "queued", { tier: "T2", age_days: 5 }), item("y", "queued", { tier: "T2", due: "2026-11-01", age_days: 9 }),
  item("z", "queued", { tier: "T2", age_days: 2 })]);
eq(q.T2, ["y", "z", "x"], "T2 by due date, then age");
out("ok");

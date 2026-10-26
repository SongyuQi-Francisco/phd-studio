// web/rmap/registry.js without a browser (views by scope): run by tests/test_rmap_history.py with node, or with
// JavaScriptCore's jsc. The test copies registry.js, strings.js and i18n.js next to this file with their imports
// made relative. Prints "ok" or throws.
import { rmap } from "./registry.mjs";

const out = typeof print === "function" ? print : console.log.bind(console);
function eq(actual, expected, what) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${what}: expected ${e}, got ${a}`);
}

const seen = [];
rmap.watch((v) => seen.push(v));
rmap.view({ id: "overview", label: "x", order: 0, render: () => null });
rmap.view({ id: "history", label: "y", order: 10, scope: "project", render: () => null });
rmap.view({ id: "results", label: "z", order: 5, scope: "project", render: () => null });
const off = rmap.view({ id: "other", label: "w", order: 20, scope: "page", render: () => null });
eq(rmap.views().map((v) => v.id), ["overview", "other"], "page views, the default scope");
eq(rmap.views("project").map((v) => v.id), ["results", "history"], "project views, in order");
eq(rmap.list("view").length, 4, "list has both kinds");
rmap.view({ id: "history", label: "y2", order: 1, scope: "project", render: () => null });
eq(rmap.views("project").map((v) => [v.id, v.label]), [["history", "y2"], ["results", "z"]], "same id replaces");
off();
eq(rmap.views().map((v) => v.id), ["overview"], "unregistered");
eq(seen.length, 6, "each change re-renders the page");
out("ok");

// web/reader-tools.js without a browser: run by tests/test_rmap_peers.py with node or JavaScriptCore's jsc.
// The test copies reader-tools.js next to this file (as .mjs). Prints "ok" or throws.
import { readerTool, readerTools, renderReaderTools, loadReaderTools } from "./reader-tools.mjs";

const out = typeof print === "function" ? print : console.log.bind(console);
function eq(actual, expected, what) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${what}: expected ${e}, got ${a}`);
}
const ids = () => readerTools().map((x) => x.id);
const render = () => null;

readerTool({ id: "b", order: 50, render });
readerTool({ id: "a", render });            // default order 100
const off = readerTool({ id: "c", order: 10, render });
eq(ids(), ["c", "b", "a"], "sorted by order");
readerTool({ id: "b", order: 200, render: () => "new" });
eq(ids(), ["c", "a", "b"], "the same id replaces the item");
eq(readerTools().find((x) => x.id === "b").render(), "new", "the replacement is used");
off();
eq(ids(), ["a", "b"], "unregister");
readerTools().pop();
eq(ids(), ["a", "b"], "the list handed out is a copy");
let threw = false;
try { readerTool({ id: "x" }); } catch { threw = true; }
eq(threw, true, "a render function is required");
// a tool whose render throws is left out; the others still render
readerTool({ id: "boom", order: 1, render: () => { throw new Error("broken"); } });
eq(renderReaderTools({ stem: "s" }).map(([id]) => id), ["a", "b"], "a throwing tool is skipped");
// a module that fails to load is logged, never thrown; the ones that load register
const loaded = [];
await loadReaderTools(["/ok.js", "/bad.js"], (src) => { if (src === "/bad.js") throw new Error("404"); loaded.push(src); return {}; });
eq(loaded, ["/ok.js"], "modules load one by one, a failure is contained");
out("ok");

# Research map (研究地图): data model and extension points

Each project gets its own map that grows with the work: question → paths / hypotheses → sub-lines →
experiments → results, with what is verified, what is blocked and what was dropped. Phase 1 (#45)
builds the data model, the single-project map and editing; later phases plug in through the
extension points below: time replay, change view and the all-projects map (#46), the peer layer
(#47), experiment results (#48, see "Experiment results" below).

Code: `app/rmap/` (back end), `web/rmap/` (front end). Tests: `tests/test_rmap.py`.

## Files

A project is a folder in the vault, under `paths.research_maps` in `studio.toml` (default
`Research Maps`, at the top of the vault):

```
Research Maps/
  <slug>/                     "Project Atlas" -> project-atlas-3f9c (title slug + 4 hex of the project uid)
    map.yaml                  project (one record, id "project"), nodes, edges
    evidence.yaml             evidence attachments
    <kind>.yaml               one file per registered attachment kind (peers, results, ...)
    events/2026-10.yaml       the change log, one file per month
    map.canvas                Obsidian Canvas view: generated, read-only
```

Folder names are lower case (the Mac's disk ignores case, so `Foo` and `foo` would be one folder with two
locks) and carry part of the project's random `uid`, so two Macs that create the same title while apart
get two folders, never one silently merged map. A title already in use (ignoring case) is refused; two
same-titled maps that arrive from two Macs are both kept and flagged (`same_title` in the listing).

Records are plain YAML and can be read by hand, but edit them in the app: hand edits leave no event and
no clock stamp (see below), so the other Mac's copy may win over them.

## Syncing two Macs

Every file is a store built on `app/store.py` (locked read-modify-write, temp files and locks in the data
dir under `rmap/`, never in the vault; a write that changes nothing leaves the file alone; Syncthing
conflict copies are merged on the next read and moved to `<data>/sync-conflicts/`). The map and
attachment files merge **field by field** (`app/rmap/records.py`); the other stores of the app keep
merging whole records by `updated_at`.

- **Clock stamps.** Every field a write sets (or clears) gets a stamp in the record's `clock`:
  `{title: "1793001234567.0000.macbook", status: …}`. Stamps come from a hybrid logical clock
  (`app/rmap/clock.py`): milliseconds that follow the wall clock but never go backwards on a Mac and never
  fall behind a stamp already seen in the map, a counter for writes in the same millisecond, and the
  device name as tie-break, so no two writes compare equal. Text order is time order.
- **Merge.** For each field the later stamp wins. Two Macs that change different fields of a node both
  keep their change; a cleared field stays cleared against an older copy. Records written before stamps
  existed count as written whole at their `updated_at`.
- **Delete and resurrect.** A delete is a field too (`deleted: true` with its stamp). A record changed
  after it was deleted (on a Mac that had not seen the delete) is live again, and so is everything the
  delete took along: a node delete tombstones its edges and attachments with `deleted_by: <node id>`, and
  they follow that node back. An edge or attachment deleted on its own stays deleted. A delete stamped
  after the edit wins.
- **Cycles.** `refines` and `depends` must stay acyclic; a write that would close a cycle is refused. Two
  Macs can still make one between them (A → B on one, B → A on the other). Reading a map marks the edge
  that closes it, taken in creation order so both Macs mark the same one, with `cycle: true`, and adds a
  `{kind: "cycle", edges: [...]}` warning. Layouts and metrics walk `dag_edges(map)` (Python) /
  `dagEdges(map, meta)` (`web/rmap/util.js`), which leave marked edges out.
- **Missing nodes.** An edge or attachment whose node is gone is not shown.

## Project

`map.yaml → project[0]`: `title`, `line` (research line name as in `profile.lines`), `note` (vault path of
the research-line note), `description`, `archived`, `uid` (random, set once).

## Nodes

`map.yaml → nodes[]`

| field | |
|---|---|
| `type` | `question` 问题 · `hypothesis` 路径/假设 · `subline` 子线 · `experiment` 实验 · `result` 结果 · `decision` 决策 · `kill` Kill criteria · `open` 开放问题 |
| `title` | one line, ≤ 200 characters |
| `status` | `todo` 未开始 · `active` 进行中 · `verified` 已验证 · `blocked` 没打通 · `refuted` 证伪 · `dropped` 放弃 |
| `body` | Markdown, optional |
| `pin` | `{x, y}` in map coordinates: the layout keeps the node there; absent = automatic |
| `order` | number, optional: order among siblings (else creation time) |
| `tags` | list of words, optional |
| `entity` | optional, see "Entities": what this node is across projects (a shared dataset, baseline) |
| `ext` | `{<extension key>: value}`: data an extension keeps on the node; a change replaces only the keys it names. A plugin validates its key with `ext_field`; a key no plugin registered holds at most 4000 characters of JSON |

The first five types have a column (0–4, left to right: 问题 · 路径 · 子线 · 实验 · 结果); decisions,
Kill criteria and open questions sit next to their parent. Refuted and dropped nodes stay on the map,
faded. `verified`, `refuted` and `dropped` count as closed. Collapsing and focusing are view state
(per Mac, in the browser), not part of the map.

## Edges

`map.yaml → edges[]`: `from`, `to` (node ids), `kind`, `label` (optional, ≤ 80 characters).

| kind | | direction |
|---|---|---|
| `refines` 细分 | `to` is a narrower part of `from` (question → path → sub-line → experiment → result) | left to right |
| `depends` 依赖 | `to` needs `from` first | left to right |
| `alternative` 备选 | two ways to the same end | lateral, symmetric |
| `conflicts` 冲突 | both cannot hold | lateral, symmetric |

One edge of a kind per pair (either direction for the lateral kinds).

## Attachments

Things hung on a node. Each kind has its own file and the same generic routes; every record has
`node` (the node id), an optional `entity`, and the kind's fields.

| kind | file | fields | phase |
|---|---|---|---|
| `evidence` 证据 | `evidence.yaml` | `type`: `paper` (paper note name) / `note` (vault path) / `file` (vault path) / `url`; `target`; `title`; `note`; `stance`: `supports` / `contradicts` / `context` | 1 |
| `peer` 同行 | `peers.yaml` | `type` paper / scholar / lab, `title`, `relation` same / competing / builds_on, … ([research-map-peers.md](research-map-peers.md)) | 3 |
| `results` 结果记录 | `results/<node id>.yaml` (one file per node) | `config`, `dataset`, `metric`, `value` (a number), `date`, `source`, and optionally `run`, `baseline`, `better` (`higher` / `lower`), `date_inferred`, `note`; only on experiment and result nodes. Logged once per batch. See "Experiment results" | 4 |
| `result_sources` 结果文件 | `result_sources.yaml` | `path`, `format`, `columns`, `mapping`, `watch`, `device`: the CSV / JSON file an experiment reads its results from | 4 |

A `url` target opens only with `http`, `https`, `obsidian` or `zotero`; a paper name, note or file is a
plain vault-relative path (no scheme, no leading `/` or `~`, no `..`). The front end opens links through
`safeUrl` (`web/rmap/util.js`) as well.

## Entities

Things several projects point at (#46 shared stations, #47 shared peers) are named by a key
`<namespace>:<key>`, the same on every Mac without any registry to keep in sync: `paper:2310.11511`
(arXiv id) or `paper:<note name>`, `doi:10.1145/…`, `openreview:<forum>`, `acl:<id>`,
`scholar:openalex:A…`, `lab:<slug>`, `dataset:msmarco`, `metric:recall@10`, `method:colbert`. Nodes and
attachment records carry it in `entity`; `GET /api/rmap/entities` (`project.entities()`) lists where each
one appears, across all maps.

## Events

`events/<YYYY-MM>.yaml → events[]`, one record per changed record, written after the change itself.
Events are never edited; conflict copies of a month merge as a union.

| field | |
|---|---|
| `hlc` | clock stamp of the change (the same stamp as the fields it wrote); with `id`, the order of the log |
| `at` | when (local ISO time, seconds), for people |
| `who` | `user` · `assistant` · `cli` · `import` · `system` |
| `device` | which Mac (`STUDIO_DEVICE` / host name) |
| `kind` | `project` · `node` · `edge` · an attachment kind · `log` |
| `action` | `add` · `update` · `delete` · `note` (log entries) |
| `target` | id of the changed record |
| `nodes` | node ids the event is about (one for a node or attachment, two for an edge) |
| `title` | node title at that time, for a readable feed |
| `record` | snapshot of the record, on add and delete |
| `changes` | `{field: [old, new]}`, on update |
| `summary` | log text, or a one-line description of an attachment |
| `group` | shared by all events of one batch (one user action, one confirmed proposal) |
| `targets`, `count` | instead of `target`, for an attachment kind logged per batch (bulk imports) |

The log is ordered by `(hlc, id)` and paged by a cursor `"<hlc>|<id>"`: nothing is lost or repeated,
however many events share a second. Events written before stamps existed sort by `at` and their place in
the file.

Each Mac also numbers events in the order it first sees them, written here or synced in
(`Project.ingest`, state in `<data>/rmap/ingest/<slug>.json`): a live feed asks for `after=<seq>` and gets
everything new, including events an offline Mac wrote last week that only arrived now. The first time a
Mac looks at a map it only records what is there.

A `log` event carries a free sentence ("ran the ablation, tool use did not help on long sessions"):
the one-sentence update is stored as its node changes + evidence + that log line, all in one group.
Replay (#46) rebuilds the map at a time from `record` / `changes` in `(hlc, id)` order, with the delete
and resurrect rule above; hand edits and data older than the log fall back to `created_at` /
`updated_at`. See [research-map-history.md](research-map-history.md) for the replay, the change view and the
all-projects map.

## Writing: one path

Every change goes through `app/rmap/ops.apply(slug, ops, who=, summary=)`: a batch validated as a
whole and written under the map's lock (all or nothing), then logged. Ids in a batch may be `$name`
refs to records added earlier in it. `ops.simulate(slug | None, ops)` runs the same checks without
writing (None: against a new, empty map) and returns the events the batch would log.

```json
[{"op": "node.add", "ref": "$h", "set": {"type": "hypothesis", "title": "Tool use helps"}, "parent": "n1a2b3c4"},
 {"op": "attach.add", "kind": "evidence", "node": "$h", "set": {"type": "paper", "target": "Attention 2017"}},
 {"op": "node.update", "id": "n5d6e7f8", "set": {"status": "verified"}},
 {"op": "edge.add", "from": "$h", "to": "n5d6e7f8", "kind": "depends"},
 {"op": "log", "text": "first ablation done"}]
```

Operations: `node.add / update / delete`, `edge.add / update / delete`, `attach.add / update / delete`,
`log`, `project.update`. A batch holds up to 400 operations; with `who="import"` or `"cli"` up to 20000
(a results CSV is still one atomic batch). The CLI (#48) and the assistant's confirmed proposals use the
same call.

## The assistant: proposals

The workbench assistant never writes a map directly. Its tools (`app/studio_tools.py`):

- `map_read(project?)`: the list of maps, or one map in a compact form (nodes with parents and evidence,
  the other edges, the latest log lines). Also offered to the read-only (non-Claude) models.
- `map_propose(project | new_project, summary, ops)`: checks the batch (`simulate`) and stores it as a
  proposal; the page shows it as a list of changes with 确认写入 / 放弃. A one-sentence progress update is
  split into node changes + evidence + the sentence as the log line (`summary`).
- `map_seed_draft(line)`: a first map drafted from a research-line note (`app/rmap/lines.py`): the working
  title as the question and the pitch as its description, Hypotheses as paths, First Experiment, Kill
  criteria (status from the note's "partially triggered / triggered" mentions), Related notes as
  evidence, the latest dated status in the summary. The assistant refines it and proposes it with
  `new_project`; the page can also propose it as is (直接从笔记生成, `POST /seed`).

Proposals (`app/rmap/proposals.py`) live per Mac in `state/<device>/rmap_proposals.json` (made and
confirmed on the same Mac). A proposal keeps its base: the clock stamps of every field it changes and of
every record it deletes, and the existing nodes it hangs new things on, as they were when it was made.
Confirming first compares them with the map now: if any field it changes was changed meanwhile (here, or
synced from the other Mac), or a record it builds on is gone, nothing is written, the reason is shown and
the proposal stays (the user discards it and asks again). Changes to other fields don't matter. Kinds logged per batch
(results) have no per-record events, so their `attach.update` / `attach.delete` operations are read for the base
directly, and an `attach.add` of a kind with an `identity` is refused if a record with the same identity has appeared
on that node by then (two proposals for one measurement never write it twice). Then the batch is written as
`who: assistant`.

## Obsidian Canvas

`app/rmap/canvas.py` writes `<project>/map.canvas` after a write made on this Mac (an `on_event`
subscriber that skips events from the other Mac: that Mac writes its own canvas and Syncthing brings it
over), never on a read, and only when the content changes, so two Macs never race over the file. It takes
a lock and a temp file of its own in the data dir, and removes Syncthing conflict copies of the canvas
(it is generated). Same layering as the page (rows along `refines`, pinned positions carried over and
kept clear), fixed-size cards with at most two evidence links (wikilinks; "n more"), colors by status, a
header saying it is generated; labels in the vault language (`[profile] vault_language`). Edits made to it
in Obsidian are overwritten by the next change.

## Experiment results

Phase 4 (#48). Code: `app/rmap/plugins/results.py` (registers the two attachment kinds and the routes),
`app/rmap/results/` (reading, mapping, importing, watching), `app/rmap/cli.py` (`studio map ...`), `web/rmap/ext/results.js`
and `web/rmap/results/` (the page), `app/studio_tools.py` (the assistant's tool). Nothing in the core files knows about it.
Tests: `tests/test_rmap_results.py`, `tests/js/rmap_results.test.mjs`.

**Records.** A measurement is one `results` record on an experiment or result node: `config` (the configuration or
method), `dataset` (optional), `metric` (as typed: `Recall@10`; the page shows it in mono capitals), `value`, `date`
(`YYYY-MM-DD`, default: the day it was recorded), `source` (the file, `paste`, `cli`, `assistant`, `page`) and optionally
`run` (a seed), `baseline: true` (the config is a baseline: it is grey in the charts and the gap is measured against the
best of them), `better` (`lower` for loss or latency; otherwise guessed from the metric name, the same word list in
`app/rmap/results/model.py` and `web/rmap/results/data.js`) and `note`. A value may be written `41.2%`, `1,234.5`,
`0.412 ± 0.003`. Several records per metric make the trend. A measurement is told apart by `model.identity`: (source,
config, dataset, metric, run, date), so writing the same measurement again updates it, and the same one on another date
is a new point. When the data has **no date** for a row (a table without a date column), the record gets the day of the
import (the file's date for a file) and `date_inferred: true`, and that date is *not* part of the identity: reading
the same table again on another day finds the same measurements (nothing is added), a changed number is updated in place
and takes the new date, and only rows that are new are added. Give the table a date column, or `--date`, for a trend.
The change log gets one line per batch (`kind: results`, `count`), plus the sentence ("imported results.csv", or the
numbers themselves for a report).

Results are stored **one file per node** (`results/<node id>.yaml`, the kind registers `shard="node"`, see "Back-end
extension points"): writing to a node rewrites only its file, an import reads only its node's records, and a listing
reads none. A write through `ops.apply` on this kind still reads all the shards (about 1.5 s per 17000 records, so a
write takes about 3 s in a map with that many); an import is capped at 19998 records (rows × metrics), checked before
anything is written.

**Tables.** `app/rmap/results/tabular.py` reads pasted text or a file: CSV, TSV (a spreadsheet paste), a Markdown table,
JSON (an array of objects, an object of `name -> metrics`, or an object holding such an array; nested objects become
`a.b` columns) and JSON lines. A file is read only when it is named (an absolute path or `~/`), must be a regular
`.csv .tsv .json .jsonl .ndjson` file of at most 8 MB **inside a results root**: `[paths] results_roots` in `studio.toml`
(a list of folders, relative to the vault or absolute / `~/`; empty, the default, means results can only be pasted).
Symbolic links are resolved first, so a link out of a root is the file it points at; the secrets folder, the app's
data and state folders, `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config` and `~/Library/Keychains` stay closed even inside a
root. The page, the watcher and the preview all follow this; the command line, where the person at the shell names the
file themselves, may import any such file once (`studio map result import`), but a file can only be *watched* from a
root. The file picker lists CSV / JSON files under the roots. A numeric cell is read with the file's decimal mark: the
cells vote (`1.234,5` and `0,5` are commas; `1,234.5` and `0.5` are points), and with no vote a `;`-separated file
counts as written with decimal commas (`1,234` is then 1.234); the mapping can say it (`decimal`, a choice in the import
form). NaN and Infinity in JSON are empty cells.

**Mapping.** `app/rmap/results/mapping.py`. Which column is which is a small JSON object (`config`, `dataset`,
`date`, `run`, `baseline` each `{col}` or a fixed `{const}`; either `metric` + `value` columns for a table with a row per
measurement, or `metrics: [columns]` for a table with a column per metric; `baselines: [config names]`; `lower:
[metrics]`; `decimal: "," | "."`). It is guessed from the header. Importing from a file keeps the file and its mapping on the node as a
`result_sources` record (it merges like any record, so both Macs know it), and the next import of that file, or of one with
the same header, starts from it. A pasted table has no file: the page remembers its mapping per header in this browser.

**Import.** `importer.run_import(slug, node, text= | path=, mapping=, watch=, dry_run=)` upserts the rows as **one
atomic batch** through `ops.apply(who="import")` (up to 19000 rows), after checking it as a whole; `dry_run` says what
would be added, updated or left alone and writes nothing. Importing the same table twice changes nothing and logs
nothing. A baseline ticked in the page is not unticked by a later import. Reading, deciding and writing run under a lock on
the project's results that holds across processes (`importer.writing`), so the app's watcher, `studio map result sync
--loop` and the CLI never both decide that a row is new. `watch=None` (what the page sends unless the box was ticked or
unticked) leaves a file's watch as it is, the other Mac's included; only an explicit True / False changes it. `bulk_set`
(`POST .../results/set`) marks a config as a baseline or sets a metric's direction on every matching record, in
chunks of 390 operations (each chunk atomic).

**Watching.** A source with `watch: true` and `device: <this Mac>` is read again when the file's size or time moves
(`importer.sync`, every 8 s in a background thread; state in `<data>/rmap/results-watch.json`, not in the vault). Only the Mac
named in `device` reads it, so two Macs never import the same file twice, and a file that cannot be read says why on the
source (once, until it changes); an unexpected error in one file is logged and the next file is still read. The thread starts the first time a results route is called (the map page does when it opens
a project) and with `studio map result sync --loop`; `studio map result sync` reads once.

**The page.** A *Results* section in the detail panel of experiment and result nodes (metric chips, the top configs against
the baseline with the gap, a small trend, add / import / open); a *Results* layer that shows the latest value of a node's
most recent metric in the corner of its card (toggle in the toolbar); a *Results* toolbar button for the whole map. The
results panel (a sheet over the page) has the comparison (a ranked bar per config for one metric and dataset, baselines
grey, the best aqua, the gap to the best baseline in mono), grouped columns across datasets, a trend line per config
(hover for the values; click the legend to hide a line), every record (mark a baseline with the star, delete), the node's
files (watch, read now), and the add and import forms. "Lower is better" flips the direction for a metric.

**Command line.** Works with the app stopped; every write is one locked `ops.apply` batch with `who: cli`, merged field by
field like the page's own edits, so an open page and the other Mac see it. PROJECT is a slug, title or research-line name,
NODE an id, an id prefix or (part of) a title.

```bash
./studio map list                                    # projects
./studio map show "Project A"                        # nodes as a tree: id, status, type, title, number of results
./studio map status "Project A" "Ablation A" verified --note "Gate passed"
./studio map note "Project A" "tool use did not help on long sessions" --node "Ablation A"
./studio map result add "Project A" "Ablation A" recall@10=0.412 ndcg@10=0.35 --config "Ours k=8" --dataset "MS MARCO" [--baseline] [--lower] [--status verified]
./studio map result list "Project A" ["Ablation A"] [--metric recall@10] [--json]
./studio map result import "Project A" "Ablation A" ~/code/exp/results.csv [--col value=score --col dataset=@MS-MARCO] [--baseline BM25] [--watch] [--dry-run]
./studio map result sync [--force] [--loop 10]       # read the files this Mac watches
```

For the vault rules file that tells agents how to work (`AGENTS.md` or similar), something like: *"After an experiment
finishes, report its numbers with `./studio map result add <project> <experiment node> <metric>=<value> --config <name>
--dataset <name>` (`--baseline` for baselines, `--status verified|refuted|blocked` when it settles the node). Never edit the
Research Maps folder by hand."*

**The assistant.** `map_record_result(project, node, results, status?, summary?)` turns numbers into a proposal that
the user confirms on the map page (nothing is written before; the proposal also fails if the node's fields changed
meanwhile, like every proposal). The summary lists the numbers. `map_read` shows each node's latest results. `map_propose`
can attach `results` records too (`attach.add`, kind `results`).

## HTTP

All under `/api/rmap` (`app/rmap/api.py`). Edits from the page are the user's own, like the reading list
and the plan: no approval card. The assistant only proposes; the user confirms.

| | |
|---|---|
| `GET /meta` | node types (with columns), statuses, edge kinds, attachment kinds, batch limits |
| `GET /ext` | front-end extension files in `web/rmap/ext/` |
| `GET /stamp` | changes whenever any map's files change |
| `GET /projects?archived=` · `POST /projects` | list (status counts, warnings) · create `{title, line?, slug?, note?, description?}` |
| `GET /entities?key=` | where each entity appears, across maps |
| `GET /projects/{slug}?deleted=` · `PATCH` | the whole map with `warnings` and this Mac's ingest `seq` (`deleted=true` keeps tombstones, for replay) · project fields |
| `GET /projects/{slug}/stamp` | change stamp of one map: poll it for live refresh |
| `GET /projects/{slug}/events?before=&since=&after=&node=&kind=&limit=` | the log, newest first: `{events (each with cursor, seq), next, seq}`; `before` / `since` take a cursor or a date; pass `next` back as `before` for the next page; `after` is an ingest sequence |
| `POST /projects/{slug}/batch` | `{ops, summary?, who?}` |
| `POST /projects/{slug}/nodes` · `PATCH` · `DELETE /nodes/{id}` | `POST` takes node fields + `parent`, `edge` |
| `POST /projects/{slug}/edges` · `PATCH` · `DELETE /edges/{id}` | |
| `GET` · `POST /projects/{slug}/attachments/{kind}` · `PATCH` · `DELETE …/{id}` | any registered kind |
| `POST /projects/{slug}/results/preview` · `/results/import` · `/results/sync` · `GET /projects/{slug}/results/status` · `GET /results/files` | experiment results (plugin routes, see "Experiment results"): columns and a mapping for pasted text or a file · upsert a table onto a node (`dry_run`) · read the watched files · what this Mac watches · CSV / JSON files in the code folders |
| `GET /proposals?slug=` · `POST /proposals/{id}/apply` · `DELETE /proposals/{id}` | the assistant's proposals (for a map, plus the ones that would create one) |
| `GET /lines` · `POST /seed {line}` | research lines with a note and their map · a first map drafted from a note, as a proposal |

Live refresh: the change stamp hashes the names, sizes and times of the map's YAML files (conflict
copies included, the generated canvas not), so an edit here, a `studio map` command or a file synced
from the other Mac all show up. The page polls it every 2 s while visible, then reloads the map and
reads the events after the last ingest sequence it handed on.

## Back-end extension points

`app/rmap/registry.py`. A later phase adds a module under `app/rmap/plugins/` and does not edit the core
files. Plugins are imported once, in name order, the first time any research map code needs the
registry: the page, the CLI, the assistant's tools and tests all get them (`registry.kinds()`); a
failing plugin is logged and skipped.

- `attachment_kind(AttachmentKind(name, prefix, fields={field: validator}, required=(), file="",
  node_types=(), summary=None, check=None, log="each", extra={}))`: a new kind gets its own file, the
  generic routes, cascade on node delete, events, and its `extra` in `/meta`. A validator takes a value
  and returns the clean one (or raises `ValueError`); `check(record)` sees the whole record after each
  add or change; `shard="node"` keeps the kind in one file per node (`<file or name>/<node id>.yaml`, a
  `ShardedStore`) so a big import on one node does not rewrite the others; `identity(record) -> tuple` says what tells two
  records of the kind apart on a node (a proposal that adds one that exists by the time it is confirmed is stale);
  `log="batch"` logs one event per batch and action (`targets`, `count`) instead of one
  per record, for kinds filled by bulk imports.
- `ext_field(key, validator)`: validate what the plugin keeps in `node.ext[key]`.
- `on_event(fn)`: `fn(slug, events)` with the events this Mac had not seen yet, oldest first: after every
  write here, and whenever events from the other Mac arrive (a background watcher, `app/rmap/watch.py`,
  looks every 5 s while the app runs; reading a map does too). Each event reaches subscribers once per
  Mac. It runs in the thread that found the events; keep it quick (errors are logged, never raised).
- `router`: a module-level `APIRouter` is mounted before the core routes. Use paths under `/api/rmap/` that
  are not core paths, e.g. `/api/rmap/overview`, `/api/rmap/projects/{slug}/results/import`.

## Front-end extension points

`web/rmap/registry.js`. Every `.js` file in `web/rmap/ext/` is imported by the page (name order) and
every `.css` file is linked, before the page reads its first map; register through `rmap`:

| slot | spec | used for |
|---|---|---|
| `rmap.layer` | `{id, label, order, default, accent, node(n, box, ctx), nodeClass(n, ctx), svg(ctx), under, legend(ctx)}` | map layers toggled in the toolbar: per-node SVG decorations, an extra class on a node (e.g. the aqua dashed 空白 outline), overlays in map coordinates (`under: true` draws them below the edges) |
| `rmap.section` | `{id, label, order, when(n, ctx), render(n, ctx)}` | detail panel sections of the selected node (peers, results) |
| `rmap.tool` | `{id, label, icon, order, run(ctx)}` or `{…, render(ctx)}` | toolbar buttons and small controls (time scrubber) |
| `rmap.view` | `{id, label, order, render(ctx)}`; `{…, icon, scope: "project"}` | whole-page views in the header's segmented control, before the projects (总图). `scope: "project"`: a view of the selected project (回放), a toolbar toggle that takes the place of the KPI row, the map and the detail panel while open, and closes on a second click or another project; its `ctx` has the map as usual plus `closeView()`. `rmap.views(scope)` lists either kind |
| `rmap.kpi` | `{id, order, render(ctx)}` | KPI tiles above the map |
| `rmap.strings` | `{zh: {...}, en: {...}}` | still accepted for an extension's labels; prefer its own locale module |

Labels are i18n keys: an extension keeps its text in its own module, `web/locales/<lang>/<module>.json`
(docs/i18n.md), and the page shows its labels with `label(key)` from `/rmap/strings.js` (which also finds
keys given to `rmap.strings`). Registering the same id again replaces the item.
CSS: the page root has class `rmap`; research map classes start with `mp-` and color tokens with `--mp-`
(`--mp-verified`, `--mp-active`, `--mp-blocked`, `--mp-faded`, `--mp-peer`, …, set for light and dark).
`web/rmap/util.js` has `safeUrl(u)` and `dagEdges(map, meta)` (no imports: it runs outside the browser too);
`web/rmap/dom.js` has `useAutoFocus`.

Events: `rmap.on(name, fn)` returns an unsubscribe function. `load` `{slug, map}` after a map is read;
`change` `{slug, events}` with the events this Mac saw since the last `change` (own edits, CLI, the other
Mac, late ones included); `select` `{slug, node}`; `view` `{id}`.

Every hook gets `ctx`:

| | |
|---|---|
| `slug`, `map` | `{project, nodes, edges, attachments: {kind: [...]}, warnings, stamp, seq}` as `GET /projects/{slug}` returns it |
| `meta`, `projects` | `/meta`; the project list with counts |
| `selected`, `select(id)` | the selected node |
| `open(slug, node?)` | switch to a project's map (e.g. from a view) and select one of its nodes once the map is read |
| `layers` | `{layer id: on}` |
| `layout` | in layer hooks: `{boxes: {node id: {x, y, w, h, col, pinned}}, edges, columns, width, height, hidden, hasKids, visible}` in SVG map coordinates (`web/rmap/layout.js`) |
| `view` | `{focus, setFocus, collapsed, setCollapsed, fold(id)}`: focus and collapsed branches (collapse is kept per map on this Mac) |
| `api` | `apply(ops, summary)` (a batch, then refresh), `refresh()`, `base` (the project's URL), `call` (`api` of lib.js) |
| `t`, `toast`, `onAsk(prompt, label)`, `onOpenNote(path)`, `onOpenPaper(stem)` | text (`label()`: i18n keys, extensions' included), a toast, hand a task to the workbench assistant, open a vault note or a paper |

## The page

`web/rmap/page.js` (shell, project switcher, proposals, KPI row, live refresh), `map.js` (the SVG map),
`layout.js` (pure layout), `detail.js` (detail panel), `kpis.js`, `proposals.js`, `describe.js` (one line per
event), `strings.js` (extensions' labels), `util.js`, `dom.js`. Tests: `tests/test_rmap_view.py`, and
`tests/js/rmap_layout.test.mjs` (the layout in node or JavaScriptCore).

- Layout: left to right, on `dagEdges`. A node's column is the furthest of its type's column (question ·
  hypothesis · branch · experiment · result) and one past the nodes it refines. Rows follow the refines
  hierarchy only, depth first, with parents centered on their children; a depends edge is a cross-link,
  drawn but never used to place a node (a node that only depends on another is a root of its own).
  Pinned nodes keep their place; automatic nodes that would land on one move down until they are clear.
- Editing: click selects; double-click renames (a node, or an edge's label); right-click opens status,
  add child, link (refines / depends / alternative / conflicts), collapse, focus, unpin, delete; dragging
  pins. The detail panel edits status, type, title, description (Markdown), evidence and links, and shows
  the node's change log. Esc cancels an edit without saving.
- Keyboard: nodes are buttons with a label (type, title, status); the selected one is in the Tab order,
  so Tab moves into and out of the map as anywhere else. On the canvas or a node: arrows move the
  selection, Enter renames, A (or ⌘↵) adds a child of the next type, F focuses, Space selects, Esc leaves
  focus or link mode. Keys on the zoom buttons, the menu or an input do what those controls do.
- Live refresh runs one refresh at a time: an edit's own refresh and the 2-second poll queue up, an
  answer for a superseded request (or another project) is dropped, and each event reaches `change` once.
- The core KPI tiles are registered like an extension's, with ids `depth`, `active` (with an 8-week trend
  rebuilt from the log), `verified`, `blank` (open paths and sub-lines with nothing attached). Register
  the same id to replace one (the peer layer redefines `blank` as "no peers").
- The 方向 page's research-line cards link to their map (by `line` or `note` path), or draft one.

## Strings

The research map's UI text is the `rmap` module of the i18n framework (#49, docs/i18n.md):
`web/locales/zh/rmap.json` and `en/rmap.json` for the page, `app/locales/<lang>/rmap.json` for the server
(proposal messages, and the canvas labels, which follow the vault language). Every file under `web/rmap/`
is marked `// i18n: done`. The results feature keeps its text in its own module, `rmap_results` (`web/locales/<lang>/rmap_results.json`, `app/locales/<lang>/rmap_results.json`), and the history's name for its record kind through `rmap.strings`. Words follow docs/glossary.md (the 研究地图 table): in the Chinese UI technical
terms stay English (Kill criteria, Gate, baseline, ablation, metric names such as Recall@10); English uses
Hypothesis, Branch, Validated, Dead end, Falsified, Abandoned.

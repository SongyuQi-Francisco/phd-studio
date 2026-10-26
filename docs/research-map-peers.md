# Research map: peers, gaps and collision risks (#47)

The third phase of the research map ([research-map.md](research-map.md) is the contract it plugs into). Who
else works near each node: papers, scholars and labs, hung on nodes like evidence, and what the map shows
about them.

Code: `app/rmap/peers.py` (kind, analysis, suggestions), `app/rmap/plugins/peers.py` (routes),
`web/rmap/peerlib.js` (the same rules for the page, no imports), `web/rmap/ext/peers.js` + `.css` (layer,
KPI, detail section, suggestions, the 同行分析 view), `web/rmap/attach.js` (the reader's 「挂到地图」, through
`web/reader-tools.js`). Text: the `rmap_peers` locale module (web and app). Tests: `tests/test_rmap_peers.py`,
`tests/js/rmap_peers.test.mjs`, `tests/js/reader_tools.test.mjs`.

## The `peer` attachment kind

`<project>/peers.yaml`, merged field by field and logged like every attachment (`ops.apply`).

| field | |
|---|---|
| `type` | `paper` · `scholar` · `lab` (required) |
| `title` | paper title, scholar or lab name (required) |
| `relation` | `same` 同一做法 · `competing` 竞争做法 · `builds_on` 在其基础上 (the node builds on it) (required) |
| `url` | http(s) only |
| `paper` | a paper note by name (opens in the reader) |
| `authors`, `venue`, `date` (YYYY, YYYY-MM or YYYY-MM-DD), `note` | optional |
| `source` | `manual` · `tracker` · `follow` · `scholar` · `reading` · `reader` · `assistant` |
| `entity` | the same peer in every project: `paper:<arXiv id>`, the paper's `doi:` / `openreview:` / `acl:` key, `paper:<title>`, `scholar:openalex:<id>` / `scholar:<name>`, `lab:<id or name>` (`peers.entity_for`, `peerlib.entityFor`) |

Evidence added from a suggestion or the reader carries the paper's `entity` too.

**One peer, several ids.** A paper can arrive with an arXiv id here, an OpenReview forum there, or only a
title. A record is known by all its aliases (`peers.aliases`, `peerlib.aliasesOf`): its entity, the ids in
its link (arXiv abs / pdf / html links with any version, query or trailing slash; OpenReview, DOI, ACL
Anthology), its paper note (`note:<name>`) and a paper's normalised title (12 characters or more). Records
sharing any alias are one peer:

- on read, two records of one peer on a node (confirmed on both Macs, or again from the reader) are merged
  and counted once, with the closest relation; the detail panel changes or removes all of them together;
- `POST /api/rmap/peers/attach` (used by the panel, the suggestions and the reader) adds nothing when the paper
  already hangs on that node as that kind, and only updates a peer's relation;
- the overview merges a peer across projects into one column;
- evidence hung before #47 (a paper note, no entity) is recognised by the note's own arXiv id, DOI and title.

## Gaps and collision risks

- **空白 (gap)**: an open branch (hypothesis or sub-line, not verified / refuted / dropped) with no peer.
- **撞车风险 (collision risk)**: a node in progress with at least 3 distinct peers taking the same or a
  competing approach, or 2 taking the same one. Builds-on peers never count.

The thresholds live in `app/rmap/peers.py` and reach the page through `/meta` (the `peer` kind's extra);
`peerlib.js` applies the same rule. The 空白分支 KPI is redefined as "open branches with no peer".

## On the page

- **同行层** (toolbar toggle): an orange pill with the peer count (solid when at risk), the aqua dashed
  outline and 「空白」 tag on gaps, a 「撞车风险」 tag.
- **Detail panel** 「同行」: the node's peers (orange initials rings, relation, where else they hang), add /
  change / remove, and the suggestions for that node.
- **推荐挂载** (toolbar): suggestions for this map.
- **同行分析** (a view): the 分支 × 同行 heat table of every map (rows: branches and every node with peers,
  depth first; columns: peers merged across projects by entity, the most widespread first; a cell's shade is
  the relation), the gaps, the collision risks and every suggestion. A row opens the map at that node
  (`ctx.open`).

## Suggestions

New papers the app already has on disk, never fetched for this: the latest two weekly tracker runs, the
cached follow feeds (arXiv, OpenReview; 60 days), the followed scholars' cached works (90 days) and the
reading list (inbox, queued, reading). Each paper is matched to nodes (questions, hypotheses, sub-lines,
experiments; not refuted or dropped ones) by shared words: rare words weigh more, the paper's title and the
node's title most, the titles of what already hangs on the node a little, and the project's research-line
keywords add a bonus (whole words, `sources.has_terms`). Stop words are dropped before and after stemming.
A paper needs three shared words, or two plus a shared pair, and enough of the weight from the node's own
words. A paper already hung on a node (any alias) is left out there, and the same paper from several
sources is one suggestion.

Suggestions are computed on the server and cached until a map or a source changes (tracker runs, feeds,
scholar caches and list, reading list, plan, paper notes, the date); a map's own suggestions read only that
map. The toolbar asks 1.5 s after the map last changed, while the page is visible. The assistant's tools run
their work in a thread.

Nothing is attached by itself: the user confirms a suggestion as a peer (with its relation) or as evidence,
or dismisses it for that map. Dismissals are kept in `<project>/peer_suggestions.yaml` with the paper's
aliases (field-merged, so both Macs stop showing them, whatever id the paper comes with next). The assistant refines the keyword match: `map_peer_suggest` reads the
candidates, `map_peer_propose` sends the chosen peers as a proposal.

## The reader: 「挂到地图」

A button in the reader's toolbar (`web/reader-tools.js`: `readerTool({id, order, render({stem, paper,
toast})})`; the modules listed there are imported on their own, so one that fails to load is logged and the
reader works as before; `web/main.js` renders `renderReaderTools(...)` after the Zotero button, skipping an
item whose render throws). It lists where the open paper already hangs
and the nodes it most likely fits (`GET /api/rmap/peers/paper?stem=`), or any node of any map, and hangs it
as evidence (its note, else its link) or as a peer, in one batch to that map.

## HTTP

| | |
|---|---|
| `GET /api/rmap/peers/overview` | the heat table of every map (archived maps, refuted and dropped nodes left out), gaps, risks, the rule, the change stamp |
| `GET /api/rmap/projects/{slug}/peers/analysis` | one map: peer counts per node, gaps, risks |
| `GET /api/rmap/peers/suggestions?slug=&node=` | suggestions, best first |
| `POST /api/rmap/peers/dismiss {key, aliases, slugs}` | stop suggesting a paper for these maps |
| `POST /api/rmap/peers/attach {slug, node, kind, set}` | hang a peer / evidence unless it already hangs there: `{state: added / updated / exists, id}` |
| `GET /api/rmap/peers/paper?stem=` | the reader's paper: title, ids, entity, likely nodes, where it hangs |

## Assistant tools

`map_gaps(project?)` and `map_peer_suggest(project?)` read (also offered to the read-only models);
`map_peer_propose(project, summary, peers)` records a proposal the user confirms on the map page. `map_read`
lists each node's peers.

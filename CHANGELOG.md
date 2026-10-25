# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- README, community files, demo data and screenshots ([#40](https://github.com/SongyuQi-Francisco/phd-studio/issues/40))

## [0.1.0] - 2026-10-25

### Added

- Foundation, built before the pull-request workflow: a PDF reader with a Claude co-reader over an Obsidian vault and Zotero; the reading list (`inbox → T1/T2/T3 → reading → done`) and the `studio` CLI; a desktop launcher and native app window; sync-safe use on two Macs; the overview, timeline and CORE + CCF venue library with a workspace assistant; and the ideas, tracking and direction pages.
- Excerpts: save selected text with its page number to a section of the paper's note, previewed before writing and undoable from the toast. Page references (`[p.5]`, `pp. 4-5`) and section references (`§5.1`, `Section 3`) in notes and chat jump to that place in the PDF. A one-click Claim Gate audit checks a note's claims against the full text and primary sources and proposes note edits for approval ([#16](https://github.com/SongyuQi-Francisco/phd-studio/pull/16))
- Tracking page: top scholars in a field and their labs' activity (OpenAlex and arXiv data, cached), as cards and charts. The scholar list lives in the vault; the repository ships only `scholars.example.yaml` ([#18](https://github.com/SongyuQi-Francisco/phd-studio/pull/18))
- Followed keywords and scholars are exported to the weekly tracker run, and the tracking page can start a run on demand and show its progress ([#19](https://github.com/SongyuQi-Francisco/phd-studio/pull/19))
- Test baseline and CI: a pytest suite that runs against a throwaway vault, data dir and state dir with the network blocked, the `tests` GitHub Actions check, PR and issue templates, and an optional `paths.state` setting. Fixes two bugs the tests found: missing acceptance rates for venues whose ccfddl title differs from their CORE name (e.g. SIGKDD / KDD), and unwatching every venue re-adding the profile's venue list ([#21](https://github.com/SongyuQi-Francisco/phd-studio/pull/21))
- CLI: `studio milestone list`, `studio milestone done` and `studio idea list` ([#22](https://github.com/SongyuQi-Francisco/phd-studio/pull/22))
- The top-scholar list is recomputed on a schedule (monthly by default) or on demand; changes are shown as a proposal and applied only after you confirm ([#23](https://github.com/SongyuQi-Francisco/phd-studio/pull/23))
- Ideas: free-form tags, and merging several ideas into one while keeping their sources; merged ideas stay hidden and can be restored. Available in the UI, the assistant's tools and the CLI (`studio idea tag`, `merge`, `restore`) ([#26](https://github.com/SongyuQi-Francisco/phd-studio/pull/26))
- Direction page: one long-running Claude conversation per research line, and a review-packet template for advisor meetings ([#27](https://github.com/SongyuQi-Francisco/phd-studio/pull/27))
- Venue details record the edition they describe and when they were verified, and warn when they may be out of date (older edition, `[venues] stale_months`, or new ccfddl data). The assistant can re-check the official site and propose changes, which are written only after you confirm the diff ([#28](https://github.com/SongyuQi-Francisco/phd-studio/pull/28))
- Zotero button in the reader: adds the paper through the running Zotero desktop app, else the Zotero Web API with your own key, after a DOI / arXiv / URL dedupe; the `zotero://` link is written to the note only after you confirm ([#29](https://github.com/SongyuQi-Francisco/phd-studio/pull/29))
- Deadline and milestone reminders as macOS notifications, with configurable lead times, quiet hours, a reminder device so two Macs do not both notify, an optional background LaunchAgent, and `studio remind` ([#30](https://github.com/SongyuQi-Francisco/phd-studio/pull/30))
- Capture box recognizes OpenReview, DOI and ACL Anthology links and title searches, and the follow feed also covers OpenReview and ACL Anthology ([#31](https://github.com/SongyuQi-Francisco/phd-studio/pull/31))
- Optional ahead-of-time bilingual PDFs: papers that become T1/T2 on the reading list are queued for pdf2zh-next, one at a time; off by default, pausable, and kept across restarts ([#32](https://github.com/SongyuQi-Francisco/phd-studio/pull/32))
- Per-conversation model choice: besides Claude, Codex, Gemini, Zhipu GLM or DeepSeek. Other models are read-only, every answer is labeled with the model that produced it, and an unavailable model is reported as an error instead of switching silently. Zhipu GLM is also available as a translation engine ([#39](https://github.com/SongyuQi-Francisco/phd-studio/pull/39))

### Changed

- Visual refresh: shared chart components (sparklines, bars, rings, donuts, heat strips, funnels) with hover tooltips and screen-reader tables, paper thumbnails, skeleton loading, empty states and view transitions ([#17](https://github.com/SongyuQi-Francisco/phd-studio/pull/17))
- Tracking page placeholders use fictional examples, and institution logos are requested only after they have been downloaded ([#37](https://github.com/SongyuQi-Francisco/phd-studio/pull/37))
- Submission targets point at a specific edition (and optionally one deadline round) of a venue; a submitted or accepted paper silences reminders only for its own edition. Adds `studio target` ([#38](https://github.com/SongyuQi-Francisco/phd-studio/pull/38))

### Fixed

- Note links with `..` segments or absolute paths no longer resolve outside the vault, and symlinks pointing outside it are skipped ([#25](https://github.com/SongyuQi-Francisco/phd-studio/pull/25))
- The paper list no longer fails with a 500 error when the local copy of the Zotero database is corrupt: the copy is made again once, and if the original cannot be read either, papers are listed without Zotero links ([#36](https://github.com/SongyuQi-Francisco/phd-studio/pull/36))

[Unreleased]: https://github.com/SongyuQi-Francisco/phd-studio/commits/main
[0.1.0]: https://github.com/SongyuQi-Francisco/phd-studio/tree/4f6199ee8e33eeb4519f8f1a82bdb1219830ead6

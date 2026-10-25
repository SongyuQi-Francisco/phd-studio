# Contributing to PhD Studio

Thanks for your interest. Bug reports, fixes and features are welcome. By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). For security problems, do not open an issue; see [SECURITY.md](SECURITY.md).

## Development setup

You need macOS (Linux works for the server and the tests; the desktop app and notifications are built for macOS), Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                               # creates .venv with the app and dev dependencies (pytest)
cp studio.example.toml studio.toml    # point it at your vault, Zotero and profile
./start.sh                            # http://127.0.0.1:8770
```

`./start.sh` runs `uvicorn app.main:app` on `127.0.0.1:8770` and passes extra arguments on to uvicorn, so `./start.sh --reload` restarts the server when Python files change. The frontend has no build step: edit a file in `web/` and reload the page.

The chat features need a Claude login: run `claude setup-token` and store the token as described in the [README](README.md#quick-start). The tests need neither a login nor a `studio.toml`.

To try the app without your own vault, `uv run python scripts/demo/make_demo.py` generates a fictional demo vault and config (in `~/Library/Application Support/PhD Studio Demo`, or `--out <dir>`) and prints the command that starts the app on it.

The desktop window: `uv run python -m app.desktop`. If a server is already answering on port 8770, it does not start another one (a running desktop window is brought to the front instead); use `STUDIO_PORT=8771 uv run python -m app.desktop` for a second, development window.

## Workflow

1. For anything beyond a small fix, open or find an issue first (there are Bug and Feature templates).
2. Use one branch per change, branched from an up-to-date `main`. A git worktree per branch is recommended:

   ```bash
   git fetch origin
   git worktree add .claude/worktrees/<name> -b <branch> origin/main
   ```

   `.claude/` is not in `.gitignore`; add it to `.git/info/exclude` (or put the worktree outside the repository) so it does not show up as untracked.
3. Open a pull request against `main`, fill in the template, and link the issue with `Closes #N`.
4. Before merging, the `tests` check must pass and the branch must be up to date with `main` (merge or rebase `main` into it if GitHub says it is behind).
5. Maintainers squash-merge, so the PR title becomes the commit message on `main`: make it describe the change.

If your change alters user-facing behavior, update `README.md`, and `docs/设计文档.md` (the design document, in Chinese) where it describes that feature.

## Tests

```bash
uv run pytest
```

CI (`.github/workflows/tests.yml`, check name `tests`) runs `uv sync --locked` and `uv run --locked pytest` with Python 3.12 on Ubuntu, on every push and pull request. Add dependencies with `uv add` (or `uv add --dev`) so that `uv.lock` stays in sync; `--locked` fails otherwise.

The suite runs in a sandbox set up by `tests/conftest.py`:

- It writes a generated config into a temporary directory and points `STUDIO_CONFIG` at it before `app.config` is imported. The run stops if the configured vault, data, state, Zotero or secrets directory lies outside that directory, so your real vault, Zotero, `studio.toml` and `state/` are not touched.
- Every test starts from an empty vault, data dir and state dir.
- `httpx.get` and `httpx.post` raise, so a test cannot reach the network. To test code that fetches, use the `net` fixture from `tests/fakenet.py`, which serves recorded responses from `tests/fixtures/sources/`.
- Fixtures such as `make_note`, `write_vault_file` and `venue_data` build test data.

Add or update tests for every behavior change. If you add a new configurable path to `app/config.py`, add its name to the list in `tests/test_sandbox.py` (and new store, lock or cache files to the second test there), and set it in the config in `tests/conftest.py` if its default would point outside the sandbox.

## Code notes

- **Backend:** FastAPI in `app/` (routes in `app/main.py` and per-feature routers such as `app/track_api.py`). Modules use `from __future__ import annotations` and type hints. There is no formatter or linter configured; match the style of the surrounding code.
- **Frontend:** Preact + htm as plain ES modules, served as static files from `web/` with an import map in `web/index.html`. No npm, bundler or build step.
- **Third-party browser libraries** are vendored in `web/vendor/` with their licenses. Add new ones there, with their license file, rather than loading them from a CDN.
- **User-facing UI strings are in Chinese**, as are API error messages and the prompts sent to the models; follow that in new UI. Code comments, docstrings and the README are in English.
- **Vault writes:** Claude's edits to the vault go through the diff approval in `app/claude_chat.py`. Keep new direct writes to the vault rare and atomic (see `app/store.py`), and show any change to a user's note before it is written, as excerpts and the Zotero link do.

## Privacy

This is a public repository; personal data stays out of it:

- Never commit personal vault content (notes, PDFs, reading lists, scholar lists), `studio.toml`, `state/`, logs, or secrets (`*.env`, API keys, tokens).
- Use fictional names and data in tests, fixtures, examples and screenshots (the tests use names such as `Ada Example` and `Project A`).
- Check screenshots and pasted logs for personal information before attaching them to issues or pull requests.

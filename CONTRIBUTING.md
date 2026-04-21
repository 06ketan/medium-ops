# Contributing to medium-ops

Thanks for picking this up. `medium-ops` is the sibling of
[`substack-ops`](https://github.com/06ketan/substack-ops): same shape, different
platform. It stays small on purpose so contributions land fast when they're
scoped tight.

## Ground rules

- **One change per PR.** A bug fix, a tool, a doc pass — not all three.
- **Dry-run is sacred.** Anything that talks to Medium must default to
  `dry_run=True` and emit an audit row.
- **Hybrid auth is the spec.** Reads default to public RSS. Authenticated reads
  go through `medium.com/_/graphql`. Writes either go through the legacy
  Integration Token (if you still have one) or through dashboard GraphQL +
  `/p/{id}/deltas` with `sid` + `xsrf`. Don't collapse the layers.
- **Tool descriptions are first-class.** When you add or edit an MCP tool, the
  description has to spell out side effects, the exact ID it expects, and the
  sibling tool a caller might confuse it with. We optimize for
  [Glama TDQS](https://glama.ai/mcp/servers).
- **No new runtime dependencies** without a strong reason. The point is
  `pip install medium-ops` ships in seconds.
- **Schema drift is a known risk.** Medium's GraphQL operation names and
  payload shapes can change silently. Use `medium-ops auth har <file.har>` to
  re-snapshot the live wire format before patching `client.py`.

## Dev setup

```bash
git clone https://github.com/06ketan/medium-ops.git
cd medium-ops
uv sync --all-extras
cp .env.example .env  # fill in MEDIUM_SID + MEDIUM_XSRF (and optionally INTEGRATION_TOKEN)
uv run medium-ops auth verify
```

## Running

```bash
uv run medium-ops --help              # CLI surface
uv run medium-ops mcp serve           # MCP server (stdio)
uv run pytest -q                      # tests
uv run ruff check .                   # lint
uv run mypy src/medium_ops            # types (optional)
```

## Layout

```
src/medium_ops/
  cli.py            # CLI entrypoints (Typer) — 31 commands
  client.py         # Medium HTTP client (REST + GraphQL + dashboard)
  rss.py            # public RSS read path (selectolax)
  har.py            # HAR ingest for cookie refresh + schema drift detection
  auth.py           # MediumConfig + cookie/token loading
  mcp/server.py     # MCP server + 23 tools
  mcp/install.py    # auto-install into Cursor / Claude / Codex
  audit.py          # JSONL audit log
  dedup.py          # SQLite dedup store
  reply_engine/     # template + ai_bulk + propose/confirm flows
```

## Adding an MCP tool

1. Implement the function in the relevant module under `src/medium_ops/`.
2. Register it in `src/medium_ops/mcp/server.py` `TOOLS` dict with:
   - **`description`** — start with the side-effect tag (`Read-only.` /
     `Write.` / `STAGE A WRITE` / `DESTRUCTIVE.`), say what it returns, name
     the sibling tool to use instead.
   - **`input_schema`** — every property gets a `description`.
3. Add a unit test in `tests/`.
4. If it mutates Medium: route through `audit.log_event()` and `dedup.check()`.

## Refreshing auth / fixing schema drift

Medium changes their dashboard GraphQL schema occasionally. If `client.py`
starts erroring with "Field X is not defined" or "Cannot destructure ...":

1. Open `medium.com` in Chrome with devtools → Network panel.
2. Reproduce the failing action (publish, comment, etc.).
3. Right-click any request → "Save all as HAR with content".
4. `medium-ops auth har ./medium.har`

Cookies refresh into `.env`. A redacted snapshot lands in
`.cache/har-snapshot.json`. Diff request/response key shapes against what
`client.py` sends.

## Reporting bugs

Open an [issue](https://github.com/06ketan/medium-ops/issues/new) with:

- `medium-ops --version`
- Python version (`python -V`)
- The exact command + stack trace
- Whether `auth verify` succeeds (and which surface — RSS vs SID vs Integration Token)

## Releasing (maintainers)

1. Bump `version` in `pyproject.toml`, `server.json`, and
   `src/medium_ops/__init__.py`.
2. `git tag -a vX.Y.Z -m "release: vX.Y.Z"`.
3. `git push --tags` — the publish workflow does the rest (PyPI + GitHub release).
4. `mcp-publisher publish` to push the new `server.json` to the official MCP
   registry.

## License

By contributing, you agree your code is released under the
[MIT License](LICENSE).

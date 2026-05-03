# medium-ops

<!-- mcp-name: io.github.06ketan/medium-ops -->

[![PyPI version](https://img.shields.io/pypi/v/medium-ops?color=00ab6c&label=pypi)](https://pypi.org/project/medium-ops/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![MCP compatible](https://img.shields.io/badge/MCP-compatible-8A2BE2)](https://modelcontextprotocol.io)

> **Standalone Medium CLI + 22-tool MCP server. Your IDE drafts the replies. Zero AI API keys.**

Stories, responses, claps, feed, profiles, stats, reply engine, MCP server.
One Python install, one binary, MIT licensed. Sibling of
[substack-ops](https://github.com/06ketan/substack-ops).

## TL;DR — MCP-native (no API key, one command)

```bash
uvx medium-ops mcp install cursor          # or claude-desktop, claude-code, print
# Restart your host. Then in chat:
#   "list unanswered responses on post abc123def456"
#   "draft a warm reply to response r1"
#   "post that draft"
```

Your **host's** LLM (Cursor's, Claude's) does the drafting via the
`propose_reply` / `confirm_reply` tools. No `ANTHROPIC_API_KEY` /
`OPENAI_API_KEY` needed.

## Why a hybrid

Medium exposes three usable surfaces and we use all of them:

1. **Public RSS (reads, no auth).** `medium.com/feed/@{user}` returns the
   author's ~10 most recent stories with `body_html`, `pubDate`, `tags`,
   hero image, and `dc:creator`. Zero credentials, faster than GraphQL,
   stable. Used by default for `list_posts` / `get_post` / `get_post_content`.
   Inspired by [Portfolio_V2's blog page](https://github.com/06ketan/Portfolio_V2/blob/main/src/utils/medium/parser.ts).
2. **Dashboard GraphQL (authenticated reads).** `medium.com/_/graphql` +
   `medium.com/_/api/*` with the `sid` cookie. Used as a **fallback** when
   you ask for more than ~10 posts, when the post isn't in the RSS window,
   or for things RSS can't give you (responses, claps, feed, stats, search).
3. **Official REST (writes).** `api.medium.com/v1/*` with an
   Integration Token. Supports `createPost`, `createPostInPublication`,
   `getUser`, `getPublications`. That's it.

Force a specific transport with `--source rss|graphql|auto` on `posts list`,
`posts show`, and `posts content`. The dashboard + GraphQL endpoints are
undocumented and Medium can change them at any time. See
[Known gaps](#known-gaps).

## Setup (dev / from source)

```bash
git clone https://github.com/06ketan/medium-ops && cd medium-ops
uv sync
uv sync --extra mcp     # mcp SDK for the MCP server (recommended)
uv sync --extra tui     # textual for the TUI
```

Auth is read from `~/.cursor/mcp.json`'s `mcpServers.medium-ops.env` (or
`medium-api` / `medium`). Override with env or `.env`.

```bash
uv run medium-ops auth verify
uv run medium-ops quickstart
```

## Command surface

Every write defaults to `--dry-run`. Flip with `--no-dry-run`. All writes
land in `.cache/audit.jsonl` and are dedup-checked against
`.cache/actions.db`.

### Auth (3)

| Command | What it does |
|---|---|
| `auth verify` | Probe both integration token (/me) and sid cookie (GraphQL Viewer). |
| `auth test` | Same but exits non-zero on failure (CI-friendly). |
| `auth setup` | Interactive: paste token / sid / uid / username to `.env`. |

### Read — Stories (5)

| Command | What it does |
|---|---|
| `posts list [--user] [--limit]` | Latest stories by a user (default: self). |
| `posts show <id_or_url>` | Story metadata (title, clap count, response count). |
| `posts content <id> [--md]` | Body HTML (or Markdown with `--md`). |
| `posts search <query> [--limit]` | Medium-side full-text search. |
| `posts publish -t "..." -f body.md [--pub] [--status draft|public|unlisted]` | Publish via integration token. |

### Read + Write — Responses (3)

| Command | What it does |
|---|---|
| `responses list <post_id> [--limit]` | Top-level responses table. |
| `responses tree <post_id> [--out file.json]` | Full response + reply tree JSON. |
| `responses add <post_id> "body" [--parent <r_id>] [--no-dry-run]` | Post a response or reply. |

### Read + Write — Claps (2)

| Command | What it does |
|---|---|
| `claps count <post_id>` | Total claps. |
| `claps give <post_id> [--claps N] [--no-dry-run]` | Clap 1-50 times. Dedup-protected. |

### Read — Discovery + Profile (5)

| Command | What it does |
|---|---|
| `feed list [--tab home\|following\|tag-{slug}] [--limit]` | Reader feed. |
| `profile me` | Your full profile (GraphQL). |
| `profile get <username>` | Any user's public profile. |
| `profile stats [--days N]` | Per-post views / reads / fans (dashboard scrape). |
| `profile publications` | Publications you can publish to (integration token). |

### Reply engine (3)

| Command | What it does |
|---|---|
| `reply template <post_id> --template thanks` | Rule-based replies (no LLM). |
| `reply bulk <post_id> --out drafts.json` | Draft every response to a file. |
| `reply bulk-send drafts.json [--no-dry-run]` | Post only `action=approved` rows. Dedup-checked. |

### Operations + safety (2)

| Command | What it does |
|---|---|
| `audit search [--kind] [--target] [--status] [--since 7d]` | Query the JSONL audit log. |
| `audit dedup-status` | Counts in the dedup SQLite DB. |

### MCP server (3)

| Command | What it does |
|---|---|
| `mcp install <cursor\|claude-desktop\|claude-code\|print> [--dry-run]` | Auto-merge config into your host. |
| `mcp serve` | stdio MCP server (22 tools). |
| `mcp list-tools` | Print the tool registry. |

### Other (1)

| Command | What it does |
|---|---|
| `quickstart` | Print a quickstart checklist. |

## Reply modes

| Mode | What it does | Safety |
|------|--------------|--------|
| `template` | YAML keyword rules under `src/medium_ops/templates/*.yaml` | dry-run default |
| `bulk` | LLM drafts every response to `drafts.json`. Edit, set `action: "approved"` | offline review, dedup-checked on send |
| `bulk-send` | Posts only items with `action: "approved"` | dry-run default; dedup DB prevents dup replies |
| MCP `propose_reply` → `confirm_reply` | Host LLM drafts, you approve per-item, token-gated | 5-min token TTL, idempotent, no API key |

## MCP server

```bash
medium-ops mcp install cursor              # auto-add to ~/.cursor/mcp.json
medium-ops mcp install claude-desktop      # auto-add to claude_desktop_config.json
medium-ops mcp install claude-code         # uses `claude mcp add`
medium-ops mcp install print               # print the snippet only
medium-ops mcp serve                       # stdio server
medium-ops mcp list-tools                  # 22 tools
```

Manual config snippet:

```json
{
  "mcpServers": {
    "medium-ops": {
      "command": "medium-ops",
      "args": ["mcp", "serve"]
    }
  }
}
```

If the `mcp` SDK is not installed, the server falls back to a minimal
stdin/stdout JSON-line dispatcher:

```bash
echo '{"tool":"list_posts","args":{"limit":3}}' | medium-ops mcp serve
```

### MCP-native draft loop (no API key)

The safety + drafting stack that makes the unattended mode safe:

| Tool | What it does |
|------|--------------|
| `get_unanswered_responses` | Worklist — responses where you haven't replied. |
| `propose_reply` | Dry-run only. Returns a `token` + payload preview. |
| `confirm_reply` | Posts the staged reply by token. Idempotent via dedup DB. Token TTL 5 min. |
| `bulk_draft_replies` / `send_approved_drafts` | File-based offline review loop. |
| `audit_search` / `dedup_status` | Read the audit log + dedup counts. |

## LLM strategy

Two layers, both free:

1. **MCP-native (default).** Host LLM drafts via `propose_reply` /
   `confirm_reply`. No env vars, no API key. Use this for interactive replies.
2. **Subprocess CLI (daemon path).** For `reply bulk` when no human is in the
   loop. Auto-detects `claude` (Claude Code), `cursor-agent`, or `codex` on
   PATH. Override with `MEDIUM_OPS_LLM_CMD`.

There is no paid-API-key path.

## Auth setup

Medium has two auth layers that map to different feature surfaces:

1. **Integration Token** — `Authorization: Bearer <token>`. Used against
   `api.medium.com/v1/*`. Gets you: `publish_post`,
   `list_own_publications`. Token generation at
   https://medium.com/me/settings → "Integration tokens".
   **Note: Medium stopped issuing new tokens in 2023.** If you never
   generated one, the write path will 401 and you'll have to use the
   sid-cookie response path for any writes.
2. **sid cookie** — from `medium.com` (Application → Cookies → `sid`).
   Used against `medium.com/_/graphql` and `medium.com/_/api/*`. Gets you:
   all reads (stories, responses, claps, feed, stats, profile), plus
   `clap_post` and `post_response` (fragile — undocumented).

```bash
medium-ops auth verify
medium-ops auth test
medium-ops auth setup
medium-ops auth har ./medium.har    # ingest a Chrome devtools HAR export
```

### Refreshing auth from a HAR

When cookies rotate or Medium changes a GraphQL schema, the fastest fix is:

1. Open `medium.com` in Chrome with devtools → Network panel.
2. Reproduce the failing action (publish a draft, post a response, etc.).
3. Right-click any request → "Save all as HAR with content".
4. `medium-ops auth har ./medium.har`

This:

- merges fresh `sid`, `uid`, `xsrf`, `cf_clearance` cookies into `.env`
  (preserving everything else)
- writes a redacted snapshot to `.cache/har-snapshot.json` listing every
  Medium GraphQL operation observed plus its request-variable / response-data
  key shapes — useful for diffing against the queries hard-coded in
  `client.py` to spot schema drift before users hit it.

> **Don't have these yet?** See [docs/AUTH-SETUP.md](./docs/AUTH-SETUP.md) for a
> 5-minute browser-DevTools walkthrough. The Medium Integration Token API has
> been deprecated since 2023 — most users today use cookie-based auth via
> `MEDIUM_SID`.

Env vars (or `~/.cursor/mcp.json` → `mcpServers.medium-ops.env`):

```bash
MEDIUM_INTEGRATION_TOKEN=2fb00...     # optional, for writes
MEDIUM_SID=1:...                      # optional, for reads
MEDIUM_UID=...                        # optional
MEDIUM_USERNAME=yourhandle            # optional but recommended
```

## Architecture

```text
mcp.json | env                  →  auth.py
                                      │
                            MediumConfig (token? sid? uid? username?)
                                      │
                                MediumClient (httpx)
                              ┌───────┼──────────┐
                              ▼       ▼          ▼
                   api.medium.com  medium.com/  medium.com/_/
                     /v1/* (REST)    _/graphql   api/* (dashboard)
                       │              │           │
                   Bearer token    sid cookie    sid cookie
                                      │
          ┌──────┬──────┬────────────┬──────┬────────┬──────────┐
          ▼      ▼      ▼            ▼      ▼        ▼          ▼
        posts  responses  claps  profile  stats  feed  reply_engine
                                                            │
                                        ┌───────────────────┼───────────────┐
                                        ▼                   ▼               ▼
                                    template            ai_bulk       MCP propose/confirm
                                        └───────────────────┬───────────────┘
                                                            ▼
                                                   base.post_response
                                                            │
                                                  ┌─────────┼─────────┐
                                                  ▼         ▼         ▼
                                                dedup     audit   dry_run
                                                (SQLite)  (jsonl)

 mcp/server.py ──── 22 tools ─── all share MediumClient
```

## Endpoints used

| Action | Method + URL |
|--------|--------------|
| Auth: integration token | `GET https://api.medium.com/v1/me` |
| Auth: sid cookie | `POST https://medium.com/_/graphql` (`Viewer`) |
| User profile | `POST /_/graphql` (`UserProfileQuery`) |
| List stories | `POST /_/graphql` (`UserStreamOverview`) |
| Story metadata | `POST /_/graphql` (`PostViewer`) |
| Story body | `POST /_/graphql` (`PostContent`) |
| Story search | `POST /_/graphql` (`SearchPosts`) |
| Responses | `POST /_/graphql` (`PostResponses`) |
| Feed | `POST /_/graphql` (`HomeFeed` / `FollowingFeed` / `TagFeed`) |
| Publish story | `POST https://api.medium.com/v1/users/{id}/posts` |
| Publish to pub | `POST https://api.medium.com/v1/publications/{pub_id}/posts` |
| Own pubs | `GET https://api.medium.com/v1/users/{id}/publications` |
| Clap | `POST https://medium.com/_/api/posts/{id}/clap` (undocumented) |
| Post response | `POST https://medium.com/_/api/posts` (undocumented) |
| Stats | `GET https://medium.com/@{username}/stats?count=...` (undocumented) |

## Tests

```bash
uv run pytest -q
```

Coverage: auth loading, client transports + XSSI stripping, dedup DB, audit
log search, MCP tool registry + dispatcher, MCP install host-config merging,
propose/confirm flow + token expiry, reply-engine template matching +
dedup+audit flow, subprocess LLM detection.

## Known gaps

- **Medium stopped issuing new Integration Tokens in 2023.** If you never
  got one, `publish_post` / `list_own_publications` will 401. The
  read + response + clap paths still work via sid. The RSS read path needs
  no credentials at all.
- **RSS is capped at ~10 posts** and lacks clap/response counts and stats.
  When you need more, pass `--source graphql` (requires sid) or set
  `--limit > 10` and the client will auto-fall back to GraphQL.
- **GraphQL operation names and schemas change silently.** The queries in
  `client.py` mirror what the dashboard uses today — expect breakage every
  couple of months. Pin this package's version.
- **`post_response`** uses GraphQL `savePostResponse(deltas: [Delta!]!,
  inResponseToPostId: ID!)`. Delta shape is
  `{type: 1, index: N, paragraph: {type: 1, text, markups: []}}` (type=1 means
  insert; paragraph.type=1 is P). Reverse-engineered from error messages.
- **`update_draft_content`** uses dashboard `POST /p/{id}/deltas` with
  `{baseRev, rev, deltas}`. For a brand-new draft, `baseRev=-1, rev=0`.
  Subsequent edits should bump both.
- **`clap_post`** still uses the undocumented `/_/api/posts/{id}/clap` shape;
  not yet re-validated against the new GraphQL surface. Dry-run first.
- **Members-only stories** return a paywall preview unless the `sid` belongs
  to a paying member.
- **No "restack" equivalent.** Medium doesn't have reshares; the closest is
  a clap + a response. Use `clap_post` + `post_response` together for that.
- **No notes / short-form.** Medium killed short-form in 2018.
- **Chrome cookie auto-grab** (the `auth_chrome` flow from substack-ops) is
  not yet implemented. Paste your `sid` into `.env` for now.
- **TUI** not yet implemented; the extras pin is there for future work.

## License

MIT. See [LICENSE](LICENSE).

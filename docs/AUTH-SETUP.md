# medium-ops — auth setup walkthrough

`medium-ops` reads from Medium's web/GraphQL stack and writes through Medium's
two public APIs. Each surface needs a different credential, and Medium has
**stopped issuing new Integration Tokens since 2023**, so most users today
authenticate by copying a session cookie out of their browser.

This guide walks through the four real paths from least-privileged to most.

---

## 1. What you actually need

> Pick one of the four paths below based on the tools you want to use.

| Tool family                                    | Surface                       | Required env vars                                     | Optional      |
|------------------------------------------------|-------------------------------|-------------------------------------------------------|---------------|
| `get_post_html`, `list_responses`, `get_clap_count`, `get_own_profile`, `list_own_publications`, `test_connection`, etc. | medium.com web (GraphQL) | `MEDIUM_SID`                                           | `MEDIUM_UID`, `MEDIUM_USERNAME` |
| `post_response`, `clap_for_post`, dashboard writes | medium.com web (GraphQL)  | `MEDIUM_SID` + `MEDIUM_XSRF`                           | `MEDIUM_UID`  |
| `publish_post`, `create_draft`, legacy v1 writes | api.medium.com/v1            | `MEDIUM_INTEGRATION_TOKEN`                             | —             |
| public reads only (`get_post_rss`, public profile fetches) | medium.com RSS / public pages | _(none)_, but `MEDIUM_USERNAME` recommended for "self" routes | —      |

`MEDIUM_CF_CLEARANCE` is only needed when Medium's Cloudflare layer challenges
you — set it as a last resort (see Path D).

---

## Path A — cookie-based (recommended, works today)

**Time: ~3 minutes. No paperwork, no waiting.**

### Step 1 — log in to medium.com

In a real browser (Chrome, Brave, Edge, Firefox), visit
[medium.com](https://medium.com) and sign in with your usual account.

### Step 2 — open DevTools → Application → Cookies

- Chrome / Brave / Edge: `Cmd-Opt-I` (mac) / `Ctrl-Shift-I` (win/linux) →
  **Application** tab → in the left sidebar expand **Cookies** → click
  **`https://medium.com`**.
- Firefox: `Cmd-Opt-I` / `Ctrl-Shift-I` → **Storage** tab →
  **Cookies** → **`https://medium.com`**.

### Step 3 — copy these cookie values

| Cookie name | Type     | Required? | What it unlocks                                         |
|-------------|----------|-----------|---------------------------------------------------------|
| `sid`       | session  | yes       | All web reads (post HTML, responses, claps, profile)    |
| `uid`       | id       | optional  | Speeds up self-lookup; `medium-ops` will resolve if missing |
| `xsrf`      | CSRF     | only for writes | Required for `post_response` / `clap_for_post` / dashboard writes |

> The `sid` value is long (~250 chars) and starts with `1:` — copy the **Value**
> column verbatim. Don't include `sid=` or quotes.

### Step 4 — paste into `~/.cursor/mcp.json`

```json
{
  "mcpServers": {
    "medium-ops": {
      "command": "uvx",
      "args": ["medium-ops", "mcp", "serve"],
      "env": {
        "MEDIUM_SID": "1:...",
        "MEDIUM_UID": "abc123",
        "MEDIUM_USERNAME": "yourhandle",
        "MEDIUM_XSRF": "..."
      }
    }
  }
}
```

Or, equivalently, export them in your shell:

```bash
export MEDIUM_SID='1:...'
export MEDIUM_UID='abc123'
export MEDIUM_USERNAME='yourhandle'
export MEDIUM_XSRF='...'
```

### Step 5 — verify

```bash
uvx medium-ops auth verify
```

Expected output (cookie path active):

```json
{
  "ok": true,
  "sid": { "configured": true, "ok": true, "id": "abc123", "username": "yourhandle" },
  "integration_token": { "configured": false }
}
```

If it fails, jump to **Troubleshooting** below.

---

## Path B — Integration Token (legacy, may not be obtainable)

Medium **stopped issuing new Integration Tokens around mid-2023**. If you
already have one from before that, it still works against the v1 publish API.

### How to check if you have one

1. Visit [medium.com/me/settings/security](https://medium.com/me/settings/security).
2. Scroll to **Integration tokens**. If you see existing tokens, you can
   reuse them. If the section says no tokens / is blank, you cannot create a
   new one — use Path A instead.

### How to use it

```bash
export MEDIUM_INTEGRATION_TOKEN='2fb00...'   # the token only, no "Bearer "
```

This unlocks `publish_post`, `create_draft`, and the v1 write surface only.
For reads (post HTML, responses, claps, etc.) you still need `MEDIUM_SID`.

### Verify

```bash
uvx medium-ops auth verify
```

Expected:

```json
{
  "ok": true,
  "integration_token": { "configured": true, "ok": true, "username": "yourhandle" },
  "sid": { "configured": false }
}
```

---

## Path C — public reads only

If you just want to fetch RSS / public-page data (e.g. someone else's posts
or your own posts without authenticating), set just `MEDIUM_USERNAME`:

```bash
export MEDIUM_USERNAME='yourhandle'
```

Tools that work without any auth:

- `get_post_rss(username)` — RSS feed for any public Medium author
- public-only paths inside `get_profile`

Tools that **will fail without `MEDIUM_SID`** in this mode:

- `get_post_html`, `list_responses`, `get_clap_count`, `get_own_profile`,
  `test_connection` (the auth check itself), and every dashboard tool

This path is only useful for read-only LLM workflows over public Medium
content.

---

## Path D — Cloudflare-challenged accounts

If `auth verify` returns `403` or `503` from Medium and the response body
mentions Cloudflare or "Just a moment...", Medium has flagged your account
or your IP and is requiring a challenge cookie. Workaround:

1. In your real browser, visit medium.com and pass the challenge once
   (the page that asks "checking your browser").
2. Re-open DevTools → Application → Cookies → `https://medium.com`.
3. Copy the value of the **`cf_clearance`** cookie.
4. Set it alongside the others:

   ```bash
   export MEDIUM_CF_CLEARANCE='abc...'
   ```

The `cf_clearance` cookie is short-lived (~30 minutes to a few hours). If
calls start failing again with the same error, refresh it the same way.
This is a last-resort path and only triggered when Cloudflare is in the
loop — most users never need it.

---

## Verify (for any path)

```bash
uvx medium-ops auth verify
```

Or, from inside an MCP client (Cursor / Claude Desktop), call the
`test_connection` tool — same data, surfaced through the MCP `tools/call`
flow.

```jsonc
// Expected — cookie path:
{
  "ok": true,
  "sid": { "configured": true, "ok": true, "id": "...", "username": "..." },
  "integration_token": { "configured": false }
}

// Expected — token path:
{
  "ok": true,
  "integration_token": { "configured": true, "ok": true, "username": "..." },
  "sid": { "configured": false }
}

// Both configured — best, unlocks every tool:
{
  "ok": true,
  "sid": { "configured": true, "ok": true, ... },
  "integration_token": { "configured": true, "ok": true, ... }
}
```

---

## Troubleshooting

| Symptom                                                        | Likely cause                                            | Fix                                                                                       |
|----------------------------------------------------------------|---------------------------------------------------------|-------------------------------------------------------------------------------------------|
| `Missing Medium credentials`                                   | Neither `MEDIUM_SID` nor `MEDIUM_INTEGRATION_TOKEN` set | Set one (Path A or B)                                                                     |
| `auth verify` returns `sid.ok: false` with `status: 401`       | `sid` cookie expired or wrong                           | Re-copy from DevTools (you've probably been signed out of medium.com); verify cookie name is exactly `sid`, not `sessionId` |
| `auth verify` returns `403` with Cloudflare HTML               | Cloudflare challenge active                             | Path D — set `MEDIUM_CF_CLEARANCE`                                                        |
| `auth verify` returns `integration_token.ok: false` `status: 401` | Token revoked / expired / typo                       | If pre-2023 token: re-copy from medium.com/me/settings/security. If never had one: switch to Path A |
| `post_response` fails with `403` or `xsrf` error               | `MEDIUM_XSRF` missing or stale                          | Copy `xsrf` cookie from DevTools again (it rotates ~daily)                                |
| `MEDIUM_SID` is set but `auth verify` returns `200` empty body | Browser/UA fingerprinting                               | Already handled by `medium-ops` (uses a Chrome UA). If still failing, try Path D            |
| `tools/list` returns 0 tools or `kwargs: Field required` error | Stale `medium-ops` install (< 0.1.2)                    | `uv tool upgrade medium-ops` — version 0.1.2+ ships the FastMCP signature fix             |

---

## Security notes

1. **`MEDIUM_SID` is equivalent to your password** — anyone with this cookie
   can read and post on your account until it's invalidated.
2. **Never paste it into screenshots, PRs, issues, chat threads, or LLM
   prompts.** Treat it like an API key.
3. **Where it's safe**:
   - `~/.cursor/mcp.json` (your home dir, your machine)
   - `.env` files outside version control (add `.env` to `.gitignore`)
   - shell exports inside a session (cleared on logout)
4. **How to rotate**: sign out of medium.com from any browser tab — Medium
   invalidates the underlying session and the cookie is dead. Then sign
   in again and re-copy the new `sid`.
5. **Containers / CI**: prefer the Integration Token if you have one (it can
   be revoked individually from settings). Avoid putting `sid` into shared
   CI runners; if you must, scope it to a throwaway Medium account.

---

## Background — why is auth this messy?

Medium has two parallel authentication systems that don't share state:

- **Integration Tokens** were the official public API path
  (`api.medium.com/v1/*`). They support `createPost`, `getUser`, and
  `getPublications`, and they live as a separate row in Medium's auth tables.
  Medium quietly stopped issuing new tokens in mid-2023 — the page still
  exists at `medium.com/me/settings/security` but creates either fail or
  silently no-op for new tokens.
- **The `sid` cookie** is the same cookie medium.com itself uses for the web
  app. It's mapped to your Medium account session and it's what every
  undocumented GraphQL operation behind `medium.com/_/graphql` reads.
  Reads (post HTML, responses, clap counts, feed, profile stats, etc.) all
  flow through this surface.

`medium-ops` supports both because some teams still have legacy tokens that
work for writes, but every read tool requires `MEDIUM_SID` regardless of
whether you also have a token. That's why the "cookie-based" path is the
recommended default in 2026.

# medium-ops MCP prompt cheatsheet

After `medium-ops mcp install <host>` and a host restart, paste these into
your chat (Cursor, Claude Desktop, Claude Code).

## 1. Daily response triage

```
Use medium-ops to:
1. list my 10 most recent stories
2. for each, call get_unanswered_responses
3. show a table: post_id | response_id | author | snippet
```

## 2. Draft → preview → confirm (no API key)

```
For response_id r1 on post abc123def456:
- read it (call list_responses)
- draft a warm 1-2 sentence reply in MY voice
- call propose_reply with that draft
- show me the preview, dedup hash, and token
- WAIT for "yes" before calling confirm_reply
```

## 3. Bulk triage, my approval per item

```
get_unanswered_responses for post abc123.
For each:
- show the response body
- propose a reply
- ask "send / edit / skip?"
- on "send" call confirm_reply
- on "edit" let me rewrite, then propose_reply again
Keep a running counter.
```

## 4. Clap a watchlist's latest story

```
For each @handle in [@author1, @author2, @author3]:
- get_profile → user_id
- list_posts limit=1
- ask me yes/no
- on yes, clap_post claps=50
```

## 5. Audit what was sent

```
audit_search status=posted since=24h.
Group by mode (mcp:confirm_reply / ai_bulk / template).
Show counts.
```

## 6. Inspect dedup DB

```
dedup_status. Then audit_search status=deduped since=7d to see skips.
```

## 7. Read-only research on someone else

```
get_profile @author
list_posts --user author
posts search "ai safety"
get_post_content <id> --md
```

## 8. Publish a draft via the integration token

```
publish_post title="My story" content_markdown="# Hello\n..." publish_status=draft
# dry_run=true by default; review the payload first.
```

## 9. Per-post stats (dashboard scrape)

```
get_stats days=30. Show top 5 by fans.
```

## Style guardrails to put in the prompt

```
House style:
- Lowercase first letter unless proper noun
- 1-3 sentences max
- No emojis unless the reader used one
- No "Great point!" / "Thanks for sharing!" openers
- Reference one specific thing they wrote
- End on a question only ~30% of the time
```

"""MCP stdio server for medium-ops.

Tool registry mirrors substack-ops where possible; semantics differ for
Medium-specific concepts (responses instead of comments, claps instead of
reactions, no restacks).

reads:
  test_connection, get_own_profile, get_profile,
  list_posts, get_post, get_post_content,
  search_posts, list_responses, get_response_replies,
  get_feed, get_stats, get_clap_count, list_own_publications
writes:
  publish_post, clap_post, post_response
bulk / safety:
  bulk_draft_replies, send_approved_drafts, audit_search, dedup_status
draft loop (no API key needed):
  get_unanswered_responses, propose_reply, confirm_reply
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

_PROPOSAL_TTL = 300.0
_proposals: dict[str, dict[str, Any]] = {}


def _purge_expired() -> None:
    now = time.time()
    expired = [t for t, p in _proposals.items() if p["expires"] < now]
    for t in expired:
        _proposals.pop(t, None)


def _make_token(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


TOOLS: dict[str, dict[str, Any]] = {
    "test_connection": {
        "description": (
            "Read-only. Verify both credentials (integration token + sid cookie) and "
            "return the authed user id, username, and publication count. Call this "
            "first if other tools 401."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    "get_own_profile": {
        "description": (
            "Read-only. Return the authenticated user's full profile (username, name, "
            "bio, follower count). For other users use get_profile."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    "get_profile": {
        "description": (
            "Read-only. Any user's public profile by @username."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "Medium handle without @. e.g. 'yourhandle'.",
                }
            },
            "required": ["username"],
        },
    },
    "list_posts": {
        "description": (
            "Read-only. List recent stories by a user (default: self)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "username": {"type": "string"},
            },
        },
    },
    "get_post": {
        "description": (
            "Read-only. Story metadata (title, url, clap count, response count) "
            "by Medium post id (12+ char hex). For the body use get_post_content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"post_id": {"type": "string"}},
            "required": ["post_id"],
        },
    },
    "get_post_content": {
        "description": (
            "Read-only. Return the story body as HTML. Set as_markdown=true to "
            "convert to Markdown. Members-only stories need a member sid."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "as_markdown": {"type": "boolean", "default": False},
            },
            "required": ["post_id"],
        },
    },
    "search_posts": {
        "description": (
            "Read-only. Medium-side search across public stories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    "list_responses": {
        "description": (
            "Read-only. Top-level responses under a story (Medium's word for "
            "comments). For replies under one response use get_response_replies. "
            "For the filtered worklist use get_unanswered_responses."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["post_id"],
        },
    },
    "get_response_replies": {
        "description": (
            "Read-only. Replies under one response id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "response_id": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["response_id"],
        },
    },
    "get_feed": {
        "description": (
            "Read-only. Reader feed. tab='home' for recommended, 'following', or "
            "'tag-{slug}' (e.g. 'tag-programming')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tab": {"type": "string", "default": "home"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    "get_stats": {
        "description": (
            "Read-only. Per-post views / reads / fans for the last N days. "
            "Requires sid cookie for dashboard access."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "default": 30}},
        },
    },
    "get_clap_count": {
        "description": "Read-only. Total claps on a story.",
        "input_schema": {
            "type": "object",
            "properties": {"post_id": {"type": "string"}},
            "required": ["post_id"],
        },
    },
    "list_own_publications": {
        "description": (
            "Read-only. Publications the authed user can publish to. Requires "
            "integration token."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    "publish_post": {
        "description": (
            "WRITE. Publish a story. content_format is markdown. Defaults to "
            "publish_status='draft' so nothing goes public by accident; set to "
            "'public' or 'unlisted' to flip it. Uses the integration token and "
            "api.medium.com/v1/*. Dry-run by default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content_markdown": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "publication_id": {"type": "string", "description": "Optional — publish into a publication."},
                "publish_status": {"type": "string", "default": "draft", "enum": ["public", "draft", "unlisted"]},
                "canonical_url": {"type": "string"},
                "notify_followers": {"type": "boolean", "default": False},
                "dry_run": {"type": "boolean", "default": True},
            },
            "required": ["title", "content_markdown"],
        },
    },
    "clap_post": {
        "description": (
            "WRITE. Clap a story 1-50 times. Uses the undocumented dashboard "
            "endpoint — fragile. Dedup-protected per post. Dry-run by default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "claps": {"type": "integer", "default": 1, "minimum": 1, "maximum": 50},
                "dry_run": {"type": "boolean", "default": True},
            },
            "required": ["post_id"],
        },
    },
    "post_response": {
        "description": (
            "WRITE. Post a top-level response under a story, or a reply under a "
            "response. Uses the undocumented dashboard endpoint — fragile. For "
            "interactive drafting prefer propose_reply -> confirm_reply. Dry-run "
            "by default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "body": {"type": "string"},
                "parent_response_id": {"type": "string"},
                "dry_run": {"type": "boolean", "default": True},
            },
            "required": ["post_id", "body"],
        },
    },
    # ------- medium-ops unique tools -------
    "bulk_draft_replies": {
        "description": (
            "WRITE TO LOCAL FILE (no Medium call). Generate reply drafts for every "
            "response on a post using the daemon-path LLM. Output is JSON with "
            "action='pending'; edit to 'approved', then run send_approved_drafts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "out": {"type": "string", "default": "drafts.json"},
                "model": {"type": "string"},
            },
            "required": ["post_id"],
        },
    },
    "send_approved_drafts": {
        "description": (
            "WRITE. Sequentially post every entry in drafts.json where "
            "action=='approved'. Honors rate_seconds throttle. Dry-run by default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "drafts_path": {"type": "string"},
                "dry_run": {"type": "boolean", "default": True},
                "rate_seconds": {"type": "number", "default": 30},
                "force": {"type": "boolean", "default": False},
            },
            "required": ["drafts_path"],
        },
    },
    "audit_search": {
        "description": (
            "Read-only. Query the local audit.jsonl of every write this server has "
            "performed or attempted. Filters compose with AND."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "target": {"type": "string"},
                "status": {"type": "string", "enum": ["posted", "error", "dry_run", "deduped"]},
                "since": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    "dedup_status": {
        "description": "Read-only. Counts from the local dedup SQLite DB.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ------- MCP-native draft loop -------
    "get_unanswered_responses": {
        "description": (
            "Read-only. Return responses on a post where the authed user has NOT "
            "yet replied. Canonical worklist: read each, draft a reply in your "
            "context, then propose_reply -> confirm_reply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["post_id"],
        },
    },
    "propose_reply": {
        "description": (
            "STAGE A WRITE (no Medium call yet). Validate a reply, compute its "
            "dedup hash, store it under a token, return token + preview. On "
            "approval call confirm_reply. Tokens expire in 5 minutes. Set "
            "parent_response_id to reply under a specific response; omit for a "
            "top-level response on the post."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "parent_response_id": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["post_id", "body"],
        },
    },
    "confirm_reply": {
        "description": (
            "EXECUTE the staged write. Look up the token, post to Medium, log to "
            "audit.jsonl, persist dedup row. Idempotent. Tokens are single-use."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "force": {"type": "boolean", "default": False},
            },
            "required": ["token"],
        },
    },
}


def list_tool_names() -> list[str]:
    return list(TOOLS.keys())


def _dispatch(name: str, args: dict[str, Any]) -> Any:
    if name not in TOOLS:
        raise ValueError(f"unknown tool: {name}")

    from medium_ops.audit import search_audit
    from medium_ops.client import MediumClient
    from medium_ops.dedup import DedupDB

    if name == "audit_search":
        return search_audit(
            kind=args.get("kind"),
            target=args.get("target"),
            status=args.get("status"),
            since=args.get("since"),
            limit=args.get("limit", 50),
        )
    if name == "dedup_status":
        return DedupDB().status()

    if name == "propose_reply":
        return _propose_reply(args)
    if name == "confirm_reply":
        return _confirm_reply(args)

    if name == "bulk_draft_replies":
        from medium_ops.reply_engine.ai_bulk import generate_drafts

        out = Path(args.get("out") or "drafts.json")
        n = generate_drafts(post_id=args["post_id"], out=out, model=args.get("model"))
        return {"drafts": n, "path": str(out)}

    if name == "send_approved_drafts":
        from medium_ops.reply_engine.ai_bulk import send_drafts

        return send_drafts(
            drafts_path=Path(args["drafts_path"]),
            dry_run=args.get("dry_run", True),
            rate_seconds=args.get("rate_seconds", 30.0),
            force=args.get("force", False),
        )

    with MediumClient.create() as c:
        if name == "test_connection":
            return c.get_my_profile()
        if name == "get_own_profile":
            return c.get_my_profile()
        if name == "get_profile":
            return c.get_profile(args["username"])
        if name == "list_posts":
            return c.list_posts(limit=args.get("limit", 20), username=args.get("username"))
        if name == "get_post":
            return c.get_post(args["post_id"])
        if name == "get_post_content":
            html = c.get_post_content(args["post_id"])
            if html and args.get("as_markdown"):
                from markdownify import markdownify

                return markdownify(html, heading_style="ATX")
            return html
        if name == "search_posts":
            return c.search_posts(query=args["query"], limit=args.get("limit", 10))
        if name == "list_responses":
            return c.list_responses(args["post_id"], limit=args.get("limit", 50))
        if name == "get_response_replies":
            return c.get_response_replies(args["response_id"], limit=args.get("limit", 50))
        if name == "get_feed":
            return c.get_feed(tab=args.get("tab", "home"), limit=args.get("limit", 20))
        if name == "get_stats":
            return c.get_stats(days=args.get("days", 30))
        if name == "get_clap_count":
            return c.get_clap_count(args["post_id"])
        if name == "list_own_publications":
            return c.list_own_publications()
        if name == "publish_post":
            return c.publish_post(
                title=args["title"],
                content_markdown=args["content_markdown"],
                tags=args.get("tags"),
                publication_id=args.get("publication_id"),
                publish_status=args.get("publish_status", "draft"),
                canonical_url=args.get("canonical_url"),
                notify_followers=args.get("notify_followers", False),
                dry_run=args.get("dry_run", True),
            )
        if name == "clap_post":
            from medium_ops.reply_engine.base import post_clap

            return post_clap(
                c,
                post_id=args["post_id"],
                claps=args.get("claps", 1),
                dry_run=args.get("dry_run", True),
                mode="mcp:clap_post",
            )
        if name == "post_response":
            from medium_ops.reply_engine.base import post_response

            return post_response(
                c,
                post_id=args["post_id"],
                parent_response_id=args.get("parent_response_id"),
                body=args["body"],
                dry_run=args.get("dry_run", True),
                mode="mcp:post_response",
            )
        if name == "get_unanswered_responses":
            my = c.get_my_profile()
            my_id = my.get("id")
            out: list[dict[str, Any]] = []

            def _has_my_reply(parent_id: str) -> bool:
                for child in c.get_response_replies(parent_id):
                    if (child.get("creator") or {}).get("id") == my_id:
                        return True
                return False

            for r in c.list_responses(args["post_id"], limit=args.get("limit", 50)):
                rid = r.get("id")
                if not rid:
                    continue
                if (r.get("creator") or {}).get("id") == my_id:
                    continue
                if _has_my_reply(rid):
                    continue
                out.append(
                    {
                        "id": rid,
                        "subtitle": ((r.get("previewContent") or {}).get("subtitle")),
                        "creator": r.get("creator"),
                        "createdAt": r.get("createdAt"),
                        "clapCount": r.get("clapCount"),
                    }
                )
            return out[: args.get("limit", 50)]

    raise ValueError(f"unknown tool: {name}")


def _propose_reply(args: dict[str, Any]) -> dict[str, Any]:
    _purge_expired()
    post_id = args.get("post_id")
    body = args["body"]
    if not post_id:
        raise ValueError("propose_reply requires post_id")
    payload = {
        "post_id": str(post_id),
        "parent_response_id": (
            str(args["parent_response_id"]) if args.get("parent_response_id") else None
        ),
        "body": body,
    }
    token = _make_token(payload)
    _proposals[token] = {
        "payload": payload,
        "expires": time.time() + _PROPOSAL_TTL,
        "created": time.time(),
    }
    return {"token": token, "expires_in": int(_PROPOSAL_TTL), "preview": payload}


def _confirm_reply(args: dict[str, Any]) -> dict[str, Any]:
    _purge_expired()
    token = args["token"]
    proposal = _proposals.get(token)
    if not proposal:
        raise ValueError(
            f"unknown or expired token: {token} (proposals expire after "
            f"{int(_PROPOSAL_TTL)}s)"
        )
    payload = proposal["payload"]
    force = bool(args.get("force", False))

    from medium_ops.client import MediumClient
    from medium_ops.reply_engine.base import post_response

    with MediumClient.create() as c:
        res = post_response(
            c,
            post_id=payload["post_id"],
            parent_response_id=payload.get("parent_response_id"),
            body=payload["body"],
            dry_run=False,
            force=force,
            mode="mcp:confirm_reply",
        )

    _proposals.pop(token, None)
    return {"token": token, "result": res}


def serve() -> None:
    """Run an MCP stdio server. Falls back to a minimal JSON-line dispatcher."""
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore[import-untyped]
    except ImportError:
        _fallback_dispatcher()
        return

    server = FastMCP("medium-ops")
    for name, spec in TOOLS.items():
        _register(server, name, spec)
    server.run()


def _register(server: Any, name: str, spec: dict[str, Any]) -> None:
    @server.tool(name=name, description=spec["description"])
    def _tool(**kwargs: Any) -> Any:
        return _dispatch(name, kwargs)

    return _tool  # type: ignore[no-any-return]


def _fallback_dispatcher() -> None:
    """One JSON request per stdin line, one JSON response per stdout line.

    Request:  {"tool": "list_posts", "args": {"limit": 5}}
    Response: {"ok": true, "result": [...]} / {"ok": false, "error": "..."}
    Special:  {"tool": "__list__"} returns tool names.
    """
    import sys

    sys.stderr.write(
        "[medium-ops mcp] running fallback dispatcher (install `mcp` SDK for stdio MCP).\n"
    )
    sys.stderr.flush()
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(json.dumps({"ok": False, "error": f"bad json: {exc}"}))
            sys.stdout.flush()
            continue
        tool = req.get("tool")
        if tool == "__list__":
            print(json.dumps({"ok": True, "result": list_tool_names()}))
            sys.stdout.flush()
            continue
        try:
            result = _dispatch(tool, req.get("args") or {})
            print(json.dumps({"ok": True, "result": result}, default=str, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": repr(exc)}))
        sys.stdout.flush()


if os.environ.get("MEDIUM_OPS_MCP_DEBUG"):
    print("[mcp] tools:", ", ".join(TOOLS.keys()))

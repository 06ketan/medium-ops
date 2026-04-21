"""Bulk drafts.json workflow.

Step 1: `reply bulk <post_id> --out drafts.json`
        — generates drafts for every response, written to a JSON file you edit.

Step 2: edit drafts.json, change `"action": "pending"` to `"approved"`
        (or `"skip"`) for each item. Optionally tweak `"draft"`.

Step 3: `reply bulk-send drafts.json`
        — posts only items where action == "approved".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from medium_ops.client import MediumClient
from medium_ops.llm import LLM
from medium_ops.reply_engine.base import (
    RateLimiter,
    post_response,
    walk_responses,
)


def generate_drafts(
    *,
    post_id: str,
    out: Path,
    model: str | None = None,
) -> int:
    llm = LLM.from_env(model)
    if llm.provider == "none":
        raise RuntimeError(
            "ai_bulk needs an LLM. Install claude/cursor-agent/codex on PATH "
            "or set MEDIUM_OPS_LLM_CMD='your-cli {prompt}'."
        )

    drafts: list[dict[str, Any]] = []
    with MediumClient.create() as c:
        post_meta = c.get_post(post_id)
        post_title = post_meta.get("title")
        my = c.get_my_profile()
        my_id = my.get("id")
        for ref in walk_responses(c, post_id, skip_self_id=my_id):
            try:
                draft = llm.draft(
                    comment_body=ref.body,
                    comment_author=ref.author,
                    post_title=post_title,
                )
            except Exception as exc:  # noqa: BLE001
                draft = f"<LLM error: {exc}>"
            drafts.append(
                {
                    "kind": "response",
                    "response_id": ref.response_id,
                    "post_id": post_id,
                    "author": ref.author,
                    "depth": ref.depth,
                    "original": ref.body,
                    "draft": draft,
                    "action": "pending",
                }
            )

    out.write_text(json.dumps(drafts, indent=2, ensure_ascii=False))
    return len(drafts)


def send_drafts(
    *,
    drafts_path: Path,
    dry_run: bool,
    rate_seconds: float,
    force: bool = False,
) -> dict[str, int]:
    drafts = json.loads(drafts_path.read_text())
    counts = {
        "approved": 0,
        "skipped": 0,
        "pending": 0,
        "posted": 0,
        "deduped": 0,
        "errors": 0,
    }

    with MediumClient.create() as c:
        limiter = RateLimiter(seconds=rate_seconds)
        for d in drafts:
            action = (d.get("action") or "pending").lower()
            counts[action] = counts.get(action, 0) + 1
            if action != "approved":
                continue
            limiter.wait()
            try:
                res = post_response(
                    c,
                    post_id=d["post_id"],
                    parent_response_id=d.get("response_id"),
                    body=d["draft"],
                    dry_run=dry_run,
                    mode="ai_bulk",
                    original_author=d.get("author"),
                    original_body=d.get("original"),
                    force=force,
                )
                if isinstance(res, dict) and res.get("_deduped"):
                    counts["deduped"] += 1
                    continue
                counts["posted"] += 1
            except Exception:  # noqa: BLE001
                counts["errors"] += 1
    return counts

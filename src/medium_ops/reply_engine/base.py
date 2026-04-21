"""Shared primitives: response iteration, rate limiting, audit logging, posting."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from medium_ops.client import MediumClient

AUDIT_PATH = Path(".cache") / "audit.jsonl"


@dataclass
class ResponseRef:
    """A flat handle on one response (or reply) in a post's response tree."""

    post_id: str
    response_id: str
    parent_id: str | None
    author: str
    author_id: str | None
    body: str
    depth: int
    raw: dict[str, Any]

    @property
    def short(self) -> str:
        b = (self.body or "").replace("\n", " ").strip()
        return f"#{self.response_id} {self.author}: {b[:120]}"


def walk_responses(
    client: MediumClient,
    post_id: str,
    *,
    skip_self_id: str | None = None,
) -> Iterator[ResponseRef]:
    """Yield every response + one level of replies, flattened."""
    for r in client.walk_responses(post_id, skip_user_id=skip_self_id):
        author = (r.get("creator") or {}).get("name") or "?"
        author_id = (r.get("creator") or {}).get("id")
        body = ((r.get("previewContent") or {}).get("subtitle")) or ""
        yield ResponseRef(
            post_id=post_id,
            response_id=str(r.get("id") or ""),
            parent_id=str(r.get("parent_id")) if r.get("parent_id") else None,
            author=author,
            author_id=author_id,
            body=body,
            depth=int(r.get("depth") or 0),
            raw=r,
        )


@dataclass
class RateLimiter:
    """Token-bucket-ish: at most 1 op per `seconds` with jitter."""

    seconds: float = 30.0
    jitter: float = 5.0
    _last: float = field(default=0.0, init=False)

    def wait(self) -> float:
        now = time.monotonic()
        gap = (self.seconds + random.uniform(0, self.jitter)) - (now - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()
        return max(gap, 0.0)


def audit_log(record: dict[str, Any], path: Path = AUDIT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def post_response(
    client: MediumClient,
    *,
    post_id: str,
    parent_response_id: str | None,
    body: str,
    dry_run: bool,
    mode: str,
    original_author: str | None = None,
    original_body: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Single point of egress for any Medium response / reply.

    Dedup target keys:
      - top-level response :  f"post:{post_id}:response"
      - reply to response  :  f"response:{parent_response_id}:reply"
    """
    from medium_ops.dedup import DedupDB, DuplicateActionError

    if parent_response_id:
        target = f"response:{parent_response_id}:reply"
        action = "response_reply"
    else:
        target = f"post:{post_id}:response"
        action = "post_response"

    if not dry_run:
        try:
            DedupDB().check(target_id=target, action=action, force=force)
        except DuplicateActionError as exc:
            audit_log(
                {
                    "mode": mode,
                    "dry_run": False,
                    "post_id": post_id,
                    "parent_id": parent_response_id,
                    "result_status": "deduped",
                    "reply_body": body,
                    "error": str(exc),
                }
            )
            return {"_deduped": True, "error": str(exc)}

    result = client.post_response(
        post_id=post_id,
        body_markdown=body,
        parent_response_id=parent_response_id,
        dry_run=dry_run,
    )

    status = "dry_run" if dry_run else "posted"
    if not dry_run:
        DedupDB().record(target_id=target, action=action)

    audit_log(
        {
            "mode": mode,
            "dry_run": dry_run,
            "post_id": post_id,
            "parent_id": parent_response_id,
            "original_author": original_author,
            "original_body": (original_body or "")[:500],
            "reply_body": body,
            "result_status": status,
            "result": {k: v for k, v in result.items() if k in ("id", "createdAt")} if not dry_run else None,
        }
    )
    return result


def post_clap(
    client: MediumClient,
    *,
    post_id: str,
    claps: int,
    dry_run: bool,
    mode: str,
    force: bool = False,
) -> dict[str, Any]:
    from medium_ops.dedup import DedupDB, DuplicateActionError

    target = f"post:{post_id}:clap"
    if not dry_run:
        try:
            DedupDB().check(target_id=target, action="clap", force=force)
        except DuplicateActionError as exc:
            audit_log(
                {"mode": mode, "dry_run": False, "post_id": post_id,
                 "result_status": "deduped", "error": str(exc)}
            )
            return {"_deduped": True, "error": str(exc)}

    result = client.clap_post(post_id=post_id, claps=claps, dry_run=dry_run)
    status = "dry_run" if dry_run else "posted"
    if not dry_run:
        DedupDB().record(target_id=target, action="clap")

    audit_log(
        {"mode": mode, "dry_run": dry_run, "post_id": post_id,
         "claps": claps, "result_status": status}
    )
    return result

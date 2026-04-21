"""Template-based replies driven by YAML rules.

Rule file shape:

    rules:
      - name: thanks
        match:
          any: ["thank", "thanks", "appreciate"]
        replies:
          - "Thanks for reading!"
          - "Glad it landed for you."
      - name: default
        match: {any: ["*"]}
        replies:
          - "Appreciate you responding."
"""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any

import yaml

from medium_ops.client import MediumClient
from medium_ops.reply_engine.base import (
    RateLimiter,
    post_response,
    walk_responses,
)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def load_rules(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text())
    return data.get("rules", [])


def pick_reply(rules: list[dict[str, Any]], body: str) -> tuple[str, str] | None:
    text = (body or "").lower()
    for rule in rules:
        match = rule.get("match") or {}
        terms = match.get("any") or []
        if "*" in terms:
            replies = rule.get("replies") or []
            if replies:
                return rule.get("name", "default"), random.choice(replies)
            continue
        for term in terms:
            if re.search(re.escape(term.lower()), text):
                replies = rule.get("replies") or []
                if replies:
                    return rule.get("name"), random.choice(replies)
    return None


def run_template(
    *,
    post_id: str,
    template_name: str,
    dry_run: bool,
    rate_seconds: float,
) -> list[dict[str, Any]]:
    template_path = TEMPLATES_DIR / f"{template_name}.yaml"
    if not template_path.exists():
        raise FileNotFoundError(
            f"Template '{template_name}' not found at {template_path}. "
            f"Available: {[p.stem for p in TEMPLATES_DIR.glob('*.yaml')]}"
        )
    rules = load_rules(template_path)

    results: list[dict[str, Any]] = []
    with MediumClient.create() as c:
        my = c.get_my_profile()
        my_id = my.get("id")
        limiter = RateLimiter(seconds=rate_seconds)
        for ref in walk_responses(c, post_id, skip_self_id=my_id):
            picked = pick_reply(rules, ref.body)
            if not picked:
                continue
            rule_name, reply = picked
            limiter.wait()
            r = post_response(
                c,
                post_id=post_id,
                parent_response_id=ref.response_id,
                body=reply,
                dry_run=dry_run,
                mode=f"template:{rule_name}",
                original_author=ref.author,
                original_body=ref.body,
            )
            results.append(
                {"response_id": ref.response_id, "rule": rule_name, "reply": reply, "result": r}
            )
    return results

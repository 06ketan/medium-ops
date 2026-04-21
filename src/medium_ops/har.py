"""HAR (HTTP Archive) ingestion for Medium.

Use case: when Medium changes their GraphQL/dashboard schema and our
hand-rolled queries break, the user can:

    1. open medium.com in Chrome devtools
    2. perform the failing action (e.g. publish a draft)
    3. right-click in Network → "Save all as HAR with content"
    4. run `medium-ops auth har ./medium.har`

This:
    - extracts the live `sid`/`uid`/`xsrf`/`cf_clearance` cookies and writes
      them to `.env` (preserving anything else in the file)
    - dumps every Medium GraphQL operation seen + its request/response shape
      to `.cache/har-snapshot.json` so we can diff it against what
      `client.py` currently sends — that's how you spot drift without
      re-probing from scratch
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

XSSI_PREFIX = "])}while(1);</x>"

INTERESTING_COOKIES = {"sid", "uid", "xsrf", "cf_clearance"}


@dataclass
class GraphQLOp:
    operation: str
    url: str
    method: str
    request_keys: list[str] = field(default_factory=list)
    request_variables: dict[str, Any] | None = None
    response_keys: list[str] = field(default_factory=list)
    response_errors: list[str] = field(default_factory=list)
    status: int | None = None


@dataclass
class DashboardOp:
    path: str
    method: str
    status: int | None
    request_body_keys: list[str] = field(default_factory=list)
    response_value_keys: list[str] = field(default_factory=list)


@dataclass
class HarSnapshot:
    cookies: dict[str, str]
    graphql: list[GraphQLOp]
    dashboard: list[DashboardOp]
    skipped: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "cookies": {
                k: ("***" + v[-6:]) if len(v) > 12 else "***"
                for k, v in self.cookies.items()
            },
            "graphql": [op.__dict__ for op in self.graphql],
            "dashboard": [op.__dict__ for op in self.dashboard],
            "skipped": self.skipped,
        }


def _strip_xssi(text: str) -> str:
    if text.startswith(XSSI_PREFIX):
        return text[len(XSSI_PREFIX):].lstrip()
    return text


def _safe_json(text: str) -> Any | None:
    if not text:
        return None
    try:
        return json.loads(_strip_xssi(text))
    except (json.JSONDecodeError, ValueError):
        return None


def _keys(obj: Any) -> list[str]:
    if isinstance(obj, dict):
        return sorted(obj.keys())
    return []


def _is_medium_host(host: str) -> bool:
    return host == "medium.com" or host.endswith(".medium.com")


def parse_har(path: Path) -> HarSnapshot:
    """Parse a HAR file and extract Medium-relevant artefacts."""
    raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    entries = raw.get("log", {}).get("entries", [])

    cookies: dict[str, str] = {}
    graphql: dict[str, GraphQLOp] = {}
    dashboard: dict[tuple[str, str], DashboardOp] = {}
    skipped = 0

    for entry in entries:
        req = entry.get("request") or {}
        res = entry.get("response") or {}
        url = req.get("url") or ""
        if not url:
            skipped += 1
            continue
        parsed = urlparse(url)
        if not _is_medium_host(parsed.hostname or ""):
            skipped += 1
            continue

        for c in req.get("cookies", []) or []:
            name = c.get("name")
            value = c.get("value")
            if name in INTERESTING_COOKIES and value:
                cookies[name] = value

        post_data = req.get("postData") or {}
        req_text = post_data.get("text") or ""
        req_json = _safe_json(req_text)

        res_content = res.get("content") or {}
        res_text = res_content.get("text") or ""
        res_json = _safe_json(res_text)

        path_lower = parsed.path.lower()

        if path_lower == "/_/graphql":
            op_name = (
                (req_json or {}).get("operationName")
                if isinstance(req_json, dict)
                else None
            )
            if not op_name:
                op_name = "(anonymous)"
            data = (res_json or {}).get("data") if isinstance(res_json, dict) else None
            errors = (res_json or {}).get("errors") if isinstance(res_json, dict) else None
            error_msgs = []
            if isinstance(errors, list):
                for e in errors:
                    if isinstance(e, dict) and e.get("message"):
                        error_msgs.append(str(e["message"])[:200])
            variables = (req_json or {}).get("variables") if isinstance(req_json, dict) else None
            graphql[op_name] = GraphQLOp(
                operation=op_name,
                url=url,
                method=req.get("method") or "POST",
                request_keys=_keys(variables),
                request_variables=variables if isinstance(variables, dict) else None,
                response_keys=_keys(data),
                response_errors=error_msgs,
                status=res.get("status"),
            )
            continue

        is_dashboard = path_lower.startswith("/_/api/") or (
            "/p/" in path_lower and path_lower.endswith("/deltas")
        )
        if is_dashboard:
            method = (req.get("method") or "GET").upper()
            key = (method, parsed.path)
            payload = (
                (res_json or {}).get("payload") if isinstance(res_json, dict) else None
            )
            value_keys: list[str] = []
            if isinstance(payload, dict):
                value = payload.get("value", payload)
                value_keys = _keys(value)
            dashboard[key] = DashboardOp(
                path=parsed.path,
                method=method,
                status=res.get("status"),
                request_body_keys=_keys(req_json),
                response_value_keys=value_keys,
            )
            continue

        skipped += 1

    return HarSnapshot(
        cookies=cookies,
        graphql=sorted(graphql.values(), key=lambda o: o.operation),
        dashboard=sorted(dashboard.values(), key=lambda o: (o.path, o.method)),
        skipped=skipped,
    )


def write_env(cookies: dict[str, str], env_path: Path) -> list[str]:
    """Merge discovered cookies into an .env file. Returns list of keys updated."""
    mapping = {
        "sid": "MEDIUM_SID",
        "uid": "MEDIUM_UID",
        "xsrf": "MEDIUM_XSRF",
        "cf_clearance": "MEDIUM_CF_CLEARANCE",
    }
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()

    updated: list[str] = []
    for cookie_name, env_key in mapping.items():
        val = cookies.get(cookie_name)
        if not val:
            continue
        line = f"{env_key}={val}"
        for i, existing in enumerate(lines):
            if existing.startswith(f"{env_key}="):
                if existing != line:
                    lines[i] = line
                    updated.append(env_key)
                break
        else:
            lines.append(line)
            updated.append(env_key)

    env_path.write_text("\n".join(lines) + "\n")
    return updated


def write_snapshot(snapshot: HarSnapshot, snapshot_path: Path) -> None:
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n")

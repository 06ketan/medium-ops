"""Auth bridge: read Medium creds from ~/.cursor/mcp.json (or env).

Medium has two independent auth layers and we support both:

1. **Integration Token** — Authorization: Bearer <token>. Only works against
   api.medium.com/v1/* (createPost, getUser, getPublications). Medium
   effectively stopped issuing new tokens in 2023; if you already have one
   it still works.

2. **sid cookie** — used by the medium.com web app. Required for reads of
   post content, responses, claps, feed, and user stats (all via the
   undocumented GraphQL at medium.com/_/graphql).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

DEFAULT_MCP_PATH = Path.home() / ".cursor" / "mcp.json"
DEFAULT_COOKIES_PATH = Path(".cache") / "cookies.json"


@dataclass(frozen=True)
class MediumConfig:
    integration_token: str | None
    sid: str | None
    uid: str | None
    username: str | None
    xsrf: str | None = None
    cf_clearance: str | None = None

    @property
    def has_writes(self) -> bool:
        return bool(self.integration_token)

    @property
    def has_reads(self) -> bool:
        return bool(self.sid)

    @property
    def has_dashboard_writes(self) -> bool:
        """Dashboard write paths (post_response, clap, draft, publish via
        sid) require both `sid` and `xsrf`."""
        return bool(self.sid) and bool(self.xsrf)


class AuthError(RuntimeError):
    pass


def _strip_jsonc(text: str) -> str:
    return re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)


def _read_mcp_env(mcp_path: Path) -> dict[str, str]:
    if not mcp_path.exists():
        return {}
    try:
        raw = _strip_jsonc(mcp_path.read_text())
        data = json.loads(raw)
    except Exception as exc:
        raise AuthError(f"could not parse {mcp_path}: {exc}") from exc

    # Support either 'medium-api' or 'medium-ops' server key.
    for key in ("medium-ops", "medium-api", "medium"):
        server = data.get("mcpServers", {}).get(key, {})
        env = server.get("env", {}) or {}
        if env:
            return env
    return {}


def load_config(mcp_path: Path | None = None) -> MediumConfig:
    """Load Medium config from env first, then mcp.json fallback.

    At least one of (integration_token, sid) is required. Missing fields are
    allowed — the client will error only when an op needs a missing credential.
    """
    load_dotenv()

    mcp_path = mcp_path or Path(os.environ.get("MEDIUM_OPS_MCP_PATH", str(DEFAULT_MCP_PATH)))
    mcp_env = _read_mcp_env(mcp_path)

    def pick(key: str) -> str | None:
        return os.environ.get(key) or mcp_env.get(key)

    token = pick("MEDIUM_INTEGRATION_TOKEN")
    sid = pick("MEDIUM_SID")
    uid = pick("MEDIUM_UID")
    username = pick("MEDIUM_USERNAME")
    xsrf = pick("MEDIUM_XSRF")
    cf_clearance = pick("MEDIUM_CF_CLEARANCE")

    if not token and not sid:
        raise AuthError(
            "Missing Medium credentials: need at least MEDIUM_INTEGRATION_TOKEN "
            "(writes) or MEDIUM_SID (reads). "
            f"Checked env and {mcp_path}."
        )

    return MediumConfig(
        integration_token=token,
        sid=sid,
        uid=str(uid) if uid else None,
        username=username,
        xsrf=xsrf,
        cf_clearance=cf_clearance,
    )


def write_cookies(cfg: MediumConfig, cookies_path: Path | None = None) -> Path:
    """Persist sid cookie so httpx / playwright can reload it.

    Shape mirrors substack-ops for parity.
    """
    if not cfg.sid:
        raise AuthError("cannot write cookies: MEDIUM_SID not configured")

    cookies_path = cookies_path or DEFAULT_COOKIES_PATH
    cookies_path.parent.mkdir(parents=True, exist_ok=True)

    cookies: list[dict[str, Any]] = [
        {
            "name": "sid",
            "value": cfg.sid,
            "domain": ".medium.com",
            "path": "/",
            "secure": True,
        },
    ]
    if cfg.uid:
        cookies.append({
            "name": "uid",
            "value": cfg.uid,
            "domain": ".medium.com",
            "path": "/",
            "secure": True,
        })
    cookies_path.write_text(json.dumps(cookies, indent=2))
    try:
        os.chmod(cookies_path, 0o600)
    except OSError:
        pass
    return cookies_path


def verify(mcp_path: Path | None = None) -> dict[str, Any]:
    """Hit known authed endpoints and confirm both credential paths.

    Integration token path:  GET api.medium.com/v1/me
    sid cookie path:         GraphQL `viewer` on medium.com/_/graphql
    """
    cfg = load_config(mcp_path)

    out: dict[str, Any] = {
        "ok": False,
        "integration_token": {"configured": bool(cfg.integration_token)},
        "sid": {"configured": bool(cfg.sid)},
    }

    headers_common = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
    }

    with httpx.Client(timeout=20, follow_redirects=True) as client:
        if cfg.integration_token:
            r = client.get(
                "https://api.medium.com/v1/me",
                headers={
                    **headers_common,
                    "Authorization": f"Bearer {cfg.integration_token}",
                    "Accept": "application/json",
                },
            )
            tok_ok = r.status_code == 200
            data = r.json().get("data", {}) if tok_ok else {}
            out["integration_token"].update({
                "status": r.status_code,
                "ok": tok_ok,
                "id": data.get("id"),
                "username": data.get("username"),
                "name": data.get("name"),
                "url": data.get("url"),
            })
            if tok_ok and not cfg.username and data.get("username"):
                out["integration_token"]["resolved_username"] = data.get("username")

        if cfg.sid:
            cookies = {"sid": cfg.sid}
            if cfg.uid:
                cookies["uid"] = cfg.uid
            query = """
            query Viewer {
              viewer {
                id
                username
                name
                imageId
              }
            }
            """
            r = client.post(
                "https://medium.com/_/graphql",
                cookies=cookies,
                headers={
                    **headers_common,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "graphql-operation": "Viewer",
                },
                json={"query": query, "variables": {}},
            )
            sid_ok = False
            viewer: dict[str, Any] = {}
            if r.status_code == 200:
                body = r.json() if r.content else {}
                viewer = ((body.get("data") or {}).get("viewer")) or {}
                sid_ok = bool(viewer.get("id"))
            out["sid"].update({
                "status": r.status_code,
                "ok": sid_ok,
                "id": viewer.get("id"),
                "username": viewer.get("username"),
                "name": viewer.get("name"),
            })

    tok_ok = out.get("integration_token", {}).get("ok", False)
    sid_ok = out.get("sid", {}).get("ok", False)
    out["ok"] = tok_ok or sid_ok
    out["username"] = (
        out.get("sid", {}).get("username")
        or out.get("integration_token", {}).get("username")
        or cfg.username
    )
    return out

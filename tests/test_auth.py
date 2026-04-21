"""Auth loading — env + mcp.json precedence, missing-cred errors."""

from __future__ import annotations

import json

import pytest

from medium_ops.auth import AuthError, load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in (
        "MEDIUM_INTEGRATION_TOKEN",
        "MEDIUM_SID",
        "MEDIUM_UID",
        "MEDIUM_USERNAME",
        "MEDIUM_XSRF",
        "MEDIUM_CF_CLEARANCE",
        "MEDIUM_OPS_MCP_PATH",
    ):
        monkeypatch.delenv(k, raising=False)
    # Stop load_dotenv() from reading a real .env at the repo root.
    monkeypatch.setattr("medium_ops.auth.load_dotenv", lambda *a, **kw: False)


def test_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIUM_INTEGRATION_TOKEN", "tok-env")
    monkeypatch.setenv("MEDIUM_SID", "sid-env")
    monkeypatch.setenv("MEDIUM_USERNAME", "me-env")
    monkeypatch.setenv("MEDIUM_OPS_MCP_PATH", str(tmp_path / "mcp.json"))

    cfg = load_config()
    assert cfg.integration_token == "tok-env"
    assert cfg.sid == "sid-env"
    assert cfg.username == "me-env"
    assert cfg.has_writes and cfg.has_reads


def test_mcp_json_fallback(tmp_path, monkeypatch):
    mcp = tmp_path / "mcp.json"
    mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "medium-ops": {
                        "env": {
                            "MEDIUM_INTEGRATION_TOKEN": "tok-file",
                            "MEDIUM_SID": "sid-file",
                        }
                    }
                }
            }
        )
    )
    monkeypatch.setenv("MEDIUM_OPS_MCP_PATH", str(mcp))

    cfg = load_config()
    assert cfg.integration_token == "tok-file"
    assert cfg.sid == "sid-file"


def test_mcp_json_alternative_key(tmp_path, monkeypatch):
    mcp = tmp_path / "mcp.json"
    mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "medium-api": {"env": {"MEDIUM_SID": "from-alt"}}
                }
            }
        )
    )
    monkeypatch.setenv("MEDIUM_OPS_MCP_PATH", str(mcp))
    cfg = load_config()
    assert cfg.sid == "from-alt"


def test_missing_both_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIUM_OPS_MCP_PATH", str(tmp_path / "nothing.json"))
    with pytest.raises(AuthError):
        load_config()


def test_only_token_is_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIUM_INTEGRATION_TOKEN", "t")
    monkeypatch.setenv("MEDIUM_OPS_MCP_PATH", str(tmp_path / "nothing.json"))
    cfg = load_config()
    assert cfg.has_writes
    assert not cfg.has_reads


def test_only_sid_is_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIUM_SID", "s")
    monkeypatch.setenv("MEDIUM_OPS_MCP_PATH", str(tmp_path / "nothing.json"))
    cfg = load_config()
    assert cfg.has_reads
    assert not cfg.has_writes


def test_jsonc_comments_stripped(tmp_path, monkeypatch):
    mcp = tmp_path / "mcp.json"
    mcp.write_text(
        """
        // top comment
        {
            "mcpServers": {
                "medium-ops": {
                    "env": { "MEDIUM_SID": "abc" }
                }
            }
        }
        """
    )
    monkeypatch.setenv("MEDIUM_OPS_MCP_PATH", str(mcp))
    cfg = load_config()
    assert cfg.sid == "abc"

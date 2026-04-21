"""MCP host installer — merges, dry-run, backup, already-present."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from medium_ops.mcp.install import _merge_json_config, _print_snippet, install_to_host


def test_merge_creates_file(tmp_path):
    p = tmp_path / "mcp.json"
    res = _merge_json_config(p, "medium-ops", dry_run=False)
    assert p.exists()
    data = json.loads(p.read_text())
    assert "medium-ops" in data["mcpServers"]
    assert data["mcpServers"]["medium-ops"]["args"] == ["mcp", "serve"]
    assert res["wrote"] is True


def test_merge_preserves_other_servers(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other-thing": {"command": "x", "args": []},
                }
            },
            indent=2,
        )
    )
    _merge_json_config(p, "medium-ops", dry_run=False)
    data = json.loads(p.read_text())
    assert "other-thing" in data["mcpServers"]
    assert "medium-ops" in data["mcpServers"]


def test_merge_idempotent(tmp_path):
    p = tmp_path / "mcp.json"
    _merge_json_config(p, "medium-ops", dry_run=False)
    res = _merge_json_config(p, "medium-ops", dry_run=False)
    assert res["already_present"] is True


def test_dry_run_does_not_write(tmp_path):
    p = tmp_path / "mcp.json"
    res = _merge_json_config(p, "medium-ops", dry_run=True)
    assert not p.exists()
    assert res["would_write"] is True
    assert "medium-ops" in res["snippet"]


def test_backup_created_on_write(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {}}))
    _merge_json_config(p, "medium-ops", dry_run=False)
    baks = list(tmp_path.glob("mcp.json.bak.*"))
    assert len(baks) == 1


def test_jsonc_input_parsed(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(
        """
        {
            // comment
            "mcpServers": {
                "other": { "command": "x", "args": [] },
            }
        }
        """
    )
    _merge_json_config(p, "medium-ops", dry_run=False)
    data = json.loads(p.read_text())
    assert "other" in data["mcpServers"]
    assert "medium-ops" in data["mcpServers"]


def test_print_snippet_returns_json():
    res = _print_snippet("medium-ops")
    assert "medium-ops" in res["snippet"]
    snippet = json.loads(res["snippet"])
    assert "mcpServers" in snippet


def test_install_unknown_host_raises():
    with pytest.raises(ValueError):
        install_to_host(host="nonsense", dry_run=True)


def test_install_cursor_routes(tmp_path):
    fake = tmp_path / "mcp.json"
    with patch("medium_ops.mcp.install._cursor_config_path", return_value=fake):
        install_to_host(host="cursor", dry_run=False)
    assert fake.exists()

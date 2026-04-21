"""Audit JSONL search — kind / target / status / since filters."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from medium_ops.audit import parse_duration, search_audit


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_parse_duration():
    assert parse_duration("7d") == timedelta(days=7)
    assert parse_duration("24h") == timedelta(hours=24)
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("45s") == timedelta(seconds=45)
    assert parse_duration("2w") == timedelta(weeks=2)


def test_parse_duration_invalid():
    with pytest.raises(ValueError):
        parse_duration("7x")


def test_search_kind_filter(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write(
        p,
        [
            {"mode": "ai_bulk", "result_status": "posted"},
            {"mode": "template:thanks", "result_status": "posted"},
            {"mode": "mcp:clap_post", "result_status": "dry_run"},
        ],
    )
    rows = search_audit(kind="template", path=p)
    assert len(rows) == 1
    rows = search_audit(kind="mcp", path=p)
    assert len(rows) == 1


def test_search_status_filter(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write(
        p,
        [
            {"mode": "m", "result_status": "posted"},
            {"mode": "m", "result_status": "deduped"},
        ],
    )
    rows = search_audit(status="deduped", path=p)
    assert len(rows) == 1


def test_search_target_filter(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write(
        p,
        [
            {"mode": "m", "post_id": "abc123"},
            {"mode": "m", "post_id": "xyz999"},
        ],
    )
    rows = search_audit(target="abc", path=p)
    assert len(rows) == 1


def test_search_since_filter(tmp_path):
    p = tmp_path / "audit.jsonl"
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    _write(p, [{"mode": "m", "ts": old}, {"mode": "m", "ts": new}])
    rows = search_audit(since="7d", path=p)
    assert len(rows) == 1


def test_search_empty_returns_nothing(tmp_path):
    p = tmp_path / "missing.jsonl"
    assert search_audit(path=p) == []

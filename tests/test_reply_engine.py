"""Reply engine — template matching, base.post_response dedup+audit."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from medium_ops.reply_engine import base, template


def _rules():
    return [
        {"name": "thanks", "match": {"any": ["thank"]}, "replies": ["you're welcome"]},
        {"name": "default", "match": {"any": ["*"]}, "replies": ["ok"]},
    ]


def test_template_matches_specific_before_default():
    assert template.pick_reply(_rules(), "thank you so much")[0] == "thanks"
    assert template.pick_reply(_rules(), "anything else")[0] == "default"


def test_template_case_insensitive():
    assert template.pick_reply(_rules(), "THANK")[0] == "thanks"


def test_post_response_dry_run_no_dedup(tmp_path, monkeypatch):
    """Dry-run should not touch dedup DB or make network calls."""
    monkeypatch.chdir(tmp_path)
    fake_client = MagicMock()
    fake_client.post_response.return_value = {"_dry_run": True}

    result = base.post_response(
        fake_client,
        post_id="post1",
        parent_response_id="r1",
        body="hey",
        dry_run=True,
        mode="test",
    )
    assert result["_dry_run"] is True
    assert fake_client.post_response.called

    audit_path = tmp_path / ".cache" / "audit.jsonl"
    assert audit_path.exists()
    rows = [json.loads(line) for line in audit_path.read_text().splitlines() if line]
    assert rows[0]["result_status"] == "dry_run"
    assert rows[0]["post_id"] == "post1"


def test_post_response_deduped_on_second_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake_client = MagicMock()
    fake_client.post_response.return_value = {"id": "new"}

    first = base.post_response(
        fake_client,
        post_id="post1",
        parent_response_id="r1",
        body="hi",
        dry_run=False,
        mode="test",
    )
    assert first.get("id") == "new"

    second = base.post_response(
        fake_client,
        post_id="post1",
        parent_response_id="r1",
        body="hi again",
        dry_run=False,
        mode="test",
    )
    assert second["_deduped"] is True


def test_post_response_force_bypasses_dedup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake_client = MagicMock()
    fake_client.post_response.return_value = {"id": "x"}

    base.post_response(
        fake_client,
        post_id="p",
        parent_response_id="r",
        body="a",
        dry_run=False,
        mode="test",
    )
    # force=True should ignore the existing record
    res = base.post_response(
        fake_client,
        post_id="p",
        parent_response_id="r",
        body="b",
        dry_run=False,
        mode="test",
        force=True,
    )
    assert res.get("id") == "x"
    assert fake_client.post_response.call_count == 2

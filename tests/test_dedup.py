"""Dedup DB — inserts, duplicate guard, force override, since, status."""

from __future__ import annotations

from datetime import timedelta

import pytest

from medium_ops.dedup import DedupDB, DuplicateActionError


def test_insert_and_has(tmp_path):
    db = DedupDB(tmp_path / "a.db")
    assert not db.has(target_id="post:1", action="clap")
    db.record(target_id="post:1", action="clap")
    assert db.has(target_id="post:1", action="clap")


def test_check_raises_on_duplicate(tmp_path):
    db = DedupDB(tmp_path / "a.db")
    db.record(target_id="post:1", action="clap")
    with pytest.raises(DuplicateActionError):
        db.check(target_id="post:1", action="clap")


def test_force_bypass(tmp_path):
    db = DedupDB(tmp_path / "a.db")
    db.record(target_id="post:1", action="clap")
    db.check(target_id="post:1", action="clap", force=True)


def test_different_action_allowed(tmp_path):
    db = DedupDB(tmp_path / "a.db")
    db.record(target_id="post:1", action="clap")
    db.check(target_id="post:1", action="post_response")


def test_status_counts(tmp_path):
    db = DedupDB(tmp_path / "a.db")
    db.record(target_id="post:1", action="clap")
    db.record(target_id="post:2", action="clap")
    db.record(target_id="post:3", action="post_response")
    st = db.status()
    assert st["total"] == 3
    assert st["actions"]["clap"] == 2
    assert st["actions"]["post_response"] == 1


def test_since_returns_recent(tmp_path):
    db = DedupDB(tmp_path / "a.db")
    db.record(target_id="post:1", action="clap")
    rows = db.since(timedelta(minutes=1))
    assert len(rows) == 1
    assert rows[0]["target_id"] == "post:1"

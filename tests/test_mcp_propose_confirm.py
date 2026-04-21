"""propose_reply → confirm_reply flow, token TTL, idempotency."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from medium_ops.mcp import server as mcp_server


@pytest.fixture(autouse=True)
def _clear_proposals():
    mcp_server._proposals.clear()
    yield
    mcp_server._proposals.clear()


def test_propose_returns_token_and_preview():
    res = mcp_server._propose_reply(
        {"post_id": "abc123", "parent_response_id": "def456", "body": "thanks"}
    )
    assert "token" in res
    assert res["preview"]["post_id"] == "abc123"
    assert res["preview"]["parent_response_id"] == "def456"
    assert res["preview"]["body"] == "thanks"
    assert res["expires_in"] == 300


def test_propose_without_post_id_raises():
    with pytest.raises(ValueError):
        mcp_server._propose_reply({"body": "x"})


def test_same_payload_same_token():
    a = mcp_server._propose_reply(
        {"post_id": "p1", "parent_response_id": "r1", "body": "hi"}
    )
    b = mcp_server._propose_reply(
        {"post_id": "p1", "parent_response_id": "r1", "body": "hi"}
    )
    assert a["token"] == b["token"]


def test_top_level_response_allowed():
    res = mcp_server._propose_reply({"post_id": "p1", "body": "hi"})
    assert res["preview"]["parent_response_id"] is None


def test_confirm_unknown_token_raises():
    with pytest.raises(ValueError):
        mcp_server._confirm_reply({"token": "not-a-real-token"})


def test_confirm_expired_token_raises():
    res = mcp_server._propose_reply({"post_id": "p1", "body": "x"})
    token = res["token"]
    mcp_server._proposals[token]["expires"] = time.time() - 1
    with pytest.raises(ValueError):
        mcp_server._confirm_reply({"token": token})


def test_confirm_consumes_token():
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = None

    with patch("medium_ops.client.MediumClient.create", return_value=fake_client), \
         patch("medium_ops.reply_engine.base.post_response") as post:
        post.return_value = {"id": "new-response"}

        res = mcp_server._propose_reply({"post_id": "p1", "body": "x"})
        token = res["token"]
        mcp_server._confirm_reply({"token": token})

        assert token not in mcp_server._proposals

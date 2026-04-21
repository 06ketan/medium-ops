"""Tests for the HAR ingestion module."""

from __future__ import annotations

import json
from pathlib import Path

from medium_ops.har import HarSnapshot, parse_har, write_env, write_snapshot


def _make_har(entries: list[dict]) -> dict:
    return {"log": {"version": "1.2", "creator": {"name": "test"}, "entries": entries}}


def _entry(
    url: str,
    method: str = "GET",
    cookies: list[dict] | None = None,
    req_text: str | None = None,
    res_text: str | None = None,
    status: int = 200,
) -> dict:
    return {
        "request": {
            "url": url,
            "method": method,
            "cookies": cookies or [],
            "postData": {"text": req_text} if req_text else {},
        },
        "response": {
            "status": status,
            "content": {"text": res_text or ""},
        },
    }


def test_parse_har_extracts_cookies(tmp_path: Path) -> None:
    har = _make_har(
        [
            _entry(
                "https://medium.com/_/graphql",
                method="POST",
                cookies=[
                    {"name": "sid", "value": "sid-value-12345678"},
                    {"name": "uid", "value": "uid-abc"},
                    {"name": "xsrf", "value": "xsrf-zzz"},
                    {"name": "cf_clearance", "value": "cf-clear-999"},
                    {"name": "noise", "value": "ignore"},
                ],
                req_text=json.dumps({"operationName": "FooQuery", "variables": {}}),
                res_text=json.dumps({"data": {"foo": {"id": "1"}}}),
            )
        ]
    )
    har_path = tmp_path / "test.har"
    har_path.write_text(json.dumps(har))

    snap = parse_har(har_path)
    assert snap.cookies == {
        "sid": "sid-value-12345678",
        "uid": "uid-abc",
        "xsrf": "xsrf-zzz",
        "cf_clearance": "cf-clear-999",
    }


def test_parse_har_extracts_graphql_op_with_keys(tmp_path: Path) -> None:
    har = _make_har(
        [
            _entry(
                "https://medium.com/_/graphql",
                method="POST",
                req_text=json.dumps(
                    {
                        "operationName": "PublishPostMutation",
                        "variables": {"postId": "abc", "notifyFollowers": True},
                        "query": "mutation { publishPost }",
                    }
                ),
                res_text=json.dumps({"data": {"publishPost": {"id": "abc", "mediumUrl": "u"}}}),
            )
        ]
    )
    har_path = tmp_path / "test.har"
    har_path.write_text(json.dumps(har))

    snap = parse_har(har_path)
    assert len(snap.graphql) == 1
    op = snap.graphql[0]
    assert op.operation == "PublishPostMutation"
    assert op.request_keys == ["notifyFollowers", "postId"]
    assert op.response_keys == ["publishPost"]
    assert op.response_errors == []
    assert op.status == 200


def test_parse_har_captures_graphql_errors(tmp_path: Path) -> None:
    har = _make_har(
        [
            _entry(
                "https://medium.com/_/graphql",
                method="POST",
                req_text=json.dumps({"operationName": "BadMutation", "variables": {}}),
                res_text=json.dumps(
                    {"data": None, "errors": [{"message": "Field foo is not defined"}]}
                ),
            )
        ]
    )
    har_path = tmp_path / "test.har"
    har_path.write_text(json.dumps(har))

    snap = parse_har(har_path)
    assert snap.graphql[0].response_errors == ["Field foo is not defined"]


def test_parse_har_handles_xssi_prefix(tmp_path: Path) -> None:
    xssi_body = "])}while(1);</x>" + json.dumps(
        {"success": True, "payload": {"value": {"id": "d1", "title": "T", "latestRev": 2}}}
    )
    har = _make_har(
        [
            _entry(
                "https://medium.com/p/abc/deltas",
                method="POST",
                req_text=json.dumps({"baseRev": -1, "rev": 0, "deltas": []}),
                res_text=xssi_body,
            )
        ]
    )
    har_path = tmp_path / "test.har"
    har_path.write_text(json.dumps(har))

    snap = parse_har(har_path)
    assert len(snap.dashboard) == 1
    op = snap.dashboard[0]
    assert op.path == "/p/abc/deltas"
    assert op.method == "POST"
    assert op.request_body_keys == ["baseRev", "deltas", "rev"]
    assert op.response_value_keys == ["id", "latestRev", "title"]


def test_parse_har_skips_non_medium(tmp_path: Path) -> None:
    har = _make_har(
        [
            _entry("https://google.com/", method="GET"),
            _entry("https://cdn-static-1.medium.com/asset.js", method="GET"),
            _entry(
                "https://medium.com/_/graphql",
                method="POST",
                req_text=json.dumps({"operationName": "X"}),
            ),
        ]
    )
    har_path = tmp_path / "test.har"
    har_path.write_text(json.dumps(har))

    snap = parse_har(har_path)
    assert len(snap.graphql) == 1
    assert snap.skipped >= 1  # google.com skipped


def test_write_env_creates_and_merges(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("EXISTING=keep\nMEDIUM_SID=oldval\n")

    updated = write_env({"sid": "newsid", "xsrf": "newxsrf"}, env)
    assert "MEDIUM_SID" in updated
    assert "MEDIUM_XSRF" in updated

    contents = env.read_text()
    assert "EXISTING=keep" in contents
    assert "MEDIUM_SID=newsid" in contents
    assert "MEDIUM_XSRF=newxsrf" in contents
    assert "MEDIUM_SID=oldval" not in contents


def test_write_env_idempotent(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    write_env({"sid": "samevalue"}, env)
    second = write_env({"sid": "samevalue"}, env)
    assert second == []


def test_write_snapshot_redacts_cookies(tmp_path: Path) -> None:
    snap = HarSnapshot(
        cookies={"sid": "supersecretvalue123"},
        graphql=[],
        dashboard=[],
        skipped=0,
    )
    out = tmp_path / "snap.json"
    write_snapshot(snap, out)

    data = json.loads(out.read_text())
    assert "supersecretvalue123" not in out.read_text()
    assert data["cookies"]["sid"].endswith("ue123")


def test_parse_har_handles_anonymous_graphql(tmp_path: Path) -> None:
    har = _make_har(
        [
            _entry(
                "https://medium.com/_/graphql",
                method="POST",
                req_text=json.dumps({"variables": {}, "query": "{ me { id } }"}),
                res_text=json.dumps({"data": {"me": {"id": "x"}}}),
            )
        ]
    )
    har_path = tmp_path / "test.har"
    har_path.write_text(json.dumps(har))

    snap = parse_har(har_path)
    assert snap.graphql[0].operation == "(anonymous)"


def test_parse_har_handles_malformed_json(tmp_path: Path) -> None:
    har = _make_har(
        [
            _entry(
                "https://medium.com/_/graphql",
                method="POST",
                req_text="not-json",
                res_text="also-not-json",
            )
        ]
    )
    har_path = tmp_path / "test.har"
    har_path.write_text(json.dumps(har))

    snap = parse_har(har_path)
    assert snap.graphql[0].operation == "(anonymous)"
    assert snap.graphql[0].request_keys == []
    assert snap.graphql[0].response_keys == []

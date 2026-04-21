"""MediumClient HTTP surface — GraphQL envelope, publish, clap, XSSI stripping."""

from __future__ import annotations

import httpx
import pytest

from medium_ops.auth import MediumConfig
from medium_ops.client import MediumAPIError, MediumClient, normalize_post_id


def _client_with_mock(cfg: MediumConfig, handler):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, timeout=5, follow_redirects=True)
    return MediumClient(cfg=cfg, http=http)


def test_graphql_happy_path():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "medium.com"
        assert req.url.path == "/_/graphql"
        body = req.read().decode()
        assert "Viewer" in body
        return httpx.Response(
            200,
            json={
                "data": {
                    "viewer": {"id": "u1", "username": "me", "name": "Me"}
                }
            },
        )

    cfg = MediumConfig(integration_token=None, sid="sid123", uid="u1", username=None)
    c = _client_with_mock(cfg, handler)
    prof = c.get_my_profile()
    assert prof["username"] == "me"


def test_graphql_errors_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"errors": [{"message": "nope"}]},
        )

    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username=None)
    c = _client_with_mock(cfg, handler)
    with pytest.raises(MediumAPIError):
        c.get_my_profile()


def test_graphql_requires_sid():
    cfg = MediumConfig(integration_token="t", sid=None, uid=None, username=None)
    http = httpx.Client()
    c = MediumClient(cfg=cfg, http=http)
    with pytest.raises(MediumAPIError) as exc:
        c.get_profile("paulg")
    assert exc.value.status == 401


def test_official_api_auth_header():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("authorization")
        captured["url"] = str(req.url)
        return httpx.Response(200, json={"data": {"id": "u1", "username": "me"}})

    cfg = MediumConfig(integration_token="tok-xyz", sid=None, uid=None, username=None)
    c = _client_with_mock(cfg, handler)
    out = c.get_my_profile()
    assert out["username"] == "me"
    assert captured["auth"] == "Bearer tok-xyz"
    assert "api.medium.com" in captured["url"]


def test_publish_dry_run_returns_payload():
    cfg = MediumConfig(integration_token="t", sid=None, uid=None, username=None)
    http = httpx.Client()
    c = MediumClient(cfg=cfg, http=http)
    res = c.publish_post(
        title="hello", content_markdown="# hi", tags=["a"], dry_run=True
    )
    assert res["_dry_run"] is True
    assert res["payload"]["title"] == "hello"
    assert res["payload"]["contentFormat"] == "markdown"
    assert res["payload"]["tags"] == ["a"]


def test_publish_requires_token_when_live():
    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username=None)
    http = httpx.Client()
    c = MediumClient(cfg=cfg, http=http)
    with pytest.raises(MediumAPIError):
        c.publish_post(title="t", content_markdown="x", dry_run=False)


def test_clap_dry_run():
    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username=None)
    http = httpx.Client()
    c = MediumClient(cfg=cfg, http=http)
    res = c.clap_post(post_id="abc123", claps=25, dry_run=True)
    assert res["_dry_run"] is True
    assert res["claps"] == 25


def test_dashboard_strips_xssi_prefix():
    payload = '])}while(1);</x>\n{"payload": {"ok": true}}'

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": "application/json"})

    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username=None)
    c = _client_with_mock(cfg, handler)
    res = c._dashboard(method="GET", path="/_/api/foo")
    assert res == {"ok": True}


def test_normalize_post_id_accepts_url():
    assert normalize_post_id("https://medium.com/@me/my-post-abc123def456") == "abc123def456"
    assert normalize_post_id("abc123def456") == "abc123def456"


_RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <item>
      <title>RSS Title</title>
      <link>https://medium.com/@me/rss-title-cafef00dbeef</link>
      <guid>https://medium.com/p/cafef00dbeef</guid>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
      <dc:creator>Me</dc:creator>
      <category>x</category>
      <content:encoded><![CDATA[<p>hello world from rss</p>]]></content:encoded>
    </item>
  </channel>
</rss>
"""


def test_list_posts_rss_first_skips_graphql():
    calls = {"rss": 0, "gql": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if "/feed/@" in str(req.url):
            calls["rss"] += 1
            return httpx.Response(200, content=_RSS_FIXTURE)
        if "/_/graphql" in str(req.url):
            calls["gql"] += 1
            return httpx.Response(500, json={"errors": [{"message": "should not be hit"}]})
        return httpx.Response(404)

    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username="me")
    c = _client_with_mock(cfg, handler)
    rows = c.list_posts(limit=1, source="auto")
    assert calls["rss"] == 1
    assert calls["gql"] == 0
    assert len(rows) == 1
    assert rows[0]["id"] == "cafef00dbeef"
    assert rows[0]["_source"] == "rss"


def test_list_posts_force_graphql_skips_rss():
    calls = {"rss": 0, "gql": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if "/feed/@" in str(req.url):
            calls["rss"] += 1
            return httpx.Response(200, content=_RSS_FIXTURE)
        if "/_/graphql" in str(req.url):
            calls["gql"] += 1
            return httpx.Response(
                200,
                json={
                    "data": {
                        "user": {
                            "username": "me",
                            "postsConnection": {
                                "edges": [
                                    {"node": {"id": "gql-id", "title": "gql"}}
                                ]
                            },
                        }
                    }
                },
            )
        return httpx.Response(404)

    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username="me")
    c = _client_with_mock(cfg, handler)
    rows = c.list_posts(limit=5, source="graphql")
    assert calls["rss"] == 0
    assert calls["gql"] == 1
    assert rows[0]["id"] == "gql-id"


def test_get_post_rss_hit_returns_dict():
    def handler(req: httpx.Request) -> httpx.Response:
        if "/feed/@" in str(req.url):
            return httpx.Response(200, content=_RSS_FIXTURE)
        return httpx.Response(404)

    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username="me")
    c = _client_with_mock(cfg, handler)
    p = c.get_post("cafef00dbeef")
    assert p["id"] == "cafef00dbeef"
    assert p["_source"] == "rss"


def test_get_post_content_rss_returns_html():
    def handler(req: httpx.Request) -> httpx.Response:
        if "/feed/@" in str(req.url):
            return httpx.Response(200, content=_RSS_FIXTURE)
        return httpx.Response(404)

    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username="me")
    c = _client_with_mock(cfg, handler)
    html = c.get_post_content("cafef00dbeef")
    assert html is not None
    assert "hello world from rss" in html


def test_list_posts_rss_then_graphql_when_limit_exceeds_feed():
    calls = {"rss": 0, "gql": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if "/feed/@" in str(req.url):
            calls["rss"] += 1
            return httpx.Response(200, content=_RSS_FIXTURE)
        if "/_/graphql" in str(req.url):
            calls["gql"] += 1
            return httpx.Response(
                200,
                json={
                    "data": {
                        "user": {
                            "username": "me",
                            "postsConnection": {
                                "edges": [
                                    {"node": {"id": f"id-{i}", "title": f"t-{i}"}}
                                    for i in range(50)
                                ]
                            },
                        }
                    }
                },
            )
        return httpx.Response(404)

    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username="me")
    c = _client_with_mock(cfg, handler)
    rows = c.list_posts(limit=50, source="auto")
    assert calls["rss"] == 1
    assert calls["gql"] == 1
    assert len(rows) == 50


def test_xsrf_header_injected_on_graphql():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["xsrf"] = req.headers.get("x-xsrf-token")
        return httpx.Response(200, json={"data": {"viewer": {"id": "u1", "username": "me"}}})

    cfg = MediumConfig(
        integration_token=None,
        sid="sid",
        uid=None,
        username=None,
        xsrf="xtoken-abc",
    )
    c = _client_with_mock(cfg, handler)
    c.get_my_profile()
    assert captured["xsrf"] == "xtoken-abc"


def test_xsrf_header_injected_on_dashboard():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["xsrf"] = req.headers.get("x-xsrf-token")
        return httpx.Response(200, content='{"payload": {"ok": true}}')

    cfg = MediumConfig(
        integration_token=None,
        sid="sid",
        uid=None,
        username=None,
        xsrf="xtoken-xyz",
    )
    c = _client_with_mock(cfg, handler)
    c._dashboard(method="POST", path="/_/api/foo", json_body={"bar": 1})
    assert captured["xsrf"] == "xtoken-xyz"


def test_no_xsrf_header_when_not_configured():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["xsrf"] = req.headers.get("x-xsrf-token")
        return httpx.Response(200, json={"data": {"viewer": {"id": "u1", "username": "me"}}})

    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username=None)
    c = _client_with_mock(cfg, handler)
    c.get_my_profile()
    assert captured["xsrf"] is None


def test_create_draft_dry_run():
    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username=None, xsrf="x")
    c = MediumClient(cfg=cfg, http=httpx.Client())
    out = c.create_draft(dry_run=True)
    assert out["_dry_run"] is True
    assert out["mutation"] == "createPost"


def test_publish_draft_dry_run():
    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username=None, xsrf="x")
    c = MediumClient(cfg=cfg, http=httpx.Client())
    out = c.publish_draft("abc", dry_run=True)
    assert out["_dry_run"] is True
    assert out["postId"] == "abc"


def test_delete_post_dry_run():
    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username=None, xsrf="x")
    c = MediumClient(cfg=cfg, http=httpx.Client())
    assert c.delete_post("abc", dry_run=True) is True


def test_post_response_uses_savePostResponse_with_indexed_deltas():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        body = req.read().decode()
        captured["body"] = body
        captured["op"] = req.headers.get("graphql-operation")
        return httpx.Response(
            200,
            json={"data": {"savePostResponse": {"id": "rid1", "mediumUrl": "u"}}},
        )

    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username=None, xsrf="x")
    c = _client_with_mock(cfg, handler)
    out = c.post_response(post_id="abc123", body_markdown="line one\n\nline two", dry_run=False)
    assert out["id"] == "rid1"
    assert captured["op"] == "SavePostResponseMutation"
    body = captured["body"]
    assert "savePostResponse" in body
    assert "\"type\": 1" in body or '"type":1' in body
    assert "\"index\": 0" in body or '"index":0' in body
    assert "\"index\": 1" in body or '"index":1' in body


def test_update_draft_content_dry_run_shapes_payload():
    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username=None, xsrf="x")
    c = MediumClient(cfg=cfg, http=httpx.Client())
    out = c.update_draft_content(
        "abc", title="T", body_paragraphs=["one", "two"], dry_run=True
    )
    assert out["_dry_run"] is True
    payload = out["payload"]
    assert payload["baseRev"] == -1
    assert payload["rev"] == 0
    assert len(payload["deltas"]) == 3  # title + 2 body paragraphs
    assert payload["deltas"][0]["paragraph"]["type"] == 3  # H3 = title
    assert payload["deltas"][0]["paragraph"]["text"] == "T"
    assert payload["deltas"][1]["paragraph"]["type"] == 1  # P
    assert payload["deltas"][2]["index"] == 2


def test_update_draft_content_strips_xssi_and_returns_value():
    payload = '])}while(1);</x>{"success":true,"payload":{"value":{"id":"d1","title":"T","latestRev":2}}}'

    def handler(req: httpx.Request) -> httpx.Response:
        assert "/p/abc/deltas" in str(req.url)
        return httpx.Response(200, content=payload)

    cfg = MediumConfig(integration_token=None, sid="sid", uid=None, username=None, xsrf="x")
    c = _client_with_mock(cfg, handler)
    out = c.update_draft_content("abc", title="T", dry_run=False)
    assert out["id"] == "d1"
    assert out["title"] == "T"
    assert out["latestRev"] == 2


def test_cf_clearance_cookie_set_in_jar():
    cfg = MediumConfig(
        integration_token=None,
        sid="sid",
        uid="u",
        username="me",
        xsrf="x",
        cf_clearance="cf-cookie-value",
    )
    c = MediumClient.create(cfg)
    try:
        jar = {ck.name: ck.value for ck in c.http.cookies.jar}
        assert jar.get("cf_clearance") == "cf-cookie-value"
        assert jar.get("xsrf") == "x"
        assert jar.get("sid") == "sid"
    finally:
        c.close()

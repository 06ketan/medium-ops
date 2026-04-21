"""MediumClient — hybrid httpx port.

Medium doesn't expose a rich first-party JSON API like Substack does. We
combine two transports to cover the realistic use cases:

 READS  (sid cookie → medium.com/_/graphql)
   - get_viewer                : current user + publications
   - get_user                  : public profile by @handle
   - list_posts                : your latest stories
   - get_post / get_post_md    : story metadata + body (HTML→MD)
   - search_posts              : Medium-side search
   - list_responses            : top-level responses (comments) under a post
   - get_response_replies      : replies under one response
   - get_clap_count            : claps on a post
   - get_feed                  : /tag/{slug}/recommended | /_/api/feed
   - get_stats                 : per-post views/reads/fans (last 30d)

 WRITES (integration token → api.medium.com/v1/*)
   - publish_post              : createPost / createPostInPublication
   - list_own_publications     : getPublications for this user
   - me                        : getUser (integration token)

 WRITES that Medium does NOT expose officially (we still attempt via the web
 dashboard endpoints — fragile, documented in README "Known gaps"):
   - clap_post                 : POST medium.com/_/api/posts/{id}/clap
   - post_response             : POST medium.com/_/api/responses

Every state-changing call accepts `dry_run=True` (default from CLI) and returns
the intended payload without hitting the network. See base.post_response for
the audited + deduped single-egress pattern.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from medium_ops.auth import MediumConfig, load_config
from medium_ops.rss import (
    get_post_via_rss,
    list_posts_via_rss,
)

GRAPHQL_URL = "https://medium.com/_/graphql"
API_URL = "https://api.medium.com/v1"
DASHBOARD_URL = "https://medium.com"

_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)


class MediumAPIError(RuntimeError):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"medium api {status}: {body!r}")
        self.status = status
        self.body = body


@dataclass
class MediumClient:
    cfg: MediumConfig
    http: httpx.Client = field(repr=False)
    _viewer_id: str | None = None

    # --------------------------------------------------------------------- #
    # lifecycle
    # --------------------------------------------------------------------- #
    @classmethod
    def create(cls, cfg: MediumConfig | None = None) -> MediumClient:
        cfg = cfg or load_config()
        cookies: dict[str, str] = {}
        if cfg.sid:
            cookies["sid"] = cfg.sid
        if cfg.uid:
            cookies["uid"] = cfg.uid
        if cfg.xsrf:
            cookies["xsrf"] = cfg.xsrf
        if cfg.cf_clearance:
            cookies["cf_clearance"] = cfg.cf_clearance

        headers = {
            "User-Agent": _DEFAULT_UA,
            "Accept": "application/json",
        }
        http = httpx.Client(
            headers=headers,
            cookies=cookies,
            timeout=30,
            follow_redirects=True,
        )
        return cls(cfg=cfg, http=http)

    def close(self) -> None:
        try:
            self.http.close()
        except Exception:
            pass

    def __enter__(self) -> MediumClient:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # --------------------------------------------------------------------- #
    # transports
    # --------------------------------------------------------------------- #
    def _gql(
        self,
        *,
        operation: str,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST medium.com/_/graphql with sid cookie."""
        if not self.cfg.sid:
            raise MediumAPIError(401, "GraphQL needs MEDIUM_SID cookie")

        gql_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "graphql-operation": operation,
            "Origin": DASHBOARD_URL,
            "Referer": f"{DASHBOARD_URL}/",
        }
        if self.cfg.xsrf:
            gql_headers["x-xsrf-token"] = self.cfg.xsrf
        r = self.http.post(
            GRAPHQL_URL,
            headers=gql_headers,
            json={"operationName": operation, "query": query, "variables": variables or {}},
        )
        if r.status_code != 200:
            raise MediumAPIError(r.status_code, r.text[:500])
        body = r.json()
        if "errors" in body and body["errors"]:
            raise MediumAPIError(200, body["errors"])
        return body.get("data") or {}

    def _api(
        self,
        *,
        method: str,
        path: str,
        json_body: Any = None,
    ) -> dict[str, Any]:
        """Call api.medium.com/v1/* with integration token."""
        if not self.cfg.integration_token:
            raise MediumAPIError(401, "official API needs MEDIUM_INTEGRATION_TOKEN")
        r = self.http.request(
            method,
            f"{API_URL}{path}",
            headers={
                "Authorization": f"Bearer {self.cfg.integration_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Charset": "utf-8",
            },
            json=json_body,
        )
        if r.status_code >= 400:
            raise MediumAPIError(r.status_code, r.text[:500])
        return (r.json() or {}).get("data") or {}

    def _dashboard(
        self,
        *,
        method: str,
        path: str,
        json_body: Any = None,
    ) -> dict[str, Any]:
        """Call medium.com/_/api/* — undocumented web app endpoints.

        Responses are JSON prefixed with `])}while(1);</x>` (Medium's XSSI
        prefix). We strip it before parsing.
        """
        if not self.cfg.sid:
            raise MediumAPIError(401, "dashboard needs MEDIUM_SID cookie")

        dash_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": DASHBOARD_URL,
            "Referer": f"{DASHBOARD_URL}/",
        }
        if self.cfg.xsrf:
            dash_headers["x-xsrf-token"] = self.cfg.xsrf
        r = self.http.request(
            method,
            f"{DASHBOARD_URL}{path}",
            headers=dash_headers,
            json=json_body,
        )
        if r.status_code >= 400:
            raise MediumAPIError(r.status_code, r.text[:500])
        text = r.text
        if text.startswith("])}"):
            text = text.split("\n", 1)[-1] if "\n" in text else text[16:]
        try:
            body = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MediumAPIError(r.status_code, f"bad json: {exc}") from exc
        return body.get("payload", body) or {}

    # --------------------------------------------------------------------- #
    # me / profile
    # --------------------------------------------------------------------- #
    def get_my_profile(self) -> dict[str, Any]:
        """Prefer sid (viewer is richer); fall back to integration token /me."""
        if self.cfg.sid:
            data = self._gql(
                operation="Viewer",
                query="""
                query Viewer {
                  viewer {
                    id
                    username
                    name
                    bio
                    imageId
                    followerCount
                    followingCount
                  }
                }
                """,
            )
            viewer = data.get("viewer") or {}
            if viewer:
                self._viewer_id = viewer.get("id")
                return viewer
        return self._api(method="GET", path="/me")

    def get_profile(self, username: str) -> dict[str, Any]:
        username = username.lstrip("@")
        data = self._gql(
            operation="UserProfileQuery",
            query="""
            query UserProfileQuery($username: ID!) {
              userResult(username: $username) {
                ... on User {
                  id
                  username
                  name
                  bio
                  imageId
                  followerCount
                  followingCount
                }
              }
            }
            """,
            variables={"username": username},
        )
        return data.get("userResult") or {}

    # --------------------------------------------------------------------- #
    # posts
    # --------------------------------------------------------------------- #
    def list_posts(
        self,
        *,
        limit: int = 20,
        username: str | None = None,
        source: str = "auto",
    ) -> list[dict[str, Any]]:
        """List latest stories by a user (default: self).

        source:
          - "auto" (default): RSS first (zero-auth, fast); GraphQL fallback
            on miss/error or when limit > 10 (RSS only returns ~10).
          - "rss":  force RSS only.
          - "graphql": force GraphQL (needs sid).
        """
        username = (username or self.cfg.username or "").lstrip("@")
        if not username and (source == "auto" or source == "graphql"):
            try:
                viewer = self.get_my_profile()
                username = viewer.get("username") or ""
            except MediumAPIError:
                pass
        if not username:
            raise MediumAPIError(400, "no username to query (set MEDIUM_USERNAME or pass username=...)")

        if source in ("auto", "rss"):
            try:
                rss_posts = list_posts_via_rss(username, http=self.http, limit=limit)
                if source == "rss":
                    return [p.to_dict() for p in rss_posts]
                # auto: return RSS result if it satisfies `limit`, else fall
                # through to GraphQL for full pagination.
                if rss_posts and len(rss_posts) >= limit:
                    return [p.to_dict() for p in rss_posts][:limit]
            except Exception:
                if source == "rss":
                    raise

        data = self._gql(
            operation="UserStreamOverview",
            query="""
            query UserStreamOverview($username: ID!, $first: Int!) {
              user(username: $username) {
                id
                username
                postsConnection(first: $first) {
                  edges {
                    node {
                      id
                      title
                      uniqueSlug
                      mediumUrl
                      firstPublishedAt
                      clapCount
                      postResponses { count }
                    }
                  }
                }
              }
            }
            """,
            variables={"username": username, "first": limit},
        )
        edges = (((data.get("user") or {}).get("postsConnection") or {}).get("edges")) or []
        return [e["node"] for e in edges if e and e.get("node")][:limit]

    def get_post(
        self,
        post_id: str,
        *,
        username: str | None = None,
        source: str = "auto",
    ) -> dict[str, Any]:
        """Fetch post metadata.

        source:
          - "auto": RSS first if username is known, else GraphQL.
          - "rss":  force RSS (requires username).
          - "graphql": force GraphQL.
        """
        username = (username or self.cfg.username or "").lstrip("@")

        if source in ("auto", "rss") and username:
            try:
                rss_post = get_post_via_rss(post_id, username, http=self.http)
                if rss_post is not None:
                    return rss_post.to_dict()
                if source == "rss":
                    return {}
            except Exception:
                if source == "rss":
                    raise

        data = self._gql(
            operation="PostViewer",
            query="""
            query PostViewer($postId: ID!) {
              post(id: $postId) {
                id
                title
                mediumUrl
                firstPublishedAt
                latestPublishedAt
                clapCount
                readingTime
                previewContent { subtitle }
                creator { id username name }
                postResponses { count }
              }
            }
            """,
            variables={"postId": post_id},
          )
        return data.get("post") or {}

    def get_post_content(
        self,
        post_id: str,
        *,
        username: str | None = None,
        source: str = "auto",
    ) -> str | None:
        """Return the story body HTML. Auth-aware (members-only stories need
        a member sid).

        source:
          - "auto": RSS first (gives clean HTML for free); GraphQL fallback.
          - "rss":  force RSS only.
          - "graphql": force the paragraph-tree reconstruction.
        """
        username = (username or self.cfg.username or "").lstrip("@")

        if source in ("auto", "rss") and username:
            try:
                rss_post = get_post_via_rss(post_id, username, http=self.http)
                if rss_post is not None and rss_post.body_html:
                    return rss_post.body_html
                if source == "rss":
                    return None
            except Exception:
                if source == "rss":
                    raise

        data = self._gql(
            operation="PostContent",
            query="""
            query PostContent($postId: ID!) {
              post(id: $postId) {
                title
                content {
                  bodyModel {
                    paragraphs {
                      type
                      text
                      href
                      markups { type start end href }
                    }
                  }
                }
              }
            }
            """,
            variables={"postId": post_id},
        )
        post = data.get("post") or {}
        body = ((post.get("content") or {}).get("bodyModel") or {}).get("paragraphs") or []
        if not body:
            return None
        html_parts: list[str] = []
        for p in body:
            text = p.get("text") or ""
            typ = (p.get("type") or "P").upper()
            if typ == "H1":
                html_parts.append(f"<h1>{text}</h1>")
            elif typ == "H2":
                html_parts.append(f"<h2>{text}</h2>")
            elif typ == "H3":
                html_parts.append(f"<h3>{text}</h3>")
            elif typ == "BQ" or typ == "PQ":
                html_parts.append(f"<blockquote>{text}</blockquote>")
            elif typ == "PRE":
                html_parts.append(f"<pre><code>{text}</code></pre>")
            elif typ == "ULI":
                html_parts.append(f"<li>{text}</li>")
            elif typ == "OLI":
                html_parts.append(f"<li>{text}</li>")
            elif typ == "IMG":
                html_parts.append(f'<img alt="{text}"/>')
            else:
                html_parts.append(f"<p>{text}</p>")
        return "\n".join(html_parts)

    def search_posts(self, *, query: str, limit: int = 10) -> list[dict[str, Any]]:
        data = self._gql(
            operation="SearchPosts",
            query="""
            query SearchPosts($query: String!, $paging: PagingOptions) {
              search(query: $query) {
                posts(paging: $paging) {
                  items {
                    id
                    title
                    mediumUrl
                    firstPublishedAt
                    clapCount
                    creator { id username name }
                  }
                }
              }
            }
            """,
            variables={"query": query, "paging": {"limit": limit}},
        )
        items = (((data.get("search") or {}).get("posts") or {}).get("items")) or []
        return items[:limit]

    def publish_post(
        self,
        *,
        title: str,
        content_markdown: str,
        tags: list[str] | None = None,
        publication_id: str | None = None,
        publish_status: str = "draft",
        canonical_url: str | None = None,
        license: str = "all-rights-reserved",
        notify_followers: bool = False,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Publish via official Integration Token (works without a sid)."""
        payload = {
            "title": title,
            "contentFormat": "markdown",
            "content": content_markdown,
            "tags": tags or [],
            "publishStatus": publish_status,
            "license": license,
            "notifyFollowers": notify_followers,
        }
        if canonical_url:
            payload["canonicalUrl"] = canonical_url

        if dry_run:
            return {"_dry_run": True, "payload": payload, "pub_id": publication_id}

        if publication_id:
            return self._api(
                method="POST",
                path=f"/publications/{publication_id}/posts",
                json_body=payload,
            )
        me = self._api(method="GET", path="/me")
        uid = me.get("id")
        if not uid:
            raise MediumAPIError(500, "/me did not return user id")
        return self._api(method="POST", path=f"/users/{uid}/posts", json_body=payload)

    # --------------------------------------------------------------------- #
    # draft + publish via GraphQL (no integration token needed)
    # --------------------------------------------------------------------- #
    def create_draft(self, *, dry_run: bool = True) -> dict[str, Any]:
        """Create a blank draft via the dashboard GraphQL `createPost`
        mutation. Title and body are not settable through this API directly
        (Medium's web editor saves them via deltas after the draft is open).

        Returns the new Post {id, mediumUrl, creator{username}} on success.
        """
        if dry_run:
            return {"_dry_run": True, "mutation": "createPost", "input": {}}
        if not self.cfg.sid:
            raise MediumAPIError(401, "create_draft needs MEDIUM_SID")
        data = self._gql(
            operation="CreatePostMutation",
            query="""
            mutation CreatePostMutation($input: CreatePostInput!) {
              createPost(input: $input) {
                id
                mediumUrl
                title
                creator { id username name }
              }
            }
            """,
            variables={"input": {}},
        )
        return data.get("createPost") or {}

    def update_draft_content(
        self,
        post_id: str,
        *,
        title: str | None = None,
        body_paragraphs: list[str] | None = None,
        base_rev: int = -1,
        rev: int = 0,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Set/append title + body on a draft via the editor's delta protocol.

        POST medium.com/p/{id}/deltas with `{baseRev, rev, deltas: [...]}`.
        Discovered via probe — Medium's web editor uses this for every save.

        Convention: a paragraph with `type=3` (H3/large header) becomes the
        story title. type=1 paragraphs become body text. The first valid
        title-bearing paragraph wins.

        For a brand-new draft, base_rev=-1, rev=0.
        """
        deltas: list[dict[str, Any]] = []
        idx = 0
        if title:
            deltas.append({
                "type": 1,
                "index": idx,
                "paragraph": {"type": 3, "text": title, "markups": []},
            })
            idx += 1
        for line in (body_paragraphs or []):
            if not line.strip():
                continue
            deltas.append({
                "type": 1,
                "index": idx,
                "paragraph": {"type": 1, "text": line, "markups": []},
            })
            idx += 1
        payload = {"baseRev": base_rev, "rev": rev, "deltas": deltas}

        if dry_run:
            return {"_dry_run": True, "endpoint": f"/p/{post_id}/deltas", "payload": payload}
        if not self.cfg.sid:
            raise MediumAPIError(401, "update_draft_content needs MEDIUM_SID")
        if not deltas:
            raise MediumAPIError(400, "no title or body provided")

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": DASHBOARD_URL,
            "Referer": f"{DASHBOARD_URL}/p/{post_id}/edit",
        }
        if self.cfg.xsrf:
            headers["x-xsrf-token"] = self.cfg.xsrf

        r = self.http.post(
            f"{DASHBOARD_URL}/p/{post_id}/deltas",
            headers=headers,
            json=payload,
        )
        text = r.text
        if text.startswith("])}"):
            text = text.split("\n", 1)[-1] if "\n" in text else text[16:]
        try:
            body = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MediumAPIError(r.status_code, f"bad json: {exc}") from exc
        if r.status_code >= 400 or not body.get("success"):
            raise MediumAPIError(r.status_code, body)
        return (body.get("payload") or {}).get("value") or body.get("payload") or body

    def publish_draft(self, post_id: str, *, dry_run: bool = True) -> dict[str, Any]:
        """Publish an existing draft via dashboard GraphQL `publishPost`.

        Distinct from `publish_post` (the official-API path that creates +
        publishes in one shot using an Integration Token).
        """
        if dry_run:
            return {"_dry_run": True, "mutation": "publishPost", "postId": post_id}
        if not self.cfg.sid:
            raise MediumAPIError(401, "publish_draft needs MEDIUM_SID")
        data = self._gql(
            operation="PublishPostMutation",
            query="""
            mutation PublishPostMutation($postId: ID!) {
              publishPost(postId: $postId) {
                id
                mediumUrl
                title
                latestPublishedAt
                creator { id username name }
              }
            }
            """,
            variables={"postId": post_id},
        )
        return data.get("publishPost") or {}

    def delete_post(self, post_id: str, *, dry_run: bool = True) -> bool:
        """Delete a draft or published post. Returns True on success."""
        if dry_run:
            return True
        if not self.cfg.sid:
            raise MediumAPIError(401, "delete_post needs MEDIUM_SID")
        data = self._gql(
            operation="DeletePostMutation",
            query="""
            mutation DeletePostMutation($targetPostId: ID!) {
              deletePost(targetPostId: $targetPostId)
            }
            """,
            variables={"targetPostId": post_id},
        )
        return bool(data.get("deletePost"))

    def list_own_publications(self) -> list[dict[str, Any]]:
        me = self._api(method="GET", path="/me")
        uid = me.get("id")
        if not uid:
            return []
        data = self._api(method="GET", path=f"/users/{uid}/publications")
        return data if isinstance(data, list) else []

    # --------------------------------------------------------------------- #
    # responses (Medium's word for comments)
    # --------------------------------------------------------------------- #
    def list_responses(self, post_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        data = self._gql(
            operation="PostResponses",
            query="""
            query PostResponses($postId: ID!, $paging: PagingOptions) {
              post(id: $postId) {
                id
                postResponses(paging: $paging) {
                  count
                  items {
                    id
                    uniqueSlug
                    createdAt
                    clapCount
                    creator { id username name }
                    previewContent { subtitle }
                    postResponses { count }
                  }
                }
              }
            }
            """,
            variables={"postId": post_id, "paging": {"limit": limit}},
        )
        post = data.get("post") or {}
        return ((post.get("postResponses") or {}).get("items")) or []

    def get_response_replies(self, response_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Replies under one response. Medium models responses as posts too."""
        return self.list_responses(response_id, limit=limit)

    def post_response(
        self,
        *,
        post_id: str,
        body_markdown: str,
        parent_response_id: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Write a response under a post (or a reply under a response).

        Uses the dashboard GraphQL `savePostResponse(deltas, inResponseToPostId)`
        mutation — proven via probe. Each non-empty line in `body_markdown`
        becomes a paragraph delta (type 1 = P). Markups stay empty for now.
        """
        target = parent_response_id or post_id
        deltas = [
            {
                "type": 1,
                "index": idx,
                "paragraph": {"type": 1, "text": line, "markups": []},
            }
            for idx, line in enumerate(
                ln for ln in body_markdown.split("\n") if ln.strip()
            )
        ]
        if not deltas:
            raise MediumAPIError(400, "empty response body")

        if dry_run:
            return {
                "_dry_run": True,
                "mutation": "savePostResponse",
                "deltas": deltas,
                "inResponseToPostId": target,
            }

        if not self.cfg.sid:
            raise MediumAPIError(401, "post_response needs MEDIUM_SID")

        data = self._gql(
            operation="SavePostResponseMutation",
            query="""
            mutation SavePostResponseMutation($deltas: [Delta!]!, $inResponseToPostId: ID!) {
              savePostResponse(deltas: $deltas, inResponseToPostId: $inResponseToPostId) {
                id
                mediumUrl
                createdAt
                creator { id username name }
              }
            }
            """,
            variables={"deltas": deltas, "inResponseToPostId": target},
        )
        return data.get("savePostResponse") or {}

    # --------------------------------------------------------------------- #
    # claps
    # --------------------------------------------------------------------- #
    def clap_post(self, post_id: str, *, claps: int = 1, dry_run: bool = True) -> dict[str, Any]:
        """Clap a story 1-50 times. Undocumented dashboard endpoint."""
        claps = max(1, min(50, int(claps)))
        if dry_run:
            return {"_dry_run": True, "post_id": post_id, "claps": claps}
        return self._dashboard(
            method="POST",
            path=f"/_/api/posts/{post_id}/clap",
            json_body={"claps": claps},
        )

    def get_clap_count(self, post_id: str) -> int:
        p = self.get_post(post_id)
        return int(p.get("clapCount") or 0)

    # --------------------------------------------------------------------- #
    # feed + discovery
    # --------------------------------------------------------------------- #
    def get_feed(self, *, tab: str = "home", limit: int = 20) -> list[dict[str, Any]]:
        """Reader feed. tab ∈ {home, following, tag-{slug}}."""
        if tab.startswith("tag-"):
            slug = tab.split("-", 1)[1]
            data = self._gql(
                operation="TagFeed",
                query="""
                query TagFeed($slug: String!, $paging: PagingOptions) {
                  tag(slug: $slug) {
                    name
                    postsConnection(paging: $paging) {
                      edges { node { id title mediumUrl clapCount creator { username } } }
                    }
                  }
                }
                """,
                variables={"slug": slug, "paging": {"limit": limit}},
            )
            edges = (((data.get("tag") or {}).get("postsConnection") or {}).get("edges")) or []
            return [e["node"] for e in edges if e and e.get("node")]

        operation = "HomeFeed" if tab == "home" else "FollowingFeed"
        query = """
        query %s($paging: PagingOptions) {
          webFeed(paging: $paging) {
            items {
              post { id title mediumUrl clapCount creator { username } }
            }
          }
        }
        """ % operation
        data = self._gql(
            operation=operation,
            query=query,
            variables={"paging": {"limit": limit}},
        )
        items = ((data.get("webFeed") or {}).get("items")) or []
        return [i.get("post") for i in items if i.get("post")]

    # --------------------------------------------------------------------- #
    # stats (dashboard only)
    # --------------------------------------------------------------------- #
    def get_stats(self, *, days: int = 30) -> list[dict[str, Any]]:
        """Per-post stats (views/reads/fans) for the current user.

        Uses the same endpoint the web dashboard hits. Results are already
        sorted by most recent.
        """
        uid = self.cfg.uid
        if not uid:
            viewer = self.get_my_profile()
            uid = viewer.get("id")
        if not uid:
            raise MediumAPIError(400, "no user id available for stats")
        data = self._dashboard(
            method="GET",
            path=f"/@{self.cfg.username or 'me'}/stats?count={days}&filter=not-response",
        )
        value = data.get("value") if isinstance(data, dict) else data
        return value if isinstance(value, list) else []

    # --------------------------------------------------------------------- #
    # helpers — reply engine calls these
    # --------------------------------------------------------------------- #
    def walk_responses(
        self,
        post_id: str,
        *,
        skip_user_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield every top-level response + one level of replies."""
        for r in self.list_responses(post_id):
            if skip_user_id and (r.get("creator") or {}).get("id") == skip_user_id:
                continue
            yield {"depth": 0, "parent_id": None, **r}
            rid = r.get("id")
            if rid and (r.get("postResponses") or {}).get("count"):
                for child in self.get_response_replies(rid):
                    if skip_user_id and (child.get("creator") or {}).get("id") == skip_user_id:
                        continue
                    yield {"depth": 1, "parent_id": rid, **child}


@contextmanager
def session() -> Iterator[MediumClient]:
    c = MediumClient.create()
    try:
        yield c
    finally:
        c.close()


_SLUG_RE = re.compile(r"^[a-f0-9]{12,}$")


def normalize_post_id(id_or_url: str) -> str:
    """Accept a medium URL or an id. URLs end in -<12-char-hex-id>."""
    s = id_or_url.strip()
    if _SLUG_RE.match(s):
        return s
    m = re.search(r"-([a-f0-9]{10,16})(?:\?|#|$)", s)
    if m:
        return m.group(1)
    return s

"""RSS client — zero-auth read transport for Medium.

Medium exposes a public RSS feed at `medium.com/feed/@{username}` that returns
the user's ~10 most recent posts including:

  - guid           → canonical post id (`/p/{id}` suffix)
  - title, link, pubDate, updated
  - dc:creator
  - category[]     → tags
  - content:encoded → full post body HTML (member-only stories: preview only,
                     unless the request comes from an authenticated session)

This is the same trick Portfolio_V2 uses for the public blog page. It is:

  - Faster than GraphQL (one HTTP call, no auth, no XSSI stripping)
  - Public (works without sid / xsrf / cf_clearance)
  - Stable (RSS has been stable for years; GraphQL mutates often)

Trade-offs:
  - Capped at ~10 posts (no pagination)
  - No clap/response counts
  - No view/read stats
  - Tags from RSS may differ slightly from GraphQL tag list

We use selectolax (lexbor) for HTML parsing — ~10x faster than bs4 and tiny.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from selectolax.parser import HTMLParser

RSS_URL = "https://medium.com/feed/@{username}"
_NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "atom": "http://www.w3.org/2005/Atom",
}
_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)


@dataclass
class RssPost:
    id: str
    title: str
    subtitle: str
    url: str
    published_at: str
    updated_at: str
    creator: str
    tags: list[str]
    image_url: str
    body_html: str
    word_count: int
    reading_time_minutes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "mediumUrl": self.url,
            "url": self.url,
            "firstPublishedAt": self.published_at,
            "latestPublishedAt": self.updated_at,
            "tags": self.tags,
            "image_url": self.image_url,
            "body_html": self.body_html,
            "wordCount": self.word_count,
            "readingTime": self.reading_time_minutes,
            "creator": {"name": self.creator},
            "_source": "rss",
        }


def fetch_rss(username: str, *, http: httpx.Client | None = None) -> str:
    """GET medium.com/feed/@{username} → raw XML."""
    username = username.lstrip("@")
    url = RSS_URL.format(username=username)
    headers = {"User-Agent": _DEFAULT_UA, "Accept": "application/rss+xml, application/xml"}
    if http is None:
        with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as c:
            r = c.get(url)
    else:
        r = http.get(url, headers=headers)
    if r.status_code != 200:
        raise RuntimeError(f"medium rss {r.status_code} for @{username}: {r.text[:200]}")
    return r.text


def _extract_post_id(guid: str, link: str) -> str:
    """guid like `https://medium.com/p/abc123def456` → `abc123def456`.

    Falls back to the trailing 12-char hex on the link slug.
    """
    if "/p/" in guid:
        return guid.rsplit("/p/", 1)[-1].strip()
    m = re.search(r"-([a-f0-9]{10,16})(?:\?|$)", link)
    if m:
        return m.group(1)
    return guid.strip()


def _iso(dt_str: str) -> str:
    """RFC822 → ISO8601. Returns '' on failure."""
    if not dt_str:
        return ""
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
    ):
        try:
            dt = datetime.strptime(dt_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC).isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return ""


def _clean_body(html: str) -> tuple[str, str, str, int]:
    """Return (cleaned_html, subtitle, hero_img_url, word_count).

    Mirrors Portfolio_V2 parser.ts:
      - drop the first <figure> if it duplicates the hero image
      - strip Medium's "was originally published" boilerplate footer
    """
    if not html:
        return "", "", "", 0

    tree = HTMLParser(html)

    hero_img = ""
    first_img = tree.css_first("img")
    if first_img is not None:
        hero_img = first_img.attributes.get("src", "") or ""
        first_fig = tree.css_first("figure")
        if first_fig is not None:
            inner = first_fig.css_first("img")
            if inner is not None and inner.attributes.get("src") == hero_img:
                first_fig.decompose()

    paragraphs = tree.css("p")
    if paragraphs:
        last = paragraphs[-1]
        if "was originally published" in (last.text() or ""):
            last.decompose()
            hrs = tree.css("hr")
            if hrs and hrs[-1].next is None:
                hrs[-1].decompose()

    cleaned = tree.body.html if tree.body is not None else tree.html
    cleaned = cleaned or ""

    text = HTMLParser(cleaned).text(separator=" ").strip()
    word_count = len(text.split()) if text else 0

    subtitle = ""
    sub_tree = HTMLParser(cleaned)
    first_p = sub_tree.css_first("p")
    if first_p is not None:
        subtitle = (first_p.text() or "").strip()[:200]

    return cleaned, subtitle, hero_img, word_count


def parse_rss(xml: str) -> list[RssPost]:
    """Parse the RSS XML into RssPost objects."""
    root = ET.fromstring(xml)
    channel = root.find("channel")
    if channel is None:
        return []

    out: list[RssPost] = []
    for item in channel.findall("item"):
        guid = (item.findtext("guid") or "").strip()
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        pub = _iso(item.findtext("pubDate") or "")
        updated = _iso(item.findtext("atom:updated", default="", namespaces=_NS) or pub)
        creator = (item.findtext("dc:creator", default="", namespaces=_NS) or "").strip()

        tags: list[str] = []
        for cat in item.findall("category"):
            t = (cat.text or "").strip()
            if t:
                tags.append(t)

        body_raw = item.findtext("content:encoded", default="", namespaces=_NS) or ""
        body_html, subtitle, hero, words = _clean_body(body_raw)

        out.append(
            RssPost(
                id=_extract_post_id(guid, link),
                title=title,
                subtitle=subtitle,
                url=link,
                published_at=pub,
                updated_at=updated,
                creator=creator,
                tags=tags,
                image_url=hero,
                body_html=body_html,
                word_count=words,
                reading_time_minutes=max(1, (words + 199) // 200),
            )
        )
    return out


def list_posts_via_rss(
    username: str,
    *,
    http: httpx.Client | None = None,
    limit: int = 20,
) -> list[RssPost]:
    """High-level: fetch + parse, return up to `limit` posts."""
    xml = fetch_rss(username, http=http)
    return parse_rss(xml)[:limit]


def get_post_via_rss(
    post_id: str,
    username: str,
    *,
    http: httpx.Client | None = None,
) -> RssPost | None:
    """High-level: find a single post by id in the user's RSS feed."""
    posts = list_posts_via_rss(username, http=http, limit=50)
    for p in posts:
        if p.id == post_id:
            return p
    return None

"""Tests for RSS client (zero-auth read transport)."""

from __future__ import annotations

from medium_ops.rss import (
    RssPost,
    _clean_body,
    _extract_post_id,
    _iso,
    parse_rss,
)

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Stories by Jane Doe on Medium</title>
    <link>https://medium.com/@jane</link>
    <item>
      <title>Hello World</title>
      <link>https://medium.com/@jane/hello-world-abc123def456</link>
      <guid isPermaLink="false">https://medium.com/p/abc123def456</guid>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
      <atom:updated>2024-01-02T12:00:00.000Z</atom:updated>
      <dc:creator>Jane Doe</dc:creator>
      <category>nextjs</category>
      <category>react</category>
      <content:encoded><![CDATA[
        <figure><img src="https://cdn.example/hero.png" /></figure>
        <p>This is the subtitle paragraph that should appear first.</p>
        <p>Body paragraph one with some words to count.</p>
        <p>Body paragraph two with even more words to count for reading time.</p>
        <hr/>
        <p><em>This story was originally published on my blog.</em></p>
      ]]></content:encoded>
    </item>
    <item>
      <title>Second Post</title>
      <link>https://medium.com/@jane/second-post-deadbeefcafe</link>
      <guid isPermaLink="false">https://medium.com/p/deadbeefcafe</guid>
      <pubDate>Tue, 02 Jan 2024 08:00:00 GMT</pubDate>
      <dc:creator>Jane Doe</dc:creator>
      <category>tooling</category>
      <content:encoded><![CDATA[<p>Just a tiny post.</p>]]></content:encoded>
    </item>
  </channel>
</rss>
"""


def test_extract_post_id_from_guid() -> None:
    assert _extract_post_id("https://medium.com/p/abc123def456", "irrelevant") == "abc123def456"


def test_extract_post_id_falls_back_to_link_slug() -> None:
    pid = _extract_post_id(
        "tag:medium.com,whatever",
        "https://medium.com/@user/some-cool-title-deadbeef1234",
    )
    assert pid == "deadbeef1234"


def test_iso_handles_rfc822_gmt() -> None:
    out = _iso("Mon, 01 Jan 2024 12:00:00 GMT")
    assert out.startswith("2024-01-01T12:00:00")


def test_iso_handles_empty() -> None:
    assert _iso("") == ""


def test_clean_body_strips_hero_figure_and_boilerplate() -> None:
    html = (
        '<figure><img src="https://cdn/hero.png"/></figure>'
        "<p>Subtitle here.</p>"
        "<p>Body content.</p>"
        "<hr/><p>This was originally published elsewhere.</p>"
    )
    cleaned, subtitle, hero, words = _clean_body(html)
    assert hero == "https://cdn/hero.png"
    assert "hero.png" not in cleaned, "hero figure should be removed"
    assert "originally published" not in cleaned
    assert subtitle == "Subtitle here."
    assert words >= 3


def test_clean_body_handles_empty() -> None:
    assert _clean_body("") == ("", "", "", 0)


def test_parse_rss_returns_two_posts() -> None:
    posts = parse_rss(SAMPLE_XML)
    assert len(posts) == 2
    p1, p2 = posts
    assert isinstance(p1, RssPost)
    assert p1.id == "abc123def456"
    assert p1.title == "Hello World"
    assert p1.creator == "Jane Doe"
    assert p1.tags == ["nextjs", "react"]
    assert p1.image_url == "https://cdn.example/hero.png"
    assert "originally published" not in p1.body_html
    assert p1.word_count > 0
    assert p1.reading_time_minutes >= 1
    assert p1.published_at.startswith("2024-01-01")
    assert p2.id == "deadbeefcafe"
    assert p2.tags == ["tooling"]


def test_parse_rss_to_dict_shape() -> None:
    posts = parse_rss(SAMPLE_XML)
    d = posts[0].to_dict()
    assert d["id"] == "abc123def456"
    assert d["mediumUrl"].startswith("https://medium.com/@jane/")
    assert d["creator"]["name"] == "Jane Doe"
    assert d["_source"] == "rss"


def test_parse_rss_handles_empty_channel() -> None:
    xml = '<?xml version="1.0"?><rss><channel></channel></rss>'
    assert parse_rss(xml) == []

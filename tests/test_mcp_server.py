"""MCP tool registry shape — descriptions, required schemas, tool count."""

from __future__ import annotations

from medium_ops.mcp.server import TOOLS, list_tool_names


def test_has_core_tools():
    names = set(list_tool_names())
    expected = {
        "test_connection",
        "get_own_profile",
        "get_profile",
        "list_posts",
        "get_post",
        "get_post_content",
        "search_posts",
        "list_responses",
        "get_response_replies",
        "get_feed",
        "get_stats",
        "get_clap_count",
        "publish_post",
        "clap_post",
        "post_response",
        "bulk_draft_replies",
        "send_approved_drafts",
        "audit_search",
        "dedup_status",
        "get_unanswered_responses",
        "propose_reply",
        "confirm_reply",
    }
    missing = expected - names
    assert not missing, f"missing tools: {missing}"


def test_every_tool_has_description_and_schema():
    for name, spec in TOOLS.items():
        assert spec.get("description"), f"{name} missing description"
        assert len(spec["description"]) >= 20, f"{name} description too short"
        assert "input_schema" in spec, f"{name} missing input_schema"
        assert spec["input_schema"].get("type") == "object"


def test_write_tools_default_dry_run():
    for name in ("publish_post", "clap_post", "post_response"):
        props = TOOLS[name]["input_schema"]["properties"]
        assert props["dry_run"]["default"] is True, (
            f"{name} must default dry_run=true"
        )

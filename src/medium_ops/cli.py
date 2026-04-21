"""medium-ops CLI — typer entry.

Command groups mirror substack-ops where possible. Every write defaults to
`--dry-run`; flip with `--no-dry-run` (and `--yes-i-mean-it` for irreversible
ones). All writes land in `.cache/audit.jsonl` and are dedup-checked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from medium_ops import __version__

app = typer.Typer(
    help="medium-ops — Medium CLI + MCP server. Hybrid official API + GraphQL.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()


# --------------------------------------------------------------------------- #
# sub-apps
# --------------------------------------------------------------------------- #
auth_app = typer.Typer(help="Auth verify + setup.")
posts_app = typer.Typer(help="Read + write stories.")
responses_app = typer.Typer(help="Read + write responses (Medium's comments).")
claps_app = typer.Typer(help="Clap stories.")
feed_app = typer.Typer(help="Reader feed + discovery.")
profile_app = typer.Typer(help="Profiles + stats.")
reply_app = typer.Typer(help="Reply engine — template / review / bulk / auto.")
audit_app = typer.Typer(help="Audit + dedup inspection.")
mcp_app = typer.Typer(help="MCP server + host installer.")

app.add_typer(auth_app, name="auth")
app.add_typer(posts_app, name="posts")
app.add_typer(responses_app, name="responses")
app.add_typer(claps_app, name="claps")
app.add_typer(feed_app, name="feed")
app.add_typer(profile_app, name="profile")
app.add_typer(reply_app, name="reply")
app.add_typer(audit_app, name="audit")
app.add_typer(mcp_app, name="mcp")


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", help="Print version and exit."),
) -> None:
    if version:
        console.print(f"medium-ops [cyan]{__version__}[/cyan]")
        raise typer.Exit(0)


def _json(data: Any) -> None:
    console.print_json(data=data)


def _client():
    from medium_ops.client import MediumClient

    return MediumClient.create()


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@auth_app.command("verify")
def auth_verify() -> None:
    """Check both integration token + sid cookie."""
    from medium_ops.auth import verify

    result = verify()
    _json(result)
    raise typer.Exit(0 if result.get("ok") else 1)


@auth_app.command("test")
def auth_test() -> None:
    """Same as verify; exit non-zero on failure (CI-friendly)."""
    from medium_ops.auth import verify

    result = verify()
    if not result.get("ok"):
        console.print("[red]auth failed[/red]")
        _json(result)
        raise typer.Exit(1)
    console.print(f"[green]OK[/green]  user={result.get('username')}")


@auth_app.command("har")
def auth_har(
    har_file: Path = typer.Argument(
        ..., exists=True, readable=True, help="HAR export from Chrome devtools."
    ),
    env_path: Path = typer.Option(Path(".env"), "--env", help="Where to write extracted cookies."),
    snapshot_path: Path = typer.Option(
        Path(".cache/har-snapshot.json"),
        "--snapshot",
        help="Where to write GraphQL/dashboard payload-shape snapshot.",
    ),
    write_env: bool = typer.Option(True, "--write-env/--no-write-env"),
) -> None:
    """Ingest a Medium HAR export to refresh cookies + snapshot API shapes.

    Use this whenever Medium changes their schema and the hand-rolled GraphQL
    queries break. Replay the failing action in your browser with devtools
    open, save a HAR ("Save all as HAR with content"), then run this command.
    The snapshot file lets you diff Medium's current request/response shapes
    against what `client.py` sends.
    """
    from medium_ops.har import parse_har, write_snapshot
    from medium_ops.har import write_env as har_write_env

    snapshot = parse_har(har_file)

    table = Table(title=f"HAR: {har_file.name}", show_header=True, header_style="bold")
    table.add_column("kind")
    table.add_column("name / path")
    table.add_column("status", justify="right")
    table.add_column("keys")

    for op in snapshot.graphql:
        table.add_row(
            "graphql",
            op.operation,
            str(op.status or "-"),
            f"req={op.request_keys} → res={op.response_keys}"
            + (f" [red]errors={op.response_errors}[/red]" if op.response_errors else ""),
        )
    for op in snapshot.dashboard:
        table.add_row(
            "dashboard",
            f"{op.method} {op.path}",
            str(op.status or "-"),
            f"req={op.request_body_keys} → res.value={op.response_value_keys}",
        )
    if not snapshot.graphql and not snapshot.dashboard:
        table.add_row("-", "(no Medium API calls found)", "-", "-")
    console.print(table)

    cookie_summary = {k: f"...{v[-6:]}" for k, v in snapshot.cookies.items()}
    console.print(f"\n[bold]cookies discovered:[/bold] {cookie_summary or '(none)'}")
    console.print(f"[dim]skipped non-Medium / non-API entries: {snapshot.skipped}[/dim]")

    write_snapshot(snapshot, snapshot_path)
    console.print(f"[green]wrote snapshot[/green] → {snapshot_path}")

    if write_env and snapshot.cookies:
        updated = har_write_env(snapshot.cookies, env_path)
        if updated:
            console.print(f"[green]updated .env[/green] keys: {updated}")
        else:
            console.print(f"[dim].env already up to date[/dim] ({env_path})")
    elif not snapshot.cookies:
        console.print("[yellow]no cookies found in HAR — make sure devtools recorded an authenticated request[/yellow]")


@auth_app.command("setup")
def auth_setup() -> None:
    """Interactive paste of integration token + sid cookie; writes .env."""
    console.print("[bold]Medium auth setup[/bold]")
    console.print(
        "Integration Token from https://medium.com/me/settings → 'Integration tokens'.\n"
        "sid cookie from browser devtools on medium.com (Application → Cookies)."
    )
    token = typer.prompt("MEDIUM_INTEGRATION_TOKEN (empty to skip)", default="", show_default=False)
    sid = typer.prompt("MEDIUM_SID (empty to skip)", default="", show_default=False)
    uid = typer.prompt("MEDIUM_UID (from cookies; empty to skip)", default="", show_default=False)
    username = typer.prompt("MEDIUM_USERNAME (empty to skip)", default="", show_default=False)

    env_path = Path(".env")
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()

    def _set(key: str, val: str) -> None:
        if not val:
            return
        line = f"{key}={val}"
        for i, ex in enumerate(lines):
            if ex.startswith(f"{key}="):
                lines[i] = line
                return
        lines.append(line)

    _set("MEDIUM_INTEGRATION_TOKEN", token)
    _set("MEDIUM_SID", sid)
    _set("MEDIUM_UID", uid)
    _set("MEDIUM_USERNAME", username)

    env_path.write_text("\n".join(lines) + "\n")
    console.print(f"[green]wrote[/green] {env_path}")


# --------------------------------------------------------------------------- #
# posts
# --------------------------------------------------------------------------- #
@posts_app.command("list")
def posts_list(
    limit: int = typer.Option(20, "--limit"),
    username: str | None = typer.Option(None, "--user"),
    source: str = typer.Option("auto", "--source", help="auto | rss | graphql"),
) -> None:
    """List your (or another user's) latest stories.

    --source auto (default) tries RSS first (zero-auth, fast) and falls back
    to GraphQL when more than ~10 posts are requested.
    """
    with _client() as c:
        rows = c.list_posts(limit=limit, username=username, source=source)

    table = Table(show_header=True, header_style="bold")
    for col in ("id", "title", "claps", "responses", "first_published"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r.get("id") or "")[:12],
            (r.get("title") or "")[:80],
            str(r.get("clapCount") or 0),
            str((r.get("postResponses") or {}).get("count") or 0),
            str(r.get("firstPublishedAt") or ""),
        )
    console.print(table)


@posts_app.command("show")
def posts_show(
    post_id: str,
    source: str = typer.Option("auto", "--source", help="auto | rss | graphql"),
    username: str | None = typer.Option(None, "--user"),
) -> None:
    """Story metadata."""
    from medium_ops.client import normalize_post_id

    with _client() as c:
        _json(c.get_post(normalize_post_id(post_id), username=username, source=source))


@posts_app.command("content")
def posts_content(
    post_id: str,
    as_markdown: bool = typer.Option(False, "--md", "--markdown"),
    source: str = typer.Option("auto", "--source", help="auto | rss | graphql"),
    username: str | None = typer.Option(None, "--user"),
) -> None:
    """Story body. --md converts HTML to Markdown."""
    from medium_ops.client import normalize_post_id

    with _client() as c:
        html = c.get_post_content(normalize_post_id(post_id), username=username, source=source)
    if not html:
        console.print("[yellow](empty body — paywalled or not found)[/yellow]")
        raise typer.Exit(1)
    if as_markdown:
        from markdownify import markdownify

        console.print(markdownify(html, heading_style="ATX"))
    else:
        console.print(html)


@posts_app.command("create-draft")
def posts_create_draft(
    dry_run: bool = typer.Option(False, "--dry-run/--live"),
) -> None:
    """Create a blank draft via dashboard GraphQL `createPost`.

    No title or body — Medium's web editor sets those via subsequent deltas.
    Use this as the first step of an unattended publish flow.
    """
    with _client() as c:
        out = c.create_draft(dry_run=dry_run)
    _json(out)


@posts_app.command("set-content")
def posts_set_content(
    post_id: str,
    title: str = typer.Option(None, "--title", "-t"),
    body_file: Path | None = typer.Option(None, "--file", "-f", help="Markdown body. One paragraph per non-empty line."),
    body: str | None = typer.Option(None, "--body", help="Inline body text. Overrides --file."),
    base_rev: int = typer.Option(-1, "--base-rev", help="Last known revision (-1 for new draft)."),
    rev: int = typer.Option(0, "--rev", help="Target revision (base_rev + 1 typically)."),
    dry_run: bool = typer.Option(False, "--dry-run/--live"),
) -> None:
    """Set title + body on an existing draft via /p/{id}/deltas.

    Use this between `posts create-draft` and `posts publish-draft` to give
    your draft real content before going public.
    """
    from medium_ops.client import normalize_post_id

    pid = normalize_post_id(post_id)
    paragraphs: list[str] = []
    if body is not None:
        paragraphs = [p for p in body.split("\n") if p.strip()]
    elif body_file:
        paragraphs = [p for p in body_file.read_text().split("\n") if p.strip()]

    with _client() as c:
        out = c.update_draft_content(
            pid,
            title=title,
            body_paragraphs=paragraphs,
            base_rev=base_rev,
            rev=rev,
            dry_run=dry_run,
        )
    _json(out)


@posts_app.command("publish-draft")
def posts_publish_draft(
    post_id: str,
    dry_run: bool = typer.Option(False, "--dry-run/--live"),
) -> None:
    """Publish an existing draft via dashboard GraphQL `publishPost`.

    Distinct from `posts publish` (the official-API path that needs an
    Integration Token). This works with just MEDIUM_SID + MEDIUM_XSRF.
    """
    from medium_ops.client import normalize_post_id

    with _client() as c:
        out = c.publish_draft(normalize_post_id(post_id), dry_run=dry_run)
    _json(out)


@posts_app.command("delete")
def posts_delete(
    post_id: str,
    yes: bool = typer.Option(False, "--yes", "-y", help="skip confirm"),
    dry_run: bool = typer.Option(False, "--dry-run/--live"),
) -> None:
    """Delete a draft or published post."""
    from medium_ops.client import normalize_post_id

    pid = normalize_post_id(post_id)
    if not dry_run and not yes:
        typer.confirm(f"Really delete post {pid}?", abort=True)
    with _client() as c:
        ok = c.delete_post(pid, dry_run=dry_run)
    _json({"ok": ok, "post_id": pid, "dry_run": dry_run})


@posts_app.command("search")
def posts_search(
    query: str,
    limit: int = typer.Option(10, "--limit"),
) -> None:
    """Medium-side full-text search."""
    with _client() as c:
        _json(c.search_posts(query=query, limit=limit))


@posts_app.command("publish")
def posts_publish(
    title: str = typer.Option(..., "--title", "-t"),
    content_file: Path = typer.Option(..., "--file", "-f", help="Markdown file."),
    tags: str = typer.Option("", "--tags", help="Comma-separated."),
    publication: str | None = typer.Option(None, "--publication", "--pub"),
    status: str = typer.Option("draft", "--status", help="public | draft | unlisted"),
    canonical: str | None = typer.Option(None, "--canonical"),
    notify: bool = typer.Option(False, "--notify-followers"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
) -> None:
    """Publish a story via official Integration Token."""
    md = content_file.read_text()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] or None
    with _client() as c:
        result = c.publish_post(
            title=title,
            content_markdown=md,
            tags=tag_list,
            publication_id=publication,
            publish_status=status,
            canonical_url=canonical,
            notify_followers=notify,
            dry_run=dry_run,
        )
    _json(result)


# --------------------------------------------------------------------------- #
# responses
# --------------------------------------------------------------------------- #
@responses_app.command("list")
def responses_list(
    post_id: str,
    limit: int = typer.Option(50, "--limit"),
) -> None:
    """Top-level responses under a story."""
    from medium_ops.client import normalize_post_id

    with _client() as c:
        rows = c.list_responses(normalize_post_id(post_id), limit=limit)

    table = Table(show_header=True, header_style="bold")
    for col in ("id", "author", "claps", "replies", "body"):
        table.add_column(col)
    for r in rows:
        body = ((r.get("previewContent") or {}).get("subtitle")) or ""
        table.add_row(
            str(r.get("id") or "")[:12],
            (r.get("creator") or {}).get("username") or "",
            str(r.get("clapCount") or 0),
            str((r.get("postResponses") or {}).get("count") or 0),
            (body or "")[:100],
        )
    console.print(table)


@responses_app.command("tree")
def responses_tree(
    post_id: str,
    out: Path | None = typer.Option(None, "--out"),
) -> None:
    """Full response + reply tree as JSON."""
    from medium_ops.client import normalize_post_id

    with _client() as c:
        pid = normalize_post_id(post_id)
        tops = c.list_responses(pid)
        tree: list[dict[str, Any]] = []
        for t in tops:
            rid = t.get("id")
            children = c.get_response_replies(rid) if rid else []
            tree.append({**t, "replies": children})

    if out:
        out.write_text(json.dumps(tree, indent=2, ensure_ascii=False))
        console.print(f"[green]wrote[/green] {out}  ({len(tree)} top-level)")
    else:
        _json(tree)


@responses_app.command("add")
def responses_add(
    post_id: str,
    body: str,
    parent: str | None = typer.Option(None, "--parent"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
) -> None:
    """Post a response (or reply to a response)."""
    from medium_ops.client import normalize_post_id
    from medium_ops.reply_engine.base import post_response

    pid = normalize_post_id(post_id)
    with _client() as c:
        _json(
            post_response(
                c,
                post_id=pid,
                parent_response_id=parent,
                body=body,
                dry_run=dry_run,
                mode="cli:responses_add",
            )
        )


# --------------------------------------------------------------------------- #
# claps
# --------------------------------------------------------------------------- #
@claps_app.command("count")
def claps_count(post_id: str) -> None:
    from medium_ops.client import normalize_post_id

    with _client() as c:
        console.print(c.get_clap_count(normalize_post_id(post_id)))


@claps_app.command("give")
def claps_give(
    post_id: str,
    claps: int = typer.Option(1, "--claps", min=1, max=50),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
) -> None:
    from medium_ops.client import normalize_post_id
    from medium_ops.reply_engine.base import post_clap

    pid = normalize_post_id(post_id)
    with _client() as c:
        _json(post_clap(c, post_id=pid, claps=claps, dry_run=dry_run, mode="cli:claps_give"))


# --------------------------------------------------------------------------- #
# feed + profile
# --------------------------------------------------------------------------- #
@feed_app.command("list")
def feed_list(
    tab: str = typer.Option("home", "--tab"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    with _client() as c:
        _json(c.get_feed(tab=tab, limit=limit))


@profile_app.command("me")
def profile_me() -> None:
    with _client() as c:
        _json(c.get_my_profile())


@profile_app.command("get")
def profile_get(username: str) -> None:
    with _client() as c:
        _json(c.get_profile(username))


@profile_app.command("stats")
def profile_stats(days: int = typer.Option(30, "--days")) -> None:
    """Per-post views/reads/fans. Requires sid cookie."""
    with _client() as c:
        _json(c.get_stats(days=days))


@profile_app.command("publications")
def profile_publications() -> None:
    """Publications you can publish to (integration token)."""
    with _client() as c:
        _json(c.list_own_publications())


# --------------------------------------------------------------------------- #
# reply engine
# --------------------------------------------------------------------------- #
@reply_app.command("template")
def reply_template(
    post_id: str,
    template: str = typer.Option("thanks", "--template"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    rate: float = typer.Option(30.0, "--rate"),
) -> None:
    """Rule-based replies (no LLM)."""
    from medium_ops.client import normalize_post_id
    from medium_ops.reply_engine.template import run_template

    results = run_template(
        post_id=normalize_post_id(post_id),
        template_name=template,
        dry_run=dry_run,
        rate_seconds=rate,
    )
    _json(results)


@reply_app.command("bulk")
def reply_bulk(
    post_id: str,
    out: Path = typer.Option(Path("drafts.json"), "--out"),
    model: str | None = typer.Option(None, "--model"),
) -> None:
    """LLM drafts every response to a file. Edit, then bulk-send."""
    from medium_ops.client import normalize_post_id
    from medium_ops.reply_engine.ai_bulk import generate_drafts

    n = generate_drafts(post_id=normalize_post_id(post_id), out=out, model=model)
    console.print(f"[green]wrote[/green] {out}  ({n} drafts, action=pending)")


@reply_app.command("bulk-send")
def reply_bulk_send(
    drafts_path: Path,
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    rate: float = typer.Option(30.0, "--rate"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Post only items with action='approved'."""
    from medium_ops.reply_engine.ai_bulk import send_drafts

    counts = send_drafts(
        drafts_path=drafts_path,
        dry_run=dry_run,
        rate_seconds=rate,
        force=force,
    )
    _json(counts)


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #
@audit_app.command("search")
def audit_search_cmd(
    kind: str | None = typer.Option(None, "--kind"),
    target: str | None = typer.Option(None, "--target"),
    status: str | None = typer.Option(None, "--status"),
    since: str | None = typer.Option(None, "--since"),
    limit: int = typer.Option(50, "--limit"),
) -> None:
    from medium_ops.audit import search_audit

    _json(
        search_audit(
            kind=kind, target=target, status=status, since=since, limit=limit
        )
    )


@audit_app.command("dedup-status")
def audit_dedup_status() -> None:
    from medium_ops.dedup import DedupDB

    _json(DedupDB().status())


# --------------------------------------------------------------------------- #
# mcp
# --------------------------------------------------------------------------- #
@mcp_app.command("install")
def mcp_install(
    host: str = typer.Argument(..., help="cursor | claude-desktop | claude-code | print"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    from medium_ops.mcp.install import install_to_host

    _json(install_to_host(host=host, dry_run=dry_run))


@mcp_app.command("serve")
def mcp_serve() -> None:
    """Run the MCP stdio server."""
    from medium_ops.mcp.server import serve

    serve()


@mcp_app.command("list-tools")
def mcp_list_tools() -> None:
    from medium_ops.mcp.server import TOOLS

    table = Table(show_header=True, header_style="bold")
    table.add_column("tool")
    table.add_column("description")
    for name, spec in TOOLS.items():
        table.add_row(name, spec["description"][:90])
    console.print(table)
    console.print(f"\n[dim]{len(TOOLS)} tools[/dim]")


# --------------------------------------------------------------------------- #
# quickstart
# --------------------------------------------------------------------------- #
@app.command("quickstart")
def quickstart() -> None:
    """Print a quickstart checklist."""
    console.print(
        """
[bold]medium-ops quickstart[/bold]

1. Set credentials (at least one of):
     export MEDIUM_INTEGRATION_TOKEN=...   # writes (publish_post)
     export MEDIUM_SID=...                 # reads  (dashboard + responses)
     export MEDIUM_UID=...
     export MEDIUM_USERNAME=yourhandle

2. Verify:
     medium-ops auth verify

3. Install the MCP server into your host:
     medium-ops mcp install cursor
     # restart cursor, then ask the agent to draft replies.

4. One-shot reads (no MCP needed):
     medium-ops posts list --limit 5
     medium-ops responses list <post-id-or-url>
     medium-ops profile stats --days 30

5. Draft workflow (offline review):
     medium-ops reply bulk <post-id> --out drafts.json
     # edit drafts.json → action: approved
     medium-ops reply bulk-send drafts.json --no-dry-run
"""
    )


if __name__ == "__main__":
    app()

"""MCP App mount — ``ui://knotica/dashboard`` resource + ``open_dashboard`` tool.

Registers the same single-file dashboard artifact that ``knotica mcp --http``
serves at ``GET /``, so Claude Desktop / claude.ai can render the loop pane
inside a sandboxed iframe (SEP-1865 / ext-apps). The iframe talks back through
the host's postMessage bridge and calls the existing ``wiki_status`` /
``metrics_read`` tools — no parallel data path.

Hosts without MCP Apps support still get a useful text result from
``open_dashboard`` (graceful fallback pointing at the HTTP mount).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from knotica.dashboard import dashboard_html

__all__ = ["DASHBOARD_URI", "MCP_APP_MIME", "register_dashboard_app"]

#: SEP-1865 resource URI for the dashboard View.
DASHBOARD_URI = "ui://knotica/dashboard"

#: Exact mimetype required by the MCP Apps spec (2026-01-26).
MCP_APP_MIME = "text/html;profile=mcp-app"

_OPEN_DASHBOARD_DESCRIPTION = (
    "Open the knotica dashboard for a topic (Vault, Ask, Loop, Arena, Ingest, Golden). "
    "On hosts that support MCP Apps (Claude Desktop Chat, claude.ai), renders the "
    "interactive UI inline. On hosts without Apps support, returns text with the "
    "standalone URL (`knotica mcp --http`). Pass topic (default agentic-systems) and "
    "optional vault name. Panes call wiki_status, query, compile_*, arena_*, etc."
)


def register_dashboard_app(mcp: FastMCP) -> None:
    """Register the ``ui://`` dashboard resource and its trigger tool.

    Pure registration — loading the HTML happens on resource read, not at
    server construction, so a missing artifact fails the read rather than
    blocking stdio startup.
    """

    @mcp.tool(
        name="open_dashboard",
        description=_OPEN_DASHBOARD_DESCRIPTION,
        meta={
            "ui": {"resourceUri": DASHBOARD_URI},
            "ui/resourceUri": DASHBOARD_URI,  # legacy host support (qr-server crib)
        },
    )
    def open_dashboard(topic: str = "agentic-systems", vault: str = "") -> list[TextContent]:
        cleaned = (topic or "agentic-systems").strip().strip("/") or "agentic-systems"
        vault_q = vault.strip()
        query = f"topic={cleaned}" + (f"&vault={vault_q}" if vault_q else "")
        vault_bit = f" vault '{vault_q}'" if vault_q else ""
        return [
            TextContent(
                type="text",
                text=(
                    f"knotica dashboard for topic '{cleaned}'{vault_bit}. "
                    "If your host supports MCP Apps, the interactive dashboard opens here "
                    "(Vault → Ask → Loop → Arena; vault name and path in the chrome). "
                    "Otherwise run `knotica mcp --http` and open "
                    f"http://127.0.0.1:8765/?{query} "
                    "(Claude Code: use the Browser pane). "
                    "Desktop install + AWM walkthrough: docs/CLAUDE_DESKTOP.md."
                ),
            )
        ]

    @mcp.resource(
        DASHBOARD_URI,
        mime_type=MCP_APP_MIME,
        description=(
            "Interactive knotica loop dashboard (MCP App). Same single-file artifact "
            "as the standalone HTTP mount; data flows through wiki_status / metrics_read "
            "via the host bridge."
        ),
    )
    def dashboard_view() -> str:
        return dashboard_html()

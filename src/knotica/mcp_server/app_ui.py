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

from urllib.parse import urlencode

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from knotica.dashboard import dashboard_html

__all__ = ["DASHBOARD_URI", "MCP_APP_MIME", "register_dashboard_app"]

#: SEP-1865 resource URI for the dashboard View.
DASHBOARD_URI = "ui://knotica/dashboard"

#: Exact mimetype required by the MCP Apps spec (2026-01-26).
MCP_APP_MIME = "text/html;profile=mcp-app"

#: Where ``knotica mcp --http`` serves the same artifact, for hosts without Apps.
_HTTP_MOUNT_URL = "http://127.0.0.1:8765/"

_OPEN_DASHBOARD_DESCRIPTION = (
    "Open the knotica dashboard on one of six process lanes. `home` (default) is a "
    "cross-topic attention inbox — what needs the user now, across every topic. `learn` "
    "follows a source from capture to a committed page; `answer` follows a question to an "
    "answer plus a signal; `improve` follows the self-improvement loop from instrument to a "
    "merged artifact; `fill` follows a knowledge gap to a closed or quarantined source; "
    '`tend` is vault health. Pass topic="" for a vault-wide view. '
    "This tool only opens a view — it performs no vault operation and mutates nothing. "
    "On hosts without MCP Apps support it returns the standalone URL instead. "
    "Requires: nothing beyond a configured vault. "
    "Params: topic (default vault-wide), an optional vault name, lane (one of the six "
    "names above), and focus — one free-form string naming a stage or an object within the "
    "lane. An unrecognised lane or focus degrades to the lane's own landing view rather "
    "than failing the call. "
    "Returns: the inline UI on an Apps host, or the standalone URL to open otherwise."
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
    def open_dashboard(
        topic: str = "", vault: str = "", lane: str = "", focus: str = ""
    ) -> list[TextContent]:
        # `lane`/`focus` are threaded through unvalidated on purpose: an
        # unrecognised value degrades in the dashboard's own resolution rather
        # than failing a navigation call the model can trivially get slightly
        # wrong (`dec-092`).
        cleaned = topic.strip().strip("/")
        params = {
            "topic": cleaned,
            "vault": vault.strip(),
            "lane": lane.strip(),
            "focus": focus.strip(),
        }
        query = urlencode({key: value for key, value in params.items() if value})
        url = f"{_HTTP_MOUNT_URL}?{query}" if query else _HTTP_MOUNT_URL
        scope = f"topic '{cleaned}'" if cleaned else "the whole vault"
        vault_bit = f" in vault '{params['vault']}'" if params["vault"] else ""
        lane_bit = f", {params['lane']} lane" if params["lane"] else ""
        return [
            TextContent(
                type="text",
                text=(
                    f"knotica dashboard for {scope}{vault_bit}{lane_bit}. "
                    "If your host supports MCP Apps, the interactive dashboard opens here "
                    "(vault name and path in the chrome). "
                    f"Otherwise run `knotica mcp --http` and open {url} "
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
            "as the standalone HTTP mount; data flows through `wiki_status` and "
            "`improve action=metrics_read` via the host bridge."
        ),
    )
    def dashboard_view() -> str:
        return dashboard_html()

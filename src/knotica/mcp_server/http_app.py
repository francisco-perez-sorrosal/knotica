"""Standalone HTTP mount for the MCP server and its dashboard.

The dashboard remains a pure MCP client: this module serves only its static
HTML and mounts the official SDK's streamable-HTTP ASGI application. It adds no
JSON/REST endpoints and holds no vault or session state.

Important: ``streamable_http_app()`` must be the *root* ASGI app (or its lifespan
must be forwarded). Wrapping it in a fresh Starlette ``Mount`` drops the session
manager's lifespan and yields ``RuntimeError: Task group is not initialized``.
The pattern here matches the official ext-apps ``qr-server`` example: CORS on
the MCP app itself, plus a ``GET /`` route for the dashboard HTML.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from knotica.dashboard import dashboard_html
from knotica.mcp_server.server import build_http_server

__all__ = ["create_http_app", "dashboard_html"]


async def _dashboard(_: Request) -> HTMLResponse:
    """Serve the same generated HTML that is included in the wheel."""
    return HTMLResponse(dashboard_html())


def create_http_app(server: FastMCP | None = None) -> Any:
    """Build the CORS-enabled ASGI application for ``knotica mcp --http``.

    The FastMCP streamable-HTTP app owns ``/mcp`` *and* its session-manager
    lifespan. We attach ``GET /`` for the dashboard onto that same app so the
    lifespan stays intact (see module docstring).
    """
    http_server = server if server is not None else build_http_server()
    app = http_server.streamable_http_app()
    # Prefer the dashboard over any catch-all on exact GET /.
    app.routes.insert(0, Route("/", _dashboard, methods=["GET"]))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id"],
    )
    return app

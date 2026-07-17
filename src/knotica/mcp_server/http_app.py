"""Standalone HTTP mount for the MCP server and its dashboard.

The dashboard remains a pure MCP client: this module serves only its static
HTML and mounts the official SDK's streamable-HTTP ASGI application. It adds no
JSON/REST endpoints and holds no vault or session state.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route

from knotica.mcp_server.server import build_http_server

__all__ = ["create_http_app", "dashboard_html"]


def dashboard_html() -> str:
    """Load the wheel-packaged dashboard, with a source-tree fallback for authors."""
    packaged = resources.files("knotica.dashboard").joinpath("app.html")
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")

    source_artifact = Path(__file__).resolve().parents[3] / "dashboard" / "dist" / "index.html"
    if source_artifact.is_file():
        return source_artifact.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "dashboard artifact is missing; run `npm --prefix dashboard run build` before packaging"
    )


async def _dashboard(_: Request) -> HTMLResponse:
    """Serve the same generated HTML that is included in the wheel."""
    return HTMLResponse(dashboard_html())


def create_http_app(server: FastMCP | None = None) -> Any:
    """Build the CORS-enabled ASGI application for ``knotica mcp --http``.

    ``FastMCP.streamable_http_app`` owns the MCP transport at ``/mcp``. The
    parent app merely gives the browser a landing page at ``/``; the dashboard
    obtains all dynamic data by calling those MCP tools over ``/mcp``.
    """
    http_server = server if server is not None else build_http_server()
    # FastMCP's streamable app already owns ``/mcp``. Mount it at ``/`` so that
    # path is preserved; the Route above claims exact ``GET /`` first.
    app = Starlette(
        routes=[
            Route("/", _dashboard, methods=["GET"]),
            Mount("/", app=http_server.streamable_http_app()),
        ]
    )
    return CORSMiddleware(
        app,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

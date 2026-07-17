"""Tests for the packaged dashboard and its standalone MCP HTTP mount."""

from starlette.testclient import TestClient

from knotica.mcp_server.http_app import create_http_app, dashboard_html


def test_dashboard_artifact_is_packaged_and_has_an_application_root() -> None:
    """The wheel-readable artifact lets installed users avoid a Node toolchain."""
    html = dashboard_html()

    assert "<!doctype html>" in html.lower()
    assert '<div id="app">' in html


def test_http_dashboard_serves_html_and_cors_preflight() -> None:
    """The browser mount is CORS-enabled while dynamic data remains MCP-only."""
    client = TestClient(create_http_app())

    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="app">' in response.text

    preflight = client.options(
        "/mcp",
        headers={
            "Origin": "http://127.0.0.1:8765",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"

"""Behavioral tests for the MCP resource band over an in-memory client session.

These drive the FastMCP server through the official SDK's in-memory transport
(``mcp.shared.memory.create_connected_server_and_client_session``) so the
assertions are made over the *wire* contract a real MCP client sees — not
against the internal ``core`` functions the resource handlers delegate to.

The pinned contract (from INTERFACE_DESIGN §5, §6.3):

- the two concrete schema/index resources (``knotica://schema/root``,
  ``knotica://index``) are advertised by ``resources/list`` and read as
  ``text/markdown``; the two parameterised ones (``schema/topic/{topic}``,
  ``schema/resolved/{topic}``) are advertised as resource *templates* and read
  by concrete URI as ``text/markdown``;
- the ``resolved`` resource serves exactly the root ⊕ overlay merge — the same
  bytes ``core.schema.resolve_schema`` computes (single source of truth: the
  resource is a convenience mirror, not a second merge);
- ``log.md`` is deliberately NOT exposed as a resource (append-only audit
  surface; unbounded payload — out of scope for Phase 1);
- an unconfigured read surfaces the uniform NOT_CONFIGURED remediation (naming
  both the ``/knotica:setup`` and ``knotica init`` paths), never a silent empty
  body — whether the SDK carries it as a raised protocol error or as error text
  in the resource contents.

Async coroutines are driven from sync test bodies via ``anyio.run`` (mcp
depends on anyio; there is no pytest async plugin configured). Production
imports of the server are deferred into helpers so collection succeeds while
Step 34 is still in flight (RED handshake: ImportError until the impl lands).
"""

from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.shared.exceptions import McpError
from pydantic import AnyUrl

from knotica.core.page import parse_page
from knotica.core.schema import resolve_schema
from knotica.store import LocalFSStore
from test_errors import assert_names_both_setup_paths

TOPIC = "agentic-systems"

ROOT_URI = "knotica://schema/root"
INDEX_URI = "knotica://index"
TOPIC_URI = f"knotica://schema/topic/{TOPIC}"
RESOLVED_URI = f"knotica://schema/resolved/{TOPIC}"

MARKDOWN = "text/markdown"


# ---------------------------------------------------------------------------
# Harness helpers — deferred imports keep collection green pre-impl.
# ---------------------------------------------------------------------------


def _build_server() -> Any:
    """Construct a fresh server instance (factory or module-level singleton)."""
    from knotica.mcp_server import server as server_mod

    if hasattr(server_mod, "build_server"):
        return server_mod.build_server()
    return server_mod.mcp


async def _list_resources(server: Any) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.list_resources()


async def _list_templates(server: Any) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.list_resource_templates()


async def _read(server: Any, uri: str) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.read_resource(AnyUrl(uri))


def list_resource_uris(*, server: Any | None = None) -> list[str]:
    result = anyio.run(_list_resources, server if server is not None else _build_server())
    return [str(r.uri) for r in result.resources]


def list_template_uris(*, server: Any | None = None) -> list[str]:
    result = anyio.run(_list_templates, server if server is not None else _build_server())
    return [r.uriTemplate for r in result.resourceTemplates]


def read_text_and_mime(uri: str, *, server: Any | None = None) -> tuple[str, str | None]:
    """Read a resource; return its concatenated text and the first mimeType."""
    result = anyio.run(_read, server if server is not None else _build_server(), uri)
    texts = [c.text for c in result.contents if getattr(c, "text", None) is not None]
    mime = next((getattr(c, "mimeType", None) for c in result.contents), None)
    return "".join(texts), mime


def read_outcome(uri: str, *, server: Any | None = None) -> str:
    """Return the text a read surfaces, whether via contents or a raised error."""
    try:
        text, _mime = read_text_and_mime(uri, server=server)
    except McpError as exc:
        return str(exc)
    return text


# ---------------------------------------------------------------------------
# Advertised surface: static resources vs parameterised templates.
# ---------------------------------------------------------------------------


def test_static_schema_and_index_resources_are_advertised(vault_config: Path) -> None:
    """resources/list surfaces the two concrete (non-parameterised) resources."""
    uris = list_resource_uris()
    assert ROOT_URI in uris, f"root schema resource not advertised: {uris!r}"
    assert INDEX_URI in uris, f"index resource not advertised: {uris!r}"


def test_parameterised_schema_resources_are_advertised_as_templates(
    vault_config: Path,
) -> None:
    """The per-topic and resolved resources carry a ``{topic}`` parameter, so
    they belong to resources/templates/list, not the static resource list."""
    templates = list_template_uris()
    assert any("schema/topic/" in t and "{topic}" in t for t in templates), (
        f"topic schema template not advertised: {templates!r}"
    )
    assert any("schema/resolved/" in t and "{topic}" in t for t in templates), (
        f"resolved schema template not advertised: {templates!r}"
    )


# ---------------------------------------------------------------------------
# Readability + mime type across every schema/index resource.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uri", [ROOT_URI, INDEX_URI, TOPIC_URI, RESOLVED_URI])
def test_each_resource_reads_as_non_empty_markdown(uri: str, vault_config: Path) -> None:
    """Every schema/index resource reads as non-empty ``text/markdown``."""
    text, mime = read_text_and_mime(uri)
    assert text.strip(), f"resource {uri} returned an empty body"
    assert mime == MARKDOWN, f"resource {uri} must be {MARKDOWN}, got {mime!r}"


# ---------------------------------------------------------------------------
# The resolved resource is a mirror of the schema merge, not a second merge.
# ---------------------------------------------------------------------------


def test_resolved_resource_equals_the_root_overlay_merge(
    vault_config: Path, template_vault: Path
) -> None:
    """The resolved resource serves exactly what ``resolve_schema`` computes —
    the root constitution ⊕ topic overlay, byte-for-byte."""
    text, _mime = read_text_and_mime(RESOLVED_URI)
    expected = resolve_schema(LocalFSStore(template_vault), TOPIC).merged
    assert text == expected, "resolved resource must mirror core.schema.resolve_schema"


def test_resolved_resource_carries_the_topic_overlay_content(
    vault_config: Path, template_vault: Path
) -> None:
    """The seed topic ships a schema overlay; the resolved body must include it,
    proving a real merge occurred (not just the root constitution served)."""
    overlay_raw = (template_vault / f"{TOPIC}/SCHEMA.md").read_text(encoding="utf-8")
    _fm, _err, overlay_body = parse_page(overlay_raw)
    text, _mime = read_text_and_mime(RESOLVED_URI)
    # A distinctive line from the overlay body must survive into the merged body.
    overlay_lines = [ln.strip() for ln in overlay_body.splitlines() if ln.strip()]
    distinctive = next((ln for ln in overlay_lines if len(ln) > 20), overlay_lines[0])
    assert distinctive in text, "resolved body must contain the topic overlay content"


# ---------------------------------------------------------------------------
# log.md is deliberately not a resource.
# ---------------------------------------------------------------------------


def test_log_is_not_exposed_as_a_resource(vault_config: Path) -> None:
    """The append-only audit log is never advertised as a resource (§5)."""
    advertised = list_resource_uris() + list_template_uris()
    assert not any("log" in uri.lower() for uri in advertised), (
        f"log.md must not be exposed as a resource: {advertised!r}"
    )


# ---------------------------------------------------------------------------
# Unconfigured read → uniform NOT_CONFIGURED remediation, never a silent body.
# ---------------------------------------------------------------------------


def test_unconfigured_resource_read_surfaces_setup_guidance(
    unconfigured_env: Path,
) -> None:
    """With no config anywhere, a schema read surfaces the uniform unconfigured
    remediation naming both setup paths — not a silent empty markdown body."""
    surfaced = read_outcome(ROOT_URI)
    assert_names_both_setup_paths(surfaced)

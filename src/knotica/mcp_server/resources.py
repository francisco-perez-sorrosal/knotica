"""MCP resources -- read-only vault surfaces mirroring the schema + catalog files.

Four resources expose the vault's schema layers and its global catalog as
``text/markdown``, all resolving config *per call* (so ``/knotica:setup`` takes
effect with no restart) and never mutating the vault. Resources are
**application-controlled and not auto-loaded** -- the operation prompts (see
:mod:`knotica.mcp_server.prompts`) instruct the client to read them; nothing is
pulled into context automatically.

* ``knotica://schema/root`` -- the root constitution (vault ``SCHEMA.md``).
* ``knotica://schema/topic/{topic}`` -- one topic's overlay (``<topic>/SCHEMA.md``);
  a note when the topic has no earned overlay yet.
* ``knotica://schema/resolved/{topic}`` -- the effective schema, root ⊕ overlay
  merged by :func:`knotica.core.schema.resolve_schema` (the one the prompts
  reference, saving the client a second fetch + merge).
* ``knotica://index`` -- the global catalog (vault ``index.md``).

``log.md`` is deliberately **not** exposed: it is an append-only audit surface,
and a full-log resource would be an unbounded payload (deferred).

An unconfigured read surfaces the same ``NOT_CONFIGURED`` grammar as the tools,
rendered as markdown (resources carry markdown, not the tool result envelope) so
the reader still gets an actionable ``code`` / message / fix. Registration
itself touches no vault -- the decorators only record metadata; every body
resolves lazily when the client reads the resource.
"""

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from knotica.core.config import resolve
from knotica.core.errors import KnoticaError
from knotica.core.lint import INDEX_PATH
from knotica.core.page import TopicNotFoundError
from knotica.core.schema import read_root_schema, read_topic_overlay, resolve_schema
from knotica.store import LocalFSStore, VaultStore

__all__ = ["register_resources"]

# --- resource descriptions (mirror the interface design's resource table) ---

_SCHEMA_ROOT_DESCRIPTION = (
    "The vault's root constitution (SCHEMA.md): the invariants every topic inherits "
    "and the vault schema_version. Read this to learn the rules a page must satisfy."
)

_SCHEMA_TOPIC_DESCRIPTION = (
    "One topic's schema overlay (<topic>/SCHEMA.md): the topic-specific entity types and "
    "page template that refine the root constitution. A topic with no earned overlay yet "
    "resolves to the root alone."
)

_SCHEMA_RESOLVED_DESCRIPTION = (
    "The effective schema for one topic: root constitution merged with the topic overlay "
    "(overlay refinements read last). Read this before writing pages in a topic — it is the "
    "single document the ingest/query/lint operations reference."
)

_INDEX_DESCRIPTION = (
    "The vault's global catalog (index.md): one line per content page. Read this for a "
    "map of what the wiki already covers."
)


def register_resources(mcp: FastMCP) -> None:
    """Register the four read-only resources on ``mcp``.

    Called once at server construction. Purely registration -- no vault access
    happens here; every resource resolves config lazily when the client reads it.
    """

    @mcp.resource(
        "knotica://schema/root",
        mime_type="text/markdown",
        description=_SCHEMA_ROOT_DESCRIPTION,
    )
    def schema_root() -> str:
        return _markdown(lambda store: read_root_schema(store).raw)

    @mcp.resource(
        "knotica://schema/topic/{topic}",
        mime_type="text/markdown",
        description=_SCHEMA_TOPIC_DESCRIPTION,
    )
    def schema_topic(topic: str) -> str:
        return _markdown(lambda store: _topic_overlay_markdown(store, topic))

    @mcp.resource(
        "knotica://schema/resolved/{topic}",
        mime_type="text/markdown",
        description=_SCHEMA_RESOLVED_DESCRIPTION,
    )
    def schema_resolved(topic: str) -> str:
        return _markdown(lambda store: resolve_schema(store, topic).merged)

    @mcp.resource(
        "knotica://index",
        mime_type="text/markdown",
        description=_INDEX_DESCRIPTION,
    )
    def index() -> str:
        return _markdown(lambda store: store.read_text(INDEX_PATH))


def _markdown(loader: Callable[[VaultStore], str]) -> str:
    """Resolve the vault per call and run ``loader``, rendering failures as markdown.

    An unconfigured vault (or a malformed one that raises the typed
    ``NOT_CONFIGURED`` error) yields the ``NOT_CONFIGURED`` grammar as markdown;
    a missing topic yields an actionable topic note. Resources always return
    valid ``text/markdown``, never a transport-level exception.
    """
    try:
        vault = resolve()
        return loader(LocalFSStore(vault.path))
    except KnoticaError as error:
        return _error_markdown(error)
    except TopicNotFoundError as exc:
        return f"# knotica: TOPIC_NOT_FOUND\n\n{exc}\n"


def _topic_overlay_markdown(store: VaultStore, topic: str) -> str:
    """Return the topic's overlay markdown, or a note when it has no overlay yet."""
    overlay = read_topic_overlay(store, topic)
    if overlay is None:
        return (
            f"<!-- Topic '{topic}' has no schema overlay; the root constitution"
            " applies unchanged (divergence is earned). -->\n"
        )
    return overlay.raw


def _error_markdown(error: KnoticaError) -> str:
    """Render a typed error as markdown, preserving its code, message, and fix."""
    return f"# knotica: {error.code.value}\n\n{error.message}\n\n**To fix:** {error.fix}\n"

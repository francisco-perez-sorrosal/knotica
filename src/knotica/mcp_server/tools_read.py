"""The five read tools -- deterministic, stateless, zero commits, no lock.

Each tool resolves ``config.toml`` -> vault root *per call* (so ``/knotica:setup``
takes effect with no restart), then delegates to the ``knotica.core`` read
functions and wraps the outcome via :mod:`knotica.mcp_server.envelope`. An
unconfigured vault returns the ``NOT_CONFIGURED`` envelope rather than raising.
Reads never write, never take the vault lock, and never touch git.

Tool descriptions are the executable interface for the model and are copied
**verbatim** from the interface design's tool-schema section -- do not paraphrase
them. Config resolution lives here in the adapter, never in ``core`` (operations
are config-agnostic and receive an already-resolved vault root).

Result shapes returned by each tool (the observable contract the test band
asserts):

* ``list_topics`` -> ``{"topics": [{"name", "page_count"}, ...]}``
* ``read_page``   -> ``{"topic", "path", "frontmatter", "frontmatter_error", "body", "content"}``
* ``search``      -> ``{"results", "next_cursor", "has_more", "total_count"}`` (the §1.6 envelope)
* ``list_links``  -> ``{"page", "direction", "out"?: [...], "in"?: [...]}`` (pointers, per direction)
* ``lint_check``  -> ``{"violations": [...]}`` (empty list == mechanically clean; a success, not an error)
"""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, cast

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.config import resolve
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.links import Link, inbound_links, outbound_links
from knotica.core.lint import lint_vault
from knotica.core.page import (
    PageNotFoundError,
    TopicNotFoundError,
)
from knotica.core.page import read_page as read_page_core
from knotica.core.status_counts import page_count
from knotica.core.topics import is_topic
from knotica.core.vault_layout import SCORED_FAMILIES, Family
from knotica.mcp_server import envelope, tool_params
from knotica.search import DEFAULT_PAGE_SIZE, InvalidCursorError, RipgrepBackend
from knotica.store import LocalFSStore, VaultStore

# --- verbatim tool descriptions (the executable interface; do not paraphrase) ---

_READ_PAGE_DESCRIPTION = (
    "Read one wiki page and return its raw markdown plus parsed frontmatter. Does NOT "
    "resolve or follow wikilinks (use `list_links`) and does NOT search (use `search`). "
    "Requires: the page exists under the given topic; call `search` first if unsure. "
    "The page argument accepts a topic-relative name (agent-memory), a "
    "vault-relative path from `search` (sources/<topic>/<citation-key> or "
    "<topic>/reports/...), or a bare citation key for a stored source. Returns: the full "
    "page body — call this only for pages you have decided to read. "
    "Pass vault to select a configured vault name (default: config default_vault)."
)

_LIST_TOPICS_DESCRIPTION = (
    "List all existing topics with their page counts. Call this FIRST in any operation to run "
    "the topic-inference policy (auto-place when a source clearly matches an existing topic; ask "
    "the user when ambiguous or when a new topic seems warranted). Returns the full set (topics "
    "are few); not paginated. "
    "Pass vault to select a configured vault name (default: config default_vault)."
)

#: The folder families `search` accepts on the wire. Personal notes are a
#: member but never a default -- see `_resolve_families`.
_VALID_FAMILIES: frozenset[str] = frozenset({"page", "source", "note"})

#: The `direction` vocabulary `_collect_links` branches on, named so the
#: published enum reads it rather than restating it.
_DIRECTIONS: tuple[str, ...] = ("out", "in", "both")

_Families = Annotated[
    list[str],
    tool_params.grounded(
        "Folder families to search. Empty (the default) searches the knowledge base "
        f"only; pass any of {sorted(_VALID_FAMILIES)} to widen it. Notes are private "
        "marginalia -- include 'note' only when the request is about the user's own "
        "notes. Must stay identical across a paginated walk.",
    ),
]

_Direction = Annotated[
    str,
    tool_params.grounded(
        "Which link edges to return: 'out' = pages this page links to, 'in' = "
        "backlinks, 'both' (the default) = both.",
        _DIRECTIONS,
    ),
]

_SEARCH_DESCRIPTION = (
    "Search page contents and return POINTERS (topic, page path, a short snippet, relevance "
    "score) — not full page bodies. Follow up with read_page for the results you choose. "
    "Paginated: pass the returned next_cursor to get the next page; has_more indicates more "
    "results exist. Default 10 results per call to keep responses small. "
    "Searches the knowledge base only by default. To include the user's personal notes, pass "
    "families=['page','source','note'] (or ['note'] for notes alone) — notes are private "
    "marginalia, so search them only when the user's request is about their own notes. "
    "families must stay identical across a paginated walk; changing it invalidates the cursor. "
    "Pass vault to select a configured vault name (default: config default_vault)."
)

_LIST_LINKS_DESCRIPTION = (
    "List wikilinks for one page. direction='out' = pages this page links to; direction='in' = "
    "backlinks (pages that link to this page); direction='both' = both. Returns link POINTERS "
    "(target topic + page + the line context), not page bodies. "
    "Pass vault to select a configured vault name (default: config default_vault)."
)

_LINT_CHECK_DESCRIPTION = (
    "Run DETERMINISTIC, mechanical lint checks over the vault (or one topic): frontmatter-schema "
    "conformance, unresolved wikilinks, reserved-name violations, root/overlay contradictions "
    "that are mechanically detectable, and index/log consistency. Returns a list of violations "
    "as DATA (an empty list means clean) — a successful call, never an error. This does NOT do "
    "semantic linting (contradictions, staleness); that is your job in the lint operation prompt, "
    "guided by the schemas. "
    "Pass vault to select a configured vault name (default: config default_vault)."
)

#: Every tool returns a ``CallToolResult`` so ``isError`` is set explicitly:
#: ``False`` on success, ``True`` on failure (the error rides in-band with the
#: MCP error flag). FastMCP forbids a ``dict | CallToolResult`` union, so the
#: success payload is wrapped uniformly rather than returned as a bare dict.
ToolResult = CallToolResult

#: Core read exceptions the adapter maps to failure envelopes (everything else
#: is a genuine bug and propagates).
_READ_EXCEPTIONS = (
    KnoticaError,
    TopicNotFoundError,
    PageNotFoundError,
    InvalidCursorError,
)


def register_read_tools(mcp: FastMCP) -> None:
    """Register the four cross-lane read primitives on ``mcp``.

    Called once at server construction. Purely registration -- no vault access
    happens here; every tool resolves config lazily when the model invokes it.
    """

    @mcp.tool(name="list_topics", description=_LIST_TOPICS_DESCRIPTION)
    def list_topics(vault: tool_params.Vault = "") -> ToolResult:
        return _read(lambda store, _root: _collect_topics(store), vault_name=vault)

    @mcp.tool(name="read_page", description=_READ_PAGE_DESCRIPTION)
    def read_page(
        topic: tool_params.Topic, page: tool_params.Page, vault: tool_params.Vault = ""
    ) -> ToolResult:
        return _read(lambda store, _root: _read_one_page(store, topic, page), vault_name=vault)

    @mcp.tool(name="search", description=_SEARCH_DESCRIPTION)
    def search(
        query: tool_params.Query,
        topic: tool_params.Topic = "",
        cursor: tool_params.Cursor = "",
        limit: tool_params.Limit = DEFAULT_PAGE_SIZE,
        # never mutated; the wire schema needs a literal `default: []`
        families: _Families = [],
        vault: tool_params.Vault = "",
    ) -> ToolResult:
        # Validated inside the _read closure so a rejected family name becomes
        # an INVALID_ARGUMENT envelope like every other bad argument, rather
        # than escaping as an unhandled exception past the adapter boundary.
        return _read(
            lambda _store, root: envelope.read_ok(
                RipgrepBackend(root)
                .search(
                    query,
                    topic=topic,
                    cursor=cursor,
                    limit=limit,
                    families=_resolve_families(families),
                )
                .render()
            ),
            vault_name=vault,
        )

    @mcp.tool(name="list_links", description=_LIST_LINKS_DESCRIPTION)
    def list_links(
        topic: tool_params.Topic,
        page: tool_params.Page,
        direction: _Direction = "both",
        vault: tool_params.Vault = "",
    ) -> ToolResult:
        return _read(
            lambda store, _root: _collect_links(store, topic, page, direction), vault_name=vault
        )


def register_read_lane_tools(mcp: FastMCP) -> None:
    """Register ``lint_check``, which is reachable only through a lane.

    Split from :func:`register_read_tools` because the published surface no
    longer carries it: ``tend action=lint_check`` is the one way in. The
    registration still exists because that is the seam the lane dispatchers
    collect their handlers through -- a lane routes to *this* function object,
    not to a copy of it. See ``tools_dispatch_lane_common.py``.
    """

    @mcp.tool(name="lint_check", description=_LINT_CHECK_DESCRIPTION)
    def lint_check(topic: tool_params.Topic = "", vault: tool_params.Vault = "") -> ToolResult:
        return _read(
            lambda store, _root: envelope.read_ok(
                {"violations": [violation.render() for violation in lint_vault(store, topic)]}
            ),
            vault_name=vault,
        )


def _read(
    operation: Callable[[VaultStore, Path], dict[str, Any]], vault_name: str = ""
) -> ToolResult:
    """Resolve the vault per call and run ``operation``, envelope-ing every outcome.

    ``vault_name`` optionally selects a configured vault (empty -> the configured
    ``default_vault``). An unconfigured vault yields the ``NOT_CONFIGURED``
    envelope before any store is built; a typed read exception from ``operation``
    is mapped to its failure envelope. Nothing reaches the transport as an
    exception.
    """
    try:
        vault = resolve(vault=vault_name.strip() or None)
    except KnoticaError as error:
        return envelope.error_envelope(error)
    store = LocalFSStore(vault.path)
    try:
        payload = operation(store, vault.path)
    except _READ_EXCEPTIONS as exc:
        return envelope.map_read_exception(exc)
    return envelope.success_result(payload)


def _collect_topics(store: VaultStore) -> dict[str, Any]:
    """Enumerate topic directories with their content-page counts."""
    topics = [
        {"name": name, "page_count": page_count(store, name)}
        for name in store.list_dir("")
        if is_topic(store, name)
    ]
    return envelope.read_ok({"topics": topics})


def _read_one_page(store: VaultStore, topic: str, page: str) -> dict[str, Any]:
    """Read one page (raising the typed not-found exceptions) and envelope it."""
    parsed = read_page_core(store, topic, page)
    return envelope.read_ok(
        {
            "topic": parsed.topic,
            "path": parsed.path,
            "frontmatter": parsed.frontmatter,
            "frontmatter_error": parsed.frontmatter_error,
            "body": parsed.body,
            "content": parsed.raw,
        }
    )


def _collect_links(store: VaultStore, topic: str, page: str, direction: str) -> dict[str, Any]:
    """Return the page's link pointers for the requested direction(s).

    The page's existence is validated first (reusing the read layer's typed
    ``TOPIC_NOT_FOUND`` / ``PAGE_NOT_FOUND`` semantics, including nearest-match
    suggestions) so a missing target is an actionable envelope, not empty output.
    """
    parsed = read_page_core(store, topic, page)
    path = parsed.path
    result: dict[str, Any] = {"page": path, "direction": direction}
    if direction in ("out", "both"):
        result["out"] = [_render_outbound(link) for link in outbound_links(store, path)]
    if direction in ("in", "both"):
        result["in"] = [_render_inbound(link) for link in inbound_links(store, path)]
    return envelope.read_ok(result)


def _render_outbound(link: Link) -> dict[str, Any]:
    """Render an outbound edge as a pointer (target path + line context)."""
    return {
        "target": link.target,
        "raw_target": link.raw_target,
        "alias": link.alias,
        "line": link.line,
        "context": link.context,
        "resolved": link.resolved,
    }


def _render_inbound(link: Link) -> dict[str, Any]:
    """Render a backlink as a pointer (source path + line context)."""
    return {
        "source": link.source,
        "raw_target": link.raw_target,
        "alias": link.alias,
        "line": link.line,
        "context": link.context,
    }


def _resolve_families(requested: list[str]) -> frozenset[Family]:
    """Validate the wire ``families`` argument into a backend family set.

    An empty list means "unspecified" and resolves to
    :data:`SCORED_FAMILIES` -- the wire schema needs a literal ``[]`` default,
    so ``[]`` cannot also mean "search nothing" without making the safe
    behaviour unreachable for a caller that simply omits the argument. A
    caller wanting no results should not call search.

    Unknown names are refused rather than ignored: silently dropping an
    unrecognised family would return the default corpus while the caller
    believed it had widened the search, which is a wrong answer wearing a
    successful envelope.
    """
    if not requested:
        return SCORED_FAMILIES
    unknown = sorted({name for name in requested if name not in _VALID_FAMILIES})
    if unknown:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"search does not know the folder families {unknown}.",
            fix=f"Use any of {sorted(_VALID_FAMILIES)}, or omit families for the default corpus.",
        )
    return frozenset(cast("set[Family]", set(requested)))

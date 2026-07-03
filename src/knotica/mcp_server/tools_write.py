"""The four mutating tools -- config-resolving adapters over ``core.operations``.

Each tool resolves ``config.toml`` -> vault root *per call* (so a freshly
configured vault takes effect with no restart), then delegates to the matching
``knotica.core.operations`` function and renders the outcome as a
:class:`~mcp.types.CallToolResult`. An unconfigured vault returns the
``NOT_CONFIGURED`` failure envelope before any store is built -- exactly as the
read adapter does.

The operations are the sole writers: they own the single
``VaultTransaction`` (lock + scrub + one-commit-per-effective-mutation) and
already return the shared result envelope (``ok`` / ``err`` from
:mod:`knotica.core.errors`). This adapter therefore does no envelope
construction of its own -- it dispatches the returned envelope on the presence
of an ``error`` key: a success envelope (the pointer, plus any secret-scrub
``warnings``) becomes an ``isError=False`` result; a failure envelope becomes an
``isError=True`` result. Config resolution lives here in the adapter, never in
``core`` (operations are config-agnostic and receive an already-resolved vault
root). This module imports no git and no store beyond the local backend needed
to hand the operations their vault -- the transaction inside ``core.operations``
is the only thing that writes.

Tool descriptions are the executable interface for the model and are copied
**verbatim** from the interface design's tool-schema section -- do not paraphrase
them.

Result shapes returned by each tool are the operation-native pointer envelopes
(the observable contract the test band asserts):

* ``write_page``     -> ``{"path", "commit_sha", "changed"}`` (+ ``warnings`` when scrub redacted)
* ``store_source``   -> ``{"path", "commit_sha", "changed"}`` (``changed=False`` on the immutable no-op)
* ``create_topic``   -> ``{"topic", "path", "commit_sha", "existed"}``
* ``curate_example`` -> ``{"path", "example_count", "appended"}``
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core import operations
from knotica.core.config import resolve
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.mcp_server import envelope
from knotica.store import LocalFSStore, VaultStore

# --- verbatim tool descriptions (the executable interface; do not paraphrase) ---

_WRITE_PAGE_DESCRIPTION = (
    "Create or replace one wiki page with full markdown content (including frontmatter). "
    "Performs, as ONE atomic unit (one commit): secret-scrub, atomic write (temp+rename), a "
    "single git commit (the audit unit), an append to log.md, and — when index_entry is "
    "supplied — an upsert of this page's line in the root index.md catalog. Idempotent: if the "
    "resulting vault state is identical (page content AND index line unchanged), no commit is "
    "made and changed=false is returned. Does NOT create topics (use create_topic). NEVER "
    "target index.md, log.md, or SCHEMA.md as the 'page' — those reserved files are maintained "
    "only as side effects here (pass index_entry to update the catalog); a reserved 'page' fails "
    "fast with RESERVED_NAME. Also fails fast on invalid frontmatter."
)

_STORE_SOURCE_DESCRIPTION = (
    "Persist a raw source immutably into sources/<topic>/<citation_key> with provenance "
    "frontmatter and a single git commit. You (the client) fetch and convert the source to "
    "markdown first (client-as-brain); this tool only persists what you pass. Immutable: if "
    "citation_key already exists with identical content the call is a no-op success; if it exists "
    "with DIFFERENT content the call FAILS (SOURCE_EXISTS) — pick a new citation_key. Use the "
    "paper's citation key as the filename (e.g. 'wang2024awm')."
)

_CREATE_TOPIC_DESCRIPTION = (
    "Create a new topic: its directory, an empty SCHEMA.md overlay that inherits root (divergence "
    "is earned, so the overlay starts empty), the hidden .knotica/ state (datasets/qa.jsonl empty; "
    "prompts/ and compiled/ empty dirs — metrics.jsonl is NOT created here: it is a lazy Phase-2 "
    "artifact whose absence means 'not yet evaluated'), and an index.md entry — as one git commit. "
    "Idempotent: if the topic already exists, returns existed=true and makes no commit. Fails fast "
    "(RESERVED_NAME) if the name collides with a reserved top-level name (sources, index.md, "
    "log.md, SCHEMA.md, START_HERE.md, .knotica, .git)."
)

_CURATE_EXAMPLE_DESCRIPTION = (
    "Append one curated (query, pages-used, answer, verdict) example to the topic's "
    ".knotica/datasets/qa.jsonl (the DSPy flywheel), as one git commit. Idempotent by "
    "content-hash: re-submitting an identical example is a no-op. Returns example_count so you can "
    "report 'N examples, M to compile-ready'. Solicit this at the end of every ingest and query "
    "operation."
)

#: Every tool returns a ``CallToolResult`` so ``isError`` is set explicitly:
#: ``False`` on success, ``True`` on failure. FastMCP forbids a
#: ``dict | CallToolResult`` union return, so both outcomes are wrapped uniformly.
ToolResult = CallToolResult


def register_write_tools(mcp: FastMCP) -> None:
    """Register the four mutating tools on ``mcp``.

    Called once at server construction. Purely registration -- no vault access
    happens here; every tool resolves config lazily when the model invokes it.
    """

    @mcp.tool(name="write_page", description=_WRITE_PAGE_DESCRIPTION)
    def write_page(
        topic: str,
        page: str,
        content: str,
        summary: str,
        index_entry: str = "",
    ) -> ToolResult:
        return _write(
            lambda store, root: operations.write_page(
                store, root, topic, page, content, summary, index_entry=index_entry or None
            )
        )

    @mcp.tool(name="store_source", description=_STORE_SOURCE_DESCRIPTION)
    def store_source(
        topic: str,
        citation_key: str,
        title: str,
        content: str,
        source_url: str,
        source_type: str = "markdown",
    ) -> ToolResult:
        return _write(
            lambda store, root: operations.store_source(
                store, root, topic, citation_key, title, content, source_url, source_type
            )
        )

    @mcp.tool(name="create_topic", description=_CREATE_TOPIC_DESCRIPTION)
    def create_topic(topic: str, description: str = "") -> ToolResult:
        return _write(
            lambda store, root: operations.create_topic(
                store, root, topic, description=description or None
            )
        )

    @mcp.tool(name="curate_example", description=_CURATE_EXAMPLE_DESCRIPTION)
    def curate_example(
        topic: str,
        query: str,
        answer: str,
        verdict: str,
        pages_used: list[str] | None = None,
        notes: str = "",
    ) -> ToolResult:
        return _write(
            lambda store, root: operations.curate_example(
                store,
                root,
                topic,
                query,
                tuple(pages_used or ()),
                answer,
                verdict,
                notes=notes or None,
            )
        )


def _write(operation: Callable[[VaultStore, Path], dict[str, Any]]) -> ToolResult:
    """Resolve the vault per call, run ``operation``, and render its envelope.

    An unconfigured vault yields the ``NOT_CONFIGURED`` failure envelope before
    any store is built. Otherwise the operation is the sole writer (it owns the
    transaction) and returns the shared result envelope, which this adapter maps
    to a ``CallToolResult`` by the presence of an ``error`` key.
    """
    try:
        vault = resolve()
    except KnoticaError as error:
        return envelope.error_envelope(error)
    store = LocalFSStore(vault.path)
    return _render(operation(store, vault.path))


def _render(result_envelope: dict[str, Any]) -> ToolResult:
    """Map an operation's result envelope to an ``isError``-flagged tool result.

    A failure envelope (``{"error": {...}}``) becomes an ``isError=True`` result;
    every other envelope is a success (its pointer plus any secret-scrub
    ``warnings``) and becomes ``isError=False``. Both shapes reuse the shared
    :mod:`~knotica.mcp_server.envelope` renderers rather than reconstructing the
    JSON/text wrapping here.
    """
    error = result_envelope.get("error")
    if error is not None:
        return envelope.error_envelope(_as_knotica_error(error))
    return envelope.success_result(result_envelope)


def _as_knotica_error(error: dict[str, Any]) -> KnoticaError:
    """Rebuild a :class:`KnoticaError` from a failure envelope's error object.

    The operations return already-rendered failure envelopes (not exceptions),
    so this round-trips the ``{code, message, fix, retryable}`` object back into
    a ``KnoticaError`` to reuse :func:`envelope.error_envelope` -- preserving the
    operation's exact ``fix`` text and ``retryable`` flag (e.g. ``LOCK_BUSY``).
    """
    return KnoticaError(
        ErrorCode(error["code"]),
        error["message"],
        fix=error["fix"],
        retryable=error["retryable"],
    )

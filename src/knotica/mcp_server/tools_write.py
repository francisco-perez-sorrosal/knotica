"""The four mutating tools -- config-resolving adapters over ``core.operations``.

Each tool resolves ``config.toml`` -> vault root *per call* (so a freshly
configured vault takes effect with no restart), then delegates to the matching
``knotica.core.operations`` function and renders the outcome as a
:class:`~mcp.types.CallToolResult`. An unconfigured vault returns the
``NOT_CONFIGURED`` failure envelope before any store is built -- exactly as the
read adapter does.

Successful mutations also append a best-effort ingest-activity event so the
dashboard activity pane can show store/write/curate checkpoints without relying
on the client to remember ``ingest_progress``.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core import operations
from knotica.core.config import resolve
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.ingest_activity import append_ingest_event
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

ToolResult = CallToolResult


def register_write_tools(mcp: FastMCP) -> None:
    """Register the four mutating tools on ``mcp``."""

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
            ),
            activity=lambda result: {
                "topic": topic,
                "stage": "write_page",
                "title": f"Wrote page {page}"
                + ("" if result.get("changed", True) else " (unchanged)"),
                "status": "ok",
                "detail": summary.strip() or page,
                "path": str(result.get("path") or ""),
                "commit_sha": str(result.get("commit_sha") or ""),
            },
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
            ),
            activity=lambda result: {
                "topic": topic,
                "stage": "store_source",
                "title": f"Stored source {citation_key}"
                + ("" if result.get("changed", True) else " (already present)"),
                "status": "ok",
                "detail": title,
                "citation_key": citation_key,
                "path": str(result.get("path") or ""),
                "commit_sha": str(result.get("commit_sha") or ""),
            },
        )

    @mcp.tool(name="create_topic", description=_CREATE_TOPIC_DESCRIPTION)
    def create_topic(topic: str, description: str = "") -> ToolResult:
        return _write(
            lambda store, root: operations.create_topic(
                store, root, topic, description=description or None
            ),
            activity=lambda result: {
                "topic": topic,
                "stage": "resolve_topic",
                "title": (
                    f"Topic {topic} ready" if result.get("existed") else f"Created topic {topic}"
                ),
                "status": "ok",
                "detail": description,
                "path": str(result.get("path") or ""),
                "commit_sha": str(result.get("commit_sha") or ""),
            },
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
            ),
            activity=lambda result: {
                "topic": topic,
                "workflow": "curate",
                "stage": "curate",
                "title": f"Curated example ({verdict})",
                "status": "ok",
                "detail": query[:160],
                "path": str(result.get("path") or ""),
                "commit_sha": str(result.get("commit_sha") or ""),
                # Finish the curate workflow so the rail does not stay "live".
                "complete": True,
            },
        )


def _write(
    operation: Callable[[VaultStore, Path], dict[str, Any]],
    *,
    activity: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> ToolResult:
    """Resolve the vault per call, run ``operation``, and render its envelope."""
    try:
        vault = resolve()
    except KnoticaError as error:
        return envelope.error_envelope(error)
    store = LocalFSStore(vault.path)
    result_envelope = operation(store, vault.path)
    if activity is not None and "error" not in result_envelope:
        _best_effort_activity(store, vault.path, activity(result_envelope))
    return _render(result_envelope)


def _best_effort_activity(store: VaultStore, vault_path: Path, fields: dict[str, Any]) -> None:
    """Never let the activity journal fail a mutating tool."""
    try:
        event = append_ingest_event(
            store,
            vault_path,
            topic=str(fields.get("topic") or ""),
            stage=str(fields.get("stage") or "info"),
            title=str(fields.get("title") or ""),
            status=str(fields.get("status") or "ok"),
            detail=str(fields.get("detail") or ""),
            citation_key=str(fields.get("citation_key") or ""),
            path=str(fields.get("path") or ""),
            commit_sha=str(fields.get("commit_sha") or ""),
            source="server",
            workflow=str(fields.get("workflow") or ""),
        )
        if fields.get("complete"):
            append_ingest_event(
                store,
                vault_path,
                topic=str(event.get("topic") or fields.get("topic") or ""),
                stage="complete",
                title="Curation complete",
                status="ok",
                run_id=str(event.get("run_id") or ""),
                path=str(fields.get("path") or ""),
                commit_sha=str(fields.get("commit_sha") or ""),
                source="server",
                workflow=str(event.get("workflow") or fields.get("workflow") or "curate"),
            )
    except Exception:  # noqa: BLE001 - journal must not break writes
        return


def _render(result_envelope: dict[str, Any]) -> ToolResult:
    """Map an operation's result envelope to an ``isError``-flagged tool result."""
    error = result_envelope.get("error")
    if error is not None:
        return envelope.error_envelope(_as_knotica_error(error))
    return envelope.success_result(result_envelope)


def _as_knotica_error(error: dict[str, Any]) -> KnoticaError:
    """Rebuild a :class:`KnoticaError` from a failure envelope's error object."""
    return KnoticaError(
        ErrorCode(error["code"]),
        error["message"],
        fix=error["fix"],
        retryable=error["retryable"],
    )

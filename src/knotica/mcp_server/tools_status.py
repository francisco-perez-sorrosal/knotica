"""Dashboard status tools — ``wiki_status`` and ``metrics_read``.

Read-only, stateless, topic-explicit. Both resolve ``config.toml`` per call and
honor the ``NOT_CONFIGURED`` contract. Business logic lives in
:mod:`knotica.core.status` / :mod:`knotica.core.metrics`; this module is the
thin MCP adapter (dec-003).

Result shapes (the observable contract; feed TS type generation in M3):

* ``wiki_status``  -> ``{schema_version, vault, vault_name, vault_path,
  default_vault, available_vaults, compile_ready_threshold, topics,
  totals, last_lint, unpushed, gate, loop}``
* ``metrics_read`` -> ``{topic, records, has_more, next_before_generation,
  skipped_malformed}``
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.config import ResolvedVault, list_vaults, resolve
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.metrics import (
    DEFAULT_METRICS_LIMIT,
    MAX_METRICS_LIMIT,
    render_metrics_window,
)
from knotica.core.page import PageNotFoundError, TopicNotFoundError
from knotica.core.status import gather_wiki_status
from knotica.mcp_server import envelope
from knotica.search import InvalidCursorError
from knotica.store import LocalFSStore, VaultStore

_WIKI_STATUS_DESCRIPTION = (
    "Report deterministic vault status for the dashboard: active vault name and "
    "path, configured vaults (for switching), per-topic page/curated/lint counts, "
    "compile-ready progress, last eval scalar (if any), and gate/loop stage. "
    "Pass topic to scope to one topic; omit or pass empty for the whole vault. "
    "Pass vault to select a configured vault name (default: config default_vault). "
    "Read-only — no commits, no lock."
)

_METRICS_READ_DESCRIPTION = (
    "Read a window of metrics.jsonl eval-history records for one topic, for "
    "scalar-over-generations charting. Returns records in ascending generation "
    "order. Pass before_generation to page into older history; has_more / "
    "next_before_generation support cursor-style windows. Pass vault to select "
    "a configured vault name. Malformed lines are skipped and counted in "
    "skipped_malformed. Read-only."
)

ToolResult = CallToolResult

_READ_EXCEPTIONS = (
    KnoticaError,
    TopicNotFoundError,
    PageNotFoundError,
    InvalidCursorError,
)


def register_status_tools(mcp: FastMCP) -> None:
    """Register ``wiki_status`` and ``metrics_read`` on ``mcp``."""

    @mcp.tool(name="wiki_status", description=_WIKI_STATUS_DESCRIPTION)
    def wiki_status(topic: str = "", vault: str = "") -> ToolResult:
        return _read(
            vault,
            lambda store, resolved: envelope.read_ok(
                _wiki_payload(store, resolved.path, resolved.name, topic=topic)
            ),
        )

    @mcp.tool(name="metrics_read", description=_METRICS_READ_DESCRIPTION)
    def metrics_read(
        topic: str,
        limit: int = DEFAULT_METRICS_LIMIT,
        before_generation: int | None = None,
        vault: str = "",
    ) -> ToolResult:
        return _read(
            vault,
            lambda store, _resolved: envelope.read_ok(
                _metrics_payload(store, topic, limit=limit, before_generation=before_generation)
            ),
        )


def _wiki_payload(store: VaultStore, vault_path: Path, vault_name: str, *, topic: str) -> dict[str, Any]:
    catalog = list_vaults()
    return gather_wiki_status(
        store,
        vault_path,
        topic=topic,
        vault_name=vault_name,
        default_vault=str(catalog.get("default_vault") or vault_name),
        available_vaults=list(catalog.get("vaults") or []),
    )


def _metrics_payload(
    store: VaultStore,
    topic: str,
    *,
    limit: int,
    before_generation: int | None,
) -> dict[str, Any]:
    """Validate window args, then render the metrics window."""
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    if limit < 1 or limit > MAX_METRICS_LIMIT:
        raise KnoticaError(
            code=ErrorCode.INVALID_CURSOR,
            message=(
                f"metrics_read failed because limit must be in 1..{MAX_METRICS_LIMIT}, got {limit}"
            ),
            fix=f"Pass limit between 1 and {MAX_METRICS_LIMIT}.",
        )
    if before_generation is not None and before_generation < 0:
        raise KnoticaError(
            code=ErrorCode.INVALID_CURSOR,
            message=(
                "metrics_read failed because before_generation must be >= 0, "
                f"got {before_generation}"
            ),
            fix="Restart the metrics window without before_generation, or pass a non-negative generation.",
        )
    return render_metrics_window(
        store,
        cleaned,
        limit=limit,
        before_generation=before_generation,
    )


def _read(
    vault_name: str,
    operation: Callable[[VaultStore, ResolvedVault], dict[str, Any]],
) -> ToolResult:
    """Resolve the vault per call and run ``operation``, envelope-ing every outcome."""
    try:
        vault = resolve(vault=_vault_arg(vault_name))
    except KnoticaError as error:
        return envelope.error_envelope(error)
    store = LocalFSStore(vault.path)
    try:
        payload = operation(store, vault)
    except _READ_EXCEPTIONS as exc:
        return envelope.map_read_exception(exc)
    return envelope.success_result(payload)


def _vault_arg(vault: str) -> str | None:
    cleaned = vault.strip()
    return cleaned or None

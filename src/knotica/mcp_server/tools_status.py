"""Dashboard status tools — ``wiki_status`` and ``metrics_read``.

Read-only, stateless, topic-explicit. Both resolve ``config.toml`` per call and
honor the ``NOT_CONFIGURED`` contract. Business logic lives in
:mod:`knotica.core.status` / :mod:`knotica.core.metrics`; this module is the
thin MCP adapter (dec-003).

Result shapes (the observable contract; feed TS type generation in M3):

* ``wiki_status``  -> ``{schema_version, vault, vault_name, vault_path,
  default_vault, available_vaults, compile_ready_threshold, topics,
  totals, last_lint, unpushed, gate, loop}`` where ``loop`` includes
  ``baseline_frozen``, ``baseline_scalar``, ``pending_candidates``, and
  ``metrics_hint`` for the Heal dashboard stepper. This is the ``view=
  "summary"`` (default) shape. ``view="scope"`` returns the cheapest
  progressive view instead: ``{schema_version, vault_name, topics, totals}``
  -- topic enumeration only, for the conversational routing scope-check.
  ``view="process_model"`` returns the served process-model declaration:
  ``{schema_version, lanes, lane_stages}`` where ``lane_stages`` maps each
  lane to its ordered ``{id, title, handoff}`` stages -- structure only, no
  predicates, vault- and topic-independent. ``view="attention"`` returns the
  cross-topic inbox: ``{schema_version, vault_name, topics, totals,
  last_lint, drift}`` where each ``topics`` row is ``{topic, suggestions,
  compile_ready, runner}`` -- every topic in the vault, no lint walk, no
  note-anchor resolution.
* ``metrics_read`` -> ``{topic, records, has_more, next_before_generation,
  skipped_malformed}``
* ``baseline_probe`` -> ``{topic, scalar, harness_version, runner_mode, …}``
  after appending one naive zero cold-start line to ``metrics.jsonl``.
"""

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.baseline_probe import run_baseline_probe
from knotica.core.config import list_vaults
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.metrics import (
    DEFAULT_METRICS_LIMIT,
    MAX_METRICS_LIMIT,
    render_metrics_window,
)
from knotica.core.page import TopicNotFoundError
from knotica.core.status import gather_wiki_status
from knotica.mcp_server import envelope
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

_WIKI_STATUS_DESCRIPTION = (
    "Report deterministic vault status for the dashboard: active vault name and "
    "path, configured vaults (for switching), per-topic page/curated/lint counts, "
    "compile-ready progress, last eval scalar (if any), and gate/loop stage. "
    "Pass topic to scope to one topic; omit or pass empty for the whole vault. "
    "Pass vault to select a configured vault name (default: config default_vault). "
    'Pass view="scope" for the cheapest read (topic names only, no stats) -- use '
    "it to check which topics this vault covers before routing a conversation "
    'turn. Pass view="process_model" to read the six process lanes and their '
    "ordered stages (id, title, handoff) directly from the server -- vault- and "
    'topic-independent. Pass view="attention" for the cross-topic inbox: every '
    "topic's pending suggestions, refused-awaiting-rework backlog, "
    "compile-readiness and loop-runner liveness, plus the last-lint date and "
    "its staleness -- deliberately cheaper than summary (it runs no lint pass "
    "and resolves no note anchors, so its drift row is a marker carrying no "
    "count) and it covers every topic, ignoring topic. "
    'Omit view or pass "summary" for the full payload above. '
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

_BASELINE_PROBE_DESCRIPTION = (
    "Persist a naive cold-start anchor (scalar 0.0) for one topic on the live "
    "vault when no eval/compile score exists yet. No LLM, no retrieval scoring, "
    "never uses golden.jsonl or qa.jsonl. Appends one metrics.jsonl record with "
    "harness_version naive-cold-start. Chart/UX floor only — not gate-quality; "
    "run knotica eval or compile before freezing the loop gate. Pass vault to "
    "select a configured vault."
)

ToolResult = CallToolResult


def register_status_tools(mcp: FastMCP) -> None:
    """Register ``wiki_status`` on ``mcp``."""

    @mcp.tool(name="wiki_status", description=_WIKI_STATUS_DESCRIPTION)
    def wiki_status(topic: str = "", vault: str = "", view: str = "summary") -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: envelope.read_ok(
                _wiki_payload(store, resolved.path, resolved.name, topic=topic, view=view)
            ),
        )


def register_status_lane_tools(mcp: FastMCP) -> None:
    """Register ``metrics_read`` and ``baseline_probe``, reachable only through a lane.

    Split from :func:`register_status_tools` because the published surface no
    longer carries them: ``improve action=metrics_read`` and
    ``improve action=baseline_probe`` are the ways in. The registrations still
    exist because that is the seam the lane dispatchers collect their handlers
    through -- a lane routes to *these* function objects, not to copies of
    them. See ``tools_dispatch_lane_common.py``.
    """

    @mcp.tool(name="metrics_read", description=_METRICS_READ_DESCRIPTION)
    def metrics_read(
        topic: str,
        limit: int = DEFAULT_METRICS_LIMIT,
        before_generation: int | None = None,
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, _resolved: envelope.read_ok(
                _metrics_payload(store, topic, limit=limit, before_generation=before_generation)
            ),
        )

    @mcp.tool(name="baseline_probe", description=_BASELINE_PROBE_DESCRIPTION)
    def baseline_probe(topic: str, vault: str = "") -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: run_baseline_probe(store, resolved.path, topic).render(),
        )


#: Views that never read the vault catalog -- ``scope`` is config + topic
#: enumeration only, ``process_model`` reads no vault state at all, and
#: ``attention`` reports on the active vault alone (its budget rules out the
#: extra ``list_vaults`` config read on every poll).
_CATALOG_FREE_VIEWS = frozenset({"scope", "process_model", "attention"})


def _wiki_payload(
    store: VaultStore, vault_path: Path, vault_name: str, *, topic: str, view: str = "summary"
) -> dict[str, Any]:
    if view in _CATALOG_FREE_VIEWS:
        return gather_wiki_status(store, vault_path, topic=topic, vault_name=vault_name, view=view)
    catalog = list_vaults()
    return gather_wiki_status(
        store,
        vault_path,
        topic=topic,
        vault_name=vault_name,
        default_vault=str(catalog.get("default_vault") or vault_name),
        available_vaults=list(catalog.get("vaults") or []),
        view=view,
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
            code=ErrorCode.INVALID_ARGUMENT,
            message=(
                f"metrics_read failed because limit must be in 1..{MAX_METRICS_LIMIT}, got {limit}"
            ),
            fix=f"Pass limit between 1 and {MAX_METRICS_LIMIT}.",
        )
    if before_generation is not None and before_generation < 0:
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message=(
                "metrics_read failed because before_generation must be >= 0, "
                f"got {before_generation}"
            ),
            fix="Pass a non-negative before_generation, or omit it to read the newest window.",
        )
    return render_metrics_window(
        store,
        cleaned,
        limit=limit,
        before_generation=before_generation,
    )

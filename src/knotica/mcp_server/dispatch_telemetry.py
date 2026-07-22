"""Dispatcher mis-selection telemetry — the falsifier instrument for the
two-tier tool surface.

The operator long-tail was collapsed into seven action-parameterized
dispatchers behind additive aliases. The dissent on that consolidation is that
an ``action`` enum can reintroduce god-endpoint selection ambiguity. This module
is the lightweight, dependency-free instrument that keeps a future per-domain
revert *evidence-based*: it emits structured log lines for three signals —

1. every dispatcher invocation (``tool``/``action``/``topic``),
2. a dispatcher call rejected for an unknown ``action``,
3. a deprecated flat alias still being invoked instead of its dispatcher.

Counting (1) vs (3) per domain reveals dispatcher adoption; (2) reveals
selection ambiguity within a domain. Either signal can justify reverting one
dispatcher back to flat tools without touching the other six.

:data:`DEPRECATED_ALIASES` is the single source of truth mapping each replaced
thin tool to its dispatcher call shape — it drives both the deprecation note
appended to a thin tool's description and the alias-invocation log line.
"""

from __future__ import annotations

import logging

__all__ = [
    "DEPRECATED_ALIASES",
    "deprecation_suffix",
    "record_deprecated_alias",
    "record_dispatch",
    "record_rejected_action",
]

_LOGGER = logging.getLogger(__name__)

#: Replaced thin tool name -> the dispatcher call shape that supersedes it.
#: One release cycle of additive aliasing keeps both reachable; this map is the
#: authority for which flat tools are deprecated and what replaces each.
DEPRECATED_ALIASES: dict[str, str] = {
    # loop dispatcher
    "loop_run_once": "loop(action='run_once', ...)",
    "loop_set_baseline": "loop(action='set_baseline', ...)",
    "loop_baseline_policy": "loop(action='baseline_policy', ...)",
    "loop_rebaseline": "loop(action='rebaseline', ...)",
    # vault_health dispatcher
    "doctor_run": "vault_health(action='doctor', ...)",
    "doctor_repair": "vault_health(action='repair', ...)",
    "okf_check": "vault_health(action='okf_check', ...)",
    "okf_repair": "vault_health(action='okf_repair', ...)",
    "vault_lint": "vault_health(action='lint', ...)",
    "vault_metadata_tree": "vault_health(action='metadata_tree', ...)",
    # branches dispatcher
    "branch_scoreboard": "branches(action='scoreboard', ...)",
    "loop_promote": "branches(action='promote_loop', ...)",
    "branch_promote": "branches(action='promote', kind=..., ...)",
    "branch_delete": "branches(action='delete', ...)",
    # compile dispatcher
    "compile_run": "compile(action='run', ...)",
    "compile_status": "compile(action='status', ...)",
    "compile_promote": "compile(action='promote', ...)",
    # datasets dispatcher
    "datasets_inventory": "datasets(action='inventory', ...)",
    "datasets_records": "datasets(action='records', ...)",
    "datasets_bootstrap": "datasets(action='bootstrap', ...)",
    "datasets_bootstrap_train": "datasets(action='bootstrap_train', ...)",
    "datasets_freeze": "datasets(action='freeze', ...)",
    # arena dispatcher
    "arena_status": "arena(action='status', ...)",
    "arena_history": "arena(action='history', ...)",
    # golden dispatcher
    "golden_review_load": "golden(action='load', ...)",
    "golden_review_save": "golden(action='save', ...)",
}


def deprecation_suffix(alias: str) -> str:
    """Return the deprecation sentence to append to ``alias``'s description.

    The thin tool stays registered and reachable for one release cycle; the
    note points the model (and human readers) at the dispatcher call shape.
    """
    return f" Deprecated: use {DEPRECATED_ALIASES[alias]}; kept for one release cycle."


def record_deprecated_alias(alias: str) -> None:
    """Log that a deprecated flat alias was invoked instead of its dispatcher."""
    _LOGGER.info(
        "deprecated-alias-invoked tool=%s superseded_by=%s",
        alias,
        DEPRECATED_ALIASES.get(alias, "?"),
    )


def record_dispatch(dispatcher: str, action: str, topic: str) -> None:
    """Log a resolved dispatcher invocation (per-domain adoption signal)."""
    _LOGGER.info("dispatch tool=%s action=%s topic=%s", dispatcher, action, topic)


def record_rejected_action(dispatcher: str, action: str, valid_actions: tuple[str, ...]) -> None:
    """Log a dispatcher call rejected for an unknown ``action`` (ambiguity signal)."""
    _LOGGER.warning(
        "dispatch-rejected tool=%s action=%r valid=%s",
        dispatcher,
        action,
        "|".join(valid_actions),
    )

"""The gap-fill drain + decide leaf -- join gaps to discovered sources, gate them.

Six modules over two committed per-topic queues, ``<topic>/.knotica/gaps/gaps.jsonl``
and ``<topic>/.knotica/suggestions/suggestions.jsonl``. This package is the import
surface: every name the rest of the codebase uses is re-exported here, so a
consumer writes ``from knotica.core.gapfill import apply_decision`` and never
names a submodule.

* :mod:`~knotica.core.gapfill.queue_io` -- the queues as files: read, index,
  replace, serialize, plus candidate identity, the published-branch protection
  both writers consult, and the shared refusal wording.
* :mod:`~knotica.core.gapfill.synthetic` -- filing a gap no eval produced
  (:func:`report_gap`, :func:`file_retracted_gap`).
* :mod:`~knotica.core.gapfill.gap_review` -- the gap lifecycle: the human
  dismiss/reopen transition, its cascade onto that gap's suggestions, and the
  ``open -> resolved`` close a merged gate verdict performs.
* :mod:`~knotica.core.gapfill.review` -- every status transition of a *suggestion*
  record: the human approve/reject/defer/mark-ingested/withdraw lifecycle and the
  machine gate verdict that mirrors its legality.
* :mod:`~knotica.core.gapfill.drain` -- one drain: select, formulate, join, heal,
  write once.
* :mod:`~knotica.core.gapfill.discovery_bridge` -- config + env keys to a real
  ``DiscoveryService``, or ``None`` when no key is configured.

This package and ``core.source_inventory`` are the only P3 code touching
``discovery/`` -- and both do so **lazily, inside the function that needs it**,
never at module top level (the inventory reaches only the ``normalize`` identity
leaf). That keeps the package importable on the MCP cold-start path (an MCP tool
delegates to :func:`apply_decision`, whose module imports no ``discovery`` at all)
without dragging the heavy search chain onto that path.
"""

from __future__ import annotations

from knotica.core.gapfill.discovery_bridge import build_default_discovery_service

# The redundant ``X as X`` aliases below mark a deliberate re-export of a name
# that is not in ``__all__``: ``DEFAULT_MAX_RESULTS`` and ``DecisionPlan`` were
# module attributes of the pre-split ``gapfill.py`` and stay reachable here, and
# ``_source_key`` is imported by name in a test (see the note below).
from knotica.core.gapfill.drain import (
    DEFAULT_MAX_RESULTS as DEFAULT_MAX_RESULTS,
    RefreshResult,
    build_suggestion_records,
    formulate_query,
    refresh_suggestions_for_gaps,
)
from knotica.core.gapfill.gap_review import GapDecisionResult, apply_gap_decision

# ``_source_key`` is re-exported deliberately: ``tests/discovery/test_normalize.py``
# pins that the queue's dedup key and ``discovery.normalize``'s cannot drift, and
# imports it from this package rather than from a submodule path.
from knotica.core.gapfill.queue_io import _source_key as _source_key
from knotica.core.gapfill.queue_io import suggestions_path
from knotica.core.gapfill.review import (
    GATE_VERDICT_MERGED,
    GATE_VERDICT_REFUSED,
    DecisionPlan as DecisionPlan,
    DecisionResult,
    apply_decision,
    apply_gate_outcome,
    plan_decision,
    require_gate_mergeable,
)
from knotica.core.gapfill.synthetic import ReportedGapResult, file_retracted_gap, report_gap

__all__ = [
    "GATE_VERDICT_MERGED",
    "GATE_VERDICT_REFUSED",
    "DecisionResult",
    "GapDecisionResult",
    "RefreshResult",
    "ReportedGapResult",
    "apply_decision",
    "apply_gap_decision",
    "apply_gate_outcome",
    "build_default_discovery_service",
    "build_suggestion_records",
    "file_retracted_gap",
    "formulate_query",
    "plan_decision",
    "refresh_suggestions_for_gaps",
    "report_gap",
    "require_gate_mergeable",
    "suggestions_path",
]

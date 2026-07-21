"""Single source of truth for the loop's branch-name namespaces.

The self-improvement loop and its source-gate/ingest satellites route every
piece of in-flight work through one of five branch-prefix families:

* ``loop/c/`` -- candidate (a proposed change awaiting the gate); also the
  publish target of a source-ingest session. Exported under two historical
  names: ``DEFAULT_BRANCH_PREFIX`` (the loop's own) and
  ``CANDIDATE_BRANCH_PREFIX`` (source-ingest's) -- the *same* namespace, so
  the loop's candidate scan and a source publish never disagree.
* ``loop/r/`` -- result/audit pointer for a completed cycle.
* ``loop/x/`` -- quarantine for a refused source candidate (kept, never
  deleted, as an audit trail).
* ``loop/wip/`` -- a private, in-progress ingest session's branch, renamed to
  its public ``loop/c/`` name at publish.
* ``compile/<topic>/`` -- a reviewed compile branch awaiting human promotion.

These literals -- and the classify/parse helpers built on top of them -- were
historically declared independently across ``core/loop.py``,
``core/source_ingest.py``, ``core/source_gate.py`` and
``core/compile_promote.py``. This module owns them once; those four modules
import (and re-export) from here so every emitted branch string stays
byte-identical to a single definition.

Imports only from :mod:`knotica.core.errors` (a leaf), so it introduces no
import cycle among the modules that consume it.
"""

from __future__ import annotations

from typing import Literal

from knotica.core.errors import ErrorCode, KnoticaError

__all__ = [
    "CANDIDATE_BRANCH_PREFIX",
    "COMPILE_BRANCH_PREFIX",
    "DEFAULT_BRANCH_PREFIX",
    "QUARANTINE_BRANCH_PREFIX",
    "RESULT_BRANCH_PREFIX",
    "WIP_BRANCH_PREFIX",
    "candidate_branch_name",
    "classify_candidate",
    "compile_branch_prefix",
    "suggestion_id_from_branch",
    "wip_branch_name",
]

#: The candidate namespace -- ``DEFAULT_BRANCH_PREFIX`` (loop) and
#: ``CANDIDATE_BRANCH_PREFIX`` (source-ingest) are two names for one literal.
CANDIDATE_BRANCH_PREFIX = "loop/c/"
DEFAULT_BRANCH_PREFIX = CANDIDATE_BRANCH_PREFIX

#: Result/audit pointer for a completed loop cycle.
RESULT_BRANCH_PREFIX = "loop/r/"

#: A refused source candidate is renamed here (kept, never deleted) -- invisible
#: to the loop's ``loop/c/`` candidate scan, but preserved as an audit trail.
QUARANTINE_BRANCH_PREFIX = "loop/x/"

#: A private, in-progress ingest session's branch, published to ``loop/c/``.
WIP_BRANCH_PREFIX = "loop/wip/"

#: Base of the compile-review namespace; :func:`compile_branch_prefix` appends
#: the (validated) topic.
COMPILE_BRANCH_PREFIX = "compile/"

#: The infix marking a branch leaf as a source candidate (vs. a prompt
#: candidate, which carries no such convention) and the id truncation the WIP
#: and candidate prefixes share.
_SOURCE_INFIX = "source-"
_ID_INFIX_LENGTH = 8


def compile_branch_prefix(topic: str) -> str:
    """Return the required ``compile/<topic>/`` prefix for promote targets."""
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise KnoticaError(
            ErrorCode.TOPIC_NOT_FOUND,
            f"compile promote failed because topic {topic!r} is invalid",
        )
    return f"{COMPILE_BRANCH_PREFIX}{cleaned}/"


def wip_branch_name(topic: str, suggestion_id: str) -> str:
    """The private branch an ingest session writes to before it is submitted."""
    return f"{WIP_BRANCH_PREFIX}{_branch_leaf(topic, suggestion_id)}"


def candidate_branch_name(topic: str, suggestion_id: str) -> str:
    """The public candidate branch name a submitted ingest publishes to."""
    return f"{CANDIDATE_BRANCH_PREFIX}{_branch_leaf(topic, suggestion_id)}"


def classify_candidate(branch: str) -> Literal["source", "prompt"] | None:
    """Classify a candidate branch by name alone (no state, no git read).

    Returns ``"source"`` for a ``loop/c/<topic>/source-<id8>`` branch,
    ``"prompt"`` for any other ``loop/c/*`` tip (today's arena/keep/discard
    candidate), and ``None`` for a branch that is not a candidate at all.
    """
    if not branch.startswith(CANDIDATE_BRANCH_PREFIX):
        return None
    topic, sep, leaf = branch.removeprefix(CANDIDATE_BRANCH_PREFIX).partition("/")
    if sep and topic and leaf.startswith(_SOURCE_INFIX) and leaf.removeprefix(_SOURCE_INFIX):
        return "source"
    return "prompt"


def suggestion_id_from_branch(branch: str) -> str:
    """Recover the ``id8`` a source candidate branch encodes.

    The branch carries the linked suggestion's id truncated to its infix length
    (``suggestion_id[:8]``); the full id is resolved against ``suggestions.jsonl``
    at gate time. Raises ``ValueError`` for a branch that is not a source
    candidate.
    """
    _topic, id8 = _parse_candidate_branch(branch)
    return id8


def _parse_candidate_branch(branch: str) -> tuple[str, str]:
    """Parse ``loop/c/<topic>/source-<id8>`` into ``(topic, id8)``.

    Raises ``ValueError`` for any branch that is not a source candidate.
    """
    if not branch.startswith(CANDIDATE_BRANCH_PREFIX):
        raise ValueError(f"{branch!r} is not a source candidate branch")
    topic, sep, leaf = branch.removeprefix(CANDIDATE_BRANCH_PREFIX).partition("/")
    id8 = leaf.removeprefix(_SOURCE_INFIX)
    if not (sep and topic and leaf.startswith(_SOURCE_INFIX) and id8):
        raise ValueError(f"{branch!r} is not a source candidate branch")
    return topic, id8


def _parse_wip_branch(candidate: str) -> tuple[str, str]:
    """Parse ``loop/wip/<topic>/source-<id8>`` into ``(topic, id8)``."""
    malformed = KnoticaError(
        ErrorCode.SUGGESTION_NOT_FOUND,
        f"{candidate!r} is not a well-formed ingest handle "
        f"(expected {WIP_BRANCH_PREFIX!r} + '<topic>/{_SOURCE_INFIX}<id8>').",
        fix="Call source_ingest_open first to obtain a candidate handle.",
    )
    if not candidate.startswith(WIP_BRANCH_PREFIX):
        raise malformed
    topic, _, leaf = candidate.removeprefix(WIP_BRANCH_PREFIX).partition("/")
    if not topic or not leaf.startswith(_SOURCE_INFIX):
        raise malformed
    id8 = leaf.removeprefix(_SOURCE_INFIX)
    if not id8:
        raise malformed
    return topic, id8


def _branch_leaf(topic: str, suggestion_id: str) -> str:
    """The ``<topic>/source-<id8>`` suffix shared by the WIP and candidate names."""
    return f"{topic}/{_SOURCE_INFIX}{_id8(suggestion_id)}"


def _id8(suggestion_id: str) -> str:
    """Truncate a full ``suggestion_id`` to the branch-name infix length."""
    return suggestion_id[:_ID_INFIX_LENGTH]

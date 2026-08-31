"""What a lane owes next -- projected from the process model, never restated.

The dashboard has carried a machine-gated ``next`` for every process since
`dec-106`: a six-phase contract whose last phase (*Next*) is a discriminated
union with **no null member** -- ``terminal`` is an answer, absence is not --
and whose destinations are census-checked against
:mod:`knotica.core.process_model`. The MCP surface had the property in only two
of three places. An **error** envelope carries ``fix`` (the "what next" for a
failure); the dashboard carries ``next`` for every process; a **successful**
tool call carried neither. ``fill action=suggestions_review decision=approve``
returned the record and stopped, saying nothing about the ``source_ingest_open``
the approved source now owes -- which is exactly where a lane stalls one step
short, and exactly what the ``approved_awaiting_ingest`` counter exists to
measure.

This module closes that asymmetry with a projection, not a second copy.
:data:`~knotica.core.process_model.LANE_MEMBERSHIP` already knows every
``(verb -> lane, stage, narration)``; :data:`~knotica.core.process_model.LANE_STAGES`
already knows what follows a stage. The only thing declared *here* is the one
fact neither can answer: whether a verb's call **advances** the lane at all.

Two rules keep the block honest:

* **A read never claims to have moved anything.** A pure read (``gaps_read``,
  ``metrics_read``, ``prompt_diff``) carries no ``next_stage``: it did not
  advance the rail, so naming a following stage would assert a transition that never
  happened. :data:`LANE_ADVANCING_VERBS` and :data:`LANE_READ_VERBS` partition
  every declared lane action between them, so a verb added to a lane cannot be
  silently unclassified -- ``tests/test_lane_next.py`` fails on the gap.
* **The end of a rail is stated, not omitted.** A verb acting on a lane's last
  stage returns ``{"kind": "terminal", ...}``. Mirroring the dashboard's union:
  a dead end that says nothing is the failure mode the contract exists to kill.

The block rides only on a **success** envelope (see
:func:`knotica.mcp_server.envelope.with_next_stage`). A failure is returned
unchanged so its own ``fix=`` stays the one thing a caller acts on -- the same
rule ``with_deprecation_note`` follows, and for the same reason.

The wire key is ``next_stage``, not ``next``: ``session_status`` already
publishes a ``next`` of its own (``{actor, do}``), a different shape answering a
different question, and one key carrying two shapes on one surface is exactly
the inconsistency a model cannot resolve.
"""

from __future__ import annotations

from typing import Any

from knotica.core import process_model

__all__ = [
    "LANE_ADVANCING_VERBS",
    "LANE_READ_VERBS",
    "next_stage",
]

#: Verbs whose call can move the lane on -- every one of them writes to the
#: vault under at least one of its actions. A sub-dispatcher (``loop``,
#: ``notes``, ``datasets``, ``golden``, ``branches``, ``compile``,
#: ``vault_health``) is listed here when *any* of its inner actions mutates:
#: the block names where the lane goes next, which is true of the stage the
#: verb sits on regardless of which inner action ran.
LANE_ADVANCING_VERBS: frozenset[str] = frozenset(
    {
        "branches",
        "compile",
        "create_topic",
        "curate_example",
        "datasets",
        "gap_report",
        "gapfill_discover",
        "golden",
        "ingest_progress",
        "loop",
        "note_capture",
        "notes",
        "review_gap",
        "source_ingest_open",
        "source_ingest_submit",
        "store_source",
        "suggestions_review",
        "vault_health",
        "write_page",
    }
)

#: The complement: verbs that read and commit nothing. Declared rather than
#: derived as "everything else" so an unclassified verb is a test failure
#: instead of a silent read.
LANE_READ_VERBS: frozenset[str] = frozenset(
    {
        "arena",
        "baseline_probe",
        "gaps_read",
        "ingest_activity_read",
        "lint_check",
        "metrics_read",
        "prompt_diff",
        "query",
        "session_status",
        "suggestions_read",
    }
)


def next_stage(lane: str, verb: str) -> dict[str, Any] | None:
    """Where ``lane`` goes after ``verb`` advanced it, or ``None`` for a read.

    Args:
        lane: One of :data:`~knotica.core.process_model.LANES`.
        verb: A lane action, as returned by
            :func:`~knotica.mcp_server.tools_dispatch_lane_common.lane_actions`.

    Returns:
        ``{"kind": "always", "lane", "stage", "action", "handoff", "why"}`` when
        a following stage exists; ``{"kind": "terminal", "lane", "why"}`` when
        the verb acted on the rail's last stage; ``None`` when the verb is a
        read, or acts on no stage of this lane's rail (a verb the lane declares
        off-rail advances nothing there).
    """
    if verb not in LANE_ADVANCING_VERBS:
        return None
    rail = process_model.LANE_STAGES[lane]
    reached = _last_position(lane, verb, rail)
    if reached is None:
        return None
    if process_model.LANE_KIND[lane] == "checklist":
        # A checklist rail is independently-evaluable peers with no watermark
        # (`LANE_KIND`), so "the stage after this one" is not a fact about it.
        # Saying so is the honest answer; naming a successor would invent an
        # ordering the declaration explicitly denies.
        return {
            "kind": "terminal",
            "lane": lane,
            "why": (
                f"{lane}'s rail is a checklist of independent checks, not a "
                f"sequence -- nothing follows {rail[reached].id}; run whichever "
                "check the vault needs next."
            ),
        }
    if reached >= len(rail) - 1:
        return {
            "kind": "terminal",
            "lane": lane,
            "why": (
                f"{rail[reached].id} is the last stage on the {lane} rail -- "
                "this item owes nothing further in this lane."
            ),
        }
    following = rail[reached + 1]
    return {
        "kind": "always",
        "lane": lane,
        "stage": following.id,
        "action": following.action,
        "handoff": following.handoff,
        "why": _stage_narration(lane, following.id),
    }


def _last_position(lane: str, verb: str, rail: tuple[process_model.Stage, ...]) -> int | None:
    """The furthest rail index ``verb`` acts on in ``lane``.

    The *furthest*, not the first: a verb declared at two positions has passed
    through both by the time it returns (``query`` answers and cites in one
    call; ``compile`` heals and promotes), so the next stage follows the later
    one. Off-rail memberships -- a stage id the lane's rail does not carry --
    are skipped rather than clamped, so they cannot fabricate a position.
    """
    ids = [stage.id for stage in rail]
    positions = [
        ids.index(stage_id)
        for (declared_verb, _discriminator), memberships in process_model.LANE_MEMBERSHIP.items()
        if declared_verb == verb
        for member_lane, stage_id, _narration in memberships
        if member_lane == lane and stage_id in ids
    ]
    return max(positions) if positions else None


def _stage_narration(lane: str, stage_id: str) -> str:
    """The declared prose for what happens at ``(lane, stage_id)``.

    Read out of ``LANE_MEMBERSHIP`` so the ``why`` a caller sees is the same
    sentence the lane's own description and ``home``'s routing table render --
    one declaration, three surfaces.

    A handoff stage has no advancing verb to read the sentence off, so the
    tie-break runs: the stage's own ``action`` first, then the
    alphabetically-first *advancing* verb declared there, then the
    alphabetically-first verb of any kind. Preferring an advancing verb keeps a
    handoff stage from being narrated by a read that merely watches it
    (``fill``'s ``ingest`` would otherwise be described by
    ``ingest_activity_read``), and alphabetical order makes the choice the same
    on every call.
    """
    rail_ids = [stage.id for stage in process_model.LANE_STAGES[lane]]
    advancing_action = process_model.LANE_STAGES[lane][rail_ids.index(stage_id)].action
    found: dict[str, str] = {}
    for (verb, _discriminator), memberships in process_model.LANE_MEMBERSHIP.items():
        for member_lane, member_stage, narration in memberships:
            if member_lane == lane and member_stage == stage_id:
                found[verb] = narration
    if advancing_action in found:
        return found[advancing_action]
    advancing = sorted(found.keys() & LANE_ADVANCING_VERBS)
    if advancing:
        return found[advancing[0]]
    return found[min(found)] if found else f"continue the {lane} lane at {stage_id}"

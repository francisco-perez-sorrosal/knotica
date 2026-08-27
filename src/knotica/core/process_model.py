"""Single source of truth for the six process lanes and their stage rails.

Knotica's work happens in six lanes -- ``home``, ``learn``, ``answer``,
``improve``, ``fill``, ``tend``. Every surface that shows a lane, its ordered
stages, or which verb advances which stage is a **projection of this module**,
never a second copy of it:

* the six MCP lane dispatchers (``mcp_server/tools_dispatch_<lane>.py``), whose
  action tables are generated from :data:`LANE_MEMBERSHIP`;
* the served declaration that rides out on ``wiki_status``;
* the generated TypeScript mirror the dashboard falls back to
  (``dashboard/src/processModel.ts``), regenerated and diff-gated by
  ``make verify``;
* the CLI's ``knotica lane <id>`` rail printout;
* the ``/knotica:*`` slash commands a handoff stage names.

The rails these surfaces used to hold as private literals -- ``LoopPane.tsx``'s
inline stage array and ``IngestPane.tsx``'s duplicated pipeline tuple -- are
replaced by this declaration, in the same spirit as
:mod:`knotica.core.branch_namespaces` for branch prefixes.

Three properties are held mechanically rather than by convention:

* **No copy.** Where an ordered stage list already exists, it is *referenced*:
  the Learn rail is projected from :data:`~knotica.core.ingest_activity.INGEST_STAGES`
  and its ``curate`` stage from :data:`~knotica.core.ingest_activity.CURATE_STAGES`,
  by identity. A value-equal copy would drift the moment either tuple is edited.
* **Handoff is structural.** ``Stage.handoff`` is ``True`` exactly when the
  dashboard cannot execute the stage's advancing action -- the client-as-brain
  invariant expressed as data, so ``handoff`` and ``action`` can never disagree.
* **"No lane" is declared.** A verb that advances no lane stage is classified
  in :data:`VERB_CLASSIFICATION` as ``"primitive"`` (a cross-lane read) or
  ``"infrastructure"`` (unlaned), so a verb is never *silently* lane-less.

This module is data only: it declares structure and holds no state, opens no
transaction, and reads no vault. Stage state derivation lives beside it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from knotica.core import ingest_activity

__all__ = [
    "LANES",
    "LANE_KIND",
    "LANE_MEMBERSHIP",
    "LANE_STAGES",
    "LANE_STAGE_IDS",
    "VERB_CLASSIFICATION",
    "LaneKind",
    "Stage",
    "StageState",
    "derive_stages",
]

#: The six lanes, in the order every surface presents them. ``home`` is first
#: and is the router, not a process lane: it carries no stage rail (see
#: :data:`LANE_STAGES`).
LANES: tuple[str, ...] = ("home", "learn", "answer", "improve", "fill", "tend")

#: How a lane-less verb is accounted for. ``primitive`` is a cross-lane read
#: every lane may call and none is advanced by; ``infrastructure`` touches no
#: wiki content and has no knowledge terminal state.
VerbClassification = Literal["primitive", "infrastructure"]


@dataclass(frozen=True, slots=True)
class Stage:
    """One position on a lane's rail.

    Args:
        id: Stable identifier, unique within its lane.
        title: Human title rendered on the rail.
        action: The verb whose call advances this stage from the dashboard, or
            ``None`` when the dashboard structurally cannot execute it. Exactly
            one of ``action``/``handoff`` is set: ``handoff=True`` requires
            ``action is None`` and vice versa.
        handoff: ``True`` when advancing is somebody else's job -- the
            client-as-brain writing through the conversation, or a surface
            outside the dashboard entirely.
        journal_stages: The already-declared journal stage ids this rail stage
            is satisfied by, referenced from their owning module rather than
            restated. Empty for a lane with no journal behind it.
    """

    id: str
    title: str
    action: str | None
    handoff: bool
    journal_stages: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# The rails. Home has none -- it is an inbox of what needs a human across every
# topic, and routes into the other five rather than running a process of its own.
# ---------------------------------------------------------------------------

#: Which Learn rail stage each ingest journal stage folds into. The rail is
#: deliberately coarser than the journal -- four positions a human follows, not
#: eight checkpoints a model emits -- and this is the only place the two
#: vocabularies meet. Keyed by journal stage so that a stage added to
#: ``INGEST_STAGES`` without a home here fails loudly at import instead of
#: quietly vanishing from the rail.
_LEARN_JOURNAL_FOLD: Mapping[str, str] = MappingProxyType(
    {
        "resolve_topic": "source",
        "read_schema": "source",
        "store_source": "source",
        "fetch": "fetch_parse",
        "parse": "fetch_parse",
        "plan": "pages",
        "write_page": "pages",
        "complete": "pages",
    }
)

#: Learn's rail, in order, with the title each position renders under. The
#: journal ids behind each are read out of ``INGEST_STAGES`` itself.
_LEARN_RAIL_TITLES: tuple[tuple[str, str], ...] = (
    ("source", "Source"),
    ("fetch_parse", "Fetch / parse"),
    ("pages", "Pages"),
    # Curation is its own workflow server-side, so an un-curated ingest is not
    # stuck: the lane's outcome (a committed page) is reached at `pages`, and
    # `curate` is a post-terminal enrichment on a journal of its own.
    ("curate", "Curate"),
)


def _build_learn_rail() -> tuple[Stage, ...]:
    """Fold the ingest journal's stages onto Learn's four rail positions.

    Every stage is a handoff: the whole lane is the client writing pages into
    the vault through the conversation, which the dashboard watches and cannot
    perform.

    Raises:
        RuntimeError: when a journal stage has no rail position, which would
            silently drop it from the rail.
    """
    unfolded = [
        stage for stage in ingest_activity.INGEST_STAGES if stage not in _LEARN_JOURNAL_FOLD
    ]
    if unfolded:
        raise RuntimeError(
            f"ingest journal stage(s) {unfolded} have no Learn rail position; "
            "add them to _LEARN_JOURNAL_FOLD"
        )
    return tuple(
        Stage(
            id=rail_id,
            title=title,
            action=None,
            handoff=True,
            journal_stages=(
                ingest_activity.CURATE_STAGES
                if rail_id == "curate"
                else tuple(
                    stage
                    for stage in ingest_activity.INGEST_STAGES
                    if _LEARN_JOURNAL_FOLD[stage] == rail_id
                )
            ),
        )
        for rail_id, title in _LEARN_RAIL_TITLES
    )


_LEARN_STAGES: tuple[Stage, ...] = _build_learn_rail()

_ANSWER_STAGES: tuple[Stage, ...] = (
    Stage(id="ask", title="Ask", action="query", handoff=False),
    # The citations come back on the same call that answers -- one advancing
    # action serving two rail positions, not two calls.
    Stage(id="cite", title="Cite", action="query", handoff=False),
    Stage(id="react", title="React", action="curate_example", handoff=False),
)

_IMPROVE_STAGES: tuple[Stage, ...] = (
    Stage(id="instrument", title="Instrument", action="datasets", handoff=False),
    Stage(id="observe", title="Observe", action="loop", handoff=False),
    Stage(id="gate", title="Gate", action="loop", handoff=False),
    Stage(id="heal", title="Heal", action="compile", handoff=False),
    Stage(id="promote", title="Promote", action="branches", handoff=False),
    Stage(id="prove", title="Prove", action="query", handoff=False),
)

_FILL_STAGES: tuple[Stage, ...] = (
    Stage(id="gap", title="Gap", action="gap_report", handoff=False),
    Stage(id="discover", title="Discover", action="gapfill_discover", handoff=False),
    Stage(id="approve", title="Approve", action="suggestions_review", handoff=False),
    # The pages inside an approved source's session are written through the
    # conversation, against the session handle -- no dashboard control exists
    # or could exist for it.
    Stage(id="ingest", title="Ingest", action=None, handoff=True),
    Stage(id="gate", title="Gate", action="loop", handoff=False),
)

_TEND_STAGES: tuple[Stage, ...] = (
    Stage(id="doctor", title="Doctor", action="vault_health", handoff=False),
    Stage(id="lint", title="Lint", action="lint_check", handoff=False),
    Stage(id="okf", title="OKF", action="vault_health", handoff=False),
    # Migration has no MCP surface: it runs from the CLI, so the rail names the
    # command and stands aside rather than offering a control that cannot work.
    Stage(id="migrate", title="Migrate", action=None, handoff=True),
    Stage(id="drift", title="Drift", action="notes", handoff=False),
)

#: Each lane's ordered rail. ``home`` declares an empty rail rather than being
#: absent, so "no rail" is a value a caller can read instead of a lookup that
#: raises.
LANE_STAGES: Mapping[str, tuple[Stage, ...]] = MappingProxyType(
    {
        "home": (),
        "learn": _LEARN_STAGES,
        "answer": _ANSWER_STAGES,
        "improve": _IMPROVE_STAGES,
        "fill": _FILL_STAGES,
        "tend": _TEND_STAGES,
    }
)

#: The ordered stage-id sequence each lane's rail is projected from. Five lanes
#: own their ids outright, so their entry is derived from the rail above. Learn
#: is the exception and the reason this mapping exists: its rail groups the
#: ingest journal's own stages, so the entry **is**
#: ``ingest_activity.INGEST_STAGES`` -- referenced by identity, never copied, so
#: a stage added to the journal cannot leave the rail silently behind. Each
#: Learn rail stage names the journal ids it covers in ``Stage.journal_stages``.
LANE_STAGE_IDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        **{lane: tuple(stage.id for stage in stages) for lane, stages in LANE_STAGES.items()},
        "learn": ingest_activity.INGEST_STAGES,
    }
)


# ---------------------------------------------------------------------------
# Lane membership. Keyed on ``(verb, discriminator)``: the discriminator is
# ``None`` unless a runtime argument decides the lane. A verb maps to the
# **set** of ``(lane, stage_id, narration)`` places its call acts in -- lanes
# are a facet, not a partition, so a shared mechanism gets one implementation
# and N narrations rather than an arbitrary owning lane.
# ---------------------------------------------------------------------------

LANE_MEMBERSHIP: Mapping[tuple[str, str | None], frozenset[tuple[str, str, str]]] = (
    MappingProxyType(
        {
            # -- Learn ---------------------------------------------------------
            ("create_topic", None): frozenset(
                {("learn", "source", "open a new topic to learn into")}
            ),
            ("store_source", None): frozenset(
                {
                    ("learn", "source", "store the fetched source beside its pages"),
                    ("fill", "ingest", "store the approved source inside the session"),
                }
            ),
            ("ingest_progress", None): frozenset(
                {
                    ("learn", "fetch_parse", "report the cognitive steps as they happen"),
                    ("fill", "ingest", "report progress inside the gap-fill session"),
                }
            ),
            ("write_page", None): frozenset(
                {
                    ("learn", "pages", "write the pages the source becomes"),
                    ("fill", "ingest", "write the session's pages onto its candidate"),
                }
            ),
            ("ingest_activity_read", None): frozenset(
                {
                    ("learn", "pages", "read the journal this rail is projected from"),
                    ("fill", "ingest", "watch the session's pages land"),
                }
            ),
            ("curate_example", None): frozenset(
                {
                    ("learn", "curate", "curate an example from the pages just written"),
                    ("answer", "react", "record this answer as a good or bad example"),
                    ("improve", "instrument", "the curated example lands in the trainset"),
                }
            ),
            # -- Answer --------------------------------------------------------
            ("query", None): frozenset(
                {
                    ("answer", "ask", "ask the wiki"),
                    ("answer", "cite", "the cited pages come back with the answer"),
                    ("improve", "prove", "probe the improvement on a question, before and after"),
                }
            ),
            ("note_capture", None): frozenset(
                {
                    ("answer", "react", "note what the answer got wrong, in place"),
                    ("tend", "drift", "the note enters the overlay this lane maintains"),
                }
            ),
            ("gap_report", None): frozenset(
                {
                    ("answer", "react", "report what the answer could not cover"),
                    ("fill", "gap", "file the gap this lane starts from"),
                }
            ),
            # -- Improve -------------------------------------------------------
            ("datasets", None): frozenset(
                {
                    (
                        "improve",
                        "instrument",
                        "inventory, bootstrap and freeze the sets the bar is measured on",
                    )
                }
            ),
            ("golden", None): frozenset(
                {("improve", "instrument", "load and save the sealed golden set")}
            ),
            ("baseline_probe", None): frozenset(
                {
                    (
                        "improve",
                        "instrument",
                        "probe what this corpus can score before freezing a bar",
                    )
                }
            ),
            ("metrics_read", None): frozenset(
                {("improve", "observe", "read the scalar history behind the chart")}
            ),
            ("loop", None): frozenset(
                {
                    ("improve", "observe", "run one eval cycle now (billed)"),
                    ("improve", "gate", "run one gate cycle on the pending candidates (billed)"),
                    ("fill", "gate", "the same cycle, measuring your approved source (billed)"),
                }
            ),
            ("arena", None): frozenset(
                {("improve", "heal", "race prompt variants when the gate refuses")}
            ),
            ("compile", None): frozenset(
                {
                    ("improve", "heal", "compile a new prompt from the trainset"),
                    ("improve", "promote", "promote the reviewed compile branch"),
                }
            ),
            ("branches", None): frozenset(
                {("improve", "promote", "score the candidate branches, promote or delete one")}
            ),
            ("prompt_diff", None): frozenset(
                {
                    ("improve", "promote", "diff the candidate against the live prompt"),
                    ("improve", "prove", "diff the compiled artifact that was merged"),
                }
            ),
            # -- Fill ----------------------------------------------------------
            ("gaps_read", None): frozenset(
                {("fill", "gap", "read the open gaps waiting on a source")}
            ),
            ("gapfill_discover", None): frozenset(
                {("fill", "discover", "propose outside sources for an open gap (billed)")}
            ),
            ("suggestions_read", None): frozenset(
                {("fill", "approve", "read the proposed sources waiting on you")}
            ),
            ("suggestions_review", None): frozenset(
                {("fill", "approve", "approve, reject or defer a proposed source")}
            ),
            ("source_ingest_open", None): frozenset(
                {
                    (
                        "fill",
                        "ingest",
                        "open a session for an approved source, or rework a refused one",
                    )
                }
            ),
            ("source_ingest_submit", None): frozenset(
                {
                    ("fill", "ingest", "submit the finished session"),
                    ("fill", "gate", "the submitted candidate reaches the gate"),
                }
            ),
            # -- Tend ----------------------------------------------------------
            ("vault_health", None): frozenset(
                {
                    ("tend", "doctor", "run the vault's checks and repairs"),
                    ("tend", "okf", "check and repair OKF conformance"),
                }
            ),
            ("lint_check", None): frozenset(
                {("tend", "lint", "check pages against the topic's schema")}
            ),
            ("notes", None): frozenset(
                {("tend", "drift", "list, read, re-anchor, detach and archive the overlay")}
            ),
            # The four-membership case: one action string whose lane is decided by
            # its `target` argument, which is why membership is keyed on
            # `(verb, discriminator)` rather than on the verb alone.
            ("notes", "promote"): frozenset(
                {
                    ("tend", "drift", "a note leaves the overlay for a durable home"),
                    ("answer", "react", "promote the note you took beside an answer"),
                    ("improve", "instrument", "target=trainset lands the note in the trainset"),
                    ("fill", "gap", "target=gap files the note as a gap to fill"),
                }
            ),
        }
    )
)


# ---------------------------------------------------------------------------
# The lane-less verbs. Membership above is what a verb *advances*; a verb that
# advances nothing still has to be accounted for, so it is classified here.
# The two sets are disjoint by construction.
# ---------------------------------------------------------------------------

VERB_CLASSIFICATION: Mapping[str, VerbClassification] = MappingProxyType(
    {
        # Cross-lane reads: every lane calls them, none is advanced by them,
        # and their names are already the best names available -- renaming one
        # into a lane would state something false.
        "search": "primitive",
        "read_page": "primitive",
        "list_topics": "primitive",
        "list_links": "primitive",
        "read_protocol": "primitive",
        "wiki_status": "primitive",
        # Unlaned: touches no wiki content, has no knowledge terminal state,
        # makes no vault commit.
        "vault": "infrastructure",
        "open_dashboard": "infrastructure",
    }
)


# ---------------------------------------------------------------------------
# Stage-state derivation. Server-side only -- the client renders state it is
# given, never derives it -- and pure: no vault I/O, no clock, just the
# watermark/checklist payload a caller's own status read already produced.
# Follows the rail contract's monotonic-watermark rules: a single integer
# plus one optional reason is enough to render a whole rail.
# ---------------------------------------------------------------------------

#: The rendered state of one stage on a rail. Four values suffice: a lane's
#: terminal condition (Fill's ``quarantined``, Improve's ``merged``) is a
#: lane-level outcome, never a fifth stage state.
StageState = Literal["pending", "active", "complete", "blocked"]

#: How a lane's rail advances. A ``sequence`` lane has one monotonic
#: watermark; the ``checklist`` lane (``tend``) is independently-evaluable
#: peers with no watermark at all. ``home`` has no rail and so no kind --
#: callers check :data:`LANE_STAGES` for emptiness before consulting this.
LaneKind = Literal["sequence", "checklist"]

LANE_KIND: Mapping[str, LaneKind] = MappingProxyType(
    {
        "learn": "sequence",
        "answer": "sequence",
        "improve": "sequence",
        "fill": "sequence",
        "tend": "checklist",
    }
)


def _stage_is_complete(index: int, watermark: int) -> bool:
    """R1: every stage before the watermark is complete. Always."""
    return index < watermark


def _stage_is_pending(index: int, watermark: int) -> bool:
    """R2: every stage after the watermark is pending. Always."""
    return index > watermark


def _sequence_stage(
    stage: Stage, index: int, watermark: int, blocked_reason: str | None
) -> dict[str, Any]:
    """One sequence stage's state, following R1-R3.

    ``watermark`` is already normalized (see :func:`_derive_sequence_stages`)
    so an idle or terminal position falls out of the same three-way
    comparison as a mid-run position -- no special casing.
    """
    if _stage_is_complete(index, watermark):
        return {"id": stage.id, "state": "complete", "reason": None}
    if _stage_is_pending(index, watermark):
        return {"id": stage.id, "state": "pending", "reason": None}
    # R3: only the watermark position remains -- active, or blocked when a
    # precondition is unmet. `blocked` is a modifier on this one position,
    # never a separate position of its own.
    if blocked_reason:
        return {"id": stage.id, "state": "blocked", "reason": blocked_reason}
    return {"id": stage.id, "state": "active", "reason": None}


def _derive_sequence_stages(
    stages: tuple[Stage, ...], payload: Mapping[str, Any]
) -> tuple[dict[str, Any], ...]:
    watermark = payload.get("watermark")
    blocked_reason = payload.get("blocked_reason")
    # An idle lane (`watermark is None`) is a watermark before every stage:
    # rendering it as position -1 lets R1-R3 produce "every stage pending"
    # with no special case. A terminal lane (`watermark == len(stages)`)
    # needs no trick at all -- it is already past every real index.
    position = -1 if watermark is None else watermark
    return tuple(
        _sequence_stage(stage, index, position, blocked_reason)
        for index, stage in enumerate(stages)
    )


def _derive_checklist_stages(
    stages: tuple[Stage, ...], payload: Mapping[str, Any]
) -> tuple[dict[str, Any], ...]:
    """C1-C2: each check is independently pending/blocked/complete, and the
    server never derives ``"active"`` -- C2 defines that as UI focus, a
    client-side concern this payload carries no key for."""
    checks: Mapping[str, str] = payload.get("checks", {})
    reasons: Mapping[str, str] = payload.get("reasons", {})
    return tuple(
        {"id": stage.id, "state": checks[stage.id], "reason": reasons.get(stage.id)}
        for stage in stages
    )


def derive_stages(lane: str, payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """The dynamic per-stage state of ``lane``'s rail, given its position payload.

    Total over every declared lane: ``home`` has an empty rail and always
    returns ``()``; the five process lanes dispatch on their declared
    :data:`LaneKind`. The illegal combination the rail contract forbids -- a
    later stage active while an earlier one has not completed -- cannot be
    constructed here, because every stage's state is read off the same one
    watermark comparison rather than assigned independently.

    Args:
        lane: One of :data:`LANES`.
        payload: For a ``sequence`` lane, ``{"watermark": int | None,
            "blocked_reason": str | None}``. For the ``checklist`` lane
            (``tend``), ``{"checks": {stage_id: state}, "reasons":
            {stage_id: reason}}``.

    Returns:
        One dict per declared stage, in rail order, each shaped
        ``{"id": str, "state": StageState, "reason": str | None}``.
    """
    stages = LANE_STAGES[lane]
    if not stages:
        return ()
    if LANE_KIND[lane] == "checklist":
        return _derive_checklist_stages(stages, payload)
    return _derive_sequence_stages(stages, payload)

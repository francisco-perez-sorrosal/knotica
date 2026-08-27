"""Behavioral tests for the server-side stage-state predicates.

``knotica.core.process_model.derive_stages(lane, payload)`` is the one place
a lane's declared rail (``LANE_STAGES``) turns into the per-stage state a
dashboard renders -- payload in, states out, no vault I/O. This module does
not exist yet; every test here fails at collection with an ``ImportError``
on the name ``derive_stages`` until it is written (the surrounding
``process_model`` module already exists, so the failure is scoped to the
missing name, not the module).

**State vocabulary binding.** The pipeline plan's prose names the returned
state enum ``ready|current|blocked|handoff``. The interface design's
``LaneStage`` contract -- the structure every dashboard rail actually
renders -- names it ``pending|active|complete|blocked``, and its five
watermark rules are stated entirely in that vocabulary. This suite binds to
**the interface design's vocabulary** (``pending``/``active``/``complete``/
``blocked``), for two reasons: (1) the watermark rules this suite is asked to
assert directly are only expressible in that vocabulary, and (2) "handoff"
cannot be a dynamic per-derivation state at all -- it is already a
*static* boolean field on ``Stage``, orthogonal to where the
watermark sits. A handoff stage is still ``pending``, ``active``, ``complete``
or ``blocked`` depending on position; conflating the two would make the
declared ``Stage.handoff`` and the derived ``state`` disagree by
construction.

**Payload shape.** ``derive_stages`` takes the *dynamic* position data a
lane's own status read would already have computed -- this suite does not
open a vault to get it, per the step's "no vault I/O needed if the payload
shape suffices" instruction:

- **Sequence lanes** (``learn``, ``answer``, ``improve``, ``fill`` --
  Section 1.3's ``kind: "sequence"``): ``{"watermark": int | None,
  "blocked_reason": str | None}``. ``watermark is None`` is the idle lane
  (R2 for every stage); ``watermark == len(stages)`` is the terminal lane
  (R1 for every stage); otherwise the stage at ``index == watermark`` is
  ``active``, or ``blocked`` with ``blocked_reason`` set (R3).
- **Checklist lanes** (``tend`` -- Section 1.3's ``kind: "checklist"``):
  ``{"checks": {stage_id: "complete" | "blocked" | "pending"}, "reasons":
  {stage_id: str}}``. Tend has no watermark (C1) and derives no ``"active"``
  state at all -- Section 1.3 C2 defines "active" for a checklist as *"a UI
  focus, not a process position"*, i.e. a client-side concern the dashboard
  applies itself, never something the server derives (this module's own
  docstring: "the client renders state it is given, never derives it" reads
  the other way for a checklist specifically -- the server never emits
  ``"active"`` for one). This suite asserts that directly.

Four invariant families, mirroring ``test_process_model.py``'s pattern of
deriving cases from the live declaration rather than hand-picking them:

1. **Idle** -- every sequence-lane stage is ``pending`` with no watermark.
2. **Terminal** -- every sequence-lane stage is ``complete`` at the rail's end.
3. **Active/blocked position** -- the five watermark rules (R1-R5), asserted
   as invariants over every ``(lane, index, blocked)`` combination the rail
   admits, plus the "illegal combination is unrepresentable" property named
   in the step's own done-when criteria.
4. **Checklist (Tend)** -- independent per-check state, a reason exactly on
   the blocked check, and the ``"active"`` state never appearing.
"""

from __future__ import annotations

from typing import Any

import pytest

from knotica.core import process_model
from knotica.core.process_model import derive_stages

# Section 1.4's cardinality table gives four sequence lanes and one checklist
# lane (`tend`); `home` carries no rail at all (`LANE_STAGES["home"] == ()`)
# and is out of scope for a state machine that has nothing to derive over.
SEQUENCE_LANES = ("learn", "answer", "improve", "fill")
CHECKLIST_LANES = ("tend",)


def _stage_ids(lane: str) -> tuple[str, ...]:
    """The declared, ordered stage ids for a lane -- read from the live rail,
    never hand-listed, so a stage added to a lane is covered automatically."""
    return tuple(stage.id for stage in process_model.LANE_STAGES[lane])


def _sequence_payload(watermark: int | None, blocked_reason: str | None = None) -> dict[str, Any]:
    return {"watermark": watermark, "blocked_reason": blocked_reason}


def _checklist_payload(
    states: dict[str, str], reasons: dict[str, str] | None = None
) -> dict[str, Any]:
    return {"checks": dict(states), "reasons": dict(reasons or {})}


# ---------------------------------------------------------------------------
# Idle and terminal positions (R1/R2/R5 at the rail's two ends).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lane", SEQUENCE_LANES)
def test_idle_lane_has_every_stage_pending_and_no_active_or_blocked_stage(lane: str) -> None:
    stages = derive_stages(lane, _sequence_payload(watermark=None))
    assert all(stage["state"] == "pending" for stage in stages)
    assert not any(stage["state"] in ("active", "blocked") for stage in stages)


@pytest.mark.parametrize("lane", SEQUENCE_LANES)
def test_terminal_lane_has_every_stage_complete_and_no_active_or_blocked_stage(lane: str) -> None:
    watermark_past_the_last_stage = len(_stage_ids(lane))
    stages = derive_stages(lane, _sequence_payload(watermark=watermark_past_the_last_stage))
    assert all(stage["state"] == "complete" for stage in stages)
    assert not any(stage["state"] in ("active", "blocked") for stage in stages)


# ---------------------------------------------------------------------------
# Active / blocked position: R1, R2, R3, R5 asserted together as one
# invariant over every valid (lane, index, blocked) combination the rail
# admits -- generated from the live declaration, not hand-picked.
# ---------------------------------------------------------------------------


def _watermark_cases() -> list[tuple[str, int, bool]]:
    return [
        (lane, index, blocked)
        for lane in SEQUENCE_LANES
        for index in range(len(_stage_ids(lane)))
        for blocked in (False, True)
    ]


_WATERMARK_CASES = _watermark_cases()
_WATERMARK_IDS = [
    f"{lane}-idx{index}-{'blocked' if blocked else 'active'}"
    for lane, index, blocked in _WATERMARK_CASES
]


def test_the_watermark_case_matrix_is_genuinely_populated() -> None:
    # Non-vacuity guard: if a future rail shrank to zero stages, every
    # parametrized test below would report success by running zero cases.
    assert len(_WATERMARK_CASES) >= 2 * len(SEQUENCE_LANES)


@pytest.mark.parametrize("lane, watermark, blocked", _WATERMARK_CASES, ids=_WATERMARK_IDS)
def test_stage_states_follow_the_monotonic_watermark_rules(
    lane: str, watermark: int, blocked: bool
) -> None:
    reason = "precondition unmet: upstream stage has not produced its input" if blocked else None
    stages = derive_stages(lane, _sequence_payload(watermark=watermark, blocked_reason=reason))
    ids = _stage_ids(lane)
    expected_watermark_state = "blocked" if blocked else "active"

    # R1: every stage before the watermark is complete.
    assert all(stage["state"] == "complete" for stage in stages[:watermark])
    # R2: every stage after the watermark is pending.
    assert all(stage["state"] == "pending" for stage in stages[watermark + 1 :])
    # R3: the watermark position itself is active, or blocked when a
    # precondition is unmet -- a modifier on the active position, never a
    # separate position.
    assert stages[watermark]["state"] == expected_watermark_state
    assert stages[watermark]["id"] == ids[watermark]
    # R5: exactly one stage is active-or-blocked when the lane is neither
    # idle nor terminal.
    active_or_blocked = [stage for stage in stages if stage["state"] in ("active", "blocked")]
    assert len(active_or_blocked) == 1


@pytest.mark.parametrize("lane, watermark, blocked", _WATERMARK_CASES, ids=_WATERMARK_IDS)
def test_no_earlier_stage_is_incomplete_while_a_later_stage_is_active_or_blocked(
    lane: str, watermark: int, blocked: bool
) -> None:
    # The step's own done-when criterion, asserted directly rather than left
    # as an inference from the rules above: the illegal LoopPane-era
    # combination (a later stage active while an earlier one has not
    # finished) must be unrepresentable in what derive_stages returns.
    reason = "precondition unmet" if blocked else None
    stages = derive_stages(lane, _sequence_payload(watermark=watermark, blocked_reason=reason))
    earlier_stages = stages[:watermark]
    assert all(stage["state"] == "complete" for stage in earlier_stages), (
        f"{lane}: stage {stages[watermark]['id']!r} is {stages[watermark]['state']!r} while an "
        "earlier stage on the same rail is not complete"
    )


@pytest.mark.parametrize("lane, watermark, blocked", _WATERMARK_CASES, ids=_WATERMARK_IDS)
def test_reason_is_present_exactly_on_the_blocked_stage(
    lane: str, watermark: int, blocked: bool
) -> None:
    reason = "precondition unmet: golden set not sealed" if blocked else None
    stages = derive_stages(lane, _sequence_payload(watermark=watermark, blocked_reason=reason))
    blocked_stages = [stage for stage in stages if stage["state"] == "blocked"]
    non_blocked_stages = [stage for stage in stages if stage["state"] != "blocked"]
    assert all(stage["reason"] for stage in blocked_stages)
    assert all(stage["reason"] is None for stage in non_blocked_stages)


# ---------------------------------------------------------------------------
# Tend -- the one checklist-kind lane (Section 1.3 C1-C3). No watermark, no
# `"active"` in the server-derived output: C2 defines "active" for a
# checklist as UI focus, a client-side concern.
# ---------------------------------------------------------------------------


def test_tend_idle_checklist_has_every_check_pending() -> None:
    ids = _stage_ids("tend")
    stages = derive_stages("tend", _checklist_payload({cid: "pending" for cid in ids}))
    assert all(stage["state"] == "pending" for stage in stages)


def test_tend_terminal_checklist_has_every_check_complete() -> None:
    ids = _stage_ids("tend")
    stages = derive_stages("tend", _checklist_payload({cid: "complete" for cid in ids}))
    assert all(stage["state"] == "complete" for stage in stages)


def test_tend_mid_run_checklist_reflects_each_checks_independent_state() -> None:
    # C1: each check is independently evaluable -- unlike a sequence lane, a
    # later check being clean does not require an earlier one to be clean
    # too. Half complete, half pending, with no ordering relationship.
    ids = _stage_ids("tend")
    midpoint = len(ids) // 2
    declared_states = {
        cid: ("complete" if i < midpoint else "pending") for i, cid in enumerate(ids)
    }
    stages = derive_stages("tend", _checklist_payload(declared_states))
    returned_states = {stage["id"]: stage["state"] for stage in stages}
    assert returned_states == declared_states


def test_tend_blocked_check_carries_a_reason_and_leaves_its_peers_untouched() -> None:
    ids = _stage_ids("tend")
    blocked_id = ids[0]
    declared_states = {cid: "pending" for cid in ids}
    declared_states[blocked_id] = "blocked"
    stages = derive_stages(
        "tend",
        _checklist_payload(declared_states, reasons={blocked_id: "lint failed on 3 pages"}),
    )
    by_id = {stage["id"]: stage for stage in stages}
    assert by_id[blocked_id]["state"] == "blocked"
    assert by_id[blocked_id]["reason"]
    other_ids = [cid for cid in ids if cid != blocked_id]
    assert all(by_id[cid]["state"] == "pending" for cid in other_ids)
    assert all(by_id[cid]["reason"] is None for cid in other_ids)


_TEND_STATE_COMBOS = [
    pytest.param({cid: "pending" for cid in _stage_ids("tend")}, id="all-pending"),
    pytest.param({cid: "complete" for cid in _stage_ids("tend")}, id="all-complete"),
    pytest.param(
        {
            cid: ("complete" if i % 2 == 0 else "pending")
            for i, cid in enumerate(_stage_ids("tend"))
        },
        id="mixed-complete-pending",
    ),
    pytest.param(
        {cid: ("blocked" if i == 0 else "pending") for i, cid in enumerate(_stage_ids("tend"))},
        id="one-blocked",
    ),
]


@pytest.mark.parametrize("declared_states", _TEND_STATE_COMBOS)
def test_tend_never_derives_the_active_state_since_focus_is_a_client_side_concern(
    declared_states: dict[str, str],
) -> None:
    stages = derive_stages("tend", _checklist_payload(declared_states))
    assert not any(stage["state"] == "active" for stage in stages)


# ---------------------------------------------------------------------------
# Totality: derive_stages is defined over every declared non-Home lane.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lane", SEQUENCE_LANES + CHECKLIST_LANES)
def test_derive_stages_returns_one_entry_per_declared_stage(lane: str) -> None:
    ids = _stage_ids(lane)
    payload = (
        _sequence_payload(watermark=None)
        if lane in SEQUENCE_LANES
        else _checklist_payload({cid: "pending" for cid in ids})
    )
    stages = derive_stages(lane, payload)
    assert tuple(stage["id"] for stage in stages) == ids

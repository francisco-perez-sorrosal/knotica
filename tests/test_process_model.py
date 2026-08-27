"""Fitness tests for the single declared process model.

Six lanes (`home`, `learn`, `answer`, `improve`, `fill`, `tend`) exist as one
declaration in `knotica.core.process_model` -- every surface that shows a lane,
its ordered stages, or which verb advances which stage is a projection of that
declaration, never a second copy of it. This module does not exist yet; every
test here fails at collection with `ModuleNotFoundError` until it is written,
and the assertions below hold as invariants over whatever the declaration says
-- never over hand-picked examples -- so adding a lane or a verb without
declaring its place fails immediately.

Four invariant families, each derived from the live declaration or the live
tool registry rather than a hand-written list:

1. **Ordering** -- each non-Home lane's stage ids are unique and the rail is an
   ordered sequence; the Home lane declares no rail at all.
2. **Handoff** -- a handoff stage carries no dashboard-executable advancing
   action; a non-handoff stage carries exactly one. This is what makes
   client-as-brain a mechanically-held property of the declaration rather than
   a convention someone has to remember.
3. **Membership totality** -- every verb the live server registers is either
   mapped to at least one lane/stage or explicitly classified lane-less
   (`"primitive"` or `"infrastructure"`); "no lane" must be a declared state,
   never an omission the census silently drops.
4. **No copy** -- the Learn rail's stage-id sequence is `ingest_activity.
   INGEST_STAGES` by identity, not a value-equal copy that could drift the
   moment either tuple changes independently.

Expected API this file pins down for the implementation step (see
`LEARNINGS_test-engineer.md` for which parts are assumptions, since the module
does not exist to read from):

- `LANES: tuple[str, ...]` -- the six lane names, `"home"` first.
- `LANE_STAGES: dict[str, tuple[Stage, ...]]` -- ordered per-lane stage rail;
  `LANE_STAGES["home"] == ()`. Each `Stage` exposes `.id`, `.title`, `.action`
  (the dashboard-executable advancing-action reference, `None` on a handoff
  stage) and `.handoff` (bool).
- `LANE_STAGE_IDS: dict[str, tuple[str, ...]]` -- the raw per-lane id sequence
  each lane's `Stage` rail is built from. `LANE_STAGE_IDS["learn"]` must be
  `ingest_activity.INGEST_STAGES` itself.
- `LANE_MEMBERSHIP: dict[tuple[str, str | None], frozenset[tuple[str, str, str]]]`
  -- every `(verb, discriminator)` pair maps to the set of `(lane, stage_id,
  narration)` places that call advances. `discriminator` is `None` for a verb
  whose lane membership never depends on a runtime argument.
- `VERB_CLASSIFICATION: dict[str, str]` -- every lane-less verb's `"primitive"`
  or `"infrastructure"` classification.
"""

from __future__ import annotations

from typing import Any

import pytest

from knotica.core import ingest_activity, process_model
from support.dispatch import build_full_server, list_tools

NON_HOME_LANES = ("learn", "answer", "improve", "fill", "tend")


def test_lanes_are_declared_in_the_fixed_six_lane_order() -> None:
    assert tuple(process_model.LANES) == ("home", "learn", "answer", "improve", "fill", "tend")


def test_home_lane_declares_no_stage_rail() -> None:
    assert process_model.LANE_STAGES["home"] == (), (
        "home has no stage rail -- a non-empty tuple here would give it a "
        "rail no lane surface is supposed to render"
    )


@pytest.mark.parametrize("lane", NON_HOME_LANES)
def test_non_home_lane_stage_ids_are_unique(lane: str) -> None:
    ids = [stage.id for stage in process_model.LANE_STAGES[lane]]
    assert len(ids) == len(set(ids)), f"duplicate stage id declared in lane {lane!r}: {ids}"


@pytest.mark.parametrize("lane", NON_HOME_LANES)
def test_non_home_lane_stage_rail_is_an_ordered_sequence(lane: str) -> None:
    # A rail built from a set or a dict's key view would have no guaranteed
    # order at all -- "declared order is stable" requires an ordered
    # container, not merely a container that happens to iterate consistently
    # today.
    assert isinstance(process_model.LANE_STAGES[lane], tuple), (
        f"lane {lane!r}'s stage rail must be an ordered tuple, not "
        f"{type(process_model.LANE_STAGES[lane]).__name__}"
    )


@pytest.mark.parametrize("lane", NON_HOME_LANES)
def test_non_home_lane_declares_at_least_one_stage(lane: str) -> None:
    # Non-vacuity guard: an empty rail would make every per-stage invariant
    # below pass on that lane by having nothing to check.
    assert process_model.LANE_STAGES[lane], f"lane {lane!r} declares no stages at all"


def _all_lane_stages() -> list[tuple[str, Any]]:
    """Every `(lane, stage)` pair across the non-Home lanes, read from the
    live declaration rather than hand-picked -- adding a lane or a stage
    automatically gets covered by the parametrized cases below."""
    return [(lane, stage) for lane in NON_HOME_LANES for stage in process_model.LANE_STAGES[lane]]


_HANDOFF_CASES = [(lane, stage) for lane, stage in _all_lane_stages() if stage.handoff]
_ADVANCING_CASES = [(lane, stage) for lane, stage in _all_lane_stages() if not stage.handoff]
_HANDOFF_IDS = [f"{lane}/{stage.id}" for lane, stage in _HANDOFF_CASES]
_ADVANCING_IDS = [f"{lane}/{stage.id}" for lane, stage in _ADVANCING_CASES]


def test_the_declaration_contains_both_handoff_and_advancing_stages() -> None:
    # Non-vacuity guard for the two parametrized tests below: if every stage
    # in the corpus landed on one side of `handoff`, the other side's test
    # would report success by running zero cases.
    assert _HANDOFF_CASES, "expected at least one handoff stage across the non-Home lanes"
    assert _ADVANCING_CASES, "expected at least one non-handoff (advancing) stage"


@pytest.mark.parametrize("lane, stage", _HANDOFF_CASES, ids=_HANDOFF_IDS)
def test_handoff_stage_has_no_dashboard_executable_advancing_action(lane: str, stage: Any) -> None:
    assert stage.action is None, (
        f"{lane}/{stage.id} is a handoff stage but declares advancing action "
        f"{stage.action!r} -- a handoff stage must be one the dashboard cannot execute"
    )


@pytest.mark.parametrize("lane, stage", _ADVANCING_CASES, ids=_ADVANCING_IDS)
def test_advancing_stage_declares_exactly_one_dashboard_executable_action(
    lane: str, stage: Any
) -> None:
    assert stage.action is not None, (
        f"{lane}/{stage.id} is not a handoff stage but declares no dashboard-"
        "executable advancing action"
    )


def test_every_registered_verb_has_lane_membership_or_a_lane_less_classification(
    vault_config: Any, template_vault: Any
) -> None:
    del vault_config, template_vault
    # A tool named after a lane IS that lane's dispatcher -- the declaration's
    # own projection onto the MCP surface, not a verb acting inside a lane. It
    # is accounted for by `LANES`, so requiring it to also appear in
    # LANE_MEMBERSHIP or VERB_CLASSIFICATION would ask a lane to be a member of
    # itself.
    verbs = {tool.name for tool in list_tools(build_full_server())} - set(process_model.LANES)
    membership_verbs = {verb for verb, _discriminator in process_model.LANE_MEMBERSHIP}
    classified_verbs = set(process_model.VERB_CLASSIFICATION)
    unaccounted = verbs - membership_verbs - classified_verbs
    assert not unaccounted, (
        f"verb(s) {sorted(unaccounted)} are neither mapped in LANE_MEMBERSHIP "
        "nor classified primitive/infrastructure -- 'no lane' must be a "
        "declared state, never an omission"
    )


def test_verb_classification_only_ever_uses_the_two_declared_categories() -> None:
    invalid = {
        verb: category
        for verb, category in process_model.VERB_CLASSIFICATION.items()
        if category not in {"primitive", "infrastructure"}
    }
    assert not invalid, f"unexpected lane-less classification(s): {invalid}"


def test_lane_less_verbs_carry_no_lane_membership_at_all() -> None:
    # Consistency guard: a verb marked lane-less must not simultaneously carry
    # a lane mapping -- that would make "no lane" a false declared state
    # rather than a true one.
    membership_verbs = {verb for verb, _discriminator in process_model.LANE_MEMBERSHIP}
    overlap = membership_verbs & set(process_model.VERB_CLASSIFICATION)
    assert not overlap, (
        f"verb(s) {sorted(overlap)} are both classified lane-less and mapped into LANE_MEMBERSHIP"
    )


def test_the_verb_census_and_the_declaration_are_both_genuinely_populated(
    vault_config: Any, template_vault: Any
) -> None:
    # Non-vacuity guard for the totality check above: it would pass trivially
    # if the live registry returned no tools, or if every verb had quietly
    # been swept into one bucket while the other stayed empty.
    del vault_config, template_vault
    verbs = {tool.name for tool in list_tools(build_full_server())}
    # Floor recalibrated with the lane re-cut: the published surface is 21
    # registrations (13 Tier-1 + 2 unlaned Tier-2 + 6 lanes), down from 35 flat
    # + 6 lanes. The guard's job is unchanged -- catch a stubbed-out registry --
    # so it sits just under the real count rather than at the old one.
    assert len(verbs) > 15, "expected the full registered tool surface, not a stub"
    assert process_model.LANE_MEMBERSHIP, "LANE_MEMBERSHIP must not be empty"
    assert process_model.VERB_CLASSIFICATION, "VERB_CLASSIFICATION must not be empty"


def test_learn_rail_stage_ids_are_ingest_activity_stages_by_identity_not_by_value() -> None:
    assert process_model.LANE_STAGE_IDS["learn"] is ingest_activity.INGEST_STAGES, (
        "the Learn rail must reference ingest_activity.INGEST_STAGES directly -- "
        "a value-equal copy would silently drift the moment either tuple is "
        "edited independently"
    )

"""Census: the success envelope's `next_stage` names destinations that exist.

The dashboard has had a machine-gated Phase 6 since `dec-106` -- a `next` whose
destinations fail the build when they point at a stage `process_model.py` does
not declare. This file is that gate's server-side twin, plus the three rules the
MCP block adds on top:

* a **read** carries no `next_stage` (it advanced nothing, so naming a successor
  would assert a transition that never happened);
* an **advancing** verb always answers -- `always` or `terminal`, never absence;
* the key is `next_stage`, because `session_status` already publishes a `next`
  of its own (`{actor, do}`) and one key may not carry two shapes.

The classification itself is the one thing `lane_next` declares rather than
projects, so `test_every_lane_action_is_classified_exactly_once` is what stops
a verb added to a lane from silently defaulting to "read".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.core import process_model
from knotica.mcp_server import lane_next
from knotica.mcp_server.tools_dispatch_lane_common import lane_actions
from support.dispatch import build_full_server, call_tool, payload_of

_PROCESS_LANES = tuple(lane for lane in process_model.LANES if lane != "home")


def _all_lane_actions() -> set[str]:
    return {verb for lane in _PROCESS_LANES for verb in lane_actions(lane)}


# --- the declaration is total and disjoint ----------------------------------


def test_every_lane_action_is_classified_exactly_once() -> None:
    advancing = lane_next.LANE_ADVANCING_VERBS
    reads = lane_next.LANE_READ_VERBS
    assert not advancing & reads
    assert _all_lane_actions() <= advancing | reads
    # No dead rows either: a classified verb no lane declares is stale.
    assert (advancing | reads) <= _all_lane_actions()


# --- the census: every emitted destination is real --------------------------


def test_every_emitted_next_names_a_declared_lane_and_stage() -> None:
    emitted = 0
    for lane in _PROCESS_LANES:
        rail = {stage.id: stage for stage in process_model.LANE_STAGES[lane]}
        for verb in lane_actions(lane):
            block = lane_next.next_stage(lane, verb)
            if block is None:
                continue
            emitted += 1
            assert block["lane"] == lane
            assert block["why"]
            if block["kind"] == "terminal":
                continue
            assert block["kind"] == "always"
            stage = rail[block["stage"]]
            # `action`/`handoff` are read off the stage, never re-derived, so
            # the block can never disagree with the rail it points at.
            assert block["action"] == stage.action
            assert block["handoff"] == stage.handoff
    assert emitted > 0


def test_a_read_verb_gets_no_next() -> None:
    for lane in _PROCESS_LANES:
        for verb in lane_actions(lane):
            if verb in lane_next.LANE_READ_VERBS:
                assert lane_next.next_stage(lane, verb) is None, (lane, verb)


def test_an_advancing_verb_always_answers() -> None:
    for lane in _PROCESS_LANES:
        for verb in lane_actions(lane):
            if verb not in lane_next.LANE_ADVANCING_VERBS:
                continue
            block = lane_next.next_stage(lane, verb)
            assert block is not None, (lane, verb)
            assert block["kind"] in ("always", "terminal")


def test_a_checklist_lane_never_claims_a_successor() -> None:
    """`tend` is peers with no watermark; an ordering there would be invented."""
    checklist = [lane for lane in _PROCESS_LANES if process_model.LANE_KIND[lane] == "checklist"]
    assert checklist == ["tend"]
    for lane in checklist:
        for verb in lane_actions(lane):
            block = lane_next.next_stage(lane, verb)
            if block is not None:
                assert block["kind"] == "terminal", (lane, verb)


# --- the wire: it rides on success, never on a refusal ----------------------


def test_a_refusal_carries_fix_and_no_next() -> None:
    payload = payload_of(call_tool(build_full_server(), "fill", {"action": "not-an-action"}))
    assert "next_stage" not in payload
    assert payload["error"]["fix"]


@pytest.mark.parametrize("lane", _PROCESS_LANES)
def test_the_seam_is_reachable_for_every_lane(lane: str) -> None:
    """Every lane declares at least one advancing verb, so every lane inherits it."""
    assert any(verb in lane_next.LANE_ADVANCING_VERBS for verb in lane_actions(lane))


def test_the_wire_key_does_not_collide_with_the_session_status_next() -> None:
    """`session_status` publishes its own `next`; the lane block must not clobber it."""
    from knotica.mcp_server import envelope

    original = {"next": {"actor": "you", "do": "write the pages"}}
    result = envelope.with_next_stage(
        envelope.success_result(original), {"kind": "terminal", "lane": "fill", "why": "done"}
    )

    assert result.structuredContent is not None
    assert result.structuredContent["next"] == original["next"]
    assert result.structuredContent["next_stage"]["kind"] == "terminal"


def test_an_advancing_lane_call_carries_the_block_on_the_wire(vault_config: Path) -> None:
    """End-to-end: the seam is wired, not merely importable."""
    del vault_config
    payload = payload_of(
        call_tool(
            build_full_server(),
            "tend",
            {"action": "vault_health", "vault_health_action": "doctor", "mode": "dry-run"},
        )
    )

    assert payload["next_stage"]["lane"] == "tend"
    assert payload["next_stage"]["kind"] == "terminal"

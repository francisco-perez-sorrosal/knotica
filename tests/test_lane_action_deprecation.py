"""RED-first tests for lane-dispatcher action deprecation and rejection.

Two behaviours are in scope, both about the string a caller passes as a lane's
``action=`` value:

1. **Rejection.** An action outside the lane's declared table returns
   ``INVALID_ARGUMENT`` with a ``fix=`` naming the live action set, and it is
   recorded through the rejected-action telemetry signal. This half is
   already wired (the lane dispatchers' shared routing already calls
   ``record_rejected_action`` on an unrecognised value), so these cases
   characterize existing behaviour rather than pin something new — they stay
   green today and guard against a future regression.
2. **Deprecation.** An action string that used to be the correct one, but has
   since been superseded by another, still returns the superseded-to action's
   payload, with a ``deprecation`` note added telling the caller what to pass
   instead.

**The contract this file defines for (2), and why it had to be invented
rather than read off an existing table.** No concrete superseded-action pair
survives in the current lane declaration, the interface design, or any
merged decision record: every verb the lane dispatchers wrap kept its exact
name when it moved from a standalone registration into a lane's action
table, and the one documented "no alias" ruling covers tool *names*, not
action *values* — a different layer. Absent a real pair to derive from, this
suite specifies the mechanism generically and proves it two ways:

* an **injection proof** — a fabricated superseded/current pair is placed on
  one lane module at test time, independent of whatever (if anything) that
  module declares for real, so the routing mechanism itself is exercised
  whether or not any lane currently has a real entry;
* a **derived, never-hand-listed structural check** over each lane module's
  own ``SUPERSEDED_ACTIONS`` mapping (empty today), so a future entry is
  picked up automatically and is held to two invariants — it names a live
  action, and it is not itself one.

Every dispatcher-owned symbol is imported lazily inside a helper or test body
so collection stays green even before a lane module declares
``SUPERSEDED_ACTIONS`` at all.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from knotica.core.process_model import LANES
from knotica.mcp_server.dispatch_telemetry import SINK_DIR_ENV_VAR, ROUTING_INVALID_ARGUMENT
from support.dispatch import TOPIC, build_dispatch_server, call_tool, payload_of

# ---------------------------------------------------------------------------
# `home` is the zero-argument router -- it has no action table to reject or
# supersede against, so every case here is scoped to the five process lanes.
# ---------------------------------------------------------------------------

_PROCESS_LANES = tuple(lane for lane in LANES if lane != "home")


def _lane_module(lane: str) -> Any:
    return importlib.import_module(f"knotica.mcp_server.tools_dispatch_{lane}")


def _lane_dispatch_server(lane: str) -> Any:
    module = _lane_module(lane)
    register = getattr(module, f"register_dispatch_{lane}_tools")
    return build_dispatch_server(register)


def _valid_actions(lane: str) -> tuple[str, ...]:
    from knotica.mcp_server.tools_dispatch_lane_common import lane_actions

    return lane_actions(lane)


def _declared_superseded(lane: str) -> Mapping[str, str]:
    """Whatever a lane module declares as superseded today -- empty is valid."""
    return getattr(_lane_module(lane), "SUPERSEDED_ACTIONS", {})


# ---------------------------------------------------------------------------
# Telemetry sink -- redirected exactly as the sink's own test module does:
# `monkeypatch.setenv(KNOTICA_TELEMETRY_DIR, tmp_path)` and nothing else.
# ---------------------------------------------------------------------------


@pytest.fixture
def sink_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "telemetry"
    monkeypatch.setenv(SINK_DIR_ENV_VAR, str(directory))
    return directory


def _sink_records(directory: Path) -> list[dict[str, Any]]:
    records = [
        json.loads(line)
        for path in sorted(directory.glob("dispatch-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    return records


# ---------------------------------------------------------------------------
# 1. Rejection -- already wired; characterized here as a regression guard.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lane", _PROCESS_LANES)
def test_an_unrecognised_action_is_rejected_with_every_valid_action_named_in_fix(
    lane: str, sink_dir: Path
) -> None:
    server = _lane_dispatch_server(lane)
    result = call_tool(server, lane, {"action": "not-a-real-action"})
    payload = payload_of(result)

    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    for action in _valid_actions(lane):
        assert action in payload["error"]["fix"], (
            f"{lane}'s fix= must list {action!r} among the valid actions: {payload['error']['fix']}"
        )


@pytest.mark.parametrize("lane", _PROCESS_LANES)
def test_an_unrecognised_action_is_recorded_as_a_rejected_action(lane: str, sink_dir: Path) -> None:
    server = _lane_dispatch_server(lane)
    call_tool(server, lane, {"action": "not-a-real-action"})

    records = _sink_records(sink_dir)
    assert len(records) == 1, f"expected exactly one telemetry record, got {records!r}"
    assert records[0]["tool"] == lane
    assert records[0]["action"] == "not-a-real-action"
    assert records[0]["outcome"] == ROUTING_INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# 2. Deprecation -- the mechanism, proved generically via an injected pair.
# ---------------------------------------------------------------------------


def test_a_superseded_action_returns_the_replacements_payload_plus_a_deprecation_note(
    vault_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent of whatever a lane module declares for real (see module
    docstring), injecting one pair here proves the mechanism a lane dispatcher
    must implement: an old action string still reaches the action it was
    replaced by, and the caller is told so in the same response."""
    del vault_config
    tend = _lane_module("tend")
    monkeypatch.setattr(tend, "SUPERSEDED_ACTIONS", {"vault_lint": "lint_check"}, raising=False)
    server = _lane_dispatch_server("tend")

    current = payload_of(call_tool(server, "tend", {"action": "lint_check", "topic": TOPIC}))
    superseded = payload_of(call_tool(server, "tend", {"action": "vault_lint", "topic": TOPIC}))

    assert "deprecation" not in current, (
        "calling the current action must never carry a deprecation note"
    )
    assert isinstance(superseded.get("deprecation"), str) and superseded["deprecation"], (
        "the superseded call must carry a non-empty deprecation note"
    )
    assert "lint_check" in superseded["deprecation"], (
        "the deprecation note must name the action to use instead"
    )
    assert {k: v for k, v in superseded.items() if k != "deprecation"} == current, (
        "the superseded call must return the replacement action's payload, unchanged"
    )


def test_an_action_only_someone_elses_alias_supersedes_is_still_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An injected pair on `tend` must not leak into `improve`'s validation --
    each lane's superseded set is its own, not a global vocabulary."""
    tend = _lane_module("tend")
    monkeypatch.setattr(tend, "SUPERSEDED_ACTIONS", {"vault_lint": "lint_check"}, raising=False)
    server = _lane_dispatch_server("improve")

    result = call_tool(server, "improve", {"action": "vault_lint"})

    assert payload_of(result)["error"]["code"] == "INVALID_ARGUMENT"


# ---------------------------------------------------------------------------
# Structural sanity over whatever a lane module declares for real -- derived,
# never hand-listed, so a future entry is picked up automatically. Empty
# today for every lane (see module docstring); the parametrization below
# collects zero cases in that state and starts asserting the moment a lane
# module declares its first real pair.
# ---------------------------------------------------------------------------


def _declared_pairs() -> list[tuple[str, str, str]]:
    return [
        (lane, old, new)
        for lane in _PROCESS_LANES
        for old, new in _declared_superseded(lane).items()
    ]


@pytest.mark.parametrize(("lane", "old", "new"), _declared_pairs())
def test_a_declared_superseded_action_points_at_a_live_action_and_not_at_itself(
    lane: str, old: str, new: str
) -> None:
    valid = _valid_actions(lane)
    assert new in valid, f"{lane}'s superseded {old!r} must point at a live action, got {new!r}"
    assert old not in valid, (
        f"{lane} declares {old!r} as superseded, but it is also still a live action"
    )

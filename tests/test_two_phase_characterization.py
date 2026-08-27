"""Characterization of the two billed two-phase flows that already work, at the wire.

Three tools mint from the same ``confirm_nonce`` seam -- ``loop action=run_eval``,
``loop action=run_once`` and ``gapfill_discover`` -- but only two of them are
*driven* correctly end to end today: the dashboard's ``run_once`` control mints a
preview, discards the nonce, and renders the preview as though it were an
outcome. Bringing that control level with the other two means copying a protocol
whose exact shape nothing currently pins at the tool surface, so this file pins
it first. It is the safety net for the copy, not a test of the copy.

Two gaps in the existing suites are what it fills:

- ``test_loop_dispatch_cadence_run_eval.py`` and ``test_loop_dispatch_run_once.py``
  call ``_loop_run_eval_payload`` / ``_loop_once_payload`` **directly**. Nothing
  drives ``confirm`` through the ``loop`` dispatcher's own parameter and off the
  wire payload, which is exactly the seam a client reproduces.
- ``dispatch_telemetry.record_two_phase`` labels the three legs ``preview`` /
  ``confirmed`` / ``stale-confirm``, and that label is the only signal that can
  answer "did that click cost anything?". ``confirmed`` is asserted nowhere in
  the suite -- ``test_mcp_gaps_read.py`` asserts its *absence*, because nothing
  there presents a live nonce.

``gapfill_discover``'s preview, mismatch, single-use and cross-kind legs are
already pinned at the wire in ``test_mcp_gaps_read.py``; only the two legs that
file leaves open (the ``confirmed`` label, and expiry) are added here.

**No test in this file can reach a billing call.** Both money boundaries are
stubbed by autouse fixtures -- see ``no_live_eval`` and ``no_live_discovery`` for
why that is a measured requirement rather than belt-and-braces.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from support.dispatch import TOPIC, build_verb_server, call_tool, payload_of

pytestmark = pytest.mark.usefixtures("vault_config")

TELEMETRY_LOGGER = "knotica.mcp_server.dispatch_telemetry"


# ---------------------------------------------------------------------------
# Wire harness
# ---------------------------------------------------------------------------


def call(tool: str, args: dict[str, Any]) -> Any:
    """Call ``tool`` on the real, fully-wired server and return its payload.

    Asserts success first: a two-phase assertion made against an error envelope
    would pass for all the wrong reasons (no nonce minted, nothing billed).
    """
    result = call_tool(build_verb_server(), tool, args)
    body = payload_of(result)
    assert getattr(result, "isError", False) is False, f"expected success, got {body!r}"
    assert not (isinstance(body, dict) and "error" in body), f"expected success, got {body!r}"
    return body


def run_eval(**args: Any) -> Any:
    """One ``loop action=run_eval`` call through the dispatcher's own parameters."""
    return call("loop", {"action": "run_eval", "topic": TOPIC, **args})


def discover(**args: Any) -> Any:
    return call("gapfill_discover", {"topic": TOPIC, **args})


def two_phase_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The rendered ``record_two_phase`` lines, in emission order."""
    return [record.getMessage() for record in caplog.records if "two-phase" in record.message]


def age_nonce(path: Path, seconds: float) -> None:
    """Backdate a minted nonce so expiry is exercised without a real sleep."""
    record = json.loads(path.read_text(encoding="utf-8"))
    record["minted_at"] = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
    path.write_text(json.dumps(record), encoding="utf-8")


def run_eval_nonce_file(vault: Path) -> Path:
    from knotica.mcp_server.tools_vault import _run_eval_nonce_path

    return _run_eval_nonce_path(vault, TOPIC)


def discover_nonce_file(vault: Path) -> Path:
    from knotica.mcp_server import confirm_nonce
    from knotica.mcp_server.tools_gaps import _DISCOVER_NONCE_KIND

    return confirm_nonce.nonce_path(vault, _DISCOVER_NONCE_KIND, TOPIC)


def nonce_lifetime() -> float:
    from knotica.mcp_server import confirm_nonce

    return confirm_nonce.NONCE_TTL_SECONDS


# ---------------------------------------------------------------------------
# Money boundaries -- stubbed for every test in this module, by construction
# ---------------------------------------------------------------------------


@dataclass
class EvalBoundary:
    """Records every eval a confirmed ``run_eval`` would have paid for."""

    topics: list[str] = field(default_factory=list)


class NonBillingRunner:
    """A real loop runner with only its spending methods intercepted.

    Constructing a runner is *not* the billing boundary: a phase-1 preview builds
    one too, to probe what would hold the action back. Replacing the factory
    wholesale therefore records a construction that never spent anything, and
    silently degrades the preview's ``holds`` probe to its best-effort fallback.
    Delegating everything except ``observe_default`` / ``poll_once`` keeps the
    preview honest while making the recorder mean "an eval ran", not "a runner
    was built".
    """

    def __init__(self, inner: Any, boundary: EvalBoundary, topic: str) -> None:
        self._inner = inner
        self._boundary = boundary
        self._topic = topic

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _stubbed_observation(self) -> SimpleNamespace:
        self._boundary.topics.append(self._topic)
        return SimpleNamespace(
            acted=True,
            decision=None,
            scalar=0.42,
            message="stubbed observation",
            branch=None,
            sha=None,
        )

    def observe_default(self, **_: Any) -> SimpleNamespace:
        return self._stubbed_observation()

    def poll_once(self, **_: Any) -> SimpleNamespace:
        return self._stubbed_observation()


@pytest.fixture(autouse=True)
def no_live_eval(monkeypatch: pytest.MonkeyPatch) -> EvalBoundary:
    """Stop a confirmed ``run_eval`` short of the worker and judge calls.

    Autouse rather than opt-in: ``resolve_api_key`` falls back to ``./.env``
    after the process environment, so a live key *does* resolve under pytest on a
    maintainer's machine. A test that forgot this fixture would bill on every
    run, and forgetting is the failure mode a per-test opt-in invites.

    Phase-1 tests take it too, and assert it was never reached -- that is what
    makes "a preview is genuinely free" a claim with evidence behind it rather
    than an absence nobody looked for.
    """
    from knotica.core.loop import build_loop_runner as real_build_loop_runner

    boundary = EvalBoundary()

    def guarded_build(vault_path: Path, topic: str, **kwargs: Any) -> NonBillingRunner:
        return NonBillingRunner(
            real_build_loop_runner(vault_path, topic, **kwargs), boundary, topic
        )

    monkeypatch.setattr("knotica.mcp_server.tools_vault.build_loop_runner", guarded_build)
    return boundary


@pytest.fixture(autouse=True)
def no_live_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the drain's search service to ``None`` so phase 2 cannot bill.

    Same measured reason as ``no_live_eval``: a search key resolves from
    ``./.env`` under pytest, so any confirmed drain would issue real, billed
    search calls. Mirrors the fixture of the same name in
    ``test_mcp_gaps_read.py``; autouse here because this module confirms.
    """
    monkeypatch.setattr(
        "knotica.mcp_server.tools_gaps.build_default_discovery_service",
        lambda *args, **kwargs: None,
    )


# ---------------------------------------------------------------------------
# loop action=run_eval -- the three legs, driven through the dispatcher
# ---------------------------------------------------------------------------


def test_a_bare_run_eval_call_returns_a_preview_and_never_reaches_the_eval(
    no_live_eval: EvalBoundary,
) -> None:
    """Phase 1 quotes a cost and mints a nonce without spending anything.

    The preview must carry everything a human needs to decide -- which models
    would run, at what concurrency, and how long the offer stands -- because the
    nonce is the only thing that can turn the decision into a charge.
    """

    body = run_eval()

    assert body["action"] == "run_eval"
    assert body["confirm_nonce"], "without a nonce there is no way to approve the spend"
    assert body["ttl"] > 0
    assert body["worker"] and body["judge"]
    assert body["estimated_cost"]
    assert "acted" not in body, "a preview must not report an eval it did not run"
    assert no_live_eval.topics == [], "phase 1 reached the eval boundary"


def test_a_matching_nonce_runs_the_eval_and_returns_an_outcome_rather_than_a_preview(
    no_live_eval: EvalBoundary,
) -> None:
    """Phase 2 executes, and its payload is distinguishable from a preview.

    The distinguishing feature is the absence of ``confirm_nonce``: a client that
    cannot tell an outcome from a preview renders "finished" over a run that
    never happened, which is the defect this contract exists to prevent.
    """
    nonce = run_eval()["confirm_nonce"]

    body = run_eval(confirm=nonce)

    assert no_live_eval.topics == [TOPIC], "a matching nonce must reach the eval exactly once"
    assert "confirm_nonce" not in body, "an outcome must not look like a fresh offer"
    assert body["acted"] is True
    assert body["billed"] is True


def test_a_confirm_that_matches_no_live_nonce_falls_back_to_a_fresh_preview(
    no_live_eval: EvalBoundary,
) -> None:
    """A wrong token buys nothing and leaks nothing about whether one was live."""
    minted = run_eval()["confirm_nonce"]

    body = run_eval(confirm="not-the-nonce")

    assert no_live_eval.topics == []
    assert body["confirm_nonce"] != minted, "the mismatch consumed the live nonce and re-minted"
    assert "acted" not in body


def test_a_replayed_nonce_cannot_run_the_eval_a_second_time(
    no_live_eval: EvalBoundary,
) -> None:
    """One approval buys one eval: consuming the nonce deletes it."""
    nonce = run_eval()["confirm_nonce"]

    run_eval(confirm=nonce)
    replay = run_eval(confirm=nonce)

    assert no_live_eval.topics == [TOPIC], "the replay bought a second eval"
    assert "confirm_nonce" in replay, "a spent nonce must fall back to a fresh offer"
    assert "acted" not in replay


def test_a_nonce_just_inside_its_lifetime_still_runs_the_eval(
    template_vault: Path,
    no_live_eval: EvalBoundary,
) -> None:
    """An offer is honoured right up to its stated deadline.

    The control for the expiry case below: without it, an implementation that
    rejected every aged nonce -- or every nonce at all -- would look correct.
    """
    nonce = run_eval()["confirm_nonce"]
    age_nonce(run_eval_nonce_file(template_vault), nonce_lifetime() - 5)

    body = run_eval(confirm=nonce)

    assert no_live_eval.topics == [TOPIC]
    assert "confirm_nonce" not in body


def test_a_nonce_past_its_lifetime_falls_back_to_a_preview(
    template_vault: Path,
    no_live_eval: EvalBoundary,
) -> None:
    """An abandoned approval cannot be replayed against a vault that moved on."""
    nonce = run_eval()["confirm_nonce"]
    age_nonce(run_eval_nonce_file(template_vault), nonce_lifetime() + 5)

    body = run_eval(confirm=nonce)

    assert no_live_eval.topics == []
    assert body["confirm_nonce"], "expiry re-offers rather than erroring"
    assert "acted" not in body


def test_a_discovery_nonce_cannot_run_the_eval(
    no_live_eval: EvalBoundary,
) -> None:
    """Approving a cheap search drain must not authorize an expensive eval.

    Both actions mint the same token shape into the same directory; only the
    per-action kind in the filename separates them.
    """
    foreign = discover()["confirm_nonce"]

    body = run_eval(confirm=foreign)

    assert no_live_eval.topics == []
    assert body["confirm_nonce"] != foreign
    assert "acted" not in body


# ---------------------------------------------------------------------------
# The routing-outcome label -- the only record of whether a click cost anything
# ---------------------------------------------------------------------------


def test_the_preview_and_confirm_legs_of_an_eval_are_labelled_distinctly_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A free preview and a confirm that billed must not read alike.

    The dispatch record is identical for both -- same tool, same action, same
    topic. Only the two-phase label separates them, and it carries the answer to
    the one question a spending surface has to be able to answer afterwards.
    """

    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        nonce = run_eval()["confirm_nonce"]
        run_eval(confirm=nonce)

    lines = two_phase_lines(caplog)

    assert any("outcome=preview" in line and "billed=False" in line for line in lines), lines
    assert any("outcome=confirmed" in line and "billed=True" in line for line in lines), (
        "a confirm that ran the eval must say it billed"
    )
    assert not any("outcome=stale-confirm" in line for line in lines), lines
    assert all("action=run_eval" in line for line in lines), lines


def test_an_eval_confirm_that_bought_nothing_is_labelled_as_such(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stale confirm is indistinguishable from a real one at the tool surface.

    The caller believed they were spending and nothing ran, so the label is the
    only place that discrepancy is recorded.
    """

    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        run_eval(confirm="not-a-live-nonce")

    lines = two_phase_lines(caplog)

    assert any("outcome=stale-confirm" in line for line in lines), lines
    assert not any("outcome=confirmed" in line for line in lines), (
        "nothing presented a live nonce, so nothing may claim to have billed"
    )


# ---------------------------------------------------------------------------
# gapfill_discover -- the two legs test_mcp_gaps_read.py leaves open
# ---------------------------------------------------------------------------


def test_a_confirmed_drain_is_labelled_as_the_leg_that_billed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The drain's confirm leg carries the same billed label the eval's does.

    Pins the label, not the drain's findings -- what a drain stages is covered
    where the gap queue is seeded.
    """

    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        nonce = discover()["confirm_nonce"]
        body = discover(confirm=nonce)

    lines = two_phase_lines(caplog)

    assert any("outcome=confirmed" in line and "billed=True" in line for line in lines), lines
    assert "confirm_nonce" not in body, "an outcome must not look like a fresh offer"
    assert "suggestions_staged" in body


def test_a_discovery_nonce_past_its_lifetime_falls_back_to_a_preview(
    template_vault: Path,
) -> None:
    """The drain honours the same deadline as the eval -- one shared mechanism."""
    nonce = discover()["confirm_nonce"]
    age_nonce(discover_nonce_file(template_vault), nonce_lifetime() + 5)

    body = discover(confirm=nonce)

    assert body["confirm_nonce"], "expiry re-offers rather than erroring"
    assert "suggestions_staged" not in body, "an expired nonce must not run the drain"

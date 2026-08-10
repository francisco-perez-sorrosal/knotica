"""A run that reached no model must not report itself as billed.

``billed: true`` was a hardcoded literal on both billed loop actions, so a tick
that declined -- unchanged HEAD, bookkeeping-only commits, an ingest in
progress -- reported spending money it had not spent. Two consecutive no-ops in
the reported session both read ``billed: true, acted: false, scalar: null``.

The paired half is the preview: a hold that will decline the call is knowable
*before* the confirm, and quoting it there is the difference between an
operator choosing to wait and an operator paying a round-trip to find out.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from knotica.core.loop import LoopCycleResult
from knotica.core.loop_state import LoopDecision
from knotica.store.local import LocalFSStore

TOPIC = "agentic-systems"


def _declined(message: str) -> LoopCycleResult:
    """What ``observe_default``/``poll_once`` return when they choose not to run."""
    return LoopCycleResult(
        acted=False,
        branch="main",
        sha="abc123",
        decision=LoopDecision.none,
        scalar=None,
        message=message,
    )


def _acted(scalar: float) -> LoopCycleResult:
    return LoopCycleResult(
        acted=True,
        branch="main",
        sha="abc123",
        decision=LoopDecision.pass_,
        scalar=scalar,
        message="observed",
    )


def _payload(result: dict) -> dict:
    return result["data"] if "data" in result else result


# ---------------------------------------------------------------------------
# billed is derived from whether an eval actually ran
# ---------------------------------------------------------------------------


def test_a_declined_run_eval_reports_it_did_not_bill(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    from knotica.mcp_server.tools_vault import _execute_run_eval

    with patch("knotica.core.loop.LoopRunner.observe_default") as observe:
        observe.return_value = _declined("observation held: ingest in progress")
        body = _payload(
            _execute_run_eval(
                LocalFSStore(template_vault),
                template_vault,
                TOPIC,
                worker="w",
                judge="j",
                num_threads=4,
            )
        )

    assert body["acted"] is False
    assert body["billed"] is False, "a call that reached no model spent nothing"


def test_an_executed_run_eval_still_reports_billed(
    vault_config: Path, template_vault: Path
) -> None:
    """The flag must stay honest in the other direction too."""
    del vault_config
    from knotica.mcp_server.tools_vault import _execute_run_eval

    with patch("knotica.core.loop.LoopRunner.observe_default") as observe:
        observe.return_value = _acted(0.66)
        body = _payload(
            _execute_run_eval(
                LocalFSStore(template_vault),
                template_vault,
                TOPIC,
                worker="w",
                judge="j",
                num_threads=4,
            )
        )

    assert body["acted"] is True
    assert body["billed"] is True


def test_a_run_once_where_neither_leg_acted_reports_it_did_not_bill(
    vault_config: Path, template_vault: Path
) -> None:
    """The reported message verbatim: only loop bookkeeping had changed."""
    del vault_config
    from knotica.mcp_server.tools_vault import _execute_run_once

    with (
        patch("knotica.core.loop.LoopRunner.observe_default") as observe,
        patch("knotica.core.loop.LoopRunner.poll_once") as poll,
    ):
        observe.return_value = _declined("only loop bookkeeping changed since last observation")
        poll.return_value = _declined("no pending loop branches")
        body = _payload(_execute_run_once(LocalFSStore(template_vault), template_vault, TOPIC))

    assert body["billed"] is False


def test_a_run_once_billing_on_the_candidate_leg_alone(
    vault_config: Path, template_vault: Path
) -> None:
    """An observation that declined does not make a gated candidate free."""
    del vault_config
    from knotica.mcp_server.tools_vault import _execute_run_once

    with (
        patch("knotica.core.loop.LoopRunner.observe_default") as observe,
        patch("knotica.core.loop.LoopRunner.poll_once") as poll,
    ):
        observe.return_value = _declined("default branch unchanged since last observation")
        poll.return_value = _acted(0.71)
        body = _payload(_execute_run_once(LocalFSStore(template_vault), template_vault, TOPIC))

    assert body["billed"] is True


# ---------------------------------------------------------------------------
# the preview quotes the holds before the confirm
# ---------------------------------------------------------------------------


def test_the_run_once_preview_reports_hold_state(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    from knotica.mcp_server.tools_vault import _loop_once_payload

    body = _payload(
        _loop_once_payload(LocalFSStore(template_vault), template_vault, TOPIC, confirm="")
    )

    assert "holds" in body, "an operator must learn about a hold before paying for the confirm"
    assert set(body["holds"]) == {"held", "reasons", "cadence_remaining_seconds"}


def test_the_run_eval_preview_reports_hold_state(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    from knotica.mcp_server.tools_vault import _loop_run_eval_payload

    body = _payload(
        _loop_run_eval_payload(
            LocalFSStore(template_vault), template_vault, TOPIC, confirm="", num_threads=None
        )
    )

    assert "holds" in body


def test_a_cadence_hold_is_quoted_with_its_remaining_seconds(
    vault_config: Path, template_vault: Path
) -> None:
    """The operator's question is "how long", not merely "is it held"."""
    del vault_config
    from knotica.mcp_server.tools_vault import _loop_once_payload

    held = {
        "held": True,
        "reasons": ["cadence held: 0.82h since last eval start < 1h interval"],
        "cadence_remaining_seconds": 638.0,
    }
    with patch("knotica.core.loop.LoopRunner.hold_preview", return_value=held):
        body = _payload(
            _loop_once_payload(LocalFSStore(template_vault), template_vault, TOPIC, confirm="")
        )

    assert body["holds"]["held"] is True
    assert body["holds"]["cadence_remaining_seconds"] == 638.0


def test_a_preview_whose_hold_probe_fails_still_quotes_a_cost_and_mints_a_nonce(
    vault_config: Path, template_vault: Path
) -> None:
    """The probe is a convenience; it must never be able to break the preview."""
    del vault_config
    from knotica.mcp_server.tools_vault import _loop_once_payload

    with patch("knotica.core.loop.LoopRunner.hold_preview", side_effect=RuntimeError("git down")):
        body = _payload(
            _loop_once_payload(LocalFSStore(template_vault), template_vault, TOPIC, confirm="")
        )

    assert body["confirm_nonce"]
    assert body["holds"]["held"] is False, "an unknown hold state must not claim a hold"

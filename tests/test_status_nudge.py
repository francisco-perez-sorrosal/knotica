"""The SessionStart ``status --nudge`` renderer leads with the active KB.

Also covers the switch to ``view="attention"``: both ``knotica home`` and
``knotica status --nudge`` must request the cheap cross-topic view rather
than the default ``"summary"`` (asserted at the seam -- the ``view`` kwarg
reaching ``gather_wiki_status`` -- not from prose), and ``render_nudge``
must render correctly from the attention payload's actual shape rather
than ``summary``'s.

RED against the code as it stands today: ``home.py``/``status.py`` do not
pass ``view=`` at all (implicit ``"summary"`` default), and ``render_nudge``
still reads ``payload["totals"]["notes"]["drifted"]``, a key the attention
payload never computes -- every attention-shaped fixture below either fails
the assertion or raises a ``KeyError`` until the CLI is switched over.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Any

import pytest

from knotica.cli import home as home_module
from knotica.cli import status as status_module
from knotica.cli.common import EXIT_SUCCESS, Console
from knotica.cli.status import render_nudge
from knotica.core.config import ConfigDiagnosis, ConfigState, ResolvedVault


def _console() -> tuple[Console, io.StringIO]:
    out = io.StringIO()
    return Console(quiet=False, verbose=False, use_color=False, out=out, err=io.StringIO()), out


def test_nudge_leads_with_the_active_kb_line() -> None:
    console, out = _console()
    payload = {
        "topics": [
            {
                "topic": "agentic-systems",
                "suggestions": {"pending": 0, "refused_awaiting_rework": 0},
                "compile_ready": False,
            }
        ],
        "totals": {"notes": {"total": 0, "drifted": 0}},
    }

    render_nudge(console, payload, ResolvedVault(name="main", path=Path("/data/knotica")))

    lines = out.getvalue().splitlines()
    assert lines[0] == "Active KB: main (/data/knotica)"
    assert "This vault covers topics: agentic-systems" in out.getvalue()


def test_nudge_states_the_active_kb_even_with_no_topics() -> None:
    console, out = _console()

    render_nudge(
        console,
        {"topics": [], "totals": {"notes": {"total": 0, "drifted": 0}}},
        ResolvedVault(name="research", path=Path("/kb/research")),
    )

    assert out.getvalue().strip() == "Active KB: research (/kb/research)"


# ---------------------------------------------------------------------------
# The drifted-notes clause: one line, only when there is something to say
# ---------------------------------------------------------------------------


def _payload_with(*, drifted: int) -> dict[str, object]:
    return {
        "topics": [
            {
                "topic": "agentic-systems",
                "suggestions": {"pending": 0, "refused_awaiting_rework": 0},
                "compile_ready": False,
            }
        ],
        "totals": {"notes": {"total": drifted + 3, "drifted": drifted}},
    }


def test_nudge_appends_a_drifted_notes_clause_when_notes_have_drifted() -> None:
    console, out = _console()

    render_nudge(
        console, _payload_with(drifted=2), ResolvedVault(name="main", path=Path("/data/knotica"))
    )

    assert "2 notes drifted" in out.getvalue()


def test_nudge_says_nothing_about_notes_when_none_have_drifted() -> None:
    """The nudge's contract: it prints nothing when there is nothing to say."""
    console, out = _console()

    render_nudge(
        console, _payload_with(drifted=0), ResolvedVault(name="main", path=Path("/data/knotica"))
    )

    assert "notes drifted" not in out.getvalue()
    assert "Needs attention" not in out.getvalue(), (
        "with zero pending/refused/compile-ready/drifted, the attention line must not appear at all"
    )


def _has_line_with(output: str, *substrings: str) -> bool:
    """Whether any single line of ``output`` contains every one of ``substrings``.

    Case-insensitive and line-scoped rather than a whole-blob substring check,
    so a match actually pins one rendered line rather than two unrelated
    fragments landing on different lines by coincidence.
    """
    return any(all(s.lower() in line.lower() for s in substrings) for line in output.splitlines())


def _attention_payload(
    *,
    pending: int = 0,
    refused: int = 0,
    compile_ready: bool = False,
    runner_alive: bool = False,
    last_lint: dict[str, Any] | None = None,
    drift: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A ``view="attention"`` payload shaped like ``core.status._attention_status``'s real return.

    One topic carries every requested count, and ``totals`` is kept consistent
    with it -- so a test does not care whether ``render_nudge`` sums the
    per-topic fields or reads the pre-summed totals directly; both read the
    same numbers from this fixture.
    """
    topic_row = {
        "topic": "agentic-systems",
        "suggestions": {"pending": pending, "refused_awaiting_rework": refused},
        "compile_ready": compile_ready,
        "runner": {"alive": runner_alive},
    }
    return {
        "schema_version": 1,
        "vault_name": "main",
        "topics": [topic_row],
        "totals": {
            "topics": 1,
            "pending": pending,
            "refused_awaiting_rework": refused,
            "compile_ready": 1 if compile_ready else 0,
            "runners_alive": 1 if runner_alive else 0,
        },
        "last_lint": (
            last_lint
            if last_lint is not None
            else {"date": "2026-08-20", "age_days": 0, "stale": False}
        ),
        "drift": drift if drift is not None else {"default_collapsed": True, "count": None},
    }


# ---------------------------------------------------------------------------
# The attention payload's own field set -- pending / refused / compile-ready /
# running, each omitted when zero (the pre-existing omit-if-zero discipline
# above, now against `view="attention"`'s field set rather than `summary`'s)
# ---------------------------------------------------------------------------


def test_nudge_reports_pending_suggestions_from_an_attention_payload() -> None:
    console, out = _console()

    render_nudge(
        console,
        _attention_payload(pending=4),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert "4 pending suggestion(s)" in out.getvalue()


def test_nudge_omits_the_pending_clause_when_no_suggestions_are_pending() -> None:
    console, out = _console()

    render_nudge(
        console,
        _attention_payload(pending=0),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert "pending suggestion" not in out.getvalue()


def test_nudge_reports_refused_awaiting_rework_from_an_attention_payload() -> None:
    console, out = _console()

    render_nudge(
        console,
        _attention_payload(refused=1),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert "1 refused-awaiting-rework" in out.getvalue()


def test_nudge_omits_the_refused_clause_when_nothing_is_refused() -> None:
    console, out = _console()

    render_nudge(
        console,
        _attention_payload(refused=0),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert "refused-awaiting-rework" not in out.getvalue()


def test_nudge_reports_compile_ready_topics_from_an_attention_payload() -> None:
    console, out = _console()

    render_nudge(
        console,
        _attention_payload(compile_ready=True),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert "1 topic(s) compile-ready" in out.getvalue()


def test_nudge_omits_the_compile_ready_clause_when_no_topic_is_compile_ready() -> None:
    console, out = _console()

    render_nudge(
        console,
        _attention_payload(compile_ready=False),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert "compile-ready" not in out.getvalue()


def test_nudge_reports_a_running_line_when_a_runner_is_alive() -> None:
    """The attention view's runner liveness has never surfaced in the CLI nudge before."""
    console, out = _console()

    render_nudge(
        console,
        _attention_payload(runner_alive=True),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert _has_line_with(out.getvalue(), "1", "running")


def test_nudge_omits_the_running_line_when_no_runner_is_alive() -> None:
    console, out = _console()

    render_nudge(
        console,
        _attention_payload(runner_alive=False),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert "running" not in out.getvalue().lower()


# ---------------------------------------------------------------------------
# `last_lint` staleness -- the attention view reports the recorded date and
# whether it has gone stale, never a re-walked count (dec-092)
# ---------------------------------------------------------------------------


def test_nudge_reports_a_staleness_line_when_the_recorded_lint_is_stale() -> None:
    console, out = _console()

    render_nudge(
        console,
        _attention_payload(last_lint={"date": "2026-01-01", "age_days": 30, "stale": True}),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert _has_line_with(out.getvalue(), "lint")


def test_nudge_says_nothing_about_lint_staleness_when_the_recorded_lint_is_fresh() -> None:
    console, out = _console()

    render_nudge(
        console,
        _attention_payload(last_lint={"date": "2026-08-20", "age_days": 0, "stale": False}),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert not _has_line_with(out.getvalue(), "lint")


# ---------------------------------------------------------------------------
# The drift row's marker shape -- one unconditional line, never a count
# (dec-092: resolving a real count means resolving every note's anchor, the
# exact cost the CLI nudge does not pay)
# ---------------------------------------------------------------------------


def test_nudge_reports_a_drift_line_from_the_minimal_marker_shape() -> None:
    """No richer ``drift`` shape is ever produced -- ``count`` is always ``None``."""
    console, out = _console()

    render_nudge(
        console,
        _attention_payload(drift={"default_collapsed": True, "count": None}),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert _has_line_with(out.getvalue(), "drift")


def test_nudge_reports_the_drift_line_even_when_nothing_else_needs_attention() -> None:
    """The drift line is unconditional -- it does not join the "Needs attention" clause list."""
    console, out = _console()

    render_nudge(
        console, _attention_payload(), ResolvedVault(name="main", path=Path("/data/knotica"))
    )

    assert _has_line_with(out.getvalue(), "drift")


def test_nudge_omits_the_needs_attention_clause_when_the_attention_payload_is_fully_quiet() -> None:
    """Pending/refused/compile-ready/running all render nothing when every count is zero."""
    console, out = _console()

    render_nudge(
        console, _attention_payload(), ResolvedVault(name="main", path=Path("/data/knotica"))
    )

    assert "Needs attention" not in out.getvalue()


# ---------------------------------------------------------------------------
# `totals.notes.drifted` is gone from the attention payload -- reading it
# today silently reads whichever value survived from a different view's shape
# ---------------------------------------------------------------------------


def test_nudge_does_not_raise_on_an_attention_payload_with_no_totals_notes_key() -> None:
    """Proves the summary-only field is truly gone, not just unread by luck."""
    console, out = _console()
    payload = _attention_payload()
    assert "notes" not in payload["totals"], "fixture must mirror the real attention shape"

    render_nudge(console, payload, ResolvedVault(name="main", path=Path("/data/knotica")))


# ---------------------------------------------------------------------------
# The seam: both commands must request `view="attention"`, not the implicit
# `"summary"` default -- asserted on the kwarg reaching `gather_wiki_status`,
# not on rendered prose
# ---------------------------------------------------------------------------


def _quiet_attention_payload() -> dict[str, Any]:
    return _attention_payload()


def _spy_gather_wiki_status(payload: dict[str, Any], calls: list[dict[str, Any]]) -> Any:
    """A stand-in for ``gather_wiki_status`` that records the kwargs it was called with.

    Returns ``payload`` unconditionally -- the seam test cares which view was
    *requested*, not how the (real) function would have routed it.
    """

    def _fake(store: Any, vault_path: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return payload

    return _fake


def _ready_diagnosis(vault_path: Path) -> ConfigDiagnosis:
    return ConfigDiagnosis(
        state=ConfigState.READY,
        detail="",
        remediation="",
        vault=ResolvedVault(name="main", path=vault_path),
    )


def test_home_command_requests_the_attention_view_from_gather_wiki_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``knotica home`` must read the cheap cross-topic view, not the expensive default."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        home_module,
        "gather_wiki_status",
        _spy_gather_wiki_status(_quiet_attention_payload(), calls),
    )
    monkeypatch.setattr(home_module, "diagnose", lambda: _ready_diagnosis(tmp_path))

    exit_code = home_module.run(argparse.Namespace(quiet=True, verbose=False, no_color=True))

    assert exit_code == EXIT_SUCCESS
    assert calls, "gather_wiki_status was never called"
    assert calls[0].get("view", "summary") == "attention"


def test_status_nudge_flag_requests_the_attention_view_from_gather_wiki_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``knotica status --nudge`` must read the cheap cross-topic view too."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        status_module,
        "gather_wiki_status",
        _spy_gather_wiki_status(_quiet_attention_payload(), calls),
    )
    monkeypatch.setattr(status_module, "diagnose", lambda: _ready_diagnosis(tmp_path))

    exit_code = status_module.run(
        argparse.Namespace(
            topic=None,
            nudge=True,
            json=False,
            wide=False,
            quiet=True,
            verbose=False,
            no_color=True,
        )
    )

    assert exit_code == EXIT_SUCCESS
    assert calls, "gather_wiki_status was never called"
    assert calls[0].get("view", "summary") == "attention"


def test_status_without_the_nudge_flag_still_uses_the_default_summary_view(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The attention switch is scoped to ``--nudge``; the table/`--json` views are unaffected."""
    calls: list[dict[str, Any]] = []
    summary_payload: dict[str, Any] = {
        "schema_version": 1,
        "vault": str(tmp_path),
        "compile_ready_threshold": 3,
        "topics": [],
        "totals": {"topics": 0, "pages": 0, "curated": 0},
        "last_lint": None,
        "unpushed": None,
    }
    monkeypatch.setattr(
        status_module,
        "gather_wiki_status",
        _spy_gather_wiki_status(summary_payload, calls),
    )
    monkeypatch.setattr(status_module, "diagnose", lambda: _ready_diagnosis(tmp_path))

    exit_code = status_module.run(
        argparse.Namespace(
            topic=None,
            nudge=False,
            json=False,
            wide=False,
            quiet=True,
            verbose=False,
            no_color=True,
        )
    )

    assert exit_code == EXIT_SUCCESS
    assert calls, "gather_wiki_status was never called"
    assert calls[0].get("view", "summary") == "summary"


# ---------------------------------------------------------------------------
# CLI/dashboard Home parity -- the three signals the nudge once could not say
# ---------------------------------------------------------------------------


def _parity_payload(**row_overrides: Any) -> dict[str, Any]:
    """An attention payload whose one topic carries the newer signal blocks."""
    payload = _attention_payload()
    payload["topics"][0].update(
        {
            "suggestions": {"pending": 0, "refused_awaiting_rework": 0, "total": 0},
            "gaps": {"open_total": 0},
            "arena": {"stage": None},
            "gate": {"baseline_unreachable": None},
        }
    )
    payload["topics"][0].update(row_overrides)
    return payload


def test_nudge_reports_an_unreachable_gate_baseline() -> None:
    """The dashboard Home shows the jam as a blocked row; a session-start nudge
    reading 'nothing needs you' over the same vault was the parity hole."""
    console, out = _console()

    render_nudge(
        console,
        _parity_payload(gate={"baseline_unreachable": {"baseline": 0.95, "last_scalar": 0.89}}),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert _has_line_with(out.getvalue(), "baseline unreachable", "rebaseline")


def test_nudge_reports_open_gaps_only_when_discovery_never_ran() -> None:
    # Same conservative rule as the dashboard: fires only when nothing was
    # ever proposed, so it cannot false-positive on a topic mid-pipeline.
    console, out = _console()

    render_nudge(
        console,
        _parity_payload(gaps={"open_total": 3}),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert _has_line_with(out.getvalue(), "3 open gap(s)", "no discovery")


def test_nudge_stays_quiet_about_gaps_once_anything_was_proposed() -> None:
    console, out = _console()

    render_nudge(
        console,
        _parity_payload(
            gaps={"open_total": 3},
            suggestions={"pending": 0, "refused_awaiting_rework": 0, "total": 1},
        ),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert "open gap" not in out.getvalue()


def test_nudge_reports_an_aborted_arena_race() -> None:
    console, out = _console()

    render_nudge(
        console,
        _parity_payload(arena={"stage": "aborted"}),
        ResolvedVault(name="main", path=Path("/data/knotica")),
    )

    assert _has_line_with(out.getvalue(), "arena race(s) aborted")


def test_nudge_renders_a_pre_parity_payload_one_signal_short_not_raising() -> None:
    """An older server omits the gate/arena/gaps blocks entirely."""
    console, out = _console()

    render_nudge(
        console, _attention_payload(pending=2), ResolvedVault(name="main", path=Path("/data"))
    )

    assert _has_line_with(out.getvalue(), "2 pending suggestion(s)")

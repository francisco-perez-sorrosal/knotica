"""The SessionStart ``status --nudge`` renderer leads with the active KB."""

from __future__ import annotations

import io
from pathlib import Path

from knotica.cli.common import Console
from knotica.cli.status import render_nudge
from knotica.core.config import ResolvedVault


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

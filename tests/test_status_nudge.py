"""The SessionStart ``status --nudge`` renderer leads with the active KB."""

from __future__ import annotations

import io
from pathlib import Path

from knotica.cli.common import Console
from knotica.cli.status import _render_nudge
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
        ]
    }

    _render_nudge(console, payload, ResolvedVault(name="main", path=Path("/data/knotica")))

    lines = out.getvalue().splitlines()
    assert lines[0] == "Active KB: main (/data/knotica)"
    assert "This vault covers topics: agentic-systems" in out.getvalue()


def test_nudge_states_the_active_kb_even_with_no_topics() -> None:
    console, out = _console()

    _render_nudge(
        console, {"topics": []}, ResolvedVault(name="research", path=Path("/kb/research"))
    )

    assert out.getvalue().strip() == "Active KB: research (/kb/research)"

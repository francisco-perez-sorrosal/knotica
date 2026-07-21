"""Characterization safety net for the loop's two divergent ``LoopRunner``
construction sites (P-A consolidation, pre-extraction baseline).

``cli/loop.py::_build_runner`` (the background watcher) and
``mcp_server/tools_source_ingest.py::_run_gate`` (the synchronous MCP-tool
gate) each construct a ``LoopRunner`` independently, with **no shared
factory** -- ``RESEARCH_FINDINGS_loop-internals.md`` §3/§4 names this as an
incidental divergence with no ADR behind it. This file captures **today's**
effective config value at each site (which kwargs are passed explicitly, and
which fall through to ``LoopRunner``'s own defaults) as the golden baseline
the coming ``build_loop_runner`` factory (P-A Step 8) must reproduce
*per call site* -- construction is meant to unify, the values are explicitly
NOT meant to converge (a separate future decision).

Derived from a direct read of both construction sites, not from any planned
factory shape. Zero network and zero real git history: ``cli/loop.py``'s site
is driven through the real ``argparse`` parser (so its defaults are the actual
CLI defaults, not a hand-guessed namespace); ``tools_source_ingest.py``'s site
is driven by replacing ``LoopRunner`` with a capturing subclass that runs the
real ``__init__`` and returns a stub result immediately, so ``_run_gate``'s
single-cycle gate loop exits without needing a real evaluate/harness call.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from knotica.core.arena import heuristic_arena_score
from knotica.core.loop import LoopRunner, harness_evaluate
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"

# ---------------------------------------------------------------------------
# cli/loop.py::_build_runner -- today's watcher-side effective config
# ---------------------------------------------------------------------------


def _parsed_loop_args(vault: Path, *extra: str) -> argparse.Namespace:
    """Parse ``knotica loop`` args through the real CLI parser (real defaults)."""
    from knotica.cli import loop as cli_loop

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    cli_loop.configure(subparsers)
    return parser.parse_args(["loop", "--topic", TOPIC, "--vault", str(vault), *extra])


def test_build_runner_in_watch_mode_captures_todays_effective_config(
    tmp_path: Path, unconfigured_env: Path
) -> None:
    """Default ``knotica loop --topic ... --vault ...`` (watch mode, no flags):
    the unconfigured host resolves ``[gapfill]`` to its off-by-default values,
    and watch mode's 20s quiet window applies (only ``--once`` zeroes it)."""
    from knotica.cli import loop as cli_loop

    args = _parsed_loop_args(tmp_path)

    runner = cli_loop._build_runner(args, tmp_path)

    assert runner._evaluate is harness_evaluate
    assert runner._prefix == "loop/c/"
    assert runner._push_remote is None
    assert runner._arena_enabled is True
    assert runner._arena_score is heuristic_arena_score
    assert runner._arena_variants is None
    assert runner._discover_on_regression is False, (
        "an unconfigured host must resolve [gapfill] discover_on_regression to its "
        "off-by-default value at this call site"
    )
    assert runner._gapfill_max_gaps == 5
    assert runner._observe_quiet_seconds == pytest.approx(20.0), (
        "watch mode's HEAD-stability window is the CLI's own default (--observe-quiet), "
        "unlike the MCP-gate site, which never sets this kwarg at all"
    )


def test_build_runner_in_once_mode_zeroes_the_quiet_window(
    tmp_path: Path, unconfigured_env: Path
) -> None:
    """``--once`` is the one flag that changes this site's effective config:
    a one-shot invocation observes immediately, never debounced."""
    from knotica.cli import loop as cli_loop

    args = _parsed_loop_args(tmp_path, "--once")

    runner = cli_loop._build_runner(args, tmp_path)

    assert runner._observe_quiet_seconds == 0.0


def test_build_runner_with_no_arena_flag_disables_arena_scoring(
    tmp_path: Path, unconfigured_env: Path
) -> None:
    """``--no-arena`` is the one flag that flips arena_enabled/arena_score off
    at this site -- captured so the factory must honor it per call site too."""
    from knotica.cli import loop as cli_loop

    args = _parsed_loop_args(tmp_path, "--no-arena")

    runner = cli_loop._build_runner(args, tmp_path)

    assert runner._arena_enabled is False
    assert runner._arena_score is None


# ---------------------------------------------------------------------------
# tools_source_ingest.py::_run_gate -- today's synchronous MCP-gate config
# ---------------------------------------------------------------------------


def test_run_gate_constructs_a_loop_runner_with_todays_effective_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The MCP-gate site hardcodes ``arena_enabled=True, arena_score=
    heuristic_arena_score`` and passes NEITHER ``discover_on_regression`` nor
    ``observe_quiet_seconds`` -- both fall through to ``LoopRunner``'s own
    (different-from-the-watcher) defaults, exactly the divergence the research
    findings name and the factory must reproduce at this call site unchanged."""
    from knotica.mcp_server import tools_source_ingest

    captured: list[LoopRunner] = []
    target_branch = "loop/c/agentic-systems/source-deadbeef"

    class _SpyLoopRunner(LoopRunner):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]
            captured.append(self)

        def poll_once(self) -> SimpleNamespace:
            return SimpleNamespace(
                acted=True, branch=target_branch, sha="deadbeef", decision=None, scalar=None
            )

    monkeypatch.setattr(tools_source_ingest, "LoopRunner", _SpyLoopRunner)
    store = LocalFSStore(tmp_path)

    tools_source_ingest._run_gate(store, tmp_path, TOPIC, target_branch)

    assert len(captured) == 1
    runner = captured[0]
    assert runner._evaluate is harness_evaluate
    assert runner._store is store
    assert runner._arena_enabled is True
    assert runner._arena_score is heuristic_arena_score
    assert runner._prefix == "loop/c/", (
        "unset at this site -- falls through to LoopRunner's own default"
    )
    assert runner._push_remote is None
    assert runner._arena_variants is None
    assert runner._discover_on_regression is False, (
        "unset at this site (unlike the watcher, which reads [gapfill] from config) -- "
        "falls through to LoopRunner's own off-by-default value"
    )
    assert runner._gapfill_max_gaps == 5
    assert runner._observe_quiet_seconds == 0.0, (
        "unset at this site -- falls through to LoopRunner's own immediate-observe "
        "default, unlike the watcher's 20s watch-mode window"
    )

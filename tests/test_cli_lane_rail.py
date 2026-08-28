"""Behavioral tests for a rail lane's bare CLI invocation.

``knotica fill``/``knotica improve``/``knotica tend`` run with no subcommand
must render that lane's rail -- each declared stage's title, its live state,
and the command that advances it -- and exit ``0``, instead of today's
``print_usage()``/``EXIT_MISUSE``. The rail's live state is read straight off
``gather_wiki_status(view="summary")``'s ``lanes`` block (already landed): this
step's own contribution is projecting that block onto stdout, never
re-deriving a second notion of stage state.

RED-first, paired with a concurrent implementer step: ``LaneCommand.run()``
still calls ``print_usage()`` on a bare invocation when this file is written,
so every position-matrix test below fails against *today's* behavior --
either a returned ``EXIT_MISUSE`` (if ``--topic`` parses) or (more likely,
since the lane parser does not accept ``--topic`` yet) an uncaught
``SystemExit`` from argparse rejecting the unrecognized flag. Either shape is
the RED baseline this step is gated on, not a collection/import error --
every production symbol this file imports already exists today.

**Position coverage mirrors the sibling adapter-level suite
(``tests/test_status_lanes_block.py``) exactly, not a fresh guess**: Fill's
already-landed adapter reaches all four rail positions (idle, active,
blocked, terminal); Improve's adapter reaches ``unknown`` (no evidence at all)
and active -- no single field unambiguously names a blocked position on its
six-stage rail, so the sibling suite asserts none and neither does this. Tend
is a checklist lane with no watermark at all, covered at the invariant level
(the checklist never reports ``active``; ``lint`` reflects a real lint
violation). Driving four positions for Improve here would assert a state its
own already-landed production adapter cannot produce -- an unfalsifiable red,
not a real regression net.

**The advancing-command assertion is deliberately loose on exact wording**:
the mapping from a stage's declared ``action`` (an MCP verb) to its CLI
surface is adapter-owned prose this plan does not pin word-for-word (some
verbs, e.g. ``suggestions_review``/``query``, have no CLI equivalent at all --
approving a suggestion or asking a question is dashboard/MCP-only). What is
pinned, and load-bearing, is the plan's own registry-resolution constraint:
whenever a ``knotica ...`` invocation is shown, it must resolve against the
*live* command registry -- never a stale, deprecated, or hand-invented one.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import pytest

from knotica.cli import COMMAND_NAMES
from knotica.cli.common import EXIT_NOT_CONFIGURED, EXIT_SUCCESS, UNCONFIGURED_MESSAGE
from knotica.core import process_model
from knotica.core.gapfill import suggestions_path
from knotica.core.loop_state import LoopStage, LoopState, write_loop_state
from knotica.core.records import SuggestionRecord
from knotica.core.status import gather_wiki_status
from knotica.core.transaction import VaultTransaction
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"
MEMORY_PAGE_RELPATH = f"{TOPIC}/agent-memory.md"

RAIL_LANES = ("fill", "improve", "tend")

FILL_STAGES = process_model.LANE_STAGES["fill"]
FILL_TITLES = {stage.id: stage.title for stage in FILL_STAGES}

IMPROVE_STAGES = process_model.LANE_STAGES["improve"]
IMPROVE_TITLES = {stage.id: stage.title for stage in IMPROVE_STAGES}

TEND_STAGES = process_model.LANE_STAGES["tend"]
TEND_TITLES = {stage.id: stage.title for stage in TEND_STAGES}


# ---------------------------------------------------------------------------
# Fixture helpers -- mirror tests/test_status_lanes_block.py's own builders so
# the same real production lifecycle seeds both the adapter-level and the
# CLI-level suites, rather than a second, drifting notion of "a blocked Fill
# suggestion" or "an in-flight Improve cycle".
# ---------------------------------------------------------------------------


def _suggestion_record(
    *, suggestion_id: str, status: str = "pending", **overrides: Any
) -> SuggestionRecord:
    payload: dict[str, Any] = {
        "suggestion_id": suggestion_id,
        "topic": TOPIC,
        "gap_id": f"gap-{suggestion_id}",
        "qa_id": f"golden-{suggestion_id}",
        "fault_class": "genuine_gap",
        "question": "How does speculative decoding interact with draft-model verification?",
        "reference_pages": ("speculative-decoding",),
        "rank": 1,
        "query_text": "speculative decoding draft model verification",
        "candidate": {
            "url": f"https://arxiv.org/abs/{suggestion_id}",
            "title": "Accelerating LLM Inference with Speculative Decoding",
            "snippet": "We propose...",
            "source_provider": "fake",
            "doi": None,
            "citation_count": 412,
            "schema_version": 1,
        },
        "status": status,
        "proposed_at": "2026-07-19T07:30:00Z",
        "decided_at": None,
        "decided_reason": None,
        "ingested_at": None,
        "detected_generation": 42,
    }
    payload.update(overrides)
    return SuggestionRecord(**payload)


def _seed_suggestions(store: LocalFSStore, vault: Path, records: list[SuggestionRecord]) -> None:
    path = suggestions_path(TOPIC)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed suggestions for test") as txn:
        txn.write(path, body)


def _refused_gate_outcome() -> dict[str, object]:
    return {
        "verdict": "refused",
        "scalar": 0.9201,
        "baseline_scalar": 0.9655,
        "ref": "loop/x/agentic-systems/source-a1b2c3d4",
        "reason": "regressed 3 previously-passing golden questions",
        "regressed_questions": ["q-0001", "q-0007", "q-0012"],
    }


def _merged_gate_outcome() -> dict[str, object]:
    return {
        "verdict": "merged",
        "scalar": 0.97,
        "baseline_scalar": 0.9655,
        "ref": "loop/r/abc123def456",
        "reason": None,
        "regressed_questions": None,
    }


def _lanes_block(store: LocalFSStore, vault: Path) -> dict[str, tuple[dict[str, Any], ...]]:
    """The real, already-landed ``lanes`` block for the fixture's one topic --
    the exact same read the CLI's own rail render must project onto stdout."""
    body = gather_wiki_status(store, vault, topic=TOPIC, view="summary")
    assert len(body["topics"]) == 1, f"expected exactly one topic row, got {body['topics']!r}"
    lanes: dict[str, tuple[dict[str, Any], ...]] = body["topics"][0]["lanes"]
    return lanes


def _assert_titles_and_states_are_rendered(
    stdout: str, titles: dict[str, str], stages: tuple[dict[str, Any], ...]
) -> None:
    """Every declared stage's title and its live state must both appear,
    co-located on some line -- loose on exact table/column formatting, but
    proves the render is total and state-bearing, not a title-only skeleton
    or a silently-empty position (the Done-when's own failure mode)."""
    lines = stdout.splitlines()
    for stage in stages:
        title = titles[stage["id"]]
        matching = [line for line in lines if title in line]
        assert matching, (
            f"stage {stage['id']!r} ({title!r}) does not appear anywhere on the "
            f"rendered rail:\n{stdout}"
        )
        assert any(stage["state"] in line for line in matching), (
            f"stage {stage['id']!r} ({title!r}) is rendered but its live state "
            f"({stage['state']!r}) is not shown alongside it:\n{stdout}"
        )


def _build_parser() -> argparse.ArgumentParser:
    """The exact live parser tree ``main`` builds, without dispatching --
    reused (not re-implemented) so registry-resolution assertions track the
    real registration path, per ``tests/test_cli_lanes.py``'s own helper."""
    from knotica.cli import _register_commands

    parser = argparse.ArgumentParser(prog="knotica")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    _register_commands(subparsers)
    return parser


def _lane_member_subcommands(parser: argparse.ArgumentParser, lane: str) -> set[str]:
    """The set of subcommand names actually registered under ``lane`` today."""
    top_action = next(
        action
        for action in parser._actions  # noqa: SLF001 -- argparse exposes no public equivalent
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    )
    lane_parser = top_action.choices[lane]
    lane_action = next(
        (
            action
            for action in lane_parser._actions  # noqa: SLF001
            if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
        ),
        None,
    )
    return set() if lane_action is None else set(lane_action.choices)


#: Matches a ``knotica <lane>`` invocation and, optionally, an immediately
#: following bare subcommand word (never a ``--flag``, which starts with a
#: hyphen and so cannot match this pattern's leading-letter requirement).
_KNOTICA_INVOCATION_RE = re.compile(r"\bknotica\s+([a-z][a-z0-9_-]*)(?:\s+([a-z][a-z0-9_-]*))?")


# ---------------------------------------------------------------------------
# Fill -- all four rail positions (idle, active, blocked, terminal), matching
# the landed adapter
# ---------------------------------------------------------------------------


def test_fill_rail_renders_every_stage_title_and_live_state_when_idle(
    template_vault: Path, vault_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from knotica.cli import main

    store = LocalFSStore(template_vault)
    expected = _lanes_block(store, template_vault)["fill"]

    exit_code = main(["fill", "--topic", TOPIC])
    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    assert captured.out.strip() != "", "a bare rail lane must never render silently empty"
    _assert_titles_and_states_are_rendered(captured.out, FILL_TITLES, expected)


def test_fill_rail_renders_every_stage_title_and_live_state_when_active_at_approve(
    template_vault: Path, vault_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from knotica.cli import main

    store = LocalFSStore(template_vault)
    _seed_suggestions(
        store, template_vault, [_suggestion_record(suggestion_id="s-pending", status="pending")]
    )
    expected = _lanes_block(store, template_vault)["fill"]

    exit_code = main(["fill", "--topic", TOPIC])
    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    _assert_titles_and_states_are_rendered(captured.out, FILL_TITLES, expected)


def test_fill_rail_renders_every_stage_title_and_live_state_when_blocked_at_gate(
    template_vault: Path, vault_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from knotica.cli import main

    store = LocalFSStore(template_vault)
    _seed_suggestions(
        store,
        template_vault,
        [
            _suggestion_record(
                suggestion_id="s-refused", status="approved", gate_outcome=_refused_gate_outcome()
            )
        ],
    )
    expected = _lanes_block(store, template_vault)["fill"]
    assert any(stage["state"] == "blocked" for stage in expected), (
        "fixture must actually reach a blocked position, or this test proves nothing"
    )

    exit_code = main(["fill", "--topic", TOPIC])
    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    _assert_titles_and_states_are_rendered(captured.out, FILL_TITLES, expected)


def test_fill_rail_renders_every_stage_title_and_live_state_when_terminal(
    template_vault: Path, vault_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from knotica.cli import main

    store = LocalFSStore(template_vault)
    _seed_suggestions(
        store,
        template_vault,
        [
            _suggestion_record(
                suggestion_id="s-merged", status="ingested", gate_outcome=_merged_gate_outcome()
            )
        ],
    )
    expected = _lanes_block(store, template_vault)["fill"]
    assert all(stage["state"] == "complete" for stage in expected), (
        "fixture must actually reach the terminal (every-stage-complete) position, "
        "or this test proves nothing"
    )

    exit_code = main(["fill", "--topic", TOPIC])
    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    _assert_titles_and_states_are_rendered(captured.out, FILL_TITLES, expected)


# ---------------------------------------------------------------------------
# Improve -- unknown/active only; the landed adapter names no blocked position
# on this rail, so neither does this suite.
# ---------------------------------------------------------------------------


def test_improve_rail_renders_every_stage_title_and_live_state_with_no_evidence_recorded(
    template_vault: Path, vault_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from knotica.cli import main

    store = LocalFSStore(template_vault)
    expected = _lanes_block(store, template_vault)["improve"]

    exit_code = main(["improve", "--topic", TOPIC])
    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    _assert_titles_and_states_are_rendered(captured.out, IMPROVE_TITLES, expected)


def test_improve_rail_renders_every_stage_title_and_live_state_when_active_at_observe(
    template_vault: Path, vault_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from knotica.cli import main

    store = LocalFSStore(template_vault)
    write_loop_state(store, template_vault, LoopState(topic=TOPIC, stage=LoopStage.evaluating))
    expected = _lanes_block(store, template_vault)["improve"]
    assert any(stage["state"] == "active" for stage in expected), (
        "fixture must actually reach the active-at-observe position, or this test proves nothing"
    )

    exit_code = main(["improve", "--topic", TOPIC])
    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    _assert_titles_and_states_are_rendered(captured.out, IMPROVE_TITLES, expected)


# ---------------------------------------------------------------------------
# Tend -- a checklist lane, covered at the invariant level (no watermark).
# ---------------------------------------------------------------------------


def test_tend_rail_renders_every_stage_title_and_live_state_on_a_clean_topic(
    template_vault: Path, vault_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from knotica.cli import main

    store = LocalFSStore(template_vault)
    expected = _lanes_block(store, template_vault)["tend"]

    exit_code = main(["tend", "--topic", TOPIC])
    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    _assert_titles_and_states_are_rendered(captured.out, TEND_TITLES, expected)


def test_tend_rail_renders_every_stage_title_and_live_state_with_a_real_lint_violation(
    template_vault: Path, vault_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from knotica.cli import main

    memory_page = template_vault / MEMORY_PAGE_RELPATH
    memory_page.write_text(memory_page.read_text() + "\n\nSee [[does-not-exist-xyz]] for more.\n")
    store = LocalFSStore(template_vault)
    expected = _lanes_block(store, template_vault)["tend"]
    assert any(stage["state"] == "blocked" for stage in expected), (
        "fixture must actually introduce a real lint violation, or this test proves nothing"
    )

    exit_code = main(["tend", "--topic", TOPIC])
    captured = capsys.readouterr()

    assert exit_code == EXIT_SUCCESS
    _assert_titles_and_states_are_rendered(captured.out, TEND_TITLES, expected)


# ---------------------------------------------------------------------------
# Unconfigured degrades through the shared helper -- never EXIT_MISUSE.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lane", RAIL_LANES)
def test_bare_rail_lane_degrades_through_the_shared_unconfigured_path(
    lane: str, unconfigured_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from knotica.cli import main

    exit_code = main([lane, "--topic", TOPIC])
    captured = capsys.readouterr()

    assert exit_code == EXIT_NOT_CONFIGURED
    assert captured.out == "", "the unconfigured gate must never leak rail data onto stdout"
    assert UNCONFIGURED_MESSAGE in captured.err


# ---------------------------------------------------------------------------
# `--help`/`-h` stay on argparse's own path, unaffected by rail rendering.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lane", RAIL_LANES)
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_bare_rail_lane_help_is_unaffected_by_rail_rendering(
    lane: str, flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from knotica.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main([lane, flag])

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage:" in out, f"{lane} {flag} must still go through argparse's own help path"


# ---------------------------------------------------------------------------
# The advancing-command strings, wherever shown, resolve against the live
# registry -- never a stale, deprecated, or hand-invented invocation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lane", RAIL_LANES)
def test_advancing_commands_on_the_rail_resolve_against_the_live_cli_registry(
    lane: str, template_vault: Path, vault_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from knotica.cli import main

    exit_code = main([lane, "--topic", TOPIC])
    captured = capsys.readouterr()
    assert exit_code == EXIT_SUCCESS

    parser = _build_parser()
    invocations = _KNOTICA_INVOCATION_RE.findall(captured.out)
    assert invocations, (
        f"no 'knotica ...' invocation appears anywhere on {lane}'s rail -- "
        f"each stage must show the command that advances it:\n{captured.out}"
    )
    for named_lane, subcommand in invocations:
        assert named_lane in COMMAND_NAMES, (
            f"{named_lane!r} is not a currently-registered top-level command "
            f"(printed on {lane}'s rail)"
        )
        if subcommand:
            assert subcommand in _lane_member_subcommands(parser, named_lane), (
                f"'knotica {named_lane} {subcommand}' does not resolve against the live "
                f"registry (printed on {lane}'s rail)"
            )

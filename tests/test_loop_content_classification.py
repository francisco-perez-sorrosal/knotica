"""Which vault paths wake the autonomous loop -- and which must never.

``LoopRunner._content_changed_since`` is the gate in front of every observation,
and a ``True`` from it bills a real eval run. These tests pin both directions of
that gate per folder family with real git commits on ``template_vault``: an
unscored personal note is inert, while pages, stored sources and the evolvable
prompt substrate all still trigger. No eval ever executes -- the injected
evaluate raises if reached.

The over-broadness half matters as much as the exclusion half: an exclusion that
accidentally silenced ``.knotica/prompts/`` would stop the loop observing its own
evolvable substrate, and one applied per *commit* rather than per *path* would
silence a bundled note-plus-page ingest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.core.loop import LoopRunner
from knotica.core.vault_layout import NOTES_DIR, SOURCES_DIR
from knotica.core.vcs import VaultVcs
from support.vault import run_git

TOPIC = "agentic-systems"

NOTE_PATH = f"{NOTES_DIR}/{TOPIC}/reflection.md"
PAGE_PATH = f"{TOPIC}/freshly-ingested.md"
SOURCE_PATH = f"{SOURCES_DIR}/{TOPIC}/newpaper2026.md"
PROMPT_PATH = ".knotica/prompts/query.md"


def _unreachable_evaluate(*_args, **_kwargs):
    raise AssertionError(
        "evaluate must not be called -- these tests exercise the change classifier only, "
        "never a real observation cycle"
    )


def _runner(vault: Path) -> LoopRunner:
    return LoopRunner(vault, TOPIC, evaluate=_unreachable_evaluate, arena_enabled=False)


def _commit_writes(vault: Path, bodies: dict[str, str]) -> str:
    """Land every vault-relative path in ``bodies`` in one default-branch commit."""
    vcs = VaultVcs(vault)
    vcs.checkout_branch(vcs.default_branch())
    for rel_path, body in bodies.items():
        target = vault / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: write {', '.join(bodies)}")
    return vcs.head_sha()


def test_a_personal_note_write_does_not_wake_the_loop(template_vault: Path) -> None:
    """Hand-authoring a note in Obsidian must not bill an eval run.

    Notes are an unscored folder family: nothing they hold can move the eval
    scalar, so an observation triggered by one is pure cost.
    """
    vcs = VaultVcs(template_vault)
    before = vcs.head_sha()

    after = _commit_writes(template_vault, {NOTE_PATH: "# reflection\n\nA private thought.\n"})

    assert _runner(template_vault)._content_changed_since(before, after) is False, (
        "a write under notes/ must classify as unscored -- writing a personal note must "
        "never wake the loop and bill an eval run"
    )


@pytest.mark.parametrize(
    ("rel_path", "why"),
    [
        (PAGE_PATH, "a new KB page is scored content"),
        (SOURCE_PATH, "a stored source is scored content"),
        (PROMPT_PATH, "prompts are the loop's own evolvable substrate"),
    ],
)
def test_scored_and_evolvable_paths_still_wake_the_loop(
    template_vault: Path, rel_path: str, why: str
) -> None:
    vcs = VaultVcs(template_vault)
    before = vcs.head_sha()

    after = _commit_writes(template_vault, {rel_path: f"# {rel_path}\n\nbody\n"})

    assert _runner(template_vault)._content_changed_since(before, after) is True, (
        f"{rel_path} must still trigger a fresh observation: {why}"
    )


def test_a_note_bundled_with_a_page_still_wakes_the_loop(template_vault: Path) -> None:
    """The exclusion is per-path, not per-commit: one scored path is enough."""
    vcs = VaultVcs(template_vault)
    before = vcs.head_sha()

    after = _commit_writes(
        template_vault,
        {NOTE_PATH: "# reflection\n\nA private thought.\n", PAGE_PATH: "# page\n\nbody\n"},
    )

    assert _runner(template_vault)._content_changed_since(before, after) is True, (
        "a commit touching both an unscored note and a scored page must still be observed -- "
        "the note must not silence its companion"
    )

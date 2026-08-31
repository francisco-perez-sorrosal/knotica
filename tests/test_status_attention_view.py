"""Budget fitness tests for ``wiki_status view="attention"`` (dec-092).

``attention`` is Home's cross-topic inbox read and must be genuinely cheaper
than ``view="summary"`` -- not "summary minus drift". Three hard budget rules
from dec-092 are asserted here as real, regression-catching assertions rather
than comments:

* **No mechanical lint walk** -- ``lint_vault`` must never be called.
* **No note-anchor resolution** -- no ``git show`` subprocess is ever spawned
  to resolve a historical anchor.
* **A small constant number of git subprocesses for the whole vault** -- the
  count must not grow as the vault gains topics.

Plus the liveness regression this view exists to fix: cross-topic runner
liveness must not inherit ``_gate_and_loop``'s multi-topic stub, which
reports every topic dead unconditionally once more than one topic is in
scope.

Tests call ``gather_wiki_status`` directly (not through the MCP wire) --
these are internal call-graph budget assertions, not wire-contract tests, so
the core function is the right granularity (a wire-level contract test for
``view="attention"`` belongs with the rest of ``wiki_status``'s shape tests).
Production imports are not deferred: ``gather_wiki_status`` already exists
today: calling it with ``view="attention"`` currently raises a ``KnoticaError``
(``"attention"`` is not yet in ``VALID_STATUS_VIEWS``) -- that raised error,
surfacing as an uncaught exception in every test below, is this file's RED
signal, not a collection failure.

Load-bearing assumption about the not-yet-landed payload shape:
``body["topics"]`` is a list
of per-topic dicts, each carrying ``"topic"`` and a ``"runner"`` sub-object
shaped like the existing single-topic ``loop["runner"]`` (``{"alive", "pid",
"beat_at", "interval_seconds"}``). The paired implementation wins on conflict.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from knotica.core import status as status_module
from knotica.core.arena import ArenaStage, ArenaState, write_arena_state
from knotica.core.gapfill import report_gap
from knotica.core.loop_heartbeat import write_heartbeat
from knotica.core.operations.capture_note import capture_note
from knotica.core.operations.create_topic import create_topic
from knotica.core.status import gather_wiki_status
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"
SECOND_TOPIC = "robotics"
THIRD_TOPIC = "materials-science"
FOURTH_TOPIC = "climate-modeling"
FIFTH_TOPIC = "genomics"
SIXTH_TOPIC = "astrophysics"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _create_topic(store: LocalFSStore, vault: Path, topic: str) -> None:
    result = create_topic(store, vault, topic)
    assert "error" not in result, f"fixture setup failed to create topic {topic!r}: {result!r}"


def _seed_multi_topic_vault(vault: Path) -> LocalFSStore:
    """A vault with three topics.

    ``view="attention"``'s own contract is "for every topic in the active
    vault" -- a single-topic fixture could never exercise the cross-topic
    aggregation this view exists for.
    """
    store = LocalFSStore(vault)
    _create_topic(store, vault, SECOND_TOPIC)
    _create_topic(store, vault, THIRD_TOPIC)
    return store


def _seed_anchored_note(
    vault: Path, store: LocalFSStore, *, topic: str, page: str, quote: str
) -> None:
    """A real, git-resolvable anchor, captured through the actual operation
    so its ``pinned_at`` sha is genuine -- mirrors the notes suite's own
    fixture precedent (``tests/core/notes/test_reanchor_note.py::_seed_captured_note``).
    """
    target = vault / page
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# Seed page\n\n{quote}.\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: seed {page}")
    result = capture_note(
        store,
        vault,
        VaultVcs(vault),
        topic,
        "a reflection worth revisiting",
        quote=quote,
        pages=[page],
    )
    assert "error" not in result, f"fixture setup failed to capture a note: {result!r}"


def _install_git_subprocess_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the git subcommand (first argument) of every ``VaultVcs`` call.

    Patches the single subprocess seam ``VaultVcs._run`` itself, not a
    higher-level wrapper -- so a call reached through any intermediate glue
    (``list_notes`` -> ``resolve_anchor`` -> ``read_file_at``, or a future
    helper the attention view's implementation introduces) is caught, not
    just a direct call from ``core/status.py``.
    """
    calls: list[str] = []
    original = VaultVcs._run

    def _spy(self: VaultVcs, arguments: Sequence[str], **kwargs: Any) -> Any:
        calls.append(arguments[0] if arguments else "")
        return original(self, arguments, **kwargs)

    monkeypatch.setattr(VaultVcs, "_run", _spy)
    return calls


def _install_lint_vault_call_counter(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record one entry per call to ``lint_vault`` as ``core/status.py`` sees it.

    Patched on ``knotica.core.status``'s own module namespace -- the name
    Python actually resolves when any function in that module calls
    ``lint_vault(...)`` -- so any current or future helper inside the
    attention view's implementation is caught, not just today's
    ``_lint_counts_by_topic``.
    """
    calls: list[int] = []
    original = status_module.lint_vault

    def _spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(status_module, "lint_vault", _spy)
    return calls


# ---------------------------------------------------------------------------
# No mechanical lint walk
# ---------------------------------------------------------------------------


def test_attention_view_never_calls_the_mechanical_lint_walk(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping only anchor resolution and keeping the lint walk would leave
    ``attention`` no cheaper than ``view="summary"``, which already pays that
    cost unconditionally on every call."""
    store = _seed_multi_topic_vault(template_vault)
    lint_calls = _install_lint_vault_call_counter(monkeypatch)

    gather_wiki_status(store, template_vault, view="attention")

    assert lint_calls == [], (
        f"view='attention' must make zero lint_vault calls, made {len(lint_calls)}"
    )

    # Non-vacuity: the same fixture, same store, DOES run the lint walk under
    # `view="summary"` -- proving the assertion above would actually catch a
    # regression rather than passing because nothing here could ever call it.
    lint_calls.clear()
    gather_wiki_status(store, template_vault, view="summary")
    assert lint_calls, "sanity check failed: view='summary' is expected to run the lint walk"


# ---------------------------------------------------------------------------
# No note-anchor resolution
# ---------------------------------------------------------------------------


def test_attention_view_never_resolves_a_note_anchor_via_git_show(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drift row is default-collapsed and pays its cost only on
    expansion -- ``attention`` must reach zero ``git show`` subprocesses (the
    anchor-resolution boundary) regardless of how many notes exist."""
    store = _seed_multi_topic_vault(template_vault)
    quote = "a passage the attention view must never resolve"
    _seed_anchored_note(
        template_vault,
        store,
        topic=TOPIC,
        page=f"{TOPIC}/attention-fitness-seed.md",
        quote=quote,
    )
    git_calls = _install_git_subprocess_spy(monkeypatch)

    gather_wiki_status(store, template_vault, view="attention")

    assert "show" not in git_calls, (
        "view='attention' must never spawn `git show` to resolve a note anchor, "
        f"recorded git subcommands: {git_calls!r}"
    )

    # Non-vacuity: the same anchored note DOES require a `git show` under
    # `view="summary"` -- proving the fixture can actually trigger the call
    # this test forbids.
    git_calls.clear()
    gather_wiki_status(store, template_vault, view="summary")
    assert "show" in git_calls, (
        "sanity check failed: view='summary' is expected to resolve the anchor via git show"
    )


# ---------------------------------------------------------------------------
# A small constant number of git subprocesses for the whole vault
# ---------------------------------------------------------------------------


def test_attention_view_git_subprocess_count_does_not_grow_with_topic_count(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-topic git read would make Home's cost scale with vault size --
    exactly the O(N) budget breach the pre-mortem calls out. The claimed
    property is "vault-wide", i.e. constant regardless of topic count, so the
    count at 3 topics and at 6 topics must be identical -- whatever the
    constant turns out to be."""
    store = _seed_multi_topic_vault(template_vault)  # TOPIC + 2 = 3 topics
    git_calls = _install_git_subprocess_spy(monkeypatch)

    git_calls.clear()
    gather_wiki_status(store, template_vault, view="attention")
    count_at_three_topics = len(git_calls)

    for name in (FOURTH_TOPIC, FIFTH_TOPIC, SIXTH_TOPIC):
        _create_topic(store, template_vault, name)

    git_calls.clear()
    gather_wiki_status(store, template_vault, view="attention")
    count_at_six_topics = len(git_calls)

    assert count_at_six_topics == count_at_three_topics, (
        "git subprocess count must stay constant as the vault grows: "
        f"{count_at_three_topics} at 3 topics vs {count_at_six_topics} at 6 topics"
    )


# ---------------------------------------------------------------------------
# Real cross-topic runner liveness, not the multi-topic stub
# ---------------------------------------------------------------------------


def test_attention_view_reports_real_liveness_for_a_topic_with_a_fresh_heartbeat(
    template_vault: Path,
) -> None:
    """``_gate_and_loop``'s multi-topic stub reports ``alive: False``
    unconditionally once more than one topic is in scope -- a wrong answer
    being worse than an absent one. ``attention`` must read the service
    layer's all-watched-topics projection instead."""
    store = _seed_multi_topic_vault(template_vault)
    write_heartbeat(template_vault, SECOND_TOPIC, interval_seconds=2.0)

    body = gather_wiki_status(store, template_vault, view="attention")

    rows = {row["topic"]: row for row in body["topics"]}
    assert rows[SECOND_TOPIC]["runner"]["alive"] is True, (
        "a topic with a fresh heartbeat must report alive -- the stubbed "
        "`_gate_and_loop` path reports every topic dead once the scope "
        "covers more than one topic"
    )
    assert rows[TOPIC]["runner"]["alive"] is False, (
        "a topic with no heartbeat file must report dead, ruling out a hardcoded-True regression"
    )


# ---------------------------------------------------------------------------
# Covers every topic, ignoring the inherited `topic` argument
# ---------------------------------------------------------------------------


def test_attention_view_covers_every_topic_regardless_of_the_topic_argument(
    template_vault: Path,
) -> None:
    """``attention`` inherits ``gather_wiki_status``'s single-``topic``
    signature from the other views, but the contract requires it to ignore
    that argument and always report on every topic in the vault."""
    store = _seed_multi_topic_vault(template_vault)

    scoped = gather_wiki_status(store, template_vault, topic=TOPIC, view="attention")
    unscoped = gather_wiki_status(store, template_vault, view="attention")

    assert scoped == unscoped
    reported = {row["topic"] for row in unscoped["topics"]}
    assert reported == {TOPIC, SECOND_TOPIC, THIRD_TOPIC}


def test_attention_view_on_an_empty_vault_returns_a_valid_empty_topics_list(
    template_vault: Path,
) -> None:
    """The degenerate case of "every topic": a vault with none must return a
    valid, empty envelope rather than raising."""
    store = LocalFSStore(template_vault)
    shutil.rmtree(template_vault / TOPIC)
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: remove the only topic")

    body = gather_wiki_status(store, template_vault, view="attention")

    assert body["topics"] == []


# ---------------------------------------------------------------------------
# The two Surface holes: open gaps nobody discovered against, and an aborted race
# ---------------------------------------------------------------------------


def _row_for(body: dict[str, Any], topic: str) -> dict[str, Any]:
    return next(row for row in body["topics"] if row["topic"] == topic)


def test_attention_row_reports_open_gaps_so_an_undiscovered_queue_can_surface(
    template_vault: Path,
) -> None:
    """A topic with open gaps and no suggestions tripped none of the four
    original signals, so Home reported "nothing needs you" while the gap queue
    sat untouched. The row now carries the two numbers that tell those apart:
    gaps are open, and nothing has ever been proposed for them."""
    store = LocalFSStore(template_vault)
    report_gap(
        store,
        template_vault,
        TOPIC,
        question="what is the retrieval story for long documents?",
    )

    row = _row_for(gather_wiki_status(store, template_vault, view="attention"), TOPIC)

    assert row["gaps"]["open_total"] == 1
    # The conservative predicate the client derives on: zero suggestions ever,
    # not merely zero pending. A topic mid-pipeline must not trip it.
    assert row["suggestions"]["total"] == 0


def test_attention_row_reports_zero_open_gaps_for_a_topic_with_none(
    template_vault: Path,
) -> None:
    """Honest zeros, not an absent key -- a client that has to distinguish
    "no gaps" from "field missing" has been handed the server's problem."""
    store = LocalFSStore(template_vault)

    row = _row_for(gather_wiki_status(store, template_vault, view="attention"), TOPIC)

    assert row["gaps"]["open_total"] == 0
    assert row["suggestions"]["total"] == 0
    assert row["gaps"]["answered_in_vault"] == 0


def test_attention_row_counts_the_open_gaps_the_vault_already_answers(
    template_vault: Path,
) -> None:
    """A gap a drain found every candidate for already stored is not waiting on
    acquisition -- it is waiting on retrieval or linking. The drain stamps that
    on the record so this view reports it as a plain count, paying no discovery
    cost of its own."""
    store = LocalFSStore(template_vault)
    report_gap(
        store,
        template_vault,
        TOPIC,
        question="what is the retrieval story for long documents?",
    )
    _stamp_every_open_gap(store, template_vault, "2026-08-30T12:00:00Z")

    row = _row_for(gather_wiki_status(store, template_vault, view="attention"), TOPIC)

    assert row["gaps"]["open_total"] == 1
    assert row["gaps"]["answered_in_vault"] == 1


def _stamp_every_open_gap(store: LocalFSStore, vault: Path, stamp: str) -> None:
    """Set ``answered_in_vault_at`` on the topic's gap records (drain-free)."""
    from dataclasses import replace

    from knotica.core.gap_classifier import gaps_path
    from knotica.core.records import parse_gaps_jsonl
    from knotica.core.transaction import VaultTransaction

    path = gaps_path(TOPIC)
    stamped = [
        replace(gap, answered_in_vault_at=stamp) for gap in parse_gaps_jsonl(store.read_text(path))
    ]
    with VaultTransaction(store, vault, "test_seed", TOPIC, "stamp gaps") as txn:
        txn.write(path, "".join(gap.to_json_line() + "\n" for gap in stamped))


def test_attention_row_reports_the_arena_stage_when_a_race_was_refused(
    template_vault: Path,
) -> None:
    """``aborted`` means refused before scoring: the arena scorer and the gate
    baseline are not the same instrument. It is a stopped pipeline needing a
    human config decision, and until now it was visible only to someone already
    standing in Improve -> Heal on that topic."""
    store = LocalFSStore(template_vault)
    write_arena_state(
        store,
        template_vault,
        ArenaState(topic=TOPIC, race_id="race-aborted", stage=ArenaStage.aborted),
        title="seed an aborted race",
    )

    row = _row_for(gather_wiki_status(store, template_vault, view="attention"), TOPIC)

    assert row["arena"]["stage"] == "aborted"


def test_attention_row_reports_a_non_aborted_arena_stage_verbatim(
    template_vault: Path,
) -> None:
    """The server returns the stage word and nothing else -- deciding that
    ``reverted`` ("raced and nobody won") is normal while ``aborted`` needs a
    human is the client's call, exactly as every other attention row is
    derived client-side."""
    store = LocalFSStore(template_vault)
    write_arena_state(
        store,
        template_vault,
        ArenaState(topic=TOPIC, race_id="race-reverted", stage=ArenaStage.reverted),
        title="seed a reverted race",
    )

    row = _row_for(gather_wiki_status(store, template_vault, view="attention"), TOPIC)

    assert row["arena"]["stage"] == "reverted"


def test_attention_row_reports_a_null_arena_stage_when_no_race_was_ever_recorded(
    template_vault: Path,
) -> None:
    """ "No race we can speak for" is null, never a guessed stage -- the same
    ruling the view already applies to runner liveness: a wrong answer is worse
    than an absent one."""
    store = LocalFSStore(template_vault)

    row = _row_for(gather_wiki_status(store, template_vault, view="attention"), TOPIC)

    assert row["arena"]["stage"] is None


def test_attention_row_carries_both_new_fields_for_every_topic_in_the_vault(
    template_vault: Path,
) -> None:
    """The view's contract is per-topic, so a field present on one row and
    absent on another is a client-side crash waiting for the second topic."""
    store = _seed_multi_topic_vault(template_vault)

    body = gather_wiki_status(store, template_vault, view="attention")

    for row in body["topics"]:
        assert "open_total" in row["gaps"], row["topic"]
        assert "answered_in_vault" in row["gaps"], row["topic"]
        assert "total" in row["suggestions"], row["topic"]
        assert "stage" in row["arena"], row["topic"]

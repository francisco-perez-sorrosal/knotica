"""Attempt identity and the attempt clock — the unit half of the no-op-write fix.

:mod:`knotica.core.loop_attempt` answers two questions the loop asks on every
observation: *would recording this attempt tell anyone anything?* and *how long
ago did the last attempt actually start?* The end-to-end commit-cost contract
lives in ``test_loop_noop_attempt_characterization.py``; these tests pin the
decision logic underneath it, and in particular the two properties the whole fix
rests on:

- the identity is a **deny-list** -- every ``LoopState`` field is information
  unless it is explicitly a timestamp, so a field added later participates
  automatically (``test_the_perturbation_table_covers_every_loop_state_field``
  fails loudly when someone adds one and does not decide which it is);
- the clock advances on **suppressed** attempts too, or the retry floor would
  release on every tick and the fix would trade commit spam for eval spam.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from knotica.core.loop_attempt import (
    is_same_content_retry,
    note_attempt,
    records_nothing_new,
    retry_hold,
)
from knotica.core.loop_retry_backoff import (
    BLOCKED_RETRY_FLOOR_SECONDS,
    FAILURE_RETRY_FLOOR_SECONDS,
)
from knotica.core.loop_state import LoopDecision, LoopStage, LoopState

TOPIC = "agentic-systems"
NOW = datetime(2026, 8, 4, 12, 0, 0)

#: Fields that time-stamp an attempt rather than describe one, plus the head
#: anchor that is compared by content instead. Excluded from the identity by
#: design -- the module docstring says why.
_TIMING_FIELDS = frozenset({"updated_at", "last_eval_started_at", "candidate_sha"})

#: Fields no attempt can vary (``Literal[1]``), so they carry no signal either way.
_IMMUTABLE_FIELDS = frozenset({"schema_version"})

#: One perturbed value per *informative* field. Every entry must break
#: suppression; the coverage test below keeps the table honest as the model grows.
_PERTURBATIONS: dict[str, Any] = {
    "topic": "another-topic",
    "stage": LoopStage.racing,
    "baseline_policy": "best",
    "baseline_scalar": 0.9,
    "baseline_harness_version": "h2",
    "baseline_corpus_ref": "git:deadbeef",
    # Information, not timing: it identifies which questions the baseline was
    # measured on, so a change to it changes what the recorded state means.
    "baseline_golden_manifest_sha": "222e2eb707b9da30",
    "candidate_branch": "loop/c/other",
    "last_scalar": 0.42,
    "last_generation": 7,
    "last_harness_version": "h3",
    "last_decision": LoopDecision.pass_,
    "last_error": "a different boom",
    "cursors": {"main": "abc123"},
    "pending_retry": False,
    "last_failure_retryable": False,
}


def _failed_state(**overrides: Any) -> LoopState:
    """A plausible recorded-failure state, with per-test overrides applied."""
    base = LoopState(
        topic=TOPIC,
        stage=LoopStage.failed,
        baseline_scalar=0.55,
        baseline_harness_version="h1",
        candidate_branch="main",
        candidate_sha="a" * 40,
        last_decision=LoopDecision.fail,
        last_error="boom",
        pending_retry=True,
        last_failure_retryable=True,
        last_eval_started_at=NOW,
    )
    return base.model_copy(update=overrides) if overrides else base


def test_the_perturbation_table_covers_every_loop_state_field() -> None:
    """The deny-list guard: a new ``LoopState`` field must be classified, not ignored.

    Adding a field without deciding whether it times an attempt or describes one
    fails here rather than silently widening what suppression hides.
    """
    classified = set(_PERTURBATIONS) | _TIMING_FIELDS | _IMMUTABLE_FIELDS
    assert classified == set(LoopState.model_fields), (
        "a LoopState field is unclassified: add it to _PERTURBATIONS if it is "
        "information, or to _TIMING_FIELDS if it merely time-stamps an attempt"
    )


@pytest.mark.parametrize("field", sorted(_PERTURBATIONS))
def test_a_change_to_any_informative_field_is_recorded(field: str) -> None:
    stored = _failed_state()
    attempt = _failed_state(**{field: _PERTURBATIONS[field]})

    assert records_nothing_new(stored, attempt, same_content=True) is False, (
        f"{field!r} describes the situation, so a change to it is news"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("updated_at", NOW + timedelta(hours=3)),
        ("last_eval_started_at", NOW + timedelta(hours=3)),
        ("candidate_sha", "b" * 40),
    ],
)
def test_a_change_to_only_a_timing_field_is_not_recorded(field: str, value: Any) -> None:
    stored = _failed_state()
    attempt = _failed_state(**{field: value})

    assert records_nothing_new(stored, attempt, same_content=True) is True


def test_differing_content_is_always_recorded_however_alike_the_states() -> None:
    """Content is rule 1: two byte-identical verdicts about different pages are two verdicts."""
    stored = _failed_state()

    assert records_nothing_new(stored, _failed_state(), same_content=False) is False


def test_a_fresh_state_is_never_a_same_content_retry() -> None:
    state = LoopState(topic=TOPIC)

    assert is_same_content_retry(state, "c" * 40, content_changed=_never_changed) is False


def test_a_pending_failure_without_a_recorded_head_is_not_a_retry() -> None:
    """``pending_retry`` alone cannot anchor a comparison — the head is the anchor."""
    state = _failed_state(candidate_sha=None)

    assert is_same_content_retry(state, "c" * 40, content_changed=_never_changed) is False


def test_a_pending_failure_on_unchanged_content_is_a_retry() -> None:
    assert is_same_content_retry(_failed_state(), "c" * 40, content_changed=_never_changed) is True


def test_a_pending_failure_on_changed_content_is_not_a_retry() -> None:
    assert (
        is_same_content_retry(_failed_state(), "c" * 40, content_changed=_always_changed) is False
    )


def _never_changed(base: str, head: str) -> bool:
    return False


def _always_changed(base: str, head: str) -> bool:
    return True


def test_a_non_retry_is_never_held(tmp_path: Path) -> None:
    assert retry_hold(tmp_path, TOPIC, _failed_state(), same_content_retry=False, now=NOW) is None


def test_a_retry_inside_the_floor_is_held_by_the_attempt_marker(tmp_path: Path) -> None:
    note_attempt(tmp_path, TOPIC, at=NOW)

    held = retry_hold(
        tmp_path,
        TOPIC,
        _failed_state(),
        same_content_retry=True,
        now=NOW + timedelta(seconds=FAILURE_RETRY_FLOOR_SECONDS - 1),
    )

    assert held is not None
    assert "failure retry held" in held


def test_a_retry_past_the_floor_is_released(tmp_path: Path) -> None:
    note_attempt(tmp_path, TOPIC, at=NOW)

    released = retry_hold(
        tmp_path,
        TOPIC,
        _failed_state(),
        same_content_retry=True,
        now=NOW + timedelta(seconds=FAILURE_RETRY_FLOOR_SECONDS + 1),
    )

    assert released is None


def test_a_blocked_failure_is_held_far_past_the_transient_floor(tmp_path: Path) -> None:
    note_attempt(tmp_path, TOPIC, at=NOW)

    held = retry_hold(
        tmp_path,
        TOPIC,
        _failed_state(last_failure_retryable=False),
        same_content_retry=True,
        now=NOW + timedelta(seconds=FAILURE_RETRY_FLOOR_SECONDS + 1),
    )

    assert held is not None
    assert "blocked retry held" in held
    assert str(BLOCKED_RETRY_FLOOR_SECONDS) in held


def test_a_later_attempt_re_arms_the_floor_even_though_nothing_was_committed(
    tmp_path: Path,
) -> None:
    """The eval-spam guard, in miniature.

    A suppressed attempt writes no state, so ``last_eval_started_at`` stays put.
    If the floor read only that, it would release on every subsequent tick and
    the fix would swap commit spam for far costlier eval spam. The marker is what
    makes the second hold happen.
    """
    state = _failed_state()
    note_attempt(tmp_path, TOPIC, at=NOW)
    retry_start = NOW + timedelta(seconds=FAILURE_RETRY_FLOOR_SECONDS + 1)
    assert retry_hold(tmp_path, TOPIC, state, same_content_retry=True, now=retry_start) is None

    note_attempt(tmp_path, TOPIC, at=retry_start)
    just_after = retry_start + timedelta(seconds=1)

    assert (
        retry_hold(tmp_path, TOPIC, state, same_content_retry=True, now=just_after) is not None
    ), "the second attempt must re-arm the floor from its own start, not from the last write"


def test_the_persisted_state_paces_the_retry_when_no_marker_exists(tmp_path: Path) -> None:
    """A fresh machine, or a cleared ``.knotica/locks/``, must still be paced."""
    held = retry_hold(
        tmp_path,
        TOPIC,
        _failed_state(),
        same_content_retry=True,
        now=NOW + timedelta(seconds=1),
    )

    assert held is not None


def test_no_clock_at_all_cannot_hold(tmp_path: Path) -> None:
    """Never invent a floor: with nothing to measure from, the retry proceeds."""
    state = _failed_state(last_eval_started_at=None)

    assert retry_hold(tmp_path, TOPIC, state, same_content_retry=True, now=NOW) is None


def test_an_incompatible_marker_clock_defers_to_the_persisted_state(tmp_path: Path) -> None:
    """Naive and aware datetimes cannot be subtracted; the loop's own value wins."""
    note_attempt(tmp_path, TOPIC, at=datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC))

    held = retry_hold(
        tmp_path,
        TOPIC,
        _failed_state(),
        same_content_retry=True,
        now=NOW + timedelta(seconds=1),
    )

    assert held is not None, "the naive persisted timestamp must still pace the retry"


def test_the_attempt_marker_is_runtime_state_not_vault_content(tmp_path: Path) -> None:
    """It lives with the heartbeat under the gitignored runtime directory."""
    note_attempt(tmp_path, TOPIC, at=NOW)

    marker = tmp_path / ".knotica" / "locks" / f"loop-attempt-{TOPIC}.json"
    assert marker.is_file()
    assert not list((tmp_path / ".knotica" / "locks").glob("*.tmp")), "no temp file left behind"

"""A race must be interpretable, and must refuse a comparison it cannot make.

Race ``18c3899b843e`` reverted four prompt variants for failing to clear a
0.9548 baseline. Two seconds earlier the topic's own corpus had scored 0.6562
on the same golden set, so every discarded variant in fact beat the live corpus
by 0.13-0.16.

The four scalars were 0.79, 0.80, 0.81, 0.82 -- exact 0.01 increments, on a
topic whose real evals produce values like 0.6562261904761905. That spacing is
:func:`heuristic_arena_score`'s signature, not a measurement: ``0.40 + 0.28 +
0.06 + 0.04 = 0.78``, then ``+0.01 x index``. The race was scored by a keyword
heuristic and judged against an eval-derived bar, and nothing on the record
said so -- ``reverted`` is also what a fair race nobody won looks like.

So: provenance on every race, and an abort instead of a revert when the two
instruments cannot be compared.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.core.arena import (
    EVAL_SCORER_ID,
    HEURISTIC_SCORER,
    HEURISTIC_SCORER_ID,
    ArenaStage,
    ScorerInfo,
    VariantSpec,
    append_arena_history,
    generate_variant_bodies,
    heuristic_arena_score,
    incomparable_reason,
    race_variants,
    read_arena_history,
)
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"

_EVAL_SCORER = ScorerInfo(
    id=EVAL_SCORER_ID,
    comparable_to_eval=True,
    n_examples=21,
    golden_manifest_sha="222e2eb707b9da30",
)


def _variants(n: int = 4) -> list[VariantSpec]:
    return [
        VariantSpec(id=f"v{i + 1}", label=f"variant-{i + 1}", body=f"body {i}") for i in range(n)
    ]


def _race(vault: Path, **kwargs):
    return race_variants(
        LocalFSStore(vault),
        vault,
        TOPIC,
        _variants(),
        score=lambda *_a: 0.90,
        promote_on_win=False,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# The stub is real: the reported scalars are reproducible from the heuristic
# ---------------------------------------------------------------------------


def test_the_reported_variant_scalars_are_reproducible_from_the_keyword_heuristic() -> None:
    """0.79/0.80/0.81/0.82 came from string matching, not from any model.

    Pins the diagnosis itself. If this ever stops holding, the interpretation
    the rest of this file rests on is no longer the right one.
    """
    base = (
        "Answer the question. Citation discipline is mandatory. "
        "Prefer shorter answers and keep citation discipline strict. Do not invent sources."
    )
    scored = [
        heuristic_arena_score(TOPIC, Path("/nonexistent"), spec.body)
        for spec in generate_variant_bodies(base, n=4)
    ]

    assert scored == [
        pytest.approx(0.79),
        pytest.approx(0.80),
        pytest.approx(0.81),
        pytest.approx(0.82),
    ]


# ---------------------------------------------------------------------------
# The comparability guard
# ---------------------------------------------------------------------------


def test_a_heuristic_scorer_is_never_comparable_to_an_eval_baseline() -> None:
    reason = incomparable_reason(HEURISTIC_SCORER, "222e2eb707b9da30")

    assert reason is not None
    assert HEURISTIC_SCORER_ID in reason


def test_an_eval_scorer_on_the_baselines_golden_set_is_comparable() -> None:
    assert incomparable_reason(_EVAL_SCORER, "222e2eb707b9da30") is None


def test_a_different_golden_set_is_not_comparable() -> None:
    """A 9-question bar cannot bound a 21-question race."""
    reason = incomparable_reason(_EVAL_SCORER, "465ad26000000000")

    assert reason is not None
    assert "465ad2600000" in reason and "222e2eb707b9" in reason


def test_unrecorded_provenance_races_rather_than_blocking() -> None:
    """Deliberately unlike the gate-verdict cache: the costs are not symmetric.

    Refusing here would disable self-healing for every baseline frozen before
    the golden-set digest was recorded at all.
    """
    assert incomparable_reason(ScorerInfo(id="x", comparable_to_eval=True), None) is None


# ---------------------------------------------------------------------------
# race_variants: abort, not revert
# ---------------------------------------------------------------------------


def test_an_incomparable_race_aborts_without_scoring_anything(template_vault: Path) -> None:
    """The reported failure, inverted: nothing is measured and nothing is thrown away."""
    scored: list[str] = []

    state = race_variants(
        LocalFSStore(template_vault),
        template_vault,
        TOPIC,
        _variants(),
        baseline_scalar=0.9548,
        score=lambda _t, _r, body: scored.append(body) or 0.82,  # type: ignore[func-returns-value]
        scorer=HEURISTIC_SCORER,
        baseline_golden_manifest_sha="222e2eb707b9da30",
        promote_on_win=False,
    )

    assert state.stage == ArenaStage.aborted
    assert scored == [], "an unwinnable race must cost nothing"
    assert state.winner_id is None
    assert all(v.status == "pending" for v in state.variants), (
        "variants that were never measured must not be marked lost"
    )
    assert HEURISTIC_SCORER_ID in (state.message or "")


def test_a_comparable_race_still_reverts_normally_when_no_variant_wins(
    template_vault: Path,
) -> None:
    """Abort must not swallow the legitimate revert -- they are different outcomes."""
    state = _race(
        template_vault,
        baseline_scalar=0.99,
        scorer=_EVAL_SCORER,
        baseline_golden_manifest_sha=_EVAL_SCORER.golden_manifest_sha,
    )

    assert state.stage == ArenaStage.reverted
    assert state.winner_id is None


# ---------------------------------------------------------------------------
# Provenance on the record
# ---------------------------------------------------------------------------


def test_a_race_records_its_scorer_examples_and_golden_set(template_vault: Path) -> None:
    state = _race(
        template_vault,
        baseline_scalar=0.10,
        scorer=_EVAL_SCORER,
        baseline_golden_manifest_sha=_EVAL_SCORER.golden_manifest_sha,
    )

    assert state.scorer_id == EVAL_SCORER_ID
    assert state.n_examples == 21
    assert state.golden_manifest_sha == "222e2eb707b9da30"
    assert state.provenance_unverified is False


def test_every_variant_carries_its_own_provenance(template_vault: Path) -> None:
    """A history row has to stay readable on its own, without the race around it."""
    state = _race(
        template_vault,
        baseline_scalar=0.10,
        scorer=_EVAL_SCORER,
        baseline_golden_manifest_sha=_EVAL_SCORER.golden_manifest_sha,
    )

    assert [v.scorer_id for v in state.variants] == [EVAL_SCORER_ID] * 4
    assert [v.n_examples for v in state.variants] == [21] * 4


def test_a_race_against_an_unrecorded_baseline_flags_itself_unverified(
    template_vault: Path,
) -> None:
    state = race_variants(
        LocalFSStore(template_vault),
        template_vault,
        TOPIC,
        _variants(),
        baseline_scalar=0.10,
        score=lambda *_a: 0.90,
        scorer=_EVAL_SCORER,
        baseline_golden_manifest_sha=None,
        promote_on_win=True,
    )

    assert state.stage == ArenaStage.completed, "an unrecorded baseline must not block the race"
    assert state.provenance_unverified is True, "but the race must say it could not be verified"


def test_history_rows_predating_provenance_are_reported_unverified(
    template_vault: Path,
) -> None:
    """Race 18c3899b843e is on record and cannot be re-interpreted after the fact."""
    store = LocalFSStore(template_vault)
    append_arena_history(
        store,
        template_vault,
        TOPIC,
        {"race_id": "18c3899b843e", "stage": "reverted", "baseline_scalar": 0.9548},
    )

    rows = read_arena_history(store, TOPIC)

    assert rows[-1]["unverified"] is True
    assert rows[-1]["scorer_id"] is None


def test_a_stamped_history_row_is_not_marked_unverified(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    _race(
        template_vault,
        baseline_scalar=0.10,
        scorer=_EVAL_SCORER,
        baseline_golden_manifest_sha=_EVAL_SCORER.golden_manifest_sha,
    )

    rows = read_arena_history(store, TOPIC)

    assert rows[-1]["unverified"] is False
    assert rows[-1]["scorer_id"] == EVAL_SCORER_ID
    assert rows[-1]["n_examples"] == 21
    assert rows[-1]["golden_manifest_sha"] == "222e2eb707b9da30"

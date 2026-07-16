"""Behavioral spec for the locked v1 topic-scalar composition.

`scalar.compose` folds four signals into one dimensionless number in ``[0,1]``:
mean per-example quality, topic lint-cleanliness, and a hinged, budget-relative,
multiplicative token-cost discount. These tests are the guardrail against silent
scalar drift -- every hand-computed constant below encodes the *shape* of the
formula, so a change to any term (a dropped hinge, a missing clamp, a re-weighted
blend) turns a test red rather than shipping a quietly different objective.

The locked v1 policy under test (constants versioned by
``SCALAR_FORMULA_VERSION = 1``):

    lint_cleanliness = max(0, 1 - lint_violations / L_ref)   # L_ref = max(1, n_content_pages)
    Q                = (1 - w_lint) * quality_answers + w_lint * lint_cleanliness   # w_lint = 0.15
    cost_factor      = clamp(1 - lam * max(0, (T - T_target) / T_target), 0, 1)     # lam = 0.3
    scalar           = Q * cost_factor                        # in [0,1]

Three properties defeat cost-swamping and are each pinned below: the **hinge**
(no penalty at/under budget, no bonus for cheapness), the **clamp** (a huge
overrun bottoms out at 0, never negative), and the **multiplicative** structure
(zero composite quality yields zero scalar regardless of cost).

Every expected value is hand-computed in a named constant -- never re-derived by
calling the formula under test -- so the assertion and the implementation cannot
drift together.

--------------------------------------------------------------------------------
PINNED INTERFACE (documented negotiable -- single reconciliation point: `_compose`)

The plan fixes the four leading positional arguments
``compose(quality_answers, lint_violations, T, T_target, ...)`` and the keyword
defaults ``w_lint=0.15, lam=0.3``. The formula additionally needs
``n_content_pages`` (``L_ref = max(1, n_content_pages)``), which the plan's
one-line signature elides. It is pinned here as a **keyword argument**, the most
natural reading, and threaded through the `_compose` helper alone. If the shipped
module names it differently (e.g. ``L_ref``) or places it positionally, a loud
``TypeError`` surfaces at the integration checkpoint and `_compose` is the one
place to reconcile -- not a silent wrong value.

Written concurrently with the implementation (disjoint files); RED until the
scoring core lands, then GREEN by convergence.
--------------------------------------------------------------------------------
"""

import socket
import subprocess
import sys

import pytest

from knotica.evals.scalar import SCALAR_FORMULA_VERSION, compose


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scalar core is pure arithmetic -- any socket use is a defect.

    Replacing ``socket.socket`` turns an unexpected network touch into a hard,
    loud failure, actively enforcing the zero-network guarantee rather than
    assuming it.
    """

    def _blocked(*args: object, **kwargs: object) -> object:
        raise RuntimeError("network access is forbidden in the scalar test suite")

    monkeypatch.setattr(socket, "socket", _blocked)


def _compose(
    *,
    quality_answers: float,
    lint_violations: int,
    tokens: float,
    tokens_target: float,
    n_content_pages: int,
    **overrides: float,
) -> float:
    """The single reconciliation point for `compose`'s interface (see PINNED block).

    Threads the four positional signals in fixed order and passes
    ``n_content_pages`` plus any ``w_lint``/``lam`` overrides by keyword.
    """
    return compose(
        quality_answers,
        lint_violations,
        tokens,
        tokens_target,
        n_content_pages=n_content_pages,
        **overrides,
    )


# ---------------------------------------------------------------------------
# Hinge: at or under budget earns full cost_factor; cheapness earns NO bonus
# ---------------------------------------------------------------------------

# With quality_answers=1.0 and zero lint violations, Q = 1.0 exactly, so the
# returned scalar *is* the cost_factor -- isolating the hinge.
_FULL_QUALITY = 1.0
_ANY_PAGES = 4
_TARGET = 10.0


@pytest.mark.parametrize(
    "tokens",
    [
        pytest.param(2.0, id="well-under-budget"),
        pytest.param(9.0, id="just-under-budget"),
        pytest.param(10.0, id="exactly-at-budget"),
    ],
)
def test_at_or_under_budget_applies_no_cost_penalty(tokens: float) -> None:
    # The hinge is max(0, (T - T_target)/T_target): for T <= T_target the overrun
    # is clamped to 0, so cost_factor is exactly 1 and a cheaper run gets no bonus
    # over an at-budget run. All three token levels must yield the same full score.
    scalar = _compose(
        quality_answers=_FULL_QUALITY,
        lint_violations=0,
        tokens=tokens,
        tokens_target=_TARGET,
        n_content_pages=_ANY_PAGES,
    )

    assert scalar == pytest.approx(1.0), (
        "at or under T_target the cost_factor must be exactly 1 (no penalty, no "
        f"cheapness bonus); got {scalar!r} for T={tokens}"
    )


# ---------------------------------------------------------------------------
# Overrun: cost_factor = 1 - lam * overrun_ratio, matched to closed form
# ---------------------------------------------------------------------------

# Hand-computed against cost_factor = 1 - 0.3 * (T - 10)/10, with Q = 1.0.
_OVERRUN_CASES = [
    # (tokens, expected_scalar): overrun_ratio -> 1 - 0.3*ratio
    pytest.param(11.0, 0.97, id="10pct-over"),  # ratio 0.1 -> 1 - 0.03
    pytest.param(13.0, 0.91, id="30pct-over"),  # ratio 0.3 -> 1 - 0.09
    pytest.param(15.0, 0.85, id="50pct-over"),  # ratio 0.5 -> 1 - 0.15
    pytest.param(20.0, 0.70, id="100pct-over"),  # ratio 1.0 -> 1 - 0.30
]


@pytest.mark.parametrize("tokens, expected_scalar", _OVERRUN_CASES)
def test_over_budget_discount_matches_the_closed_form(
    tokens: float, expected_scalar: float
) -> None:
    scalar = _compose(
        quality_answers=_FULL_QUALITY,
        lint_violations=0,
        tokens=tokens,
        tokens_target=_TARGET,
        n_content_pages=_ANY_PAGES,
    )

    assert scalar == pytest.approx(expected_scalar), (
        f"over-budget cost_factor must equal 1 - 0.3*((T-T_target)/T_target); "
        f"T={tokens} expected {expected_scalar}, got {scalar!r}"
    )


# ---------------------------------------------------------------------------
# lambda bounds and shades the discount -- overridable per call
# ---------------------------------------------------------------------------

# At a fixed 30%-over run (ratio 0.3), cost_factor = 1 - lam*0.3.
_LAMBDA_CASES = [
    pytest.param(0.3, 0.91, id="default-lambda"),  # 1 - 0.3*0.3
    pytest.param(0.6, 0.82, id="double-lambda"),  # 1 - 0.6*0.3
    pytest.param(1.0, 0.70, id="max-lambda"),  # 1 - 1.0*0.3
    pytest.param(0.0, 1.00, id="zero-lambda-disables-penalty"),
]


@pytest.mark.parametrize("lam, expected_scalar", _LAMBDA_CASES)
def test_lambda_scales_the_cost_discount_and_is_overridable(
    lam: float, expected_scalar: float
) -> None:
    # lam is the knob that bounds how much cost can shade quality; passing a
    # non-default value must change the discount by exactly that factor.
    scalar = _compose(
        quality_answers=_FULL_QUALITY,
        lint_violations=0,
        tokens=13.0,
        tokens_target=_TARGET,
        n_content_pages=_ANY_PAGES,
        lam=lam,
    )

    assert scalar == pytest.approx(expected_scalar), (
        f"lam must scale the hinge discount linearly; lam={lam} expected "
        f"{expected_scalar}, got {scalar!r}"
    )


# ---------------------------------------------------------------------------
# Clamp: a huge overrun bottoms out at exactly 0, never negative
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tokens",
    [
        pytest.param(50.0, id="ratio-4-would-be-minus-0.2"),  # 1 - 0.3*4 = -0.2
        pytest.param(100.0, id="ratio-9-would-be-minus-1.7"),  # 1 - 0.3*9 = -1.7
        pytest.param(1000.0, id="astronomical-overrun"),
    ],
)
def test_massive_overrun_clamps_cost_factor_to_zero_never_negative(tokens: float) -> None:
    # Without the clamp, 1 - lam*ratio goes negative for ratio > 1/lam; the scalar
    # would then flip sign. The clamp floors cost_factor at 0, so with Q = 1.0 the
    # scalar is exactly 0 -- and, critically, never below it.
    scalar = _compose(
        quality_answers=_FULL_QUALITY,
        lint_violations=0,
        tokens=tokens,
        tokens_target=_TARGET,
        n_content_pages=_ANY_PAGES,
    )

    assert scalar == pytest.approx(0.0), (
        f"cost_factor must clamp to 0 under massive overrun; got {scalar!r}"
    )
    assert scalar >= 0.0, "the clamp must never let the scalar go negative"


# ---------------------------------------------------------------------------
# lint_cleanliness: L_ref floor, zero-violation full credit, non-negative floor
# ---------------------------------------------------------------------------


def test_zero_lint_violations_gives_full_cleanliness_credit() -> None:
    # With no violations, lint_cleanliness = 1, so Q blends full cleanliness at
    # weight w_lint=0.15 even when quality_answers is 0: Q = 0.85*0 + 0.15*1.
    scalar = _compose(
        quality_answers=0.0,
        lint_violations=0,
        tokens=5.0,  # under budget -> cost_factor 1, isolating Q
        tokens_target=_TARGET,
        n_content_pages=_ANY_PAGES,
    )

    assert scalar == pytest.approx(0.15), (
        "a clean-lint topic contributes w_lint (0.15) to Q even at zero answer "
        f"quality; got {scalar!r}"
    )


def test_zero_content_pages_floors_l_ref_at_one_without_division_blowup() -> None:
    # L_ref = max(1, n_content_pages): with zero content pages the divisor floors
    # to 1, so 1 violation yields cleanliness max(0, 1 - 1/1) = 0 -- and crucially
    # no ZeroDivisionError. Q = 0.85*1 + 0.15*0 = 0.85.
    scalar = _compose(
        quality_answers=1.0,
        lint_violations=1,
        tokens=5.0,
        tokens_target=_TARGET,
        n_content_pages=0,
    )

    assert scalar == pytest.approx(0.85), (
        f"L_ref must floor at 1 so a zero-page topic divides cleanly; expected 0.85, got {scalar!r}"
    )


def test_lint_cleanliness_floors_at_zero_and_never_goes_negative() -> None:
    # violations (10) far exceed L_ref (4): 1 - 10/4 = -1.5, which must floor to 0.
    # If it did not, Q would be 0.85*1 + 0.15*(-1.5) = 0.625; the floor makes it
    # 0.85*1 + 0.15*0 = 0.85. The distinct expected value pins the max(0, ...).
    scalar = _compose(
        quality_answers=1.0,
        lint_violations=10,
        tokens=5.0,
        tokens_target=_TARGET,
        n_content_pages=4,
    )

    assert scalar == pytest.approx(0.85), (
        "lint_cleanliness must floor at 0 (not go to -1.5); expected 0.85 "
        f"(not the unfloored 0.625), got {scalar!r}"
    )


# ---------------------------------------------------------------------------
# w_lint is overridable per call and re-weights the quality blend
# ---------------------------------------------------------------------------


def test_w_lint_override_reweights_the_quality_blend() -> None:
    # Fix cleanliness at 0.5 (2 violations over L_ref 4) and answer quality at 1.0,
    # under budget so cost_factor = 1. Default w_lint=0.15 -> Q = 0.85 + 0.075 =
    # 0.925; w_lint=0.5 -> Q = 0.5 + 0.25 = 0.75. The override must move the blend.
    common = {
        "quality_answers": 1.0,
        "lint_violations": 2,
        "tokens": 5.0,
        "tokens_target": _TARGET,
        "n_content_pages": 4,
    }

    default_blend = _compose(**common)
    heavier_lint = _compose(**common, w_lint=0.5)

    assert default_blend == pytest.approx(0.925), (
        f"default w_lint=0.15 blend expected 0.925, got {default_blend!r}"
    )
    assert heavier_lint == pytest.approx(0.75), (
        f"w_lint=0.5 must re-weight the blend to 0.75, got {heavier_lint!r}"
    )


# ---------------------------------------------------------------------------
# Multiplicative: zero composite quality yields zero scalar regardless of cost
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tokens",
    [
        pytest.param(5.0, id="best-possible-cost"),  # under budget, cost_factor 1
        pytest.param(15.0, id="over-budget-cost"),
    ],
)
def test_zero_composite_quality_yields_zero_scalar_regardless_of_cost(tokens: float) -> None:
    # scalar = Q * cost_factor. Drive Q to 0 by zeroing BOTH terms: zero answer
    # quality AND lint_violations == L_ref (4/4) so cleanliness = 0. Even at the
    # best possible cost_factor (=1, under budget), a zero-quality topic cannot
    # buy score -- the multiplicative structure forbids it.
    scalar = _compose(
        quality_answers=0.0,
        lint_violations=4,
        tokens=tokens,
        tokens_target=_TARGET,
        n_content_pages=4,
    )

    assert scalar == pytest.approx(0.0), (
        "a cheap run cannot rescue zero composite quality (multiplicative form); "
        f"got {scalar!r} at T={tokens}"
    )


# ---------------------------------------------------------------------------
# Total function: the result is always in [0,1] across the input space
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "quality_answers, lint_violations, tokens, tokens_target, n_content_pages",
    [
        pytest.param(0.0, 0, 1.0, 10.0, 1, id="min-quality-cheap"),
        pytest.param(1.0, 0, 1.0, 10.0, 30, id="max-quality-cheap"),
        pytest.param(1.0, 0, 1000.0, 10.0, 30, id="max-quality-astronomical-cost"),
        pytest.param(0.5, 7, 12.0, 10.0, 4, id="mid-everything-slight-overrun"),
        pytest.param(1.0, 100, 5.0, 10.0, 2, id="violations-swamp-l_ref"),
        pytest.param(0.0, 0, 0.0, 1.0, 0, id="degenerate-zeros"),
    ],
)
def test_scalar_is_always_bounded_to_the_unit_interval(
    quality_answers: float,
    lint_violations: int,
    tokens: float,
    tokens_target: float,
    n_content_pages: int,
) -> None:
    scalar = _compose(
        quality_answers=quality_answers,
        lint_violations=lint_violations,
        tokens=tokens,
        tokens_target=tokens_target,
        n_content_pages=n_content_pages,
    )

    assert 0.0 <= scalar <= 1.0, (
        f"the composed scalar must be a total function into [0,1]; got {scalar!r}"
    )


# ---------------------------------------------------------------------------
# The formula version is exposed for cross-generation comparability
# ---------------------------------------------------------------------------


def test_scalar_formula_version_is_the_locked_v1_value() -> None:
    # harness_version fingerprints this constant; a shape change to the formula
    # must bump it so scalars from different formula generations are never
    # silently compared.
    assert SCALAR_FORMULA_VERSION == 1, (
        "the locked v1 policy must expose scalar_formula_version == 1; "
        f"got {SCALAR_FORMULA_VERSION!r}"
    )


# ---------------------------------------------------------------------------
# Purity: the pure scoring core imports no heavy LLM dependencies
# ---------------------------------------------------------------------------


def test_importing_the_scoring_core_pulls_in_no_llm_dependencies() -> None:
    # The scalar/citations core is pure and must stay cheap to import: no
    # ``anthropic``, no ``dspy`` (even transitively). A fresh interpreter is
    # required -- a same-process ``sys.modules`` check false-positives once any
    # sibling eval test has loaded ``dspy`` in the full-suite run. A stray
    # top-level import of either heavy package in the core would leak into the
    # child's ``sys.modules`` and fail this loudly.
    script = (
        "import sys\n"
        "import knotica.evals.scalar\n"
        "import knotica.evals.citations\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m in ('anthropic', 'dspy') or m.startswith(('anthropic.', 'dspy.'))\n"
        ")\n"
        "assert not leaked, leaked\n"
        "print('SCORING_CORE_PURE_OK')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "importing the pure scoring core must not import anthropic or dspy; "
        f"child stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "SCORING_CORE_PURE_OK" in result.stdout

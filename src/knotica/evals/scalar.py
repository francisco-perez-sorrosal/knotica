"""Topic-scalar composition for the eval harness -- the locked v1 formula, pure.

This module owns the *topic-level* composition that turns per-example quality,
topic lint-cleanliness, and a token-cost signal into one stable scalar in
``[0, 1]`` (``SCALAR_FORMULA_VERSION == 1``). The per-example quality weights
(``w_qa`` / ``w_cite``) live in the scorer, not here -- this module blends the
already-composed mean quality with lint-cleanliness and applies the cost
discount.

The formula (locked v1 policy)::

    lint_cleanliness = max(0, 1 - lint_violations / L_ref)   # L_ref = max(1, n_content_pages)
    Q                = (1 - w_lint) * quality_answers + w_lint * lint_cleanliness
    cost_factor      = clamp(1 - lam * max(0, (T - T_target) / T_target), 0, 1)
    scalar           = Q * cost_factor

Three properties defeat cost-penalty swamping and are load-bearing to the shape:
a **hinge** (``max(0, ...)`` -- no penalty at or under budget, and no bonus for a
degenerate terse answer), a **budget-relative** ratio (``/ T_target`` --
dimensionless, so it survives cross-generation output-length changes), and a
**multiplicative** discount (``* Q`` -- a cheap low-quality answer cannot buy
score). ``lam`` in ``[0, 1]`` bounds the maximum discount so cost can shade but
never dominate quality.

:func:`compose` is a total function over its bounded inputs: it never raises (a
non-positive ``T_target`` is treated as an absent budget signal, no penalty) and
always returns a value in ``[0, 1]`` (the result is clamped). Every formula
constant is a module-level default, overridable per call, so the harness config
layer can thread packaged or CLI-overridden values through without a hidden
global.
"""

__all__ = [
    "LAMBDA",
    "SCALAR_FORMULA_VERSION",
    "W_LINT",
    "compose",
]

#: Version of the scalar *shape*. Bumped when the formula's structure changes
#: (not when the packaged constant values are merely retuned), so a stored
#: scalar stays interpretable against the formula that produced it.
SCALAR_FORMULA_VERSION = 1

#: v1 weight of topic lint-cleanliness within the quality composite ``Q``; the
#: complement ``1 - W_LINT`` weights the mean per-example answer quality.
W_LINT = 0.15

#: v1 cost-discount coefficient: the maximum fractional discount applied when
#: tokens run over budget, bounding how hard cost can shade the scalar.
LAMBDA = 0.3


def compose(
    quality_answers: float,
    lint_violations: int,
    T: float,
    T_target: float,
    *,
    n_content_pages: int = 1,
    w_lint: float = W_LINT,
    lam: float = LAMBDA,
) -> float:
    """Compose the topic scalar in ``[0, 1]`` from quality, lint, and token cost.

    ``quality_answers`` is the mean per-example quality (already ``w_qa`` /
    ``w_cite`` blended by the scorer); ``lint_violations`` is the raw topic
    violation count; ``T`` is the per-item median total tokens for the generation
    being scored and ``T_target`` its frozen budget. ``n_content_pages`` sets the
    lint reference ``L_ref = max(1, n_content_pages)`` so the violation count is
    normalized by vault size. ``w_lint`` and ``lam`` are the v1 constants,
    overridable per call.

    Total by construction: a non-positive ``T_target`` yields no cost penalty
    rather than a division error, and the returned scalar is clamped to
    ``[0, 1]`` even if ``quality_answers`` is passed outside that range.
    """
    l_ref = max(1, n_content_pages)
    lint_cleanliness = max(0.0, 1.0 - lint_violations / l_ref)
    quality = (1.0 - w_lint) * quality_answers + w_lint * lint_cleanliness
    cost_factor = _cost_factor(T, T_target, lam)
    return _clamp(quality * cost_factor, 0.0, 1.0)


def _cost_factor(T: float, T_target: float, lam: float) -> float:
    """The hinged, budget-relative, multiplicative discount in ``[0, 1]``.

    ``1.0`` at or under budget (the hinge floors the overage at zero); a
    non-positive ``T_target`` is treated as an absent budget signal (no penalty),
    keeping the division safe.
    """
    if T_target <= 0:
        return 1.0
    overage = max(0.0, (T - T_target) / T_target)
    return _clamp(1.0 - lam * overage, 0.0, 1.0)


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` into the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))

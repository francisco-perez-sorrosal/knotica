"""Packaged eval-harness defaults and the ``harness_version`` fingerprint.

This module is the single surface for every tunable the harness ships with -- the
pinned model snapshots, the judge sampling count, the scalar weights and cost
coefficients, the ``dspy.Evaluate`` runner config, and the per-run spend ceilings
-- plus the validation that keeps an override from putting the harness in an
unsafe state. Everything here is a packaged default that a CLI flag can override
through :class:`HarnessConfig` (a frozen dataclass) and its
:meth:`~HarnessConfig.with_overrides` path; the resolved config is then recorded
per run so a stored scalar stays reproducible.

**Import stays cheap.** This module pulls only stdlib and the pure
:mod:`knotica.evals.scalar` leaf -- never ``anthropic`` or ``dspy``. The installed
``dspy`` version that folds into the fingerprint is resolved *lazily* from
distribution metadata (:func:`_installed_dspy_version`) at call time, so importing
``knotica.evals.config`` costs nothing near the ~1-2s ``dspy`` import and cannot
drag the eval dependency group onto an unrelated path (the MCP cold start).

**Single source of truth.** The scalar-formula coefficients (``W_LINT``,
``LAMBDA``, ``SCALAR_FORMULA_VERSION``) are imported from
:mod:`knotica.evals.scalar` (the formula's home) and re-exported here as the
unified defaults surface -- never redefined. The per-example quality weights
(:data:`W_QA`, :data:`W_CITE`) and the bool-branch :data:`DEFAULT_THRESHOLD` are
*owned here* and imported by :mod:`knotica.evals.scorer`; this module never imports
the scorer, so the dependency arrow points one way (``scorer -> config``) with no
cycle. The golden-set floor lives in :mod:`knotica.evals.golden`
(``EVAL_MIN_GOLDEN``) and is read there directly -- it counts a golden-set, not a
harness default, so it is deliberately not surfaced or duplicated here.

**Model-snapshot pins (constraint prose, verified against the live catalog).** The
judge and worker snapshots are pinned to the *exact* catalog model ids. For the
4.6 generation the alias-form strings ``claude-opus-4-6`` / ``claude-sonnet-4-6``
ARE the complete, exact ids -- there is no dated snapshot variant, and appending a
date suffix (e.g. ``-20251114``) resolves to a 404. That is precisely why these
pins are not date-suffixed: a dated form does not exist for this generation, so the
alias-form id is the exact pin, not a floating alias.

Two determinism-relevant API facts about the 4.6 generation, encoded by the runner
and judge and recorded here so an upgrade does not silently break them:

* ``temperature=0`` is accepted on the 4.6 generation. It is REMOVED on Opus 4.7+
  / Sonnet 5 (those 400 on a ``temperature`` argument) -- rotating either snapshot
  to a newer generation therefore requires dropping ``temperature=0`` from the
  runner/judge calls, and bumps :func:`harness_version` here.
* A request that OMITS the ``thinking`` parameter runs WITHOUT thinking on the 4.6
  generation -- the deterministic default the runner and judge want, and which they
  already produce by never sending ``thinking``.

**The fingerprint.** :func:`harness_version` hashes ``{scalar_formula_version,
judge_snapshot, worker_snapshot, judge_prompt_hash, runner_config_hash}``, where
``runner_config_hash`` folds in the installed ``dspy`` version and
``failure_score`` (never ``num_threads`` — parallelism does not change the
measurement). ``judge_prompt_hash`` is passed in by
the caller (the judge owns it) so this module stays decoupled from the judge
instrument. The digest is deterministic across processes (canonical sorted-key
serialization), carries no secrets, and rotates whenever any folded input changes
-- so two scalars produced under different instruments are never silently compared.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, version

from knotica.evals.scalar import LAMBDA, SCALAR_FORMULA_VERSION, W_LINT

__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_THRESHOLD",
    "FAILURE_SCORE",
    "JUDGE_SNAPSHOT",
    "LAMBDA",
    "MAX_TOTAL_TOKENS_PER_RUN",
    "MAX_USD_PER_RUN",
    "MAX_NUM_THREADS",
    "NUM_THREADS",
    "N_JUDGE_SAMPLES",
    "SCALAR_FORMULA_VERSION",
    "TAU",
    "W_CITE",
    "W_LINT",
    "W_QA",
    "WORKER_SNAPSHOT",
    "HarnessConfig",
    "harness_version",
]

# --------------------------------------------------------------------------- #
# Model snapshot pins (exact catalog ids -- see the module docstring's
# constraint prose for why the 4.6 generation carries no dated form).
# --------------------------------------------------------------------------- #

#: The pinned judge model: an Opus-class snapshot (maximally stable reference
#: grader). ``claude-opus-4-6`` is the exact catalog id -- not a floating alias and
#: not date-suffixed, because the 4.6 generation has no dated snapshot variant.
JUDGE_SNAPSHOT = "claude-opus-4-6"

#: The pinned worker/baseline model: a Sonnet-class snapshot (the answerer whose
#: output is scored). ``claude-sonnet-4-6`` is the exact catalog id, same
#: no-dated-form reasoning as :data:`JUDGE_SNAPSHOT`.
WORKER_SNAPSHOT = "claude-sonnet-4-6"

# --------------------------------------------------------------------------- #
# Judge sampling.
# --------------------------------------------------------------------------- #

#: Packaged judge-sampling policy: the number of temperature-0 samples the harness
#: asks the judge to median over. Must be odd (validated) so the median is an
#: actual drawn sample. Equal by design to ``judge.DEFAULT_N_JUDGE_SAMPLES`` (the
#: grade() standalone fallback) but an independent harness-policy knob -- the same
#: distinct-yet-equal relationship ``EVAL_MIN_GOLDEN`` has with
#: ``COMPILE_READY_MIN_EXAMPLES``.
N_JUDGE_SAMPLES = 3

# --------------------------------------------------------------------------- #
# Per-example quality weights (owned here; imported by evals.scorer). Kept here
# rather than in the scorer so config -> scorer stays a one-way arrow: the scorer
# needs DEFAULT_THRESHOLD from config, so config must not import the scorer back.
# --------------------------------------------------------------------------- #

#: v1 weight on the judge-assessed QA-accuracy leg of per-example quality. The
#: dominant term -- answer correctness matters more than citation bookkeeping.
W_QA = 0.7

#: v1 weight on the deterministic citation-validity leg. Complements :data:`W_QA`
#: (the two sum to ``1.0`` so the per-example quality blend stays in ``[0, 1]``).
W_CITE = 0.3

#: v1 bool-branch cutoff: ``quality >= DEFAULT_THRESHOLD`` marks an example a "good
#: enough" demonstration for the DSPy-bootstrap contract. Exercised only on the
#: optimizer path (``trace is not None``); untuned and CLI-overridable.
DEFAULT_THRESHOLD = 0.5

# --------------------------------------------------------------------------- #
# Cost / budget coefficient.
# --------------------------------------------------------------------------- #

#: v1 budget-slack multiplier: ``T_target = TAU * median(T_i)`` on generation 0
#: (then frozen per topic), so a modest token increase over the generation-0 median
#: is not penalized before the hinge engages.
TAU = 1.3

# --------------------------------------------------------------------------- #
# dspy.Evaluate runner config.
# --------------------------------------------------------------------------- #

#: Packaged default thread count for ``dspy.Evaluate``. Concurrency up to
#: :data:`MAX_NUM_THREADS` is supported: the shared cache, usage accounting, and
#: progress wrapper are lock-guarded, and per-example results plus their
#: devset-order aggregation are thread-count-independent — which is why
#: ``num_threads`` never folds into the fingerprint and results are identical
#: to a sequential run. Default ``4`` cuts eval wall-time ~3-4×; drop to ``1``
#: (``--eval-threads 1``) when debugging or rate-limited.
NUM_THREADS = 4

#: Upper bound on Evaluate worker threads (API rate limits vs speedup).
MAX_NUM_THREADS = 8

#: The score attributed to an example whose program call raises inside
#: ``dspy.Evaluate`` (the Evaluate ``failure_score`` policy). ``0.0`` matches dspy's
#: own default; folded into the fingerprint so a change rotates ``harness_version``.
FAILURE_SCORE = 0.0

# --------------------------------------------------------------------------- #
# Per-run spend ceilings (hard-abort; the harness enforces, config owns the
# numbers + validation). A cache-keying bug or an oversized golden set could
# otherwise run up a surprise API bill with no ceiling.
# --------------------------------------------------------------------------- #

#: Per-run total-token hard-abort ceiling. Sized to fit a legitimate reference-topic
#: run with headroom, not to trip a normal one: the first full live eval -- 25 golden
#: questions over a real topic with large pages -- measured ~2.14M tokens cold, so the
#: former 2M default aborted a legitimate reference-topic run. Raised to 5M so such a
#: run fits comfortably while a genuine runaway loop still trips it; the USD ceiling
#: (:data:`MAX_USD_PER_RUN`) remains the tighter, mode-independent guard. Enforced by
#: the harness; a non-positive override is rejected by validation.
MAX_TOTAL_TOKENS_PER_RUN = 5_000_000

#: Per-run USD hard-abort ceiling. Same intent as :data:`MAX_TOTAL_TOKENS_PER_RUN`
#: on the cost axis -- a normal run stays well under it; a runaway trips it and
#: aborts. Enforced by the harness; a non-positive override is rejected.
MAX_USD_PER_RUN = 10.0


def _reject_even_or_nonpositive_samples(n: int) -> None:
    """Reject a judge-sample count that is not a positive odd number.

    Mirrors the judge's own sampling rule: an odd count keeps the median an actual
    drawn sample rather than an average of two middles.
    """
    if n < 1 or n % 2 == 0:
        raise ValueError(
            f"n_judge_samples must be a positive odd number, got {n}. An odd count "
            "keeps the judge median an actual drawn sample. To fix: use an odd n."
        )


def _reject_multithreading(num_threads: int) -> None:
    """Bound the Evaluate thread count to ``1..MAX_NUM_THREADS``.

    Concurrency above 1 is supported: the shared surfaces are thread-safe
    (``ResponseCache`` takes per-key compute locks, the usage accountant and the
    progress wrapper are lock-guarded, and ``BaselineProgram.forward`` is
    stateless per call). The upper bound keeps a fat thread pool from tripping
    API rate limits for negligible extra speedup.
    """
    if num_threads < 1:
        raise ValueError(f"num_threads must be a positive integer, got {num_threads}.")
    if num_threads > MAX_NUM_THREADS:
        raise ValueError(
            f"num_threads must be at most {MAX_NUM_THREADS}, got {num_threads} — "
            "higher counts mostly trade API rate-limit errors for no wall-time gain. "
            f"To fix: set num_threads<={MAX_NUM_THREADS}."
        )


def _reject_nonpositive_ceiling(name: str, value: float, unit: str) -> None:
    """Reject a non-positive per-run spend ceiling.

    A ceiling is a per-run hard-abort limit; a non-positive one would abort the run
    before any work, which is never the intent.
    """
    if value <= 0:
        raise ValueError(
            f"{name} must be a positive {unit} ceiling, got {value}. It is a per-run "
            "hard-abort limit. To fix: pass a positive ceiling."
        )


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    """The resolved, immutable set of eval-harness knobs for one run.

    Every field defaults to the packaged module constant, so ``HarnessConfig()`` is
    the shipped default and :meth:`with_overrides` threads CLI flags on top.
    Construction validates the fields (via ``__post_init__``), so an unsafe
    override -- a multithreaded run, a non-positive spend ceiling, an even judge
    sample count -- fails fast at build time rather than mid-run.
    """

    judge_snapshot: str = JUDGE_SNAPSHOT
    worker_snapshot: str = WORKER_SNAPSHOT
    n_judge_samples: int = N_JUDGE_SAMPLES
    w_qa: float = W_QA
    w_cite: float = W_CITE
    w_lint: float = W_LINT
    lam: float = LAMBDA
    tau: float = TAU
    threshold: float = DEFAULT_THRESHOLD
    num_threads: int = NUM_THREADS
    failure_score: float = FAILURE_SCORE
    scalar_formula_version: int = SCALAR_FORMULA_VERSION
    max_total_tokens: int = MAX_TOTAL_TOKENS_PER_RUN
    max_usd: float = MAX_USD_PER_RUN

    def __post_init__(self) -> None:
        """Validate the safety-critical knobs; unsafe values fail fast at build."""
        _reject_even_or_nonpositive_samples(self.n_judge_samples)
        _reject_multithreading(self.num_threads)
        _reject_nonpositive_ceiling("max_total_tokens", self.max_total_tokens, "token")
        _reject_nonpositive_ceiling("max_usd", self.max_usd, "USD")

    def with_overrides(self, **overrides: object) -> HarnessConfig:
        """Return a new config with ``overrides`` applied, re-validated.

        The clean CLI-override path: ``DEFAULT_CONFIG.with_overrides(num_threads=2)``
        raises the same validation error as constructing it directly, because
        :func:`dataclasses.replace` runs ``__post_init__`` on the new instance.
        """
        return replace(self, **overrides)


#: The shipped packaged defaults -- the config a run uses when no flag overrides it.
DEFAULT_CONFIG = HarnessConfig()

#: Distribution names to probe for the installed ``dspy`` version, in order. PyPI
#: ships it as ``dspy`` (alias ``dspy-ai``); the harness pins ``dspy``.
_DSPY_DISTRIBUTION_NAMES = ("dspy", "dspy-ai")


def _installed_dspy_version() -> str:
    """Resolve the installed ``dspy`` version without importing ``dspy``.

    Reads distribution metadata via :func:`importlib.metadata.version`, so it pays
    nothing near the ~1-2s ``dspy`` import and pulls neither ``dspy`` nor
    ``anthropic`` into an import of this module. Returns ``"unknown"`` when the eval
    group is not installed (the harness only calls this under that group), keeping
    :func:`harness_version` total. Folded into ``runner_config_hash`` so a ``dspy``
    upgrade -- a new runner behaviour -- rotates the fingerprint.
    """
    for dist_name in _DSPY_DISTRIBUTION_NAMES:
        try:
            return version(dist_name)
        except PackageNotFoundError:
            continue
    return "unknown"


def _sha256_canonical(payload: Mapping[str, object]) -> str:
    """Digest a payload deterministically: sorted-key compact JSON, then ``sha256``.

    Sorted keys and compact separators make the digest identical across processes
    and dict orderings -- the property the frozen-corpus reproducibility rests on.
    """
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def harness_version(judge_prompt_hash: str, config: HarnessConfig = DEFAULT_CONFIG) -> str:
    """Fingerprint the immutable harness -- the instrument a scalar was produced by.

    Hashes ``{scalar_formula_version, judge_snapshot, worker_snapshot,
    judge_prompt_hash, runner_config_hash}``, where ``runner_config_hash`` folds in
    the installed ``dspy`` version and ``failure_score`` — never ``num_threads``,
    so scalars stay comparable across thread counts. ``judge_prompt_hash`` is
    supplied by the caller (the judge owns its instrument hash) so this module
    stays decoupled from the judge.

    Deterministic across processes, carries no secrets, and changes whenever any
    folded input changes -- so a rotated snapshot, an edited judge prompt, a bumped
    formula, or a ``dspy`` upgrade all yield a distinct ``harness_version``, and two
    scalars from different instruments are never silently compared.

    Args:
        judge_prompt_hash: The judge instrument's ``sha256``
            (``knotica.evals.judge.JUDGE_PROMPT_HASH``), passed in by the caller.
        config: The resolved run config whose snapshots / formula version / Evaluate
            config are fingerprinted. Defaults to the packaged :data:`DEFAULT_CONFIG`.

    Returns:
        The hex ``sha256`` fingerprint string.
    """
    # ``num_threads`` is deliberately NOT folded in: execution parallelism does
    # not change what is measured (per-example results are independent and the
    # aggregation order is devset order), so scalars must stay comparable across
    # thread counts — a threading change must never flip the gate to "unknown".
    runner_config_hash = _sha256_canonical(
        {
            "dspy_version": _installed_dspy_version(),
            "failure_score": config.failure_score,
        }
    )
    return _sha256_canonical(
        {
            "judge_prompt_hash": judge_prompt_hash,
            "judge_snapshot": config.judge_snapshot,
            "runner_config_hash": runner_config_hash,
            "scalar_formula_version": config.scalar_formula_version,
            "worker_snapshot": config.worker_snapshot,
        }
    )

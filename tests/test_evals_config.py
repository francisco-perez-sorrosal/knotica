"""Behavioral spec for the eval harness's packaged config + reproducibility fingerprint.

The config module is the "immutable harness" identity: the packaged model
snapshots, the runner knobs, the spend ceilings, and -- the load-bearing piece --
the ``harness_version`` fingerprint that stamps every stored scalar so a number is
never interpreted against the wrong instrument. These tests pin that identity and
the guards around it:

- **Snapshot pins are exact catalog ids.** The judge and worker default snapshots
  are the exact undated catalog aliases the live model list ships for this
  generation -- there is no dated snapshot variant, and appending a ``-YYYYMMDD``
  suffix would resolve to a 404. The pins are asserted byte-for-byte *and* guarded
  against a future "helpful" suffixing regression.
- **Runner knobs are safe by default, and unsafe overrides fail fast.** The config
  is a frozen ``HarnessConfig`` validated at construction. The default thread count
  parallelizes per-question scoring within a bounded cap (the shared cache, usage
  accounting, and progress wrapper are lock-guarded; results are identical to a
  sequential run); counts beyond the cap or below one are refused. The
  judge draws an odd number of samples (default three) so the median is a real drawn
  sample; an even count is refused, mirroring the judge's own odd-only rule. A
  non-positive value for either is refused.
- **Spend is bounded.** Per-run token and USD ceilings default to positive values
  and refuse a non-positive override, so a cache-keying regression or an oversized
  golden set cannot turn into a surprise API bill.
- **The fingerprint is a deterministic, secret-free sha256.** ``harness_version``
  returns a 64-char hex digest, stable within a process *and across a fresh
  interpreter* (no hash-seed dependence), that changes when any folded component
  changes -- the scalar-formula version, either model snapshot, the judge prompt
  hash, or any runner-config input (failure policy, dspy version — never the
  thread count, which changes wall-time, not the measurement).
  The overridable knobs reach it by *passing a ``HarnessConfig``*, not via hidden
  globals. It never folds in the ``ANTHROPIC_API_KEY``: a clone's committed record
  must not carry the secret. The tunable weight/lambda *values* are deliberately not
  folded -- a retune that keeps the formula shape keeps the version and the
  fingerprint (those values are per-run manifest columns).
- **The eval golden floor is its own number.** ``EVAL_MIN_GOLDEN`` is twenty and is
  a distinct constant from the compile-ready floor -- equal today, independent by
  design, so conflating the held-out eval set with the flywheel trainset is
  impossible.
- **Import stays cold.** Importing the config module pulls in neither ``dspy`` nor
  ``anthropic`` -- the dspy-version lookup inside the fingerprint reads distribution
  metadata lazily, so the cold-start-isolation guarantee that keeps the MCP launch
  path lean is preserved.

Written concurrently with the config implementation (disjoint files); the two
converged mid-session on the frozen-``HarnessConfig`` surface -- overrides are
threaded by passing a config to ``harness_version`` rather than mutating module
state, which is the cleaner (no-hidden-global) shape.
"""

import re
import subprocess
import sys

import pytest

from knotica.cli.status import COMPILE_READY_MIN_EXAMPLES
from knotica.core.errors import KnoticaError
from knotica.evals import config
from knotica.evals.config import (
    DEFAULT_THRESHOLD,
    JUDGE_SNAPSHOT,
    MAX_TOTAL_TOKENS_PER_RUN,
    MAX_USD_PER_RUN,
    N_JUDGE_SAMPLES,
    NUM_THREADS,
    SCALAR_FORMULA_VERSION,
    WORKER_SNAPSHOT,
    HarnessConfig,
    harness_version,
)
from knotica.evals.golden import EVAL_MIN_GOLDEN

#: The exact undated catalog ids for this model generation -- the alias *is* the
#: complete id; a date-suffixed variant would 404. Hand-pinned, not re-derived.
EXPECTED_JUDGE_SNAPSHOT = "claude-opus-4-6"
EXPECTED_WORKER_SNAPSHOT = "claude-sonnet-4-6"

#: A dated-snapshot tail (``-20260501`` and the like). The snapshots must NOT match
#: it -- this is the forward guard against a future "helpful" suffixing regression.
_DATED_SUFFIX_RE = re.compile(r"-20\d{6}$")

#: Structurally-plausible sha256-shaped judge-prompt-hash stand-ins used as the one
#: fingerprint argument. Fixed literals so the tests are self-contained.
SAMPLE_JUDGE_PROMPT_HASH = "0" * 64
OTHER_JUDGE_PROMPT_HASH = "f" * 64

#: A synthetic sentinel that stands in for a real credential -- NOT a real key. Used
#: to prove the fingerprint never folds in the API key.
SENTINEL_KEY = "sk-ant-api03-SENTINEL-do-not-leak-0000000000"

#: The env var the harness authenticates with elsewhere -- scrubbed here so a real
#: exported key can never influence a fingerprint under test.
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"


@pytest.fixture(autouse=True)
def _scrub_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from the absent-key state.

    A real ``ANTHROPIC_API_KEY`` exported on the dev machine must never leak into
    the fingerprint tests: it would mask the "secret never folded in" contract.
    The one test that needs the key present sets it explicitly.
    """
    monkeypatch.delenv(ANTHROPIC_KEY_ENV, raising=False)


# ---------------------------------------------------------------------------
# Snapshot pins -- exact undated catalog ids, no date suffix
# ---------------------------------------------------------------------------


def test_the_judge_snapshot_is_the_pinned_opus_catalog_id() -> None:
    assert JUDGE_SNAPSHOT == EXPECTED_JUDGE_SNAPSHOT, (
        "the judge snapshot must be the exact undated catalog id; a floating alias "
        "or a dated variant would resolve to a different (or missing) model"
    )


def test_the_worker_snapshot_is_the_pinned_sonnet_catalog_id() -> None:
    assert WORKER_SNAPSHOT == EXPECTED_WORKER_SNAPSHOT, (
        "the worker snapshot must be the exact undated catalog id"
    )


def test_neither_snapshot_carries_a_dated_suffix() -> None:
    # For this model generation the undated alias is the complete id; a
    # ``-YYYYMMDD`` tail would 404. This guards against a future edit that
    # "helpfully" appends a date to pin a snapshot that does not exist.
    assert _DATED_SUFFIX_RE.search(JUDGE_SNAPSHOT) is None, (
        f"the judge snapshot must not carry a dated suffix; got {JUDGE_SNAPSHOT!r}"
    )
    assert _DATED_SUFFIX_RE.search(WORKER_SNAPSHOT) is None, (
        f"the worker snapshot must not carry a dated suffix; got {WORKER_SNAPSHOT!r}"
    )


# ---------------------------------------------------------------------------
# Runner knobs -- safe defaults, unsafe overrides refused at construction
# ---------------------------------------------------------------------------


def test_the_default_thread_count_is_parallel_within_the_cap() -> None:
    from knotica.evals.config import MAX_NUM_THREADS

    assert 1 < NUM_THREADS <= MAX_NUM_THREADS, (
        "the default parallelizes per-question scoring (results are proven "
        "identical to sequential) and must respect the rate-limit cap"
    )


def test_thread_counts_up_to_the_cap_are_accepted_and_beyond_rejected() -> None:
    # Concurrency is supported now that the shared surfaces (response cache,
    # usage accounting, progress wrapper) are lock-guarded; the cap bounds API
    # rate-limit exposure.
    assert HarnessConfig(num_threads=2).num_threads == 2
    assert HarnessConfig(num_threads=8).num_threads == 8
    with pytest.raises((ValueError, KnoticaError)) as excinfo:
        HarnessConfig(num_threads=9)
    assert "at most" in str(excinfo.value)


def test_a_nonpositive_thread_count_is_rejected() -> None:
    with pytest.raises((ValueError, KnoticaError)):
        HarnessConfig(num_threads=0)


def test_the_default_judge_sample_count_is_three() -> None:
    assert N_JUDGE_SAMPLES == 3, (
        "the default judge sample count is three -- odd, so the median is a real "
        "drawn sample rather than an average of two middles"
    )


def test_an_even_judge_sample_count_is_rejected() -> None:
    # Mirrors the judge's own odd-only contract: an even count has no single
    # median sample.
    with pytest.raises((ValueError, KnoticaError)):
        HarnessConfig(n_judge_samples=4)


def test_a_nonpositive_judge_sample_count_is_rejected() -> None:
    with pytest.raises((ValueError, KnoticaError)):
        HarnessConfig(n_judge_samples=0)


# ---------------------------------------------------------------------------
# Spend ceilings -- positive defaults, non-positive overrides refused
# ---------------------------------------------------------------------------


def test_the_spend_ceilings_default_to_positive_values() -> None:
    assert MAX_TOTAL_TOKENS_PER_RUN > 0, "the per-run token ceiling must be a positive bound"
    assert MAX_USD_PER_RUN > 0, "the per-run USD ceiling must be a positive bound"


def test_the_default_token_ceiling_fits_a_real_reference_topic_run() -> None:
    # The first full live eval (25 golden questions over a real topic with large
    # pages) measured ~2.14M tokens cold, so the former 2M default aborted a
    # legitimate run. Pin the recalibrated 5M default so a real reference-topic run
    # fits with headroom while a genuine runaway still trips it.
    assert MAX_TOTAL_TOKENS_PER_RUN == 5_000_000, (
        "the default token ceiling must fit a real reference-topic run (~2.14M cold "
        "measured) with headroom -- pinned at 5M"
    )


def test_a_nonpositive_token_ceiling_is_rejected() -> None:
    with pytest.raises((ValueError, KnoticaError)):
        HarnessConfig(max_total_tokens=0)


def test_a_nonpositive_usd_ceiling_is_rejected() -> None:
    with pytest.raises((ValueError, KnoticaError)):
        HarnessConfig(max_usd=0)


def test_the_packaged_defaults_are_a_valid_config() -> None:
    # The shipped defaults are themselves a valid config -- constructing with no
    # overrides must not trip any guard.
    HarnessConfig()


# ---------------------------------------------------------------------------
# The reproducibility fingerprint -- shape, determinism, sensitivity, no secret
# ---------------------------------------------------------------------------


def test_the_fingerprint_is_a_sha256_hex_digest() -> None:
    fingerprint = harness_version(SAMPLE_JUDGE_PROMPT_HASH)

    assert re.fullmatch(r"[0-9a-f]{64}", fingerprint), (
        f"the harness fingerprint must be a lowercase sha256 hex digest; got {fingerprint!r}"
    )


def test_the_fingerprint_is_deterministic_for_identical_inputs() -> None:
    assert harness_version(SAMPLE_JUDGE_PROMPT_HASH) == harness_version(SAMPLE_JUDGE_PROMPT_HASH), (
        "identical inputs must fingerprint identically within a process"
    )


def test_the_fingerprint_is_stable_across_a_fresh_interpreter() -> None:
    # A same-process repeat cannot catch hash-seed / dict-order dependence: a
    # fresh interpreter recomputes the digest independently and must land on the
    # exact same value, which is what lets a stored scalar be re-verified later.
    in_process = harness_version(SAMPLE_JUDGE_PROMPT_HASH)
    script = (
        "from knotica.evals.config import harness_version\n"
        f"print(harness_version({SAMPLE_JUDGE_PROMPT_HASH!r}))\n"
    )

    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)

    assert result.returncode == 0, (
        f"the child interpreter failed to compute the fingerprint; stderr={result.stderr!r}"
    )
    assert result.stdout.strip() == in_process, (
        "the fingerprint must be identical across interpreters (no hash-seed dependence); "
        f"in-process={in_process!r} child={result.stdout.strip()!r}"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"judge_snapshot": "claude-opus-4-6-OTHER"},
        {"worker_snapshot": "claude-sonnet-4-6-OTHER"},
        {"failure_score": 0.5},
        {"scalar_formula_version": SCALAR_FORMULA_VERSION + 1},
    ],
)
def test_the_fingerprint_is_sensitive_to_a_folded_config_field(
    overrides: dict[str, object],
) -> None:
    # Passing a non-default config (the CLI override path) must change the
    # fingerprint, so a scalar produced under one instrument is never silently
    # compared against one produced under another. This doubles as the override
    # path: a non-default snapshot flows straight into the recorded version.
    baseline = harness_version(SAMPLE_JUDGE_PROMPT_HASH)
    changed = harness_version(SAMPLE_JUDGE_PROMPT_HASH, HarnessConfig(**overrides))

    assert changed != baseline, f"overriding {overrides} must change the harness fingerprint"


def test_the_fingerprint_ignores_the_thread_count() -> None:
    # Parallelism changes wall-time, never the measurement: per-example results
    # are independent and aggregated in devset order, so a thread-count change
    # must NOT rotate the instrument (a rotation would flip the gate to
    # "unknown" for a purely operational tweak).
    baseline = harness_version(SAMPLE_JUDGE_PROMPT_HASH)
    parallel = HarnessConfig(num_threads=4)

    assert harness_version(SAMPLE_JUDGE_PROMPT_HASH, parallel) == baseline, (
        "the thread count is execution config, not instrument config"
    )


def test_the_fingerprint_is_sensitive_to_the_judge_prompt_hash() -> None:
    assert harness_version(SAMPLE_JUDGE_PROMPT_HASH) != harness_version(OTHER_JUDGE_PROMPT_HASH), (
        "a different judge prompt hash must produce a different fingerprint -- an edit "
        "to the grading instrument must rotate the recorded harness version"
    )


def test_the_fingerprint_is_sensitive_to_the_dspy_version(monkeypatch: pytest.MonkeyPatch) -> None:
    # The runner-config leg folds in the installed dspy version, resolved through
    # ``_installed_dspy_version``. Patching that resolution seam lets us vary the
    # reported version and prove it is a fingerprint input -- a runner upgrade must
    # rotate the recorded harness version.
    monkeypatch.setattr(config, "_installed_dspy_version", lambda: "1.0.0-fake")
    one_version = harness_version(SAMPLE_JUDGE_PROMPT_HASH)
    monkeypatch.setattr(config, "_installed_dspy_version", lambda: "2.0.0-fake")
    other_version = harness_version(SAMPLE_JUDGE_PROMPT_HASH)

    assert one_version != other_version, (
        "the installed dspy version must be a fingerprint input, so a runner upgrade "
        "is reflected in the recorded harness version"
    )


def test_a_retune_of_weights_or_lambda_does_not_change_the_fingerprint() -> None:
    # The fingerprint folds the formula *version*, not the tunable weight/lambda
    # *values* -- those are per-run manifest columns. A retune that keeps the
    # formula shape keeps the same version and therefore the same fingerprint.
    baseline = harness_version(SAMPLE_JUDGE_PROMPT_HASH)
    retuned = HarnessConfig(w_qa=0.55, w_cite=0.45, lam=0.99)

    assert harness_version(SAMPLE_JUDGE_PROMPT_HASH, retuned) == baseline, (
        "retuning weights or lambda without a formula-shape change must not change the "
        "fingerprint (the tuned values live in the per-run manifest, not the version stamp)"
    )


def test_the_fingerprint_never_folds_in_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # A committed clone record must not carry the secret. If the key were a
    # fingerprint input, setting it would change the digest.
    without_key = harness_version(SAMPLE_JUDGE_PROMPT_HASH)
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, SENTINEL_KEY)
    with_key = harness_version(SAMPLE_JUDGE_PROMPT_HASH)

    assert with_key == without_key, (
        "the fingerprint must not depend on the API key -- the key is not part of the "
        "harness identity and must never leak into a committed record"
    )
    assert SENTINEL_KEY not in with_key, "the key value must not appear in the fingerprint output"


# ---------------------------------------------------------------------------
# The eval golden floor -- its own number, distinct from the compile-ready floor
# ---------------------------------------------------------------------------


def test_the_eval_golden_floor_is_twenty() -> None:
    assert EVAL_MIN_GOLDEN == 20, "the held-out eval golden floor is twenty pairs"


def test_the_eval_golden_floor_is_independent_of_the_compile_ready_floor() -> None:
    # Two deliberately-disjoint counts: the held-out eval set vs the flywheel
    # trainset. Each is its own named constant — collapsing them would invite
    # conflating the two sets.
    assert EVAL_MIN_GOLDEN == 20
    assert COMPILE_READY_MIN_EXAMPLES == 30


# ---------------------------------------------------------------------------
# The default threshold is owned by config and shared with the scorer
# ---------------------------------------------------------------------------


def test_the_default_threshold_is_owned_by_config_and_shared_with_the_scorer() -> None:
    from knotica.evals import scorer

    # config is the single source of the bool-branch cutoff; the scorer reads the
    # same value rather than carrying its own copy.
    assert scorer.DEFAULT_THRESHOLD == DEFAULT_THRESHOLD, (
        "the scorer must read the packaged threshold from config, not duplicate it"
    )


# ---------------------------------------------------------------------------
# Importing the config module stays cold -- no dspy, no anthropic
# ---------------------------------------------------------------------------


def test_importing_the_config_module_imports_neither_dspy_nor_anthropic() -> None:
    # A fresh interpreter is required: a same-process check false-positives if an
    # earlier test happened to import either package. The dspy-version lookup reads
    # distribution metadata (never imports dspy), so importing the module alone
    # stays cold. Both packages are installed in this interpreter, so a leak would
    # land in the child's sys.modules.
    script = (
        "import sys\n"
        "import knotica.evals.config\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m == 'dspy' or m.startswith('dspy.')\n"
        "    or m == 'anthropic' or m.startswith('anthropic.')\n"
        ")\n"
        "assert not leaked, leaked\n"
        "print('IMPORT_ISOLATION_OK')\n"
    )

    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)

    assert result.returncode == 0, (
        "importing the config module must not import dspy or anthropic; "
        f"child stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "IMPORT_ISOLATION_OK" in result.stdout

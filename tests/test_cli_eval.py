"""Behavioral tests for ``knotica eval`` — the thin CLI over the eval harness.

``eval`` is a *thin* adapter: it resolves config, delegates the whole (network-
bound) evaluation to the harness ``run_eval``, and renders the returned
``MetricsRecord`` as a table or ``--json``. Its own behavior — command
registration, argument parsing, exit codes, error rendering, the instrument-
drift warning, and the ``--bootstrap`` route — is what these tests pin. Every
test replaces ``run_eval`` at the CLI boundary with a stand-in (network-free) so
the adapter's dispatch/exit-code/render logic is exercised without ever running
the real harness (which clones the vault and calls Anthropic).

The command is driven in-process via ``knotica.cli.main([...])`` rather than a
subprocess (as ``status``/``doctor`` are) precisely so ``run_eval`` can be
stubbed — a subprocess cannot be monkeypatched. The stub target is the name
bound in the ``knotica.cli.eval`` namespace, because the module imports
``run_eval`` by name at module top.

Contract pinned here (documented interface + the harness's typed-error surface):

- ``eval`` is a registered command and ``eval --help`` lists its flags;
- a successful run exits 0 and reports the scalar (table + ``--json`` carry the
  same record facts, ``--json`` round-trips);
- an unconfigured vault exits with the uniform not-configured code *before* the
  harness is ever called;
- a missing golden set exits with a dedicated code, distinct from the generic
  error and the not-configured code — the harness's type-discrimination
  (all golden/harness errors share one ``code``, told apart only by type) made
  observable at the CLI seam;
- every typed harness/instrument failure renders as the house error envelope
  (message + actionable fix) on stderr, exits the generic-error code, and never
  leaks a stack trace;
- with no ``ANTHROPIC_API_KEY`` the run reports the actionable eval-not-configured
  message, and a configured key value never appears anywhere in the output;
- a run whose instrument (``harness_version``) differs from the topic's previous
  recorded run warns; a matching or first-ever run does not;
- ``--bootstrap`` stages synthetic golden candidates for review and never runs the
  metrics eval; an absent ``ANTHROPIC_API_KEY`` on that path reports the same clean
  not-configured message; the golden set is never frozen (nothing auto-accepts).

The ``--bootstrap`` path delegates to ``golden.bootstrap`` behind an
``AnthropicClient``; both are stubbed at the ``knotica.cli.eval`` module boundary
(the names are bound there at module top) so the adapter's dispatch / render /
exit-code logic is exercised network-free. The missing-key leg deliberately keeps
the *real* client so its env-key guard fires authentically before any network.
"""

import json
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

import pytest

from knotica.cli import COMMAND_NAMES
from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_NOT_CONFIGURED,
    EXIT_SUCCESS,
    UNCONFIGURED_MESSAGE,
)
from knotica.core.errors import KnoticaError
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.evals.config import WORKER_SNAPSHOT
from knotica.evals.golden import (
    GoldenSetContaminationError,
    GoldenSetIntegrityError,
    GoldenSetMissingError,
    golden_staging_path,
)
from knotica.evals.harness import EvalRunError, EvalRunResult, SpendCeilingExceededError
from knotica.evals.judge import JudgeParseError
from knotica.evals.llm import (
    API_KEY_ENV_VAR,
    OAUTH_TOKEN_ENV_VAR,
    AnthropicClient,
    MeteredApiKeyFallbackWarning,
)
from knotica.evals.runner import MalformedResponseError

SEED_TOPIC = "agentic-systems"
SENTINEL_API_KEY = "sk-ant-SENTINEL-must-never-leak-abc123"
SENTINEL_OAUTH_TOKEN = "sk-ant-oat01-SENTINEL-must-never-leak-abc123"
DEFAULT_HARNESS_VERSION = "hv-fingerprint-alpha"

#: A synthetic clone root the stub reports as ``run_eval``'s committed clone -- a
#: render value only (never touched on disk), so the CLI's clone-root and resolved-
#: manifest output can be asserted without running the real (vault-cloning) harness.
STUB_CLONE_ROOT = Path(tempfile.gettempdir()) / "knotica-eval-stub" / "clone"


# ---------------------------------------------------------------------------
# In-process invocation + boundary stub
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CliResult:
    """The outcome of one in-process ``knotica`` invocation."""

    code: int
    out: str
    err: str
    leaked: BaseException | None  # a non-SystemExit escape == a CLI that failed to render


def _exit_code(raised: SystemExit) -> int:
    code = raised.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1  # argparse-style string message


@pytest.fixture
def invoke(capsys: pytest.CaptureFixture[str]):
    """Return a callable that runs ``knotica <args>`` in-process and captures I/O.

    ``argparse``'s ``SystemExit`` (``--help``, misuse) is normalized to its exit
    code; any *other* escaping exception is recorded on ``leaked`` so a test can
    assert the CLI rendered a failure cleanly instead of crashing.
    """

    def _invoke(*args: str) -> _CliResult:
        from knotica.cli import main

        leaked: BaseException | None = None
        code = -1
        try:
            code = main(list(args))
        except SystemExit as raised:
            code = _exit_code(raised)
        except Exception as escaped:  # noqa: BLE001 — pinning "the CLI must not leak"
            leaked = escaped
        out, err = capsys.readouterr()
        return _CliResult(code=code, out=out, err=err, leaked=leaked)

    return _invoke


class _RunEvalStub:
    """A network-free stand-in for the harness ``run_eval``.

    Records every call so a test can assert the harness was (or was not) reached,
    and replays either a canned ``MetricsRecord`` (wrapped in the ``EvalRunResult``
    ``run_eval`` returns, with a synthetic clone root) or a raised exception.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._result: MetricsRecord | None = None
        self._error: BaseException | None = None
        self._clone_root: Path = STUB_CLONE_ROOT

    def returns(self, record: MetricsRecord, *, clone_root: Path | None = None) -> "_RunEvalStub":
        self._result = record
        self._error = None
        if clone_root is not None:
            self._clone_root = clone_root
        return self

    def raises(self, error: BaseException) -> "_RunEvalStub":
        self._error = error
        self._result = None
        return self

    def __call__(self, topic: str, **kwargs: object) -> EvalRunResult:
        self.calls.append((topic, kwargs))
        if self._error is not None:
            raise self._error
        assert self._result is not None, "the stub was invoked before a result was set"
        return EvalRunResult(record=self._result, clone_root=self._clone_root)


@pytest.fixture
def run_eval_stub(monkeypatch: pytest.MonkeyPatch) -> _RunEvalStub:
    """Install the ``run_eval`` stub at every binding the CLI might reach it through.

    ``cli.eval`` binds ``run_eval`` by name at module top, so that binding is the
    effective target; the package re-export and the harness source are patched too
    as belt-and-suspenders against an import-style change.
    """
    stub = _RunEvalStub()
    for target in (
        "knotica.cli.eval.run_eval",
        "knotica.evals.run_eval",
        "knotica.evals.harness.run_eval",
    ):
        try:
            monkeypatch.setattr(target, stub)
        except (ImportError, AttributeError):
            continue
    return stub


class _BootstrapStub:
    """A network-free stand-in for ``golden.bootstrap`` at the CLI boundary.

    Records ``(topic, snapshot)`` per call so a test can assert the caller-supplied
    worker snapshot is threaded through, and replays a canned candidate list.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._candidates: list[dict[str, object]] = [
            {"question": "what is an agent?", "reference_answer": "a loop", "citations": []}
        ]

    def returns(self, candidates: list[dict[str, object]]) -> "_BootstrapStub":
        self._candidates = candidates
        return self

    def __call__(
        self, store: object, topic: str, client: object, snapshot: str
    ) -> list[dict[str, object]]:
        self.calls.append((topic, snapshot))
        return list(self._candidates)


@pytest.fixture
def bootstrap_stub(monkeypatch: pytest.MonkeyPatch) -> _BootstrapStub:
    """Stub ``golden.bootstrap`` and the LLM client at the ``cli.eval`` boundary.

    Both names are bound in the ``knotica.cli.eval`` namespace at module top, so
    those bindings are the effective targets. The client is stubbed to a no-op
    factory so neither an API key nor the SDK is needed. The missing-key leg does
    NOT use this fixture — it exercises the real client's env-key guard.
    """
    stub = _BootstrapStub()
    monkeypatch.setattr("knotica.cli.eval.bootstrap", stub)
    monkeypatch.setattr("knotica.cli.eval.AnthropicClient", lambda: object())
    return stub


# ---------------------------------------------------------------------------
# Builders & assertion helpers (the "how" — behavior stays in the test bodies)
# ---------------------------------------------------------------------------


def _metrics_record(
    *,
    harness_version: str = DEFAULT_HARNESS_VERSION,
    scalar: float = 0.8137,
    generation: int = 2,
) -> MetricsRecord:
    """A record with distinctive, collision-free field values for render assertions."""
    return MetricsRecord(
        topic=SEED_TOPIC,
        timestamp="2026-07-15T12:00:00+00:00",
        generation=generation,
        harness_version=harness_version,
        scalar=scalar,
        components=MetricsComponents(
            qa_accuracy=0.92,
            citation_validity=0.71,
            lint_violations=0.13,
            token_cost=0.04,
        ),
        n_examples=24,
        corpus_ref="git:c0ffee1234567",
        artifact_ref="agentic-systems/.knotica/eval-manifest-2.json",
    )


def _seed_previous_metrics(vault: Path, topic: str, *, harness_version: str) -> None:
    """Write one prior ``metrics.jsonl`` record for ``topic`` in the source vault."""
    metrics_dir = vault / topic / ".knotica"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    line = _metrics_record(harness_version=harness_version, generation=1).to_json_line()
    (metrics_dir / "metrics.jsonl").write_text(line + "\n", encoding="utf-8")


def _missing_key_error() -> KnoticaError:
    """Capture the real no-credential error the harness raises (network-free).

    ``AnthropicClient()`` resolves its credential (OAuth-first) before importing
    the SDK, so with *both* ``CLAUDE_CODE_OAUTH_TOKEN`` and ``ANTHROPIC_API_KEY``
    absent it raises the exact house error the eval path would — no hardcoded
    wording, no network. The caller must have cleared both variables first.
    """
    try:
        AnthropicClient()
    except KnoticaError as raised:
        return raised
    raise AssertionError("AnthropicClient() must raise when ANTHROPIC_API_KEY is unset")


def _has_traceback(result: _CliResult) -> bool:
    return "Traceback (most recent call last)" in (result.out + result.err)


def _leaf_values(obj: object) -> list[object]:
    """Every scalar leaf (str / int / float) in a nested JSON structure."""
    if isinstance(obj, (str, int, float)):
        return [obj]
    if isinstance(obj, dict):
        return [value for child in obj.values() for value in _leaf_values(child)]
    if isinstance(obj, list):
        return [value for item in obj for value in _leaf_values(item)]
    return []


_HOUSE_ERRORS = [
    GoldenSetIntegrityError(SEED_TOPIC, "recorded sha256 does not match golden.jsonl"),
    GoldenSetContaminationError(SEED_TOPIC, ("what is an agent?",)),
    SpendCeilingExceededError(SEED_TOPIC, "run crossed the per-run token ceiling"),
    EvalRunError(SEED_TOPIC, "3 of 24 examples failed with an instrument error"),
]

_PARSE_ERRORS = [
    MalformedResponseError("worker completion was not the structured answer shape"),
    JudgeParseError("judge reply carried no parseable score: 'maybe'"),
]


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------


def test_eval_is_a_registered_command():
    assert "eval" in COMMAND_NAMES, "eval must be dispatchable through the CLI registry"


def test_eval_help_lists_its_flags(invoke):
    result = invoke("eval", "--help")

    assert result.code == EXIT_SUCCESS
    for flag in ("--topic", "--bootstrap", "--ref", "--json"):
        assert flag in result.out, f"eval --help must document {flag}; got {result.out!r}"


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


def test_successful_run_exits_zero_and_reports_the_scalar(
    vault_config: Path, run_eval_stub: _RunEvalStub, invoke
):
    run_eval_stub.returns(_metrics_record(scalar=0.8137))

    result = invoke("eval", "--topic", SEED_TOPIC)

    assert result.code == EXIT_SUCCESS, result.err
    assert "81" in result.out, f"the human table must report the 0.8137 scalar; got {result.out!r}"
    assert DEFAULT_HARNESS_VERSION in result.out, "the table must name the instrument fingerprint"


def test_unconfigured_vault_exits_not_configured_before_touching_the_harness(
    unconfigured_env: Path, run_eval_stub: _RunEvalStub, invoke
):
    """The config gate fires ahead of any harness work: exit 3, the uniform
    remediation on stderr, and ``run_eval`` never called."""
    result = invoke("eval", "--topic", SEED_TOPIC)

    assert result.code == EXIT_NOT_CONFIGURED, (
        f"unconfigured must exit 3; got {result.code}, stderr {result.err!r}"
    )
    assert UNCONFIGURED_MESSAGE in result.err
    assert run_eval_stub.calls == [], "the harness must not run before config is resolved"


def test_missing_golden_set_exits_with_the_dedicated_code_distinct_from_generic_errors(
    vault_config: Path, run_eval_stub: _RunEvalStub, invoke
):
    """A missing golden set gets its own exit code — the type-discrimination the
    harness needs (all golden/harness errors share one ``code``) made observable:
    the golden-absent code is neither the generic-error nor the not-configured one."""
    from knotica.cli.common import EXIT_NO_GOLDEN_SET

    assert EXIT_NO_GOLDEN_SET not in {EXIT_ERROR, EXIT_NOT_CONFIGURED}, (
        "the golden-absent exit code must be dedicated, not reused"
    )
    run_eval_stub.raises(GoldenSetMissingError(SEED_TOPIC))

    result = invoke("eval", "--topic", SEED_TOPIC)

    assert result.code == EXIT_NO_GOLDEN_SET, (
        f"a missing golden set must exit the dedicated code; got {result.code}"
    )
    assert "bootstrap" in result.err.lower(), "the message must point at `eval --bootstrap`"
    assert not _has_traceback(result)


# ---------------------------------------------------------------------------
# Typed-error rendering (house envelope, no stack trace, generic-error exit)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("house_error", _HOUSE_ERRORS, ids=lambda e: type(e).__name__)
def test_house_error_renders_message_and_fix_without_a_traceback(
    vault_config: Path, run_eval_stub: _RunEvalStub, invoke, house_error: KnoticaError
):
    run_eval_stub.raises(house_error)

    result = invoke("eval", "--topic", SEED_TOPIC)

    assert result.leaked is None, f"the CLI leaked {result.leaked!r} instead of rendering it"
    assert result.code == EXIT_ERROR, "a typed harness failure exits the generic-error code"
    assert house_error.message in result.err, "the house envelope must carry the error message"
    assert house_error.fix in result.err, "the house envelope must carry the actionable fix"
    assert not _has_traceback(result), "a house error must never surface a stack trace"


@pytest.mark.parametrize("parse_error", _PARSE_ERRORS, ids=lambda e: type(e).__name__)
def test_instrument_parse_error_is_rendered_not_crashed(
    vault_config: Path, run_eval_stub: _RunEvalStub, invoke, parse_error: ValueError
):
    """An escaped instrument (parse) failure is a ``ValueError``, not a house
    error — the CLI still renders it cleanly with an actionable fix, no crash."""
    run_eval_stub.raises(parse_error)

    result = invoke("eval", "--topic", SEED_TOPIC)

    assert result.leaked is None, f"the CLI leaked {result.leaked!r} instead of rendering it"
    assert result.code == EXIT_ERROR
    assert str(parse_error) in result.err, "the offending detail must reach the operator"
    assert "To fix:" in result.err, "even an escaped parse error must offer a next action"
    assert not _has_traceback(result)


# ---------------------------------------------------------------------------
# API-key handling: actionable when absent, never echoed when present
# ---------------------------------------------------------------------------


def test_absent_api_key_reports_the_eval_not_configured_message(
    vault_config: Path, run_eval_stub: _RunEvalStub, invoke, monkeypatch: pytest.MonkeyPatch
):
    # Both credentials must be absent for the not-configured error: this
    # environment exports a real CLAUDE_CODE_OAUTH_TOKEN that would otherwise
    # resolve to OAuth mode and never raise.
    monkeypatch.delenv(OAUTH_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    run_eval_stub.raises(_missing_key_error())

    result = invoke("eval", "--topic", SEED_TOPIC)

    assert result.code == EXIT_NOT_CONFIGURED, (
        f"an absent key is a not-configured condition; got {result.code}"
    )
    assert API_KEY_ENV_VAR in result.err, "the message must name the missing environment variable"
    assert "not configured" in result.err.lower(), "the grammar must match the not-configured shape"
    assert "To fix:" in result.err, "the message must state how to set the key"
    assert not _has_traceback(result)


@pytest.mark.parametrize(
    "outcome",
    ["success", "failure"],
    ids=["success-path", "error-path"],
)
def test_a_configured_api_key_value_never_appears_in_output(
    vault_config: Path,
    run_eval_stub: _RunEvalStub,
    invoke,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
):
    """With a real key in the environment, the sentinel value must not leak into
    stdout or stderr on any path (the CLI resolves the key only deep in the harness,
    never rendering it)."""
    monkeypatch.setenv(API_KEY_ENV_VAR, SENTINEL_API_KEY)
    if outcome == "success":
        run_eval_stub.returns(_metrics_record())
    else:
        run_eval_stub.raises(GoldenSetIntegrityError(SEED_TOPIC, "sha256 mismatch"))

    result = invoke("eval", "--topic", SEED_TOPIC, "--verbose")

    assert SENTINEL_API_KEY not in result.out, "the API key must never reach stdout"
    assert SENTINEL_API_KEY not in result.err, "the API key must never reach stderr"


@pytest.mark.parametrize(
    "outcome",
    ["success", "failure"],
    ids=["success-path", "error-path"],
)
def test_a_configured_oauth_token_value_never_appears_in_output(
    vault_config: Path,
    run_eval_stub: _RunEvalStub,
    invoke,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
):
    """The subscription OAuth token is just as secret as the API key: with it set in
    the environment, its sentinel value must never leak into stdout or stderr."""
    monkeypatch.setenv(OAUTH_TOKEN_ENV_VAR, SENTINEL_OAUTH_TOKEN)
    if outcome == "success":
        run_eval_stub.returns(_metrics_record())
    else:
        run_eval_stub.raises(GoldenSetIntegrityError(SEED_TOPIC, "sha256 mismatch"))

    result = invoke("eval", "--topic", SEED_TOPIC, "--verbose")

    assert SENTINEL_OAUTH_TOKEN not in result.out, "the OAuth token must never reach stdout"
    assert SENTINEL_OAUTH_TOKEN not in result.err, "the OAuth token must never reach stderr"


# ---------------------------------------------------------------------------
# Noisy metered-fallback warning surfaced as a visible stderr WARNING line
# ---------------------------------------------------------------------------

#: A distinctive fallback-warning message used to prove the CLI passes the library's
#: warning text through verbatim (its exact wording is pinned in the library tests).
_FALLBACK_MESSAGE = (
    "metered ANTHROPIC_API_KEY fallback: CLAUDE_CODE_OAUTH_TOKEN is unset, spending credits"
)


def test_metered_fallback_warning_is_surfaced_on_the_eval_path(
    vault_config: Path, invoke, monkeypatch: pytest.MonkeyPatch
):
    """When the library falls back to the metered key on the eval path, the CLI must
    surface the warning as a visible ``WARNING:`` stderr line so spend is never
    silent."""

    def _warn_then_return(topic: str, **kwargs: object) -> EvalRunResult:
        warnings.warn(_FALLBACK_MESSAGE, MeteredApiKeyFallbackWarning, stacklevel=2)
        return EvalRunResult(record=_metrics_record(), clone_root=STUB_CLONE_ROOT)

    monkeypatch.setattr("knotica.cli.eval.run_eval", _warn_then_return)

    result = invoke("eval", "--topic", SEED_TOPIC)

    assert result.code == EXIT_SUCCESS, result.err
    assert "WARNING:" in result.err, "the metered-fallback warning must show as a WARNING line"
    assert _FALLBACK_MESSAGE in result.err, "the CLI must pass the library warning text through"


def test_metered_fallback_warning_is_surfaced_on_the_bootstrap_path(
    vault_config: Path, run_eval_stub: _RunEvalStub, invoke, monkeypatch: pytest.MonkeyPatch
):
    """The bootstrap path resolves the credential inside ``AnthropicClient``; a
    metered fallback there must be surfaced by the CLI as a ``WARNING:`` line too."""

    def _warning_client_factory() -> object:
        warnings.warn(_FALLBACK_MESSAGE, MeteredApiKeyFallbackWarning, stacklevel=2)
        return object()

    monkeypatch.setattr("knotica.cli.eval.AnthropicClient", _warning_client_factory)
    monkeypatch.setattr(
        "knotica.cli.eval.bootstrap",
        lambda store, topic, client, snapshot: [
            {"question": "q", "reference_answer": "a", "citations": []}
        ],
    )

    result = invoke("eval", "--bootstrap", "--topic", SEED_TOPIC)

    assert result.code == EXIT_SUCCESS, result.err
    assert run_eval_stub.calls == [], "--bootstrap must not run the metrics eval"
    assert "WARNING:" in result.err, "the metered-fallback warning must show on the bootstrap path"
    assert _FALLBACK_MESSAGE in result.err, "the CLI must pass the library warning text through"


def test_oauth_mode_run_emits_no_metered_warning(
    vault_config: Path, run_eval_stub: _RunEvalStub, invoke
):
    """An ordinary run that resolves OAuth (or any run that does not fall back) emits
    no fallback warning: the CLI must not print a spurious ``WARNING:`` line."""
    run_eval_stub.returns(_metrics_record())

    result = invoke("eval", "--topic", SEED_TOPIC)

    assert result.code == EXIT_SUCCESS, result.err
    assert "WARNING:" not in result.err, "no fallback means no metered-spend WARNING line"


# ---------------------------------------------------------------------------
# Instrument-drift warning (cross-instrument scalar comparison guard)
# ---------------------------------------------------------------------------


def _drift_warned(result: _CliResult) -> bool:
    """The drift warning names ``harness_version`` on stderr; the success table
    prints ``harness version`` (space) to stdout, so stderr is an unambiguous probe."""
    return "harness_version" in result.err


def test_changed_instrument_warns_about_scalar_incomparability(
    vault_config: Path, template_vault: Path, run_eval_stub: _RunEvalStub, invoke
):
    _seed_previous_metrics(template_vault, SEED_TOPIC, harness_version="hv-fingerprint-previous")
    run_eval_stub.returns(_metrics_record(harness_version="hv-fingerprint-current"))

    result = invoke("eval", "--topic", SEED_TOPIC)

    assert result.code == EXIT_SUCCESS, result.err
    assert _drift_warned(result), (
        "a run whose harness_version differs from the previous record must warn"
    )


def test_matching_instrument_emits_no_drift_warning(
    vault_config: Path, template_vault: Path, run_eval_stub: _RunEvalStub, invoke
):
    _seed_previous_metrics(template_vault, SEED_TOPIC, harness_version="hv-fingerprint-steady")
    run_eval_stub.returns(_metrics_record(harness_version="hv-fingerprint-steady"))

    result = invoke("eval", "--topic", SEED_TOPIC)

    assert result.code == EXIT_SUCCESS, result.err
    assert not _drift_warned(result), "a matching instrument must not warn"


def test_first_ever_record_emits_no_drift_warning(
    vault_config: Path, template_vault: Path, run_eval_stub: _RunEvalStub, invoke
):
    """No prior ``metrics.jsonl`` means nothing to compare against — no warning."""
    run_eval_stub.returns(_metrics_record())

    result = invoke("eval", "--topic", SEED_TOPIC)

    assert result.code == EXIT_SUCCESS, result.err
    assert not _drift_warned(result), "a first-ever record has no previous instrument to warn about"


# ---------------------------------------------------------------------------
# --json envelope
# ---------------------------------------------------------------------------


def test_json_output_round_trips_and_carries_the_record_facts(
    vault_config: Path, run_eval_stub: _RunEvalStub, invoke
):
    record = _metrics_record()
    run_eval_stub.returns(record)

    result = invoke("eval", "--topic", SEED_TOPIC, "--json")

    assert result.code == EXIT_SUCCESS, result.err
    payload = json.loads(result.out)
    assert json.loads(json.dumps(payload)) == payload, "the --json envelope must round-trip"

    leaves = _leaf_values(payload)
    for expected in (
        record.scalar,
        record.harness_version,
        record.corpus_ref,
        record.artifact_ref,  # the manifest path
        record.components.qa_accuracy,
        record.components.citation_validity,
        record.components.lint_violations,
        record.components.token_cost,
    ):
        assert expected in leaves, f"the --json envelope must carry {expected!r}; leaves {leaves!r}"


# ---------------------------------------------------------------------------
# Artifact discoverability: the clone root + a resolvable manifest path so the
# eval commit is reviewable (the run committed to a throwaway clone, not the vault)
# ---------------------------------------------------------------------------


def test_successful_run_surfaces_the_clone_root_and_a_resolvable_manifest_path(
    vault_config: Path, run_eval_stub: _RunEvalStub, invoke
):
    """The clone the run committed to must be discoverable: the table prints the
    clone root, the manifest as a path resolvable against it (not the bare clone-
    relative ref), and a review-the-clone handoff — so a human can open the manifest
    and review the eval commit."""
    record = _metrics_record()
    run_eval_stub.returns(record)

    result = invoke("eval", "--topic", SEED_TOPIC)

    assert result.code == EXIT_SUCCESS, result.err
    assert str(STUB_CLONE_ROOT) in result.out, "the table must name the clone root the run wrote to"
    resolved_manifest = str(STUB_CLONE_ROOT / record.artifact_ref)
    assert resolved_manifest in result.out, (
        "the manifest must be a path resolvable against the clone root, not the bare ref"
    )
    assert "Review" in result.out, "the table must include the review-the-clone handoff hint"


def test_json_output_carries_the_clone_root_and_the_resolved_manifest_path(
    vault_config: Path, run_eval_stub: _RunEvalStub, invoke
):
    record = _metrics_record()
    run_eval_stub.returns(record)

    result = invoke("eval", "--topic", SEED_TOPIC, "--json")

    assert result.code == EXIT_SUCCESS, result.err
    payload = json.loads(result.out)
    assert payload["clone_root"] == str(STUB_CLONE_ROOT), (
        "the --json envelope must carry the clone root the run committed to"
    )
    assert payload["manifest_path"] == str(STUB_CLONE_ROOT / record.artifact_ref), (
        "the --json envelope must carry the manifest resolved against the clone root"
    )


# ---------------------------------------------------------------------------
# --bootstrap: stage golden candidates for review, never run the metrics eval
# ---------------------------------------------------------------------------


def test_bootstrap_stages_candidates_without_running_the_metrics_eval(
    vault_config: Path, run_eval_stub: _RunEvalStub, bootstrap_stub: _BootstrapStub, invoke
):
    """``--bootstrap`` is the golden-set path: it stages candidates for review and
    must never run the metrics eval. The no-eval-run assertion is durable; the exit
    code and the staged-candidates handoff now assert the real landed behavior."""
    bootstrap_stub.returns(
        [
            {"question": "q1", "reference_answer": "a1", "citations": []},
            {"question": "q2", "reference_answer": "a2", "citations": ["k"]},
        ]
    )

    result = invoke("eval", "--bootstrap", "--topic", SEED_TOPIC)

    assert run_eval_stub.calls == [], "--bootstrap must not run the metrics eval"
    assert result.leaked is None, f"--bootstrap must not crash; leaked {result.leaked!r}"
    assert result.code == EXIT_SUCCESS, result.err
    assert "staged candidates" in result.out, "the handoff must report the staged count"
    assert golden_staging_path(SEED_TOPIC) in result.out, "the handoff must name the staging file"
    assert not _has_traceback(result)


def test_bootstrap_threads_the_worker_snapshot_to_the_generator(
    vault_config: Path, bootstrap_stub: _BootstrapStub, invoke
):
    """The CLI passes the resolved worker snapshot (not a hardcoded default) to the
    synthesis call, so a ``--worker-snapshot`` override would reach the generator."""
    result = invoke("eval", "--bootstrap", "--topic", SEED_TOPIC)

    assert result.code == EXIT_SUCCESS, result.err
    assert bootstrap_stub.calls == [(SEED_TOPIC, WORKER_SNAPSHOT)], (
        "bootstrap must be called once with the topic and the pinned worker snapshot"
    )


def test_bootstrap_absent_api_key_reports_the_eval_not_configured_message(
    vault_config: Path, run_eval_stub: _RunEvalStub, invoke, monkeypatch: pytest.MonkeyPatch
):
    """With neither credential set the bootstrap path reports the clean
    not-configured error (exit 3) before any synthesis is attempted — the real
    client's env-credential guard fires before the generator is ever reached."""
    monkeypatch.delenv(OAUTH_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)

    def _never_reached(*args: object, **kwargs: object) -> object:
        raise AssertionError("bootstrap must not run when ANTHROPIC_API_KEY is absent")

    monkeypatch.setattr("knotica.cli.eval.bootstrap", _never_reached)

    result = invoke("eval", "--bootstrap", "--topic", SEED_TOPIC)

    assert result.code == EXIT_NOT_CONFIGURED, (
        f"an absent key on the bootstrap path is a not-configured condition; got {result.code}"
    )
    assert API_KEY_ENV_VAR in result.err, "the message must name the missing environment variable"
    assert "not configured" in result.err.lower(), "the grammar must match the not-configured shape"
    assert "To fix:" in result.err, "the message must state how to set the key"
    assert run_eval_stub.calls == [], "--bootstrap never runs the metrics eval"
    assert not _has_traceback(result)


def test_bootstrap_json_reports_the_staging_path_and_count(
    vault_config: Path, bootstrap_stub: _BootstrapStub, invoke
):
    """``--bootstrap --json`` emits a stable envelope carrying the staging path and
    candidate count, so a script can find the file to review."""
    bootstrap_stub.returns(
        [
            {"question": "q1", "reference_answer": "a1", "citations": []},
            {"question": "q2", "reference_answer": "a2", "citations": []},
            {"question": "q3", "reference_answer": "a3", "citations": []},
        ]
    )

    result = invoke("eval", "--bootstrap", "--topic", SEED_TOPIC, "--json")

    assert result.code == EXIT_SUCCESS, result.err
    payload = json.loads(result.out)
    assert payload["topic"] == SEED_TOPIC
    assert payload["staging_ref"] == golden_staging_path(SEED_TOPIC)
    assert payload["n_candidates"] == 3

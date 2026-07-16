"""Behavioral spec for the eval-harness orchestrator (``run_eval``).

``run_eval`` is the top of the evaluator: it clones an already-resolved *source*
vault to a throwaway frozen corpus, builds the golden devset, runs the vault's own
headless query baseline through ``dspy.Evaluate`` with the bound metric, blends the
per-example qualities with lint-cleanliness and a token-cost discount into one
stable scalar, and appends exactly one ``MetricsRecord`` line to the *clone* through
the single mutation path -- leaving the live vault byte-identical. Every collaborator
below it is already built and separately pinned; this suite pins the *composition*.

The behaviours pinned here, each an observable outcome:

- **Happy path, end to end (offline).** A seeded source + a frozen golden set →
  ``run_eval`` → exactly one ``knotica(eval): <topic> — generation N`` commit on the
  clone (frozen grammar), a ``MetricsRecord`` whose ``scalar`` is bounded and whose
  four ``components`` are populated, ``corpus_ref`` pinned as ``git:<sha>``, and an
  ``artifact_ref`` that resolves to a committed, readable per-run manifest.
- **The source is untouched.** HEAD, commit count, and porcelain status of the
  source vault are identical before and after; the metrics line landed on the clone
  only.
- **Warm-cache reproducibility.** A second run over the same frozen inputs
  reproduces the scalar bit-for-bit, and -- with the response cache shared -- makes
  *zero* additional judge LLM calls (the frozen-corpus re-run is free).
- **Live-vault escape guard.** A write target equal to the source root is refused
  before any write; the source is never mutated.
- **Spend ceiling.** A tiny configured token ceiling hard-aborts the run with a
  typed, actionable error before any metrics commit lands on the clone.
- **No key leakage.** With a sentinel ``ANTHROPIC_API_KEY`` in the environment, the
  manifest bytes and every working-tree file the run wrote contain no sentinel.
- **Disjointness is enforced.** A question shared between ``golden.jsonl`` and the
  flywheel ``qa.jsonl`` aborts the run with the typed contamination error -- proof
  the held-out-split guard is actually called on the harness load path.
- **Absent golden set.** A topic with no ``golden.jsonl`` surfaces the typed
  missing-set error (the CLI's dedicated-exit-code seam), never a bare crash.
- **Instrument failure stays visible.** An unparseable judge response aborts the run
  rather than being folded into the scalar as a silent ``0.0``.
- **The DSPy leg is exercised now.** The run drives a real ``dspy.Evaluate`` over the
  bound metric with no ``dspy.settings.lm`` configured, scoring every devset example.

Zero network throughout: a routing fake ``LLMClient`` (worker vs judge dispatched by
system-prompt) drives the worker synthesis and the judge grading offline, and an
autouse guard turns any socket creation into a loud failure.

================================================================================
NOTES (interface facts and one reconciled contract)

* **Explicit source.** ``run_eval`` takes the already-resolved ``source_root`` as a
  keyword (config resolution lives in the CLI layer, per the SYSTEMS_PLAN data flow),
  so this suite passes a ``tmp_path`` vault directly -- no config fixture needed.
* **Clone visibility.** ``run_eval`` clones the source *into* ``work_root`` (which
  must not pre-exist), so the frozen clone tree is inspectable: the one eval commit,
  the committed manifest, and the no-key-leak sweep all read it.
* **Config overrides** thread through a ``HarnessConfig`` object (``config=``), so the
  spend-ceiling case passes ``DEFAULT_CONFIG.with_overrides(max_total_tokens=1)``.
* **Live-vault guard fires before the clone.** The purpose-built typed refusal
  (``LiveVaultTargetError``) is checked *before* any clone, so a *direct*
  ``work_root == source_root`` collision surfaces that typed error -- never a generic
  git clone-into-existing-dir failure -- while still leaving the source byte-identical.
  This suite pins both halves: the typed refusal and the untouched source.

Written concurrently with ``evals/harness.py`` (disjoint files).
================================================================================
"""

import hashlib
import json
import socket
from collections.abc import Iterable, Sequence
from pathlib import Path

import pytest

from knotica.core.records import MetricsRecord, QARecord
from knotica.evals.cache import ResponseCache
from knotica.evals.config import DEFAULT_CONFIG, HarnessConfig
from knotica.evals.golden import GoldenSetContaminationError, GoldenSetMissingError
from knotica.evals.harness import LiveVaultTargetError, run_eval
from knotica.evals.llm import Completion, TokenUsage
from support.vault import (
    git_commit_count,
    git_commit_subjects,
    git_head_sha,
    git_status_porcelain,
    parse_knotica_commit,
    run_git,
)

# ``dspy`` lives in the eval-only dependency group; skip this whole module (not
# abort collection) when the base test env has not installed it, so the plain
# ``uv run pytest`` loop still collects the rest of the suite.
dspy = pytest.importorskip("dspy")

#: The topic the template vault ships (entity pages + one stored source whose key
#: ``wang2024awm`` the runner's retrieval and the citation check both resolve).
TOPIC = "agentic-systems"

#: Vault-relative path the harness appends its per-generation eval history to.
METRICS_PATH = f"{TOPIC}/.knotica/metrics.jsonl"

#: The stored-source citation key that resolves under ``sources/<topic>/`` in the
#: template -- both the worker's cited answer and the golden reference use it, so
#: citation validity is a deterministic ``1.0``.
RESOLVING_CITATION = "wang2024awm"

#: A distinctive fake API key. It is never *read* (a fake ``LLMClient`` answers), so
#: its presence in any run artifact would be a genuine leak, not an expected write.
SENTINEL_API_KEY = "sk-ant-SENTINEL-DO-NOT-LEAK-abcdef0123456789"

#: The subscription OAuth-token sentinel -- the preferred credential is just as
#: secret; its presence in any run artifact would be a genuine leak.
SENTINEL_OAUTH_TOKEN = "sk-ant-oat01-SENTINEL-DO-NOT-LEAK-abcdef0123456789"

#: The judge system prompt's stable signature phrase -- the routing fake's
#: worker-vs-judge discriminator (the worker system prompt never contains it).
_JUDGE_MARKER = "impartial grader"


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any socket creation in this module fail loudly.

    The harness reaches the network only through the injected ``LLMClient``, and the
    routing fake never does; ``dspy.Evaluate`` makes no LLM call on this path, and
    git/ripgrep use OS pipes rather than ``socket.socket``. Replacing ``socket.socket``
    turns any accidental live connection into a hard failure. ``dspy`` is imported at
    module load (before this fixture), so its ``ssl`` init is unaffected.
    """

    def _blocked(*args: object, **kwargs: object) -> object:
        raise RuntimeError("network access is forbidden in the eval harness test suite")

    monkeypatch.setattr(socket, "socket", _blocked)


# --------------------------------------------------------------------------- #
# Routing fake LLM client (the "how" -- kept out of the test bodies)
# --------------------------------------------------------------------------- #


class _RoutingLLMClient:
    """A zero-network ``LLMClient`` that answers worker vs judge calls differently.

    The harness threads one ``LLMClient`` into both the baseline runner (which asks
    for a structured ``{"answer", "citations"}`` synthesis) and the judge (which asks
    for a bounded ``{"score"}`` grade). A single canned completion cannot serve both,
    so this fake dispatches on the system prompt: the judge's system prompt carries a
    stable signature phrase the worker's never does. Worker and judge invocations are
    counted separately, so a test can prove a warm cache made zero *judge* calls even
    though the (uncached) runner ran again.
    """

    def __init__(self, *, worker: Completion, judge: Completion) -> None:
        self._worker = worker
        self._judge = judge
        self.worker_calls = 0
        self.judge_calls = 0

    def complete(
        self,
        *,
        snapshot: str,
        system: str,
        messages: list[object],
        temperature: float = 0.0,
        max_tokens: int,
    ) -> Completion:
        if _JUDGE_MARKER in system:
            self.judge_calls += 1
            return self._judge
        self.worker_calls += 1
        return self._worker


def _usage(*, input_tokens: int = 120, output_tokens: int = 60) -> TokenUsage:
    """Synthetic per-call token usage -- the ground truth for the scalar's cost term."""
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _worker_completion(
    *,
    answer: str = "Agentic memory distils reusable routines from past episodes.",
    citations: Sequence[str] = (RESOLVING_CITATION,),
) -> Completion:
    """A canned worker synthesis in the runner's parsed ``{"answer","citations"}`` shape."""
    payload = json.dumps({"answer": answer, "citations": list(citations)})
    return Completion(text=payload, usage=_usage())


def _judge_completion(*, score: float = 0.8) -> Completion:
    """A canned judge grade in the parsed ``{"score"}`` shape (median of N identical = score)."""
    payload = json.dumps({"reasoning": "the candidate matches the reference", "score": score})
    return Completion(text=payload, usage=_usage(input_tokens=200, output_tokens=20))


def _unparseable_judge_completion() -> Completion:
    """A judge response carrying no parseable score -- an instrument failure, not a grade."""
    return Completion(text="the grader could not decide", usage=_usage())


def _routing_fake(*, judge: Completion | None = None) -> _RoutingLLMClient:
    """The default routing fake: a valid worker answer and a valid (or supplied) judge grade."""
    return _RoutingLLMClient(
        worker=_worker_completion(), judge=judge if judge is not None else _judge_completion()
    )


# --------------------------------------------------------------------------- #
# Source-vault seeding (a frozen golden set committed onto the source)
# --------------------------------------------------------------------------- #


def _qa_record(*, record_id: str, query: str) -> QARecord:
    """A valid held-out golden ``QARecord`` whose reference cites a resolving source."""
    return QARecord(
        id=record_id,
        topic=TOPIC,
        created="2026-07-16",
        query=query,
        pages_used=("agent-workflow-memory",),
        answer="Reusable task strategies persisted and reused across episodes.",
        citations=(RESOLVING_CITATION,),
        verdict="good",
        corrected_answer=None,
        source="curate_example",
        model="test-worker-snapshot-00000000",
    )


#: Three distinct golden questions -> three distinct judge input tuples, enough to
#: exercise the mean/median composition without the real ~20-30 floor.
_GOLDEN_QUERIES = (
    "What distinguishes an agentic workflow memory?",
    "How are induced workflows reused across tasks?",
    "What grounds a cited answer in the wiki?",
)


def _golden_records(queries: Sequence[str] = _GOLDEN_QUERIES) -> list[QARecord]:
    """A synthetic golden devset: one record per distinct question."""
    return [
        _qa_record(record_id=f"golden-{index:04d}", query=query)
        for index, query in enumerate(queries)
    ]


def _jsonl_body(records: Iterable[QARecord]) -> str:
    """Render records as a ``.jsonl`` body: one JSON line each, newline-terminated."""
    return "".join(record.to_json_line() + "\n" for record in records)


def _manifest_body(*, golden_text: str, size: int, split: str = "held_out") -> str:
    """A sibling ``MANIFEST.json`` content-addressing the frozen golden bytes."""
    payload = {
        "sha256": hashlib.sha256(golden_text.encode("utf-8")).hexdigest(),
        "version": "2026-07-16",
        "source": "synthetic",
        "split": split,
        "size": size,
    }
    return json.dumps(payload)


def _seed_source(
    vault: Path,
    golden: list[QARecord],
    *,
    qa_records: list[QARecord] | None = None,
) -> None:
    """Plant and commit a frozen golden set on the source vault (so the clone gets it).

    ``git clone`` copies only committed content, so the golden set is committed here
    -- the harness clones the source at HEAD and reads the golden set from the clone.
    An optional ``qa_records`` plants the flywheel ``qa.jsonl`` used by the
    contamination case.
    """
    datasets = vault / TOPIC / ".knotica" / "datasets"
    datasets.mkdir(parents=True, exist_ok=True)

    golden_text = _jsonl_body(golden)
    (datasets / "golden.jsonl").write_text(golden_text, encoding="utf-8")
    (datasets / "MANIFEST.json").write_text(
        _manifest_body(golden_text=golden_text, size=len(golden)), encoding="utf-8"
    )
    if qa_records is not None:
        (datasets / "qa.jsonl").write_text(_jsonl_body(qa_records), encoding="utf-8")

    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "eval: seed frozen golden set for harness tests")


@pytest.fixture
def seeded_source(template_vault: Path) -> Path:
    """A source vault carrying a committed, valid golden set for ``TOPIC``.

    The default golden set is seeded and committed so ``run_eval(TOPIC,
    source_root=...)`` clones it and finds a loadable devset.
    """
    _seed_source(template_vault, _golden_records())
    return template_vault


# --------------------------------------------------------------------------- #
# The single call seam + inspection helpers
# --------------------------------------------------------------------------- #


def _run_eval(
    topic: str,
    *,
    source_root: Path,
    llm_client: _RoutingLLMClient,
    work_root: Path,
    cache: ResponseCache | None = None,
    config: HarnessConfig | None = None,
) -> MetricsRecord:
    """Invoke ``run_eval`` with an explicit source, the clone target, and (optional) knobs.

    Centralized so a plumbing detail changes in one place without touching a single
    behavioural assertion.

    A fresh in-memory ``ResponseCache`` is passed by default so no test touches the
    harness's global on-disk default cache (content-addressed by ``corpus_sha``,
    which fast identical-golden tests can collide on) -- keeping every test hermetic
    and parallel-safe. The warm-cache case passes its own shared cache to override.

    ``run_eval`` returns an ``EvalRunResult`` (the record plus its clone root); this
    helper unwraps ``.record`` centrally so the behavioural cases keep asserting on
    the record directly. The clone-root leg is pinned by the tests that call
    ``run_eval`` without this helper.
    """
    kwargs: dict[str, object] = {
        "source_root": source_root,
        "llm_client": llm_client,
        "work_root": work_root,
        "cache": cache if cache is not None else ResponseCache(),
    }
    if config is not None:
        kwargs["config"] = config
    return run_eval(topic, **kwargs).record


def _run_eval_error(topic: str, **kwargs: object) -> BaseException:
    """Run ``run_eval`` expecting a failure; return the raised error, else fail loudly.

    A normal return here is the "silent success" the failure cases forbid, so it is an
    assertion failure, not a returned value.
    """
    try:
        result = _run_eval(topic, **kwargs)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - the harness's error taxonomy is asserted, not its base
        return exc
    raise AssertionError(
        f"run_eval({topic!r}) must fail here, but returned {result!r} -- a failing eval "
        "must never complete as a silent success"
    )


def _eval_subjects(clone: Path) -> list[str]:
    """The clone's commit subjects that match the frozen eval grammar (newest first)."""
    return [
        subject
        for subject in git_commit_subjects(clone)
        if (parsed := parse_knotica_commit(subject)) is not None and parsed["op"] == "eval"
    ]


def _read_manifest(clone: Path, record: MetricsRecord) -> dict[str, object]:
    """Read and parse the per-run manifest the record's ``artifact_ref`` points at."""
    assert record.artifact_ref is not None, "the record must reference a per-run manifest"
    manifest_path = clone / record.artifact_ref
    assert manifest_path.exists(), f"artifact_ref must resolve to an existing file: {manifest_path}"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _git_tracked(clone: Path, relpath: str) -> bool:
    """Whether ``relpath`` is a committed (tracked) file on the clone."""
    return bool(run_git(clone, "ls-files", "--", relpath).strip())


def _files_containing(root: Path, needle: bytes) -> list[str]:
    """Working-tree files (excluding ``.git``) whose bytes contain ``needle``."""
    hits: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if needle in data:
            hits.append(str(path.relative_to(root)))
    return hits


def _any_list_of_length(obj: object, length: int) -> bool:
    """Whether any list nested anywhere in ``obj`` has exactly ``length`` items."""
    if isinstance(obj, list):
        return len(obj) == length or any(_any_list_of_length(item, length) for item in obj)
    if isinstance(obj, dict):
        return any(_any_list_of_length(value, length) for value in obj.values())
    return False


def _error_text(exc: BaseException) -> str:
    """The actionable text of a raised error, lowercased, across error shapes."""
    parts = (getattr(exc, "message", ""), getattr(exc, "fix", ""), str(exc))
    return " ".join(part for part in parts if part).lower()


def _mentions_any(text: str, keywords: Iterable[str]) -> bool:
    """Whether any keyword appears in ``text`` (loop kept out of the test bodies)."""
    return any(keyword.lower() in text for keyword in keywords)


# --------------------------------------------------------------------------- #
# Happy path -- one eval commit on the clone, a bounded four-component scalar
# --------------------------------------------------------------------------- #


def test_run_eval_lands_exactly_one_eval_commit_on_the_clone(
    seeded_source: Path, tmp_path: Path
) -> None:
    clone = tmp_path / "eval-clone"

    _run_eval(TOPIC, source_root=seeded_source, llm_client=_routing_fake(), work_root=clone)

    subjects = _eval_subjects(clone)
    assert len(subjects) == 1, "one eval run is exactly one knotica(eval) commit on the clone"
    parsed = parse_knotica_commit(subjects[0])
    assert parsed is not None and parsed["op"] == "eval", "the commit uses the frozen eval grammar"
    assert parsed["topic"] == TOPIC, "the commit records the evaluated topic"
    assert parsed["title"].startswith("generation"), (
        "the eval commit title carries the generation slot (generation N)"
    )


def test_run_eval_returns_a_bounded_scalar_with_four_populated_components(
    seeded_source: Path, tmp_path: Path
) -> None:
    record = _run_eval(
        TOPIC, source_root=seeded_source, llm_client=_routing_fake(), work_root=tmp_path / "clone"
    )

    assert 0.0 <= record.scalar <= 1.0, "the emitted scalar is bounded to [0, 1]"
    components = record.components
    assert 0.0 < components.qa_accuracy <= 1.0, (
        "qa_accuracy reflects the judge's positive grade, not a defaulted zero"
    )
    assert components.citation_validity == pytest.approx(1.0), (
        "every cited key resolves to a stored source, so citation validity is a perfect 1.0"
    )
    assert components.lint_violations >= 0.0, (
        "the raw lint-violation count is a non-negative number"
    )
    assert 0.0 <= components.token_cost <= 1.0, "the token-cost discount multiplier is in [0, 1]"


def test_run_eval_pins_the_corpus_ref_to_the_frozen_clone_snapshot(
    seeded_source: Path, tmp_path: Path
) -> None:
    record = _run_eval(
        TOPIC, source_root=seeded_source, llm_client=_routing_fake(), work_root=tmp_path / "clone"
    )

    assert record.corpus_ref.startswith("git:"), "corpus_ref pins the evaluated corpus as git:<sha>"
    assert record.topic == TOPIC, "the record names the evaluated topic"
    assert record.generation == 1, (
        "the first eval against a source with no metrics history is generation 1 (1-indexed)"
    )


def test_run_eval_writes_a_committed_manifest_the_artifact_ref_points_at(
    seeded_source: Path, tmp_path: Path
) -> None:
    clone = tmp_path / "eval-clone"

    record = _run_eval(
        TOPIC, source_root=seeded_source, llm_client=_routing_fake(), work_root=clone
    )

    assert record.artifact_ref is not None, "the record references a per-run manifest"
    manifest = _read_manifest(clone, record)
    assert isinstance(manifest, dict), "the manifest is a JSON object of reproducibility columns"
    assert _git_tracked(clone, record.artifact_ref), (
        "artifact_ref must point at a committed file in the clone's tree, not untracked scratch"
    )


def test_the_committed_metrics_line_round_trips_to_the_returned_record(
    seeded_source: Path, tmp_path: Path
) -> None:
    clone = tmp_path / "eval-clone"

    record = _run_eval(
        TOPIC, source_root=seeded_source, llm_client=_routing_fake(), work_root=clone
    )

    stored = (clone / METRICS_PATH).read_text(encoding="utf-8")
    lines = [line for line in stored.splitlines() if line.strip()]
    assert len(lines) == 1, "exactly one metrics record was appended to the clone"
    parsed = MetricsRecord.from_json_line(lines[0])
    assert parsed == record, "the on-disk record is exactly the record run_eval returned"


# --------------------------------------------------------------------------- #
# The source vault is untouched -- the eval has zero side effect on live content
# --------------------------------------------------------------------------- #


def test_the_source_vault_is_byte_identical_after_an_eval_run(
    seeded_source: Path, tmp_path: Path
) -> None:
    head_before = git_head_sha(seeded_source)
    count_before = git_commit_count(seeded_source)
    status_before = git_status_porcelain(seeded_source)

    _run_eval(
        TOPIC, source_root=seeded_source, llm_client=_routing_fake(), work_root=tmp_path / "clone"
    )

    assert git_head_sha(seeded_source) == head_before, "the source HEAD must not move"
    assert git_commit_count(seeded_source) == count_before, "the source gains no commit"
    assert git_status_porcelain(seeded_source) == status_before, "the source tree is unchanged"
    assert not (seeded_source / METRICS_PATH).exists(), (
        "the metrics write landed on the clone only, never on the live source vault"
    )


# --------------------------------------------------------------------------- #
# Warm-cache reproducibility -- same scalar, zero additional judge calls
# --------------------------------------------------------------------------- #


def test_a_second_run_reproduces_the_scalar_bit_for_bit(
    seeded_source: Path, tmp_path: Path
) -> None:
    fake = _routing_fake()

    first = _run_eval(
        TOPIC, source_root=seeded_source, llm_client=fake, work_root=tmp_path / "clone-1"
    )
    second = _run_eval(
        TOPIC, source_root=seeded_source, llm_client=fake, work_root=tmp_path / "clone-2"
    )

    assert second.scalar == first.scalar, (
        "the same frozen inputs must reproduce the scalar bit-for-bit (determinism)"
    )


def test_a_warm_shared_cache_makes_zero_additional_judge_calls(
    seeded_source: Path, tmp_path: Path
) -> None:
    fake = _routing_fake()
    cache = ResponseCache()

    _run_eval(
        TOPIC,
        source_root=seeded_source,
        llm_client=fake,
        work_root=tmp_path / "clone-1",
        cache=cache,
    )
    judge_calls_after_cold_run = fake.judge_calls
    assert judge_calls_after_cold_run > 0, (
        "non-vacuity: the judge must actually be called on the first (cold) run"
    )

    _run_eval(
        TOPIC,
        source_root=seeded_source,
        llm_client=fake,
        work_root=tmp_path / "clone-2",
        cache=cache,
    )

    assert fake.judge_calls == judge_calls_after_cold_run, (
        "a warm shared cache serves every judge tuple from storage -- the frozen re-run "
        "makes zero additional judge LLM calls"
    )


# --------------------------------------------------------------------------- #
# Live-vault escape guard -- a write target equal to the source is refused
# --------------------------------------------------------------------------- #


def test_a_write_target_equal_to_the_source_raises_the_typed_live_vault_refusal(
    seeded_source: Path,
) -> None:
    # A path-confusion bug that made the harness write to the source instead of a
    # throwaway clone would pollute the real wiki. The guard fires BEFORE the clone,
    # so a target == source collision surfaces the purpose-built typed refusal (not a
    # generic git clone-into-existing-dir failure) and leaves the source
    # byte-identical -- never a metrics commit on the live vault.
    head_before = git_head_sha(seeded_source)
    count_before = git_commit_count(seeded_source)

    error = _run_eval_error(
        TOPIC, source_root=seeded_source, llm_client=_routing_fake(), work_root=seeded_source
    )

    assert isinstance(error, LiveVaultTargetError), (
        "a write target equal to the source must surface the typed live-vault refusal "
        f"raised before any clone, not a generic git error; got {type(error).__name__}"
    )
    assert git_head_sha(seeded_source) == head_before, (
        "the source must gain no commit from a refusal"
    )
    assert git_commit_count(seeded_source) == count_before, "the source commit count is unchanged"
    assert not (seeded_source / METRICS_PATH).exists(), (
        "no metrics line may land on the source when the write target collides with it"
    )


# --------------------------------------------------------------------------- #
# Spend ceiling -- a tiny token budget hard-aborts before committing metrics
# --------------------------------------------------------------------------- #


def test_a_tiny_token_ceiling_hard_aborts_before_committing_metrics(
    seeded_source: Path, tmp_path: Path
) -> None:
    clone = tmp_path / "eval-clone"

    error = _run_eval_error(
        TOPIC,
        source_root=seeded_source,
        llm_client=_routing_fake(),
        work_root=clone,
        config=DEFAULT_CONFIG.with_overrides(max_total_tokens=1),
    )

    assert _mentions_any(
        _error_text(error), {"token", "ceiling", "budget", "limit", "abort", "spend"}
    ), (
        "an exceeded spend ceiling must hard-abort with an actionable message naming the limit; "
        f"got: {_error_text(error)!r}"
    )
    if clone.exists() and (clone / ".git").exists():
        assert _eval_subjects(clone) == [], (
            "no metrics commit may land on the clone once the spend ceiling trips"
        )
    assert not (seeded_source / METRICS_PATH).exists(), "the source is untouched by an aborted run"


def test_the_per_run_manifest_records_the_cache_hit_rate(
    seeded_source: Path, tmp_path: Path
) -> None:
    clone = tmp_path / "eval-clone"

    record = _run_eval(
        TOPIC, source_root=seeded_source, llm_client=_routing_fake(), work_root=clone
    )

    manifest = _read_manifest(clone, record)
    # A silent cache failure (unstable keys -> 100% miss -> surprise spend) is made
    # visible by recording the run's hit-rate in the manifest.
    assert "hit" in json.dumps(manifest).lower(), (
        "the per-run manifest records the response-cache hit-rate so a silent cache failure shows"
    )


# --------------------------------------------------------------------------- #
# No credential leakage -- neither credential sentinel appears in any run artifact,
# and the manifest records the (non-secret) resolved auth mode
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "env_var, sentinel",
    [
        ("ANTHROPIC_API_KEY", SENTINEL_API_KEY),
        ("CLAUDE_CODE_OAUTH_TOKEN", SENTINEL_OAUTH_TOKEN),
    ],
    ids=["api-key", "oauth-token"],
)
def test_no_run_artifact_contains_the_credential_sentinel(
    seeded_source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_var: str,
    sentinel: str,
) -> None:
    monkeypatch.setenv(env_var, sentinel)
    clone = tmp_path / "eval-clone"

    record = _run_eval(
        TOPIC, source_root=seeded_source, llm_client=_routing_fake(), work_root=clone
    )

    manifest = _read_manifest(clone, record)
    assert sentinel not in json.dumps(manifest), "the manifest must not capture the credential"
    leaked = _files_containing(clone, sentinel.encode("utf-8"))
    assert leaked == [], f"the {env_var} sentinel leaked into run artifacts: {leaked}"


def test_the_manifest_records_the_resolved_auth_mode(seeded_source: Path, tmp_path: Path) -> None:
    # The per-run manifest carries an ``auth_mode`` column so a reader knows whether
    # ``cost_usd`` is a real bill (``api_key``) or notional (``oauth``). An injected
    # fake resolves no real credential, so the recorded mode is honestly ``None`` --
    # the field's *presence* is what the manifest contract guarantees.
    clone = tmp_path / "eval-clone"

    record = _run_eval(
        TOPIC, source_root=seeded_source, llm_client=_routing_fake(), work_root=clone
    )

    manifest = _read_manifest(clone, record)
    assert "auth_mode" in manifest, "the manifest must record the resolved auth mode column"
    assert manifest["auth_mode"] is None, "an injected fake client resolves no real auth mode"


# --------------------------------------------------------------------------- #
# Disjointness -- a shared golden/flywheel question aborts the run
# --------------------------------------------------------------------------- #


def test_a_golden_question_shared_with_the_flywheel_aborts_the_run(
    template_vault: Path, tmp_path: Path
) -> None:
    # Proof the held-out-split guard is actually called on the harness load path: a
    # question shared between golden.jsonl and the flywheel qa.jsonl is contamination.
    shared_question = _GOLDEN_QUERIES[0]
    golden = _golden_records()
    contaminating_trainset = [_qa_record(record_id="qa-0001", query=shared_question)]
    _seed_source(template_vault, golden, qa_records=contaminating_trainset)

    error = _run_eval_error(
        TOPIC, source_root=template_vault, llm_client=_routing_fake(), work_root=tmp_path / "clone"
    )

    assert isinstance(error, GoldenSetContaminationError), (
        "a golden question shared with the flywheel trainset must raise the typed contamination "
        f"error (proving verify_disjoint_from_trainset is called), got {type(error).__name__}"
    )
    assert not (template_vault / METRICS_PATH).exists(), "a contaminated set produces no metrics"


# --------------------------------------------------------------------------- #
# Absent golden set -- the typed missing-set error propagates (the exit-code seam)
# --------------------------------------------------------------------------- #


def test_a_topic_without_a_golden_set_raises_the_typed_missing_error(
    template_vault: Path, tmp_path: Path
) -> None:
    # The template ships the topic but no golden.jsonl; nothing is seeded here.
    error = _run_eval_error(
        TOPIC, source_root=template_vault, llm_client=_routing_fake(), work_root=tmp_path / "clone"
    )

    assert isinstance(error, GoldenSetMissingError), (
        "a topic with no golden set must surface the typed missing-set error the CLI maps to its "
        f"dedicated exit code, not a bare crash, got {type(error).__name__}"
    )


# --------------------------------------------------------------------------- #
# Instrument failure stays visible -- an unparseable judge is never a silent 0.0
# --------------------------------------------------------------------------- #


def test_an_unparseable_judge_response_surfaces_instead_of_a_silent_zero(
    seeded_source: Path, tmp_path: Path
) -> None:
    # A judge response with no parseable score is an instrument failure, not a
    # legitimate low grade. The harness aborts rather than folding it into the scalar
    # as a silent 0.0 -- so the run must fail, not return a record built from a mask.
    fake = _routing_fake(judge=_unparseable_judge_completion())

    error = _run_eval_error(
        TOPIC, source_root=seeded_source, llm_client=fake, work_root=tmp_path / "clone"
    )

    assert _mentions_any(_error_text(error), {"score", "judge", "instrument", "trustworthy"}), (
        "an unparseable judge response must surface as an instrument failure, not be folded into "
        f"the scalar as a silent 0.0; got {type(error).__name__}: {_error_text(error)!r}"
    )
    assert not (seeded_source / METRICS_PATH).exists(), "an untrustworthy run commits no metrics"


# --------------------------------------------------------------------------- #
# The DSPy leg is exercised now -- real dspy.Evaluate, no LM, every example scored
# --------------------------------------------------------------------------- #


def test_the_run_uses_dspy_evaluate_with_no_language_model_configured(
    seeded_source: Path, tmp_path: Path
) -> None:
    # The design's load-bearing claim: the metric leg runs through real dspy.Evaluate
    # with dspy.settings.lm unset -- BaselineProgram calls only our own runner, never
    # dspy.Predict/LM, so a full offline pass needs no configured language model.
    assert dspy.settings.lm is None, "precondition: no dspy LM is configured"

    record = _run_eval(
        TOPIC, source_root=seeded_source, llm_client=_routing_fake(), work_root=tmp_path / "clone"
    )

    assert isinstance(record, MetricsRecord), "the run completes through dspy.Evaluate offline"
    assert dspy.settings.lm is None, "the harness configures no dspy LM as a side effect"


def test_the_run_scores_every_devset_example(template_vault: Path, tmp_path: Path) -> None:
    golden = _golden_records(
        (
            "What is a workflow memory?",
            "How are workflows induced?",
            "What grounds an agent claim?",
            "Why is retrieval deterministic here?",
        )
    )
    _seed_source(template_vault, golden)
    clone = tmp_path / "eval-clone"

    record = _run_eval(
        TOPIC, source_root=template_vault, llm_client=_routing_fake(), work_root=clone
    )

    assert record.n_examples == len(golden), (
        "dspy.Evaluate scored every golden example; n_examples matches the devset size"
    )
    manifest = _read_manifest(clone, record)
    assert _any_list_of_length(manifest, len(golden)), (
        "the manifest records one per-example entry per devset example (reproducibility columns)"
    )


def test_run_eval_hands_dspy_evaluate_the_configured_failure_score(
    seeded_source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Evaluate failure-score policy is folded into harness_version and written to
    # the manifest, so the value dspy.Evaluate actually applies must be the configured
    # one -- otherwise the recorded instrument does not describe the run. A spy that
    # records the constructor kwargs and delegates to the real Evaluate proves the
    # non-default value reaches dspy without changing the offline behaviour.
    captured: dict[str, object] = {}
    real_evaluate = dspy.Evaluate

    def _spy(**kwargs: object) -> object:
        captured.update(kwargs)
        return real_evaluate(**kwargs)

    monkeypatch.setattr(dspy, "Evaluate", _spy)

    _run_eval(
        TOPIC,
        source_root=seeded_source,
        llm_client=_routing_fake(),
        work_root=tmp_path / "clone",
        config=DEFAULT_CONFIG.with_overrides(failure_score=0.25),
    )

    assert captured["failure_score"] == 0.25, (
        "run_eval must pass the configured failure_score to dspy.Evaluate, not dspy's default"
    )


# --------------------------------------------------------------------------- #
# The result surfaces the clone root so the clone-relative manifest resolves
# --------------------------------------------------------------------------- #


def test_run_eval_returns_the_clone_root_the_manifest_resolves_against(
    seeded_source: Path, tmp_path: Path
) -> None:
    clone = tmp_path / "eval-clone"

    result = run_eval(
        TOPIC,
        source_root=seeded_source,
        llm_client=_routing_fake(),
        work_root=clone,
        cache=ResponseCache(),
    )

    assert isinstance(result.record, MetricsRecord), "the result carries the appended record"
    assert result.clone_root == clone, "the result surfaces the clone the run committed to"
    assert result.record.artifact_ref is not None, "the record references a per-run manifest"
    resolved = result.clone_root / result.record.artifact_ref
    assert resolved.exists(), (
        "the record's clone-relative artifact_ref resolves to a real file under the "
        "returned clone root -- the manifest a human reviews the eval commit from"
    )

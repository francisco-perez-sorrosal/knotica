"""Behavioral spec for the golden-devset builder's load/verify half.

``evals.golden`` reads a topic's frozen, held-out eval set from
``<topic>/.knotica/datasets/golden.jsonl`` and turns it into the devset the
harness scores. This suite pins the *loading and verification* contract (the
interactive bootstrap/freeze half is a later, sequential extension of the same
file):

- **Content-addressed load.** A valid ``golden.jsonl`` plus a matching sibling
  ``MANIFEST.json`` loads into the topic's curated ``QARecord``s, and the
  returned count matches the manifest's declared ``size``.
- **Tamper detection bites.** The manifest records a ``sha256`` of the frozen
  file body. If the golden content changes after the manifest was frozen, the
  digest no longer matches and load refuses the set with a typed, actionable
  error -- distinct from the error for a manifest that is simply missing.
- **Absent set is a first-class outcome, never an empty success.** A topic with
  no ``golden.jsonl`` yields a typed "no golden set" error (not a bare
  ``FileNotFoundError``, not an empty list) -- the outcome the CLI later maps to
  its own dedicated exit code, so it is distinct from the manifest-verification
  errors.
- **Held-out split guard.** A manifest whose ``split`` is anything other than
  ``held_out`` is rejected: the eval-scalar set must never be a public/trainset
  partition.
- **Disjoint from the flywheel.** A record id that appears in both the golden
  set and the topic's flywheel ``qa.jsonl`` is rejected, so the held-out
  eval-scalar set can never be contaminated by the trainset.
- **dspy.Example conversion.** ``to_example`` maps a ``QARecord`` onto a
  ``dspy.Example`` with the question as the sole input key, so the devset drives
  ``dspy.Evaluate`` directly.
- **Import purity.** Importing the module pulls in neither ``dspy`` nor
  ``anthropic`` -- the heavy imports are deferred so the module stays cheap to
  import and the cold-start-isolation guarantee holds.

--------------------------------------------------------------------------------
PINNED negotiables (reconciliation points if the implementation diverges)

1. **Exception taxonomy.** The design fixes the *behaviors* -- distinct, typed,
   actionable outcomes for absent-set / tampered / missing-manifest / wrong-split
   / overlap -- but not the concrete exception *classes*. These tests therefore
   assert on the observable contract (an exception is raised, it is not a bare
   generic error where the design forbids one, its message is actionable, and the
   modes the design separates raise *different* types) rather than importing
   specific class names. ``_load_error`` is the single capture seam; if the
   taxonomy is expressed with a discriminating attribute on one class instead of
   distinct classes, the two type-distinctness tests are where that reconciles.

2. **Digest convention.** The manifest ``sha256`` is taken over the exact UTF-8
   bytes of ``golden.jsonl`` as written. Every fixture writes the file and
   computes the digest from the same string, so the happy path is internally
   consistent regardless of newline conventions; only a genuine content change
   (or a corrupt recorded digest) can break the match.

Written concurrently with the implementation (disjoint files); RED until
``evals/golden.py`` lands.
--------------------------------------------------------------------------------
"""

import hashlib
import json
import socket
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

import pytest

from knotica.core.page import topic_relative_page_name
from knotica.core.records import QARecord
from knotica.evals.golden import (
    EVAL_MIN_GOLDEN,
    GOLDEN_SPLIT,
    GoldenSetContaminationError,
    GoldenSetMissingError,
    golden_dataset_path,
    golden_manifest_path,
    load,
    to_example,
    verify_disjoint_from_trainset,
)
from knotica.evals.llm import Completion, FakeLLMClient, TokenUsage
from knotica.store import LocalFSStore
from support.vault import git_commit_count, git_commit_subjects, parse_knotica_commit

# ``dspy`` lives in the eval-only dependency group; skip this whole module (not
# abort collection) when the base test env has not installed it, so the plain
# ``uv run pytest`` loop still collects the rest of the suite.
dspy = pytest.importorskip("dspy")

# NOTE on deferred imports: the write-side entry points ``bootstrap`` and
# ``freeze`` are imported *inside* each write-side test body below, never at
# module top. They do not exist until the bootstrap-workflow step lands, and a
# top-level import of a missing name would break collection of the 14 already
# green read-side tests above. Deferring the import keeps those green and lets
# each write-side test fail in isolation (ImportError) until the implementation
# arrives -- the standard RED handshake for a file that extends a live suite.

#: The topic whose golden set the happy-path cases build under ``tmp_path``.
TOPIC = "agentic-systems"

#: Vault-relative datasets directory every eval dataset lives in, per the
#: ``create_topic`` scaffolding (``<topic>/.knotica/datasets/``).
_DATASETS_SEGMENTS = (".knotica", "datasets")


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The golden loader is a pure filesystem read plus a local dspy conversion.

    Replacing ``socket.socket`` turns any accidental network touch into a loud
    failure, actively enforcing the zero-network guarantee for this suite. The
    import-isolation child runs in a separate interpreter over OS pipes, so it is
    unaffected.
    """

    def _blocked(*args: object, **kwargs: object) -> object:
        raise RuntimeError("network access is forbidden in the golden-set test suite")

    monkeypatch.setattr(socket, "socket", _blocked)


# --------------------------------------------------------------------------- #
# Builders (the "how" -- kept out of the test bodies)
# --------------------------------------------------------------------------- #


def _qa_record(
    *,
    record_id: str,
    query: str = "What distinguishes an agentic workflow memory?",
    answer: str = "It persists reusable task strategies across episodes.",
    citations: tuple[str, ...] = ("wang2024awm",),
) -> QARecord:
    """A valid held-out golden ``QARecord`` (frozen ``source: curate_example``)."""
    return QARecord(
        id=record_id,
        topic=TOPIC,
        created="2026-07-16",
        query=query,
        pages_used=("agentic-workflow-memory",),
        answer=answer,
        citations=citations,
        verdict="good",
        corrected_answer=None,
        source="curate_example",
        model="test-worker-snapshot-00000000",
    )


def _jsonl_body(records: Iterable[QARecord]) -> str:
    """Render records as a ``.jsonl`` body: one JSON line each, newline-terminated."""
    return "".join(record.to_json_line() + "\n" for record in records)


def _sha256(text: str) -> str:
    """Hex digest of ``text``'s UTF-8 bytes -- the manifest's content-address convention."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _manifest_json(*, sha256: str, size: int, split: str = "held_out") -> str:
    """A sibling ``MANIFEST.json`` body carrying the frozen-set provenance fields."""
    payload = {
        "sha256": sha256,
        "version": "2026-07-16",
        "source": "synthetic",
        "split": split,
        "size": size,
    }
    return json.dumps(payload)


def _datasets_dir(vault_root: Path, topic: str) -> Path:
    return vault_root.joinpath(topic, *_DATASETS_SEGMENTS)


def _write_golden_set(
    vault_root: Path,
    topic: str,
    records: list[QARecord],
    *,
    split: str = "held_out",
    write_manifest: bool = True,
    tamper_content: bool = False,
) -> None:
    """Plant a topic's frozen golden set (``golden.jsonl`` + ``MANIFEST.json``) on disk.

    The manifest records the pristine ``records`` digest. ``tamper_content``
    rewrites ``golden.jsonl`` with a still-valid but byte-different body *after*
    the manifest is frozen (proving load re-hashes the actual file);
    ``write_manifest=False`` omits the sibling manifest entirely.
    """
    datasets = _datasets_dir(vault_root, topic)
    datasets.mkdir(parents=True, exist_ok=True)

    body = _jsonl_body(records)
    (datasets / "golden.jsonl").write_text(body, encoding="utf-8")

    if write_manifest:
        (datasets / "MANIFEST.json").write_text(
            _manifest_json(sha256=_sha256(body), size=len(records), split=split),
            encoding="utf-8",
        )

    if tamper_content:
        edited = [replace(records[0], answer=records[0].answer + " (edited)"), *records[1:]]
        (datasets / "golden.jsonl").write_text(_jsonl_body(edited), encoding="utf-8")


def _write_flywheel(vault_root: Path, topic: str, records: list[QARecord]) -> None:
    """Plant only the topic's flywheel ``qa.jsonl`` (the future DSPy trainset)."""
    datasets = _datasets_dir(vault_root, topic)
    datasets.mkdir(parents=True, exist_ok=True)
    (datasets / "qa.jsonl").write_text(_jsonl_body(records), encoding="utf-8")


def _load_error(vault_root: Path, topic: str) -> BaseException:
    """Load a malformed set and return the raised error; fail loudly if none is raised.

    A missing raise is exactly the "empty success" the design forbids -- a
    malformed or absent set must never load quietly -- so a normal return here is
    an assertion failure, not a returned ``None``.
    """
    store = LocalFSStore(vault_root)
    try:
        result = load(store, topic)
    except Exception as exc:  # noqa: BLE001 - the impl's exception taxonomy is the pinned negotiable
        return exc
    raise AssertionError(
        f"load({topic!r}) must reject this set, but returned {result!r} -- a malformed or "
        "absent golden set must never load as an empty success"
    )


def _error_text(exc: BaseException) -> str:
    """The actionable text of a raised error, lowercased, across error shapes.

    Reads the house error's ``message``/``fix`` when present and always folds in
    ``str(exc)``, so the actionability assertions do not depend on the concrete
    exception class the implementer chose.
    """
    parts = (getattr(exc, "message", ""), getattr(exc, "fix", ""), str(exc))
    return " ".join(part for part in parts if part).lower()


def _mentions_any(text: str, keywords: Iterable[str]) -> bool:
    """Whether any keyword appears in ``text`` (loop kept out of the test bodies)."""
    return any(keyword.lower() in text for keyword in keywords)


# --------------------------------------------------------------------------- #
# Happy path -- a valid, matching set loads into curated records
# --------------------------------------------------------------------------- #


def test_a_valid_golden_set_loads_all_records_as_qa_records(tmp_path: Path) -> None:
    records = [
        _qa_record(record_id="golden-0001", query="What is workflow memory?"),
        _qa_record(record_id="golden-0002", query="How are workflows reused?"),
        _qa_record(record_id="golden-0003", query="What grounds an agent claim?"),
    ]
    _write_golden_set(tmp_path, TOPIC, records)

    loaded = load(LocalFSStore(tmp_path), TOPIC)

    assert [record.id for record in loaded] == ["golden-0001", "golden-0002", "golden-0003"], (
        "load returns every frozen record, in order"
    )
    assert all(isinstance(record, QARecord) for record in loaded), (
        "the loaded devset is built from curated QARecords"
    )


def test_the_loaded_record_count_matches_the_manifest_size(tmp_path: Path) -> None:
    records = [_qa_record(record_id=f"golden-{index:04d}") for index in range(4)]
    _write_golden_set(tmp_path, TOPIC, records)

    loaded = load(LocalFSStore(tmp_path), TOPIC)

    # The manifest declares size == 4 (built from the same records); a load that
    # dropped or duplicated a line would diverge from the attested count.
    assert len(loaded) == 4


# --------------------------------------------------------------------------- #
# Absent golden set -- a typed outcome, never an empty success
# --------------------------------------------------------------------------- #


def test_a_topic_with_no_golden_file_raises_a_typed_no_golden_set_error(tmp_path: Path) -> None:
    # Nothing is planted for this topic: no golden.jsonl exists.
    error = _load_error(tmp_path, "topic-without-a-golden-set")

    assert not isinstance(error, FileNotFoundError), (
        "an absent golden set must surface as a purpose-built error the CLI can map "
        "to its own exit code, not a raw FileNotFoundError leaked from the store"
    )
    assert _mentions_any(_error_text(error), {"golden", "bootstrap"}), (
        "the no-golden-set error must name the set and point at the fix (bootstrap); "
        f"got: {_error_text(error)!r}"
    )


# --------------------------------------------------------------------------- #
# Manifest verification -- sha256 tamper detection and a missing manifest
# --------------------------------------------------------------------------- #


def test_tampered_golden_content_is_rejected_after_the_manifest_is_frozen(tmp_path: Path) -> None:
    records = [_qa_record(record_id="golden-0001"), _qa_record(record_id="golden-0002")]
    _write_golden_set(tmp_path, TOPIC, records, tamper_content=True)

    error = _load_error(tmp_path, TOPIC)

    assert _mentions_any(
        _error_text(error), {"sha", "hash", "digest", "tamper", "integrity", "match"}
    ), (
        "content that diverges from its recorded digest must be refused with an error "
        f"that names the integrity failure; got: {_error_text(error)!r}"
    )


def test_a_missing_manifest_raises_its_own_typed_error(tmp_path: Path) -> None:
    records = [_qa_record(record_id="golden-0001")]
    _write_golden_set(tmp_path, TOPIC, records, write_manifest=False)

    error = _load_error(tmp_path, TOPIC)

    assert _mentions_any(_error_text(error), {"manifest"}), (
        "a golden set with no sibling MANIFEST.json must be refused with an error that "
        f"names the missing manifest; got: {_error_text(error)!r}"
    )


def test_tampered_content_and_a_missing_manifest_are_diagnosed_differently(tmp_path: Path) -> None:
    records = [_qa_record(record_id="golden-0001")]
    _write_golden_set(tmp_path, "topic-tampered", list(records), tamper_content=True)
    _write_golden_set(tmp_path, "topic-no-manifest", list(records), write_manifest=False)

    tampered = _load_error(tmp_path, "topic-tampered")
    missing_manifest = _load_error(tmp_path, "topic-no-manifest")

    # Both are verification failures, but a modified file and an absent manifest are
    # different problems needing different remediation: the reader must be told which
    # one it is, so the actionable diagnoses differ (the tampered text names the sha
    # mismatch; the missing text names the absent manifest).
    assert _error_text(tampered) != _error_text(missing_manifest), (
        "a tampered set and an absent manifest must be diagnosed with distinct actionable "
        f"messages; both produced: {_error_text(tampered)!r}"
    )


def test_the_no_golden_set_outcome_is_distinct_from_a_verification_failure(tmp_path: Path) -> None:
    _write_golden_set(
        tmp_path, "topic-tampered", [_qa_record(record_id="golden-0001")], tamper_content=True
    )

    absent = _load_error(tmp_path, "topic-without-a-golden-set")
    tampered = _load_error(tmp_path, "topic-tampered")

    assert type(absent) is not type(tampered), (
        "the no-golden-set outcome is what the CLI maps to a dedicated exit code; it must "
        f"be distinguishable from a verification failure, but both raised {type(absent).__name__}"
    )


# --------------------------------------------------------------------------- #
# Held-out split guard
# --------------------------------------------------------------------------- #


def test_a_manifest_declaring_a_non_held_out_split_is_rejected(tmp_path: Path) -> None:
    records = [_qa_record(record_id="golden-0001")]
    # The digest is correct; the only fault is the split marker.
    _write_golden_set(tmp_path, TOPIC, records, split="public")

    error = _load_error(tmp_path, TOPIC)

    assert _mentions_any(_error_text(error), {"split", "held_out", "held-out"}), (
        "the eval-scalar set must be held-out; a public/trainset split must be refused with "
        f"an error that names the split requirement; got: {_error_text(error)!r}"
    )


# --------------------------------------------------------------------------- #
# Disjoint from the flywheel qa.jsonl
#
# The held-out guarantee is enforced by ``verify_disjoint_from_trainset``, keyed
# on the shared *question* (the leakage that matters: a question an optimizer
# trained on cannot also be the eval-scalar question). See LEARNINGS for two
# reconciliation notes vs the step brief -- the guard is a standalone function
# (not wired into ``load``) and keys on question, not record id.
# --------------------------------------------------------------------------- #


def test_a_golden_question_shared_with_the_flywheel_is_flagged_as_contamination(
    tmp_path: Path,
) -> None:
    shared_question = "What distinguishes an agentic workflow memory?"
    _write_flywheel(tmp_path, TOPIC, [_qa_record(record_id="qa-0001", query=shared_question)])
    golden = [_qa_record(record_id="golden-0001", query=shared_question)]

    with pytest.raises(GoldenSetContaminationError) as excinfo:
        verify_disjoint_from_trainset(LocalFSStore(tmp_path), TOPIC, golden)

    assert shared_question in excinfo.value.overlap, (
        "the contamination error must name the offending shared question so it can be removed; "
        f"got overlap={excinfo.value.overlap!r}"
    )


def test_a_golden_set_with_no_shared_questions_is_disjoint_from_the_flywheel(
    tmp_path: Path,
) -> None:
    _write_flywheel(
        tmp_path, TOPIC, [_qa_record(record_id="qa-0001", query="A flywheel question?")]
    )
    golden = [_qa_record(record_id="golden-0001", query="A distinct held-out question?")]

    # No shared question -> the guard is silent (returns None, raises nothing).
    assert verify_disjoint_from_trainset(LocalFSStore(tmp_path), TOPIC, golden) is None


def test_disjointness_holds_trivially_when_the_topic_has_no_flywheel(tmp_path: Path) -> None:
    golden = [_qa_record(record_id="golden-0001")]

    # No qa.jsonl exists for this topic, so there is nothing to overlap with.
    assert (
        verify_disjoint_from_trainset(LocalFSStore(tmp_path), "topic-without-a-flywheel", golden)
        is None
    )


# --------------------------------------------------------------------------- #
# dspy.Example conversion -- field mapping and the sole input key
# --------------------------------------------------------------------------- #


def test_to_example_maps_the_record_fields_onto_a_dspy_example() -> None:
    record = _qa_record(
        record_id="golden-0001",
        query="What is workflow memory?",
        answer="Reusable task strategies persisted across episodes.",
        citations=("wang2024awm", "smith2023alpha"),
    )

    example = to_example(record)

    assert isinstance(example, dspy.Example), "to_example produces a dspy.Example"
    assert example.question == record.query, "the question field carries the record's query"
    assert example.reference_answer == record.answer, (
        "the reference_answer field carries the record's answer (the scorer's golden reference)"
    )
    assert tuple(example.citations) == record.citations, (
        "the citations field carries the record's citation keys"
    )


def test_to_example_marks_question_as_the_only_input_key() -> None:
    record = _qa_record(record_id="golden-0001", query="What grounds an agent claim?")

    inputs = to_example(record).inputs()

    assert set(inputs.keys()) == {"question"}, (
        "only the question is an input; reference_answer and citations are labels dspy.Evaluate "
        "passes to the metric, not inputs to the program"
    )
    assert dict(inputs) == {"question": record.query}, (
        "the sole input carries the record's query verbatim"
    )


def test_to_example_carries_the_records_stable_id_as_non_input_metadata() -> None:
    # The breakdown loop reads gold.id to key the manifest's per_example entry on
    # a stable identifier -- never on question text, which can be edited.
    record = _qa_record(record_id="golden-0001", query="What grounds an agent claim?")

    example = to_example(record)

    assert example.id == record.id, "the example's id must equal the golden record's stable id"
    assert "id" not in example.inputs(), (
        "id is metadata for the breakdown loop, never fed to the program as an input"
    )


# --------------------------------------------------------------------------- #
# Import purity -- the module pulls in neither dspy nor anthropic
# --------------------------------------------------------------------------- #


def test_importing_the_golden_module_imports_neither_dspy_nor_anthropic() -> None:
    # A fresh interpreter is required: a same-process check false-positives
    # because dspy/anthropic may already be loaded by an earlier test. A
    # top-level import of either in the module would land in the child's
    # sys.modules and fail this regardless of what is installed.
    script = (
        "import sys\n"
        "import knotica.evals.golden\n"
        "leaked = sorted(\n"
        "    name for name in sys.modules\n"
        "    if name in ('dspy', 'anthropic')\n"
        "    or name.startswith('dspy.')\n"
        "    or name.startswith('anthropic.')\n"
        ")\n"
        "assert not leaked, leaked\n"
        "print('IMPORT_ISOLATION_OK')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "importing the golden-set module must defer both dspy and anthropic; "
        f"child stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "IMPORT_ISOLATION_OK" in result.stdout


# =========================================================================== #
# Write side -- interactive bootstrap (synthetic generation) + review-freeze.
#
# This half of ``evals.golden`` produces a golden set: a strong model reads the
# topic's entity pages and proposes candidate QA pairs to a review *staging*
# file (never committed, never masquerading as the frozen set); a human accepts
# a subset; ``freeze`` writes the accepted pairs to ``golden.jsonl`` plus a
# content-addressing ``MANIFEST.json`` through the one mutation path, disjoint
# from the flywheel trainset. The read side above is the round-trip anchor:
# whatever ``freeze`` writes, ``load`` must verify and return.
#
# PINNED negotiables (reconciliation points if the implementation diverges --
# the plan proposes these shapes but leaves the concrete write-side API a
# later-step call; the tests assert observable behaviour, not these choices):
#
#   A. Interface names / call shape. Exercised through module-level
#      ``bootstrap(store, topic, llm_client, snapshot) -> list[dict]`` and
#      ``freeze(store, vault_root, topic, accepted)``. Imported inside each test
#      body (see the deferred-import note near the top of this file).
#   B. Candidate shape. A generated/accepted candidate is a dict carrying one QA
#      pair's human-reviewable content -- a question, a reference answer, and
#      reference citations (plan: ``(question, reference_answer,
#      supporting_pages/citations)``). ``freeze`` stamps the frozen provenance
#      (``source: curate_example``, id, verdict, model, date) itself. ``_candidate``
#      is the single seam if the implementation keys these differently.
#   C. Staging file. ``bootstrap`` persists candidates to a review file distinct
#      from ``golden.jsonl`` (plan: ``golden.staging.jsonl``) and never commits
#      it. The tests assert "a new dataset file that is not the frozen set or the
#      trainset", not a hardcoded staging filename.
#   D. Commit grammar / op. ``freeze`` commits through one ``VaultTransaction``;
#      the op slot is the implementer's call (plan default: ``curate_example``).
#      The tests assert the commit parses under the frozen grammar for the right
#      topic and that exactly one lands -- never a specific op string.
#   E. Below-floor surfacing. A freeze below ``EVAL_MIN_GOLDEN`` is permitted
#      (the human is the gate), not a hard block. The channel the warning travels
#      (return value / envelope / ``warnings``) is unpinned; the tests pin only
#      that it does not raise and the small set still freezes and loads.
# =========================================================================== #

#: A distinctive worker snapshot. Asserting it reaches the fake proves the
#: caller-supplied snapshot is threaded through to the generation call, not a
#: hardcoded default substituted somewhere inside ``bootstrap``.
BOOTSTRAP_SNAPSHOT = "worker-snapshot-SENTINEL-00000000"

#: The candidate keys the freeze side reads as human-reviewable content (seam B).
_CANDIDATE_FIELDS = frozenset({"question", "reference_answer", "citations"})


def _candidate(
    *,
    question: str,
    reference_answer: str = "Reusable task strategies persisted and reused across episodes.",
    citations: tuple[str, ...] = ("wang2024awm",),
    supporting_pages: tuple[str, ...] = ("agent-workflow-memory",),
) -> dict[str, object]:
    """One human-reviewable candidate QA pair (see PINNED negotiable B)."""
    return {
        "question": question,
        "reference_answer": reference_answer,
        "citations": list(citations),
        "supporting_pages": list(supporting_pages),
    }


def _completion_for(candidate: dict[str, object]) -> Completion:
    """A canned worker completion whose text is one candidate, in the runner's JSON shape."""
    return Completion(
        text=json.dumps(candidate),
        usage=TokenUsage(input_tokens=140, output_tokens=70),
    )


def _distinct_completions(count: int) -> list[Completion]:
    """A sequence of distinct canned candidates -- one per generation call, distinct questions."""
    return [
        _completion_for(_candidate(question=f"Synthetic golden question {index}?"))
        for index in range(count)
    ]


def _staging_files(vault_root: Path, topic: str) -> list[Path]:
    """Dataset ``.jsonl`` files bootstrap may have staged: not the frozen set, not the trainset."""
    reserved = {
        Path(golden_dataset_path(topic)).name,
        Path(golden_manifest_path(topic)).name,
        "qa.jsonl",
    }
    return [
        path
        for path in sorted(_datasets_dir(vault_root, topic).glob("*.jsonl"))
        if path.is_file() and path.name not in reserved
    ]


def _carries_candidate_fields(candidate: object) -> bool:
    """Whether ``candidate`` is a mapping carrying the human-reviewable QA fields (seam B)."""
    return isinstance(candidate, dict) and _CANDIDATE_FIELDS <= set(candidate)


def _all_calls_used(fake: FakeLLMClient, *, snapshot: str, temperature: float) -> bool:
    """Whether every recorded generation call used the given snapshot and temperature."""
    return all(call.snapshot == snapshot and call.temperature == temperature for call in fake.calls)


# --------------------------------------------------------------------------- #
# Support-quote provenance builders (located spans in the review staging file)
# --------------------------------------------------------------------------- #

#: A throwaway entity page with fully known line structure, planted so a located
#: span asserts a *fixed* 1-based range independent of the demo template's drift.
_LOCATOR_PAGE_NAME = "locator-fixture"
_LOCATOR_PAGE_BODY = (
    "# Locator Fixture\n"  # line 1
    "\n"  # line 2
    "The reference answer is grounded in this exact sentence.\n"  # line 3
    "\n"  # line 4
    "A trailing paragraph rounds out the page.\n"  # line 5
)
#: The verbatim quote the model "returns"; it is the whole of line 3 above.
_LOCATOR_QUOTE = "The reference answer is grounded in this exact sentence."


def _completion_with_support(
    *,
    question: str,
    support_quotes: tuple[str, ...],
    reference_answer: str = "Reusable task strategies persisted and reused across episodes.",
    citations: tuple[str, ...] = ("wang2024awm",),
) -> Completion:
    """A canned worker completion whose JSON carries model-supplied ``support_quotes``."""
    payload = {
        "question": question,
        "reference_answer": reference_answer,
        "citations": list(citations),
        "support_quotes": list(support_quotes),
    }
    return Completion(
        text=json.dumps(payload), usage=TokenUsage(input_tokens=140, output_tokens=70)
    )


def _write_entity_page(vault_root: Path, topic: str, name: str, body: str) -> None:
    """Plant one extra entity page in ``topic`` (untracked; bootstrap reads, never commits)."""
    (vault_root / topic / f"{name}.md").write_text(body, encoding="utf-8")


def _candidate_for_page(candidates: list[dict[str, object]], page_name: str) -> dict[str, object]:
    """The single bootstrap candidate generated from ``page_name`` (kept out of test bodies)."""
    matches = [candidate for candidate in candidates if candidate.get("pages_used") == [page_name]]
    assert matches, f"no candidate was generated for page {page_name!r}; got {candidates!r}"
    return matches[0]


def _support(candidate: dict[str, object]) -> object:
    """The candidate's located support entries; asserts the optional key is present."""
    assert "support" in candidate, f"candidate carries located support entries; got {candidate!r}"
    return candidate["support"]


# --------------------------------------------------------------------------- #
# bootstrap -- synthesise candidates to a review staging file, never freeze
# --------------------------------------------------------------------------- #


def test_bootstrap_stages_candidates_without_freezing_a_golden_set(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import bootstrap

    store = LocalFSStore(template_vault)
    fake = FakeLLMClient(_distinct_completions(5))
    commits_before = git_commit_count(template_vault)

    candidates = bootstrap(store, TOPIC, fake, BOOTSTRAP_SNAPSHOT)

    assert candidates, "bootstrap synthesises at least one candidate from the topic's entity pages"
    assert _staging_files(template_vault, TOPIC), (
        "the candidates land in a review staging file distinct from the frozen golden set"
    )
    assert not store.exists(golden_dataset_path(TOPIC)), (
        "bootstrap stages only -- it never writes golden.jsonl directly"
    )
    assert not store.exists(golden_manifest_path(TOPIC)), "bootstrap writes no MANIFEST.json"
    assert git_commit_count(template_vault) == commits_before, (
        "staging is never auto-committed -- the human review-and-freeze gate owns the commit"
    )


def test_bootstrap_calls_the_worker_at_temperature_zero_with_the_supplied_snapshot(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import bootstrap

    store = LocalFSStore(template_vault)
    fake = FakeLLMClient(_distinct_completions(5))

    bootstrap(store, TOPIC, fake, BOOTSTRAP_SNAPSHOT)

    assert fake.call_count > 0, "non-vacuity: bootstrap actually reaches the worker model at all"
    assert _all_calls_used(fake, snapshot=BOOTSTRAP_SNAPSHOT, temperature=0.0), (
        "every generation call is deterministic (temperature 0) and uses the caller-supplied "
        "snapshot, not a hardcoded default"
    )


def test_bootstrap_candidates_carry_a_question_reference_answer_and_citations(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import bootstrap

    store = LocalFSStore(template_vault)
    fake = FakeLLMClient(_distinct_completions(5))

    candidates = bootstrap(store, TOPIC, fake, BOOTSTRAP_SNAPSHOT)

    assert candidates, "non-vacuity: bootstrap produced candidates whose shape can be checked"
    assert all(_carries_candidate_fields(candidate) for candidate in candidates), (
        "each staged candidate carries the human-reviewable QA content -- a question, a reference "
        f"answer, and reference citations; got {candidates!r}"
    )


def test_a_bootstrapped_but_unfrozen_topic_still_reports_no_golden_set(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import bootstrap

    store = LocalFSStore(template_vault)
    fake = FakeLLMClient(_distinct_completions(5))

    bootstrap(store, TOPIC, fake, BOOTSTRAP_SNAPSHOT)

    # The staging file must never masquerade as a frozen golden set: with only
    # staged candidates present, load still reports the set as absent.
    with pytest.raises(GoldenSetMissingError):
        load(store, TOPIC)


# --------------------------------------------------------------------------- #
# bootstrap page-subset filter -- restricting which entity pages seed candidates
# --------------------------------------------------------------------------- #


def test_bootstrap_with_explicit_pages_none_covers_every_entity_page(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import bootstrap, entity_pages

    store = LocalFSStore(template_vault)
    all_pages = entity_pages(store, TOPIC)
    fake = FakeLLMClient(_distinct_completions(len(all_pages)))

    candidates = bootstrap(store, TOPIC, fake, BOOTSTRAP_SNAPSHOT, pages=None)

    assert len(candidates) == len(all_pages), (
        "an explicit pages=None still synthesizes one candidate per entity page, the same as "
        "omitting the argument entirely"
    )
    assert fake.call_count == len(all_pages)


def test_bootstrap_restricts_generation_to_the_given_page_subset(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import bootstrap, entity_pages

    store = LocalFSStore(template_vault)
    all_pages = entity_pages(store, TOPIC)
    assert len(all_pages) >= 2, "the fixture vault must offer at least two entity pages"
    target = all_pages[0]
    target_name = topic_relative_page_name(TOPIC, target.path)
    excluded_names = {topic_relative_page_name(TOPIC, page.path) for page in all_pages[1:]}

    fake = FakeLLMClient(_distinct_completions(1))
    candidates = bootstrap(store, TOPIC, fake, BOOTSTRAP_SNAPSHOT, pages=[target.path])

    assert len(candidates) == 1, "only the named page is synthesized from"
    assert candidates[0]["pages_used"] == [target_name], (
        "the sole candidate is grounded in the page named by the subset"
    )
    assert not any(
        candidate["pages_used"] == [name] for candidate in candidates for name in excluded_names
    ), "pages outside the subset contribute no candidates"
    assert fake.call_count == 1, "the worker model is called exactly once, for the named page"


def test_bootstrap_with_a_page_subset_never_touches_the_frozen_golden_set(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import bootstrap, entity_pages, freeze

    store = LocalFSStore(template_vault)
    freeze(
        store,
        template_vault,
        TOPIC,
        [_candidate(question="What is the frozen baseline question?")],
    )
    frozen_before = (template_vault / golden_dataset_path(TOPIC)).read_text(encoding="utf-8")

    all_pages = entity_pages(store, TOPIC)
    fake = FakeLLMClient(_distinct_completions(1))

    bootstrap(store, TOPIC, fake, BOOTSTRAP_SNAPSHOT, pages=[all_pages[0].path])

    frozen_after = (template_vault / golden_dataset_path(TOPIC)).read_text(encoding="utf-8")
    assert frozen_after == frozen_before, (
        "a page-restricted bootstrap only writes the uncommitted review staging file -- the "
        "already-frozen golden set is left untouched"
    )


def test_bootstrap_with_an_empty_page_list_produces_no_candidates_without_error(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import bootstrap

    store = LocalFSStore(template_vault)
    fake = FakeLLMClient(_distinct_completions(1))

    candidates = bootstrap(store, TOPIC, fake, BOOTSTRAP_SNAPSHOT, pages=[])

    assert candidates == [], "an empty page subset is a deliberate no-op, not an error"
    assert fake.call_count == 0, "no page means no worker calls at all"


# --------------------------------------------------------------------------- #
# freeze -- accepted pairs become a content-addressed, held-out golden set
# --------------------------------------------------------------------------- #


def test_freeze_writes_a_content_addressed_golden_set_that_load_verifies(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import freeze

    store = LocalFSStore(template_vault)
    accepted = [
        _candidate(question="What is workflow memory?"),
        _candidate(question="How are induced workflows reused?"),
        _candidate(question="What grounds a cited agent claim?"),
    ]

    freeze(store, template_vault, TOPIC, accepted)

    # The round-trip anchor: load succeeds only if the sibling MANIFEST verifies.
    loaded = load(store, TOPIC)
    assert len(loaded) == len(accepted), "every accepted pair is frozen into the golden set"
    assert all(record.source == "curate_example" for record in loaded), (
        "human review is a curation act -- frozen records carry source 'curate_example'"
    )

    golden_text = (template_vault / golden_dataset_path(TOPIC)).read_text(encoding="utf-8")
    manifest = json.loads(
        (template_vault / golden_manifest_path(TOPIC)).read_text(encoding="utf-8")
    )
    assert manifest["sha256"] == _sha256(golden_text), (
        "MANIFEST.json content-addresses the exact frozen golden.jsonl bytes"
    )
    assert manifest["split"] == GOLDEN_SPLIT, "the frozen set is marked held-out, never a trainset"
    assert manifest["size"] == len(loaded), "MANIFEST size matches the frozen record count"


def test_freeze_lands_exactly_one_commit_in_the_frozen_grammar(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import freeze

    store = LocalFSStore(template_vault)
    commits_before = git_commit_count(template_vault)

    freeze(store, template_vault, TOPIC, [_candidate(question="What is workflow memory?")])

    assert git_commit_count(template_vault) == commits_before + 1, (
        "freezing a golden set is exactly one commit through the single mutation path"
    )
    subject = git_commit_subjects(template_vault)[0]
    parsed = parse_knotica_commit(subject)
    assert parsed is not None, "the freeze commit uses the frozen knotica(<op>) grammar"
    assert parsed["topic"] == TOPIC, "the freeze commit records the evaluated topic"


def test_freeze_writes_only_the_reviewer_accepted_subset(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import freeze

    store = LocalFSStore(template_vault)
    generated = [
        _candidate(question="Accepted question one?"),
        _candidate(question="Rejected question?"),
        _candidate(question="Accepted question two?"),
    ]
    accepted = [generated[0], generated[2]]  # the reviewer drops the middle candidate

    freeze(store, template_vault, TOPIC, accepted)

    loaded = load(store, TOPIC)
    assert sorted(record.query for record in loaded) == [
        "Accepted question one?",
        "Accepted question two?",
    ], (
        "only the reviewer-accepted pairs are frozen; the rejected candidate never reaches golden.jsonl"
    )


def test_freeze_refuses_a_candidate_already_in_the_flywheel_trainset(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import freeze

    shared_question = "What distinguishes an agentic workflow memory?"
    _write_flywheel(template_vault, TOPIC, [_qa_record(record_id="qa-0001", query=shared_question)])
    store = LocalFSStore(template_vault)
    commits_before = git_commit_count(template_vault)

    with pytest.raises(GoldenSetContaminationError):
        freeze(store, template_vault, TOPIC, [_candidate(question=shared_question)])

    # Disjointness is verified pre-freeze: a contaminated set writes nothing --
    # no golden.jsonl, no MANIFEST.json, no commit -- so there is no partial state.
    assert not store.exists(golden_dataset_path(TOPIC)), (
        "a contaminated freeze writes no golden.jsonl"
    )
    assert not store.exists(golden_manifest_path(TOPIC)), "a contaminated freeze writes no MANIFEST"
    assert git_commit_count(template_vault) == commits_before, "a refused freeze lands no commit"


def test_freezing_below_the_minimum_floor_succeeds_rather_than_blocking(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import freeze

    store = LocalFSStore(template_vault)
    # Far fewer than EVAL_MIN_GOLDEN: the human is the gate, so a small set is a
    # permitted (warn-worthy) freeze, never a hard block.
    accepted = [_candidate(question=f"Below-floor question {index}?") for index in range(3)]
    assert len(accepted) < EVAL_MIN_GOLDEN, "precondition: the set is below the recommended floor"

    freeze(store, template_vault, TOPIC, accepted)  # must not raise -- a small set is permitted

    loaded = load(store, TOPIC)
    assert len(loaded) == len(accepted), "the below-floor set is still frozen and loadable"


# --------------------------------------------------------------------------- #
# End to end -- the generate -> review -> freeze pipeline connects
# --------------------------------------------------------------------------- #


def test_bootstrapped_candidates_freeze_and_load_end_to_end(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import bootstrap, freeze

    store = LocalFSStore(template_vault)
    fake = FakeLLMClient(_distinct_completions(5))

    candidates = bootstrap(store, TOPIC, fake, BOOTSTRAP_SNAPSHOT)
    freeze(store, template_vault, TOPIC, candidates)

    loaded = load(store, TOPIC)
    assert len(loaded) == len(candidates), (
        "the whole generate -> review -> freeze pipeline connects: every bootstrapped candidate "
        "freezes into a loadable golden record"
    )
    assert all(record.source == "curate_example" for record in loaded), (
        "frozen records carry the curation provenance regardless of their synthetic origin"
    )


# =========================================================================== #
# Support-quote provenance -- verbatim excerpts located to 1-based line spans.
#
# A staged candidate may carry an optional ``support`` list: each entry is a
# verbatim quote the model returned, located back to a deterministic 1-based
# inclusive line range in the page's raw text so the review app can display and
# deep-link the evidence. Model-supplied line numbers are never trusted -- the
# range is recomputed here. ``_locate_span`` is the pure locator; the bootstrap
# legs assert it is wired through the parser onto the candidate.
# =========================================================================== #


# --------------------------------------------------------------------------- #
# _locate_span -- the pure locator (direct unit tests over controlled raw text)
# --------------------------------------------------------------------------- #


def test_locate_span_returns_the_single_line_of_an_exact_hit() -> None:
    from knotica.evals.golden import _locate_span

    raw = "alpha beta gamma\ndelta epsilon\n"

    assert _locate_span(raw, "beta gamma") == (1, 1), (
        "a hit within one line reports that line as both start and end"
    )


def test_locate_span_spans_first_to_last_line_of_a_multiline_hit() -> None:
    from knotica.evals.golden import _locate_span

    raw = "intro line\nkey fact one\nkey fact two\noutro line\n"

    assert _locate_span(raw, "key fact one\nkey fact two") == (2, 3), (
        "a quote containing a real newline spans from its first line to its last"
    )


def test_locate_span_matches_a_quote_across_a_soft_wrapped_line_break() -> None:
    from knotica.evals.golden import _locate_span

    # In the page the phrase wraps across a newline; the model copied it with a
    # single space. Whitespace-normalized matching recovers it and maps the span
    # back to the real line range (start line of "soft", end line of "phrase").
    raw = "line one\nsecond line has a soft\nwrapped phrase here\nlast line\n"

    assert _locate_span(raw, "soft wrapped phrase") == (2, 3), (
        "collapsing whitespace recovers a quote the model copied across a soft wrap"
    )


def test_locate_span_returns_none_for_an_absent_quote() -> None:
    from knotica.evals.golden import _locate_span

    raw = "some content here\nmore content there\n"

    assert _locate_span(raw, "a phrase that is simply not present") is None, (
        "an unlocatable quote returns None -- the caller records it unverified, never guessed"
    )


def test_locate_span_locates_a_hit_at_the_file_start_on_line_one() -> None:
    from knotica.evals.golden import _locate_span

    raw = "first line here\nsecond line here\n"

    assert _locate_span(raw, "first line here") == (1, 1), (
        "a quote at the very start of the file is line one"
    )


def test_locate_span_locates_a_hit_at_the_file_end_on_the_last_line() -> None:
    from knotica.evals.golden import _locate_span

    raw = "first line here\nlast line here"  # no trailing newline

    assert _locate_span(raw, "last line here") == (2, 2), (
        "a quote at the end of the file (no trailing newline) is the last line"
    )


def test_locate_span_returns_the_first_occurrence_of_duplicated_text() -> None:
    from knotica.evals.golden import _locate_span

    raw = "alpha\nrepeat\nbeta\nrepeat\ngamma\n"

    assert _locate_span(raw, "repeat") == (2, 2), (
        "duplicated text resolves to its first occurrence (documented tie-break)"
    )


# --------------------------------------------------------------------------- #
# bootstrap -- support quotes flow through the parser onto the candidate
# --------------------------------------------------------------------------- #


def test_bootstrap_locates_support_quotes_to_1_based_line_spans(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import bootstrap

    _write_entity_page(template_vault, TOPIC, _LOCATOR_PAGE_NAME, _LOCATOR_PAGE_BODY)
    store = LocalFSStore(template_vault)
    fake = FakeLLMClient(
        _completion_with_support(
            question="What grounds the answer?", support_quotes=(_LOCATOR_QUOTE,)
        )
    )

    candidates = bootstrap(store, TOPIC, fake, BOOTSTRAP_SNAPSHOT)

    located = _candidate_for_page(candidates, _LOCATOR_PAGE_NAME)
    assert _support(located) == [
        {
            "quote": _LOCATOR_QUOTE,
            "page": _LOCATOR_PAGE_NAME,
            "line_start": 3,
            "line_end": 3,
            "verified": True,
        }
    ], "a verbatim quote lands as a located, verified entry with its exact 1-based line range"


def test_bootstrap_marks_an_unlocatable_support_quote_unverified(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import bootstrap

    store = LocalFSStore(template_vault)
    absent_quote = "this exact phrase appears in no entity page whatsoever zzz"
    fake = FakeLLMClient(
        _completion_with_support(
            question="What grounds the answer?", support_quotes=(absent_quote,)
        )
    )

    candidates = bootstrap(store, TOPIC, fake, BOOTSTRAP_SNAPSHOT)

    assert candidates, "non-vacuity: bootstrap produced candidates to inspect"
    first = candidates[0]
    assert _support(first) == [
        {"quote": absent_quote, "page": first["pages_used"][0], "verified": False}
    ], "an unlocatable quote is kept as a verified-False entry carrying no line numbers"


def test_bootstrap_tolerates_a_completion_that_omits_support_quotes(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import bootstrap

    store = LocalFSStore(template_vault)
    # ``_distinct_completions`` emits question/reference_answer/citations with no
    # ``support_quotes`` field at all -- the model simply omitted it.
    fake = FakeLLMClient(_distinct_completions(5))

    candidates = bootstrap(store, TOPIC, fake, BOOTSTRAP_SNAPSHOT)

    assert candidates, "non-vacuity: bootstrap produced candidates to inspect"
    assert all("support" not in candidate for candidate in candidates), (
        "a model that omits support_quotes yields candidates without a support key -- "
        "absence is tolerated, not an error"
    )


# --------------------------------------------------------------------------- #
# freeze -- a support-carrying accepted candidate freezes to an unchanged record
# --------------------------------------------------------------------------- #


def test_freeze_tolerates_a_support_carrying_accepted_candidate(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import freeze

    store = LocalFSStore(template_vault)
    accepted = {
        "question": "What does the reviewer accept?",
        "reference_answer": "The reviewer accepts a grounded, cited answer.",
        "citations": ["wang2024awm"],
        "pages_used": ["agent-workflow-memory"],
        "support": [
            {
                "quote": "AWM induces commonly reused routines",
                "page": "agent-workflow-memory",
                "line_start": 31,
                "line_end": 31,
                "verified": True,
            }
        ],
    }

    freeze(store, template_vault, TOPIC, [accepted])  # unknown 'support' key must be ignored

    loaded = load(store, TOPIC)
    assert len(loaded) == 1, "the support-carrying candidate freezes into exactly one record"
    record = loaded[0]
    # The frozen QARecord is a fixed-field dataclass: the support provenance is a
    # review-time concern that never reaches the frozen set. The record's content
    # is exactly what a support-less candidate with the same fields would produce.
    assert record.query == accepted["question"]
    assert record.answer == accepted["reference_answer"]
    assert record.citations == ("wang2024awm",)
    assert record.pages_used == ("agent-workflow-memory",)
    assert record.source == "curate_example"
    assert record.verdict == "good"

    golden_text = (template_vault / golden_dataset_path(TOPIC)).read_text(encoding="utf-8")
    assert "line_start" not in golden_text, (
        "the support provenance never leaks into the frozen golden.jsonl bytes"
    )


# =========================================================================== #
# Composition characterization -- the review-then-freeze plumbing a source
# ingest depends on.
#
# A source ingest generates held-out golden candidates from the source text
# before the source itself is committed, stages them for human review through
# ``knotica.core.golden_review``, and only later promotes the accepted subset
# into the frozen golden set through ``freeze``. These tests pin three
# behaviors of that existing plumbing with no production code changes:
#
# 1. Staging a candidate for review never touches the flywheel trainset --
#    the staged file and ``qa.jsonl`` are unrelated paths.
# 2. ``freeze``'s disjointness guard governs a staged-and-reloaded candidate
#    exactly as it governs any other: a collision with the trainset is
#    rejected, a genuinely new question is accepted.
# 3. ``freeze`` always replaces the whole frozen set rather than appending --
#    so re-freezing with only a new candidate silently drops every
#    previously frozen question, unless the caller reads the existing frozen
#    set first and freezes the union.
# =========================================================================== #


def _review_candidate(
    *,
    question: str,
    reference_answer: str = "Grounded strictly in the source text, before the source is ingested.",
    citations: tuple[str, ...] = (),
    pages_used: tuple[str, ...] = (),
) -> dict[str, object]:
    """One human-reviewable candidate in the ``golden_review`` save/load shape.

    Distinct from :func:`_candidate` above: this shape carries ``pages_used``
    (the field ``golden_review.save_golden_review`` requires), not
    ``supporting_pages``.
    """
    return {
        "question": question,
        "reference_answer": reference_answer,
        "citations": list(citations),
        "pages_used": list(pages_used),
    }


def test_staging_a_source_derived_candidate_leaves_the_flywheel_trainset_untouched(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.core.golden_review import save_golden_review

    _write_flywheel(template_vault, TOPIC, [_qa_record(record_id="qa-0001")])
    trainset_path = _datasets_dir(template_vault, TOPIC) / "qa.jsonl"
    trainset_before = trainset_path.read_text(encoding="utf-8")
    staged_question = "What does this source say about induced routines, before it is ingested?"
    store = LocalFSStore(template_vault)

    save_golden_review(store, template_vault, TOPIC, [_review_candidate(question=staged_question)])

    trainset_after = trainset_path.read_text(encoding="utf-8")
    assert trainset_after == trainset_before, (
        "staging a held-out candidate for review must never touch the flywheel trainset"
    )
    assert staged_question not in trainset_after, (
        "the staged, not-yet-ingested question must stay absent from qa.jsonl"
    )


def test_freezing_a_staged_candidate_disjoint_from_the_trainset_succeeds(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.core.golden_review import load_golden_review, save_golden_review
    from knotica.evals.golden import freeze

    _write_flywheel(
        template_vault,
        TOPIC,
        [_qa_record(record_id="qa-0001", query="An existing flywheel question?")],
    )
    new_question = "A genuinely new held-out question the source answers?"
    store = LocalFSStore(template_vault)
    save_golden_review(store, template_vault, TOPIC, [_review_candidate(question=new_question)])
    staged = load_golden_review(store, template_vault, TOPIC)["candidates"]

    freeze(store, template_vault, TOPIC, staged)  # must not raise: no overlap with the trainset

    loaded = load(store, TOPIC)
    assert [record.query for record in loaded] == [new_question], (
        "a staged candidate disjoint from the trainset, read back through golden_review_load, "
        "freezes cleanly"
    )


def test_freezing_a_staged_candidate_that_collides_with_the_trainset_is_rejected(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.core.golden_review import load_golden_review, save_golden_review
    from knotica.evals.golden import freeze

    shared_question = "What distinguishes an agentic workflow memory?"
    _write_flywheel(template_vault, TOPIC, [_qa_record(record_id="qa-0001", query=shared_question)])
    store = LocalFSStore(template_vault)
    save_golden_review(store, template_vault, TOPIC, [_review_candidate(question=shared_question)])
    staged = load_golden_review(store, template_vault, TOPIC)["candidates"]
    commits_before_freeze = git_commit_count(template_vault)

    with pytest.raises(GoldenSetContaminationError):
        freeze(store, template_vault, TOPIC, staged)

    assert not store.exists(golden_dataset_path(TOPIC)), (
        "a staged candidate colliding with the trainset must never reach the frozen set"
    )
    assert git_commit_count(template_vault) == commits_before_freeze, (
        "contamination is caught before any commit -- the rejected freeze lands nothing"
    )


def test_freeze_replaces_the_whole_frozen_set_so_a_naive_refreeze_loses_prior_entries(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.evals.golden import freeze

    store = LocalFSStore(template_vault)
    prior_question = "What was frozen before this source was ingested?"
    freeze(store, template_vault, TOPIC, [_candidate(question=prior_question)])

    new_question = "What does the newly ingested source add?"
    # A naive re-freeze: only the new candidate, without first reading what is
    # already frozen.
    freeze(store, template_vault, TOPIC, [_candidate(question=new_question)])

    loaded = load(store, TOPIC)
    assert [record.query for record in loaded] == [new_question], (
        "freeze replaced the whole frozen set -- the prior question, never re-supplied, is "
        f"gone; got {[record.query for record in loaded]!r}"
    )


def test_reading_the_existing_frozen_set_before_refreezing_preserves_prior_entries(
    template_vault: Path, isolated_home: Path
) -> None:
    from knotica.core.golden_review import load_golden_review, save_golden_review
    from knotica.evals.golden import freeze

    store = LocalFSStore(template_vault)
    prior_question = "What was frozen before this source was ingested?"
    freeze(store, template_vault, TOPIC, [_candidate(question=prior_question)])
    already_frozen = load(store, TOPIC)

    new_question = "What does the newly ingested source add?"
    save_golden_review(store, template_vault, TOPIC, [_review_candidate(question=new_question)])
    newly_staged = load_golden_review(store, template_vault, TOPIC)["candidates"]

    # The protocol-correct flow: merge what is already frozen with the newly
    # staged candidate, then freeze the union -- never the new candidate alone.
    merged = [
        {
            "question": record.query,
            "reference_answer": record.answer,
            "citations": list(record.citations),
            "pages_used": list(record.pages_used),
        }
        for record in already_frozen
    ] + newly_staged

    freeze(store, template_vault, TOPIC, merged)

    loaded = load(store, TOPIC)
    assert sorted(record.query for record in loaded) == sorted([prior_question, new_question]), (
        "reading the existing frozen set before re-freezing keeps the prior question alongside "
        f"the newly staged one; got {[record.query for record in loaded]!r}"
    )

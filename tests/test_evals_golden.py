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

import dspy
import pytest

from knotica.core.records import QARecord
from knotica.evals.golden import (
    GoldenSetContaminationError,
    load,
    to_example,
    verify_disjoint_from_trainset,
)
from knotica.store import LocalFSStore

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

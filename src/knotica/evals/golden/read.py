"""The deterministic read side: load a frozen golden set, verify it, convert it.

Three entry points the harness calls in order at the top of every run -- read the
set, prove it is uncontaminated, hand each record to dspy. All three are pure
reads: nothing here writes the vault.

The two failure modes are kept distinct on purpose. An *absent* set is
:class:`~knotica.evals.golden.GoldenSetMissingError` -- the "run the bootstrap"
outcome, never an empty list masquerading as an empty set. A *present but
untrustworthy* set is :class:`~knotica.evals.golden.GoldenSetIntegrityError`.

``dspy`` is imported **lazily**, inside :func:`to_example` only, so importing this
module never pulls the eval dependency group onto an unrelated import path.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.records import QARecord, parse_qa_jsonl
from knotica.evals.golden.contract import (
    GoldenSetContaminationError,
    GoldenSetIntegrityError,
    GoldenSetMissingError,
    golden_dataset_path,
    golden_manifest_path,
)
from knotica.evals.golden.manifest import _parse_manifest, _verify_manifest
from knotica.store import VaultStore

if TYPE_CHECKING:  # `dspy` lives in the optional eval group; import it for types only.
    import dspy


def load(store: VaultStore, topic: str) -> list[QARecord]:
    """Read and verify a topic's frozen golden set, returning its QA records.

    Raises :class:`~knotica.evals.golden.GoldenSetMissingError` when the set is
    absent (the "run the bootstrap" outcome, never an empty list masquerading as
    an empty set), and :class:`~knotica.evals.golden.GoldenSetIntegrityError` when
    the sibling ``MANIFEST.json`` is absent, malformed, declares the wrong split,
    or records a sha256 that does not match the golden file's bytes (i.e. the
    frozen set was modified after freezing).
    """
    golden_path = golden_dataset_path(topic)
    if not store.exists(golden_path):
        raise GoldenSetMissingError(topic)
    golden_text = store.read_text(golden_path)

    manifest_path = golden_manifest_path(topic)
    if not store.exists(manifest_path):
        raise GoldenSetIntegrityError(topic, "its MANIFEST.json is absent")
    manifest = _parse_manifest(store.read_text(manifest_path), topic=topic)
    _verify_manifest(manifest, golden_text, topic=topic)

    return parse_qa_jsonl(golden_text)


def to_example(record: QARecord) -> "dspy.Example":
    """Convert a golden QA record into the ``dspy.Example`` the metric runner reads.

    Maps the record's question, reference answer, and reference citations onto the
    example fields the scorer duck-types, and marks ``question`` as the sole input
    key -- so ``dspy.Evaluate`` calls the program with just the question. Also
    carries the record's stable ``id`` as metadata the per-example breakdown loop
    reads via ``gold.id``; it is never fed to the program (``question`` stays the
    sole input key). ``dspy`` is imported lazily here to keep the module import
    free of the eval group.
    """
    import dspy

    return dspy.Example(
        id=record.id,
        question=record.query,
        reference_answer=record.answer,
        citations=record.citations,
    ).with_inputs("question")


def verify_disjoint_from_trainset(
    store: VaultStore, topic: str, records: Sequence[QARecord]
) -> None:
    """Raise if ``records`` share any question with the topic's flywheel trainset.

    The held-out golden set must stay disjoint from ``qa.jsonl`` (the future DSPy
    trainset). A question appearing in both is the contamination signal and raises
    :class:`~knotica.evals.golden.GoldenSetContaminationError`. A topic with no
    ``qa.jsonl`` is trivially disjoint.
    """
    overlap = _trainset_overlap(store, topic, records)
    if overlap:
        raise GoldenSetContaminationError(topic, overlap)


def _trainset_overlap(
    store: VaultStore, topic: str, records: Sequence[QARecord]
) -> tuple[str, ...]:
    """The unique questions in ``records`` that also appear in the topic's ``qa.jsonl``."""
    trainset_path = qa_dataset_path(topic)
    if not store.exists(trainset_path):
        return ()
    trainset = parse_qa_jsonl(store.read_text(trainset_path))
    trainset_queries = {record.query for record in trainset}
    return tuple(
        query
        for query in dict.fromkeys(record.query for record in records)
        if query in trainset_queries
    )

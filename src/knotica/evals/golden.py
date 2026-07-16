"""Golden devset builder for the eval harness -- load, verify, convert (read side).

A topic's *golden set* is the frozen, human-reviewed, held-out set of QA pairs the
eval scalar is measured against. It lives at
``<topic>/.knotica/datasets/golden.jsonl`` with a sibling ``MANIFEST.json`` that
content-addresses it (a sha256 of the file's exact bytes) and marks it
``split: held_out``. The golden set is kept deliberately *disjoint* from the
flywheel ``qa.jsonl`` (the future DSPy trainset), so the eval scalar can never be
measured on the very examples an optimizer trained against.

This module owns the deterministic read side:

* :func:`load` reads and verifies a topic's golden set, distinguishing its failure
  modes with typed, actionable errors -- the set is absent
  (:class:`GoldenSetMissingError`, the "run the bootstrap" outcome) or present but
  untrustworthy (:class:`GoldenSetIntegrityError` -- a missing, malformed,
  wrong-split, or mismatched ``MANIFEST.json``).
* :func:`to_example` converts a :class:`~knotica.core.records.QARecord` into the
  ``dspy.Example`` the DSPy metric runner consumes.
* :func:`verify_disjoint_from_trainset` is the held-out-split guard: a question
  shared between ``golden.jsonl`` and ``qa.jsonl`` is a contamination signal and
  raises :class:`GoldenSetContaminationError`.

``dspy`` is imported **lazily**, inside :func:`to_example` only, so ``import
knotica.evals.golden`` never pulls the eval dependency group onto an unrelated
import path (for example the MCP cold start). The interactive synthetic-generation
and review-freeze workflow that *produces* a golden set is a separate write-path
concern, built on the shapes and paths this module exposes.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.records import QARecord, body_sha256, parse_qa_jsonl
from knotica.store import VaultStore

if TYPE_CHECKING:  # `dspy` lives in the optional eval group; import it for types only.
    import dspy

__all__ = [
    "EVAL_MIN_GOLDEN",
    "GOLDEN_SPLIT",
    "GoldenManifest",
    "GoldenSetContaminationError",
    "GoldenSetError",
    "GoldenSetIntegrityError",
    "GoldenSetMissingError",
    "golden_dataset_path",
    "golden_manifest_path",
    "load",
    "to_example",
    "verify_disjoint_from_trainset",
]

#: Minimum number of frozen golden pairs a topic should have before its eval
#: scalar is stable enough to gate keep/discard. Deliberately a *separate*
#: constant from ``knotica.cli.status.COMPILE_READY_MIN_EXAMPLES``: that one counts
#: the flywheel trainset (``qa.jsonl``); this one counts the held-out eval set
#: (``golden.jsonl``) -- two disjoint sets that share a floor value today but are
#: independent by design.
EVAL_MIN_GOLDEN = 20

#: The ``split`` value a conforming golden-set manifest must declare -- the marker
#: that this dataset is the held-out eval set, not the trainset.
GOLDEN_SPLIT = "held_out"

#: The frozen golden set and its manifest live beside the flywheel ``qa.jsonl`` in
#: the topic's hidden datasets directory (the layout owned by
#: ``knotica.core.operations.create_topic``).
_GOLDEN_FILENAME = "golden.jsonl"
_MANIFEST_FILENAME = "MANIFEST.json"


def golden_dataset_path(topic: str) -> str:
    """Vault-relative path of ``topic``'s frozen held-out golden set.

    The single source of truth for the ``golden.jsonl`` location -- both the read
    side here and the freeze side derive from it. Sibling of the topic's
    ``qa.jsonl`` (:func:`knotica.core.operations.create_topic.qa_dataset_path`).
    """
    return _datasets_sibling(topic, _GOLDEN_FILENAME)


def golden_manifest_path(topic: str) -> str:
    """Vault-relative path of the golden set's sibling ``MANIFEST.json``."""
    return _datasets_sibling(topic, _MANIFEST_FILENAME)


def _datasets_sibling(topic: str, filename: str) -> str:
    """A file beside ``qa.jsonl`` in the topic's datasets directory."""
    datasets_dir = qa_dataset_path(topic).rsplit("/", 1)[0]
    return f"{datasets_dir}/{filename}"


class GoldenSetError(KnoticaError):
    """A topic's golden set could not be read or trusted for evaluation.

    Carries the house error envelope so an adapter renders it as a clean,
    actionable message rather than a stack trace. Every variant uses the
    ``NOT_CONFIGURED`` code -- the eval is not ready to run for this topic -- and
    the variants are told apart by their concrete type, not by the code.
    """


class GoldenSetMissingError(GoldenSetError):
    """The topic has no ``golden.jsonl`` -- there is nothing to evaluate against."""

    def __init__(self, topic: str) -> None:
        super().__init__(
            ErrorCode.NOT_CONFIGURED,
            (
                f"No golden set exists for topic '{topic}': "
                f"{golden_dataset_path(topic)} is absent, so there is no held-out "
                "set to evaluate against."
            ),
            fix=(
                f"Bootstrap one with `knotica eval --bootstrap --topic {topic}`, "
                "then review and freeze the generated pairs."
            ),
        )
        self.topic = topic


class GoldenSetIntegrityError(GoldenSetError):
    """The golden set is present but its ``MANIFEST.json`` proof does not hold.

    Covers an absent, malformed, or wrong-``split`` manifest, and the tampered
    case where the recorded sha256 does not match the golden file's bytes.
    """

    def __init__(self, topic: str, reason: str) -> None:
        super().__init__(
            ErrorCode.NOT_CONFIGURED,
            f"The golden set for topic '{topic}' failed verification: {reason}.",
            fix=(
                "Re-freeze the golden set so its MANIFEST.json records the sha256 of "
                "golden.jsonl's exact bytes and declares split 'held_out'."
            ),
        )
        self.topic = topic
        self.reason = reason


class GoldenSetContaminationError(GoldenSetError):
    """The golden set shares questions with the flywheel trainset (``qa.jsonl``).

    A held-out set that overlaps the trainset would let the eval scalar be measured
    on examples an optimizer trained against. The overlapping questions are carried
    on :attr:`overlap` for callers that report the detail.
    """

    def __init__(self, topic: str, overlap: Sequence[str]) -> None:
        overlapping = tuple(overlap)
        super().__init__(
            ErrorCode.NOT_CONFIGURED,
            (
                f"The golden set for topic '{topic}' is not disjoint from the "
                f"flywheel trainset: {len(overlapping)} question(s) appear in both "
                "golden.jsonl and qa.jsonl, so the held-out eval scalar would be "
                "contaminated."
            ),
            fix=(
                "Remove the overlapping question(s) from golden.jsonl (or qa.jsonl) "
                "and re-freeze; the held-out set must stay disjoint from the trainset."
            ),
        )
        self.topic = topic
        self.overlap = overlapping


@dataclass(frozen=True, kw_only=True)
class GoldenManifest:
    """The sibling ``MANIFEST.json`` that content-addresses a frozen golden set.

    ``sha256`` is the digest of ``golden.jsonl``'s exact UTF-8 bytes; ``split`` is
    ``"held_out"`` for a conforming set; ``version``, ``source``, and ``size``
    record the freeze provenance. Parsed and verified on the read side; written on
    the freeze side.
    """

    sha256: str
    version: str
    source: str
    split: str
    size: int


def load(store: VaultStore, topic: str) -> list[QARecord]:
    """Read and verify a topic's frozen golden set, returning its QA records.

    Raises :class:`GoldenSetMissingError` when the set is absent (the "run the
    bootstrap" outcome, never an empty list masquerading as an empty set), and
    :class:`GoldenSetIntegrityError` when the sibling ``MANIFEST.json`` is absent,
    malformed, declares the wrong split, or records a sha256 that does not match the
    golden file's bytes (i.e. the frozen set was modified after freezing).
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
    key -- so ``dspy.Evaluate`` calls the program with just the question. ``dspy``
    is imported lazily here to keep the module import free of the eval group.
    """
    import dspy

    return dspy.Example(
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
    :class:`GoldenSetContaminationError`. A topic with no ``qa.jsonl`` is trivially
    disjoint.
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


def _parse_manifest(text: str, *, topic: str) -> GoldenManifest:
    """Parse a golden-set manifest, raising a typed integrity error on malformed input."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise GoldenSetIntegrityError(topic, "its MANIFEST.json is not valid JSON") from error
    if not isinstance(data, dict):
        raise GoldenSetIntegrityError(topic, "its MANIFEST.json is not a JSON object")
    return GoldenManifest(
        sha256=_manifest_str(data, "sha256", topic=topic),
        version=_manifest_str(data, "version", topic=topic),
        source=_manifest_str(data, "source", topic=topic),
        split=_manifest_str(data, "split", topic=topic),
        size=_manifest_int(data, "size", topic=topic),
    )


def _verify_manifest(manifest: GoldenManifest, golden_text: str, *, topic: str) -> None:
    """Check the manifest's declared split and its sha256 against the golden bytes."""
    if manifest.split != GOLDEN_SPLIT:
        raise GoldenSetIntegrityError(
            topic,
            f"its MANIFEST.json declares split {manifest.split!r}, not {GOLDEN_SPLIT!r}",
        )
    if manifest.sha256 != body_sha256(golden_text):
        raise GoldenSetIntegrityError(
            topic,
            "its golden.jsonl does not match the sha256 recorded in MANIFEST.json "
            "(the frozen set was modified after freezing)",
        )


def _manifest_field(data: dict[str, object], key: str, *, topic: str) -> object:
    """Return a required manifest field, raising a typed integrity error when absent."""
    if key not in data:
        raise GoldenSetIntegrityError(topic, f"its MANIFEST.json is missing the {key!r} field")
    return data[key]


def _manifest_str(data: dict[str, object], key: str, *, topic: str) -> str:
    """Return a required string manifest field, typed-error on the wrong type."""
    value = _manifest_field(data, key, topic=topic)
    if not isinstance(value, str):
        raise GoldenSetIntegrityError(topic, f"its MANIFEST.json field {key!r} must be a string")
    return value


def _manifest_int(data: dict[str, object], key: str, *, topic: str) -> int:
    """Return a required integer manifest field, typed-error on the wrong type."""
    value = _manifest_field(data, key, topic=topic)
    if not isinstance(value, int) or isinstance(value, bool):
        raise GoldenSetIntegrityError(topic, f"its MANIFEST.json field {key!r} must be an integer")
    return value

"""What a golden set *is*: where it lives, the floor it declares, and how it refuses.

The leaf both sides of the package stand on. The read side needs the paths to find
a set and the errors to reject an untrustworthy one; the write side needs the same
paths to write it and the same errors to refuse a bad candidate -- so neither side
can own them without the other importing it.

Every ``GoldenSetError`` variant carries the house ``NOT_CONFIGURED`` envelope --
the eval is not ready to run for this topic -- and the variants are told apart by
their concrete type, not by the code.
"""

from collections.abc import Sequence

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.operations.create_topic import qa_dataset_path

#: Minimum number of frozen golden pairs a topic should have before its eval
#: scalar is stable enough to gate keep/discard. Deliberately a *separate*
#: constant from ``knotica.cli.status.COMPILE_READY_MIN_EXAMPLES``: that one counts
#: the flywheel trainset (``qa.jsonl``); this one counts the held-out eval set
#: (``golden.jsonl``) -- two disjoint sets that share a floor value today but are
#: independent by design.
EVAL_MIN_GOLDEN = 20

#: The frozen golden set and its manifest live beside the flywheel ``qa.jsonl`` in
#: the topic's hidden datasets directory (the layout owned by
#: ``knotica.core.operations.create_topic``).
_GOLDEN_FILENAME = "golden.jsonl"
_MANIFEST_FILENAME = "MANIFEST.json"
#: The *uncommitted* review scratchpad :func:`~knotica.evals.golden.bootstrap`
#: writes synthetic candidates to -- a sibling of ``golden.jsonl`` a human edits
#: before :func:`~knotica.evals.golden.freeze` promotes the accepted subset into
#: the frozen set.
_STAGING_FILENAME = "golden.staging.jsonl"


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


def golden_staging_path(topic: str) -> str:
    """Vault-relative path of the uncommitted review staging file.

    Sibling of ``golden.jsonl``; :func:`~knotica.evals.golden.bootstrap` writes
    generated candidates here for a human to review, and it is deliberately never
    committed (it is not the frozen eval set -- :func:`~knotica.evals.golden.freeze`
    produces that from the accepted subset).
    """
    return _datasets_sibling(topic, _STAGING_FILENAME)


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
                f"Bootstrap one with `knotica improve eval --bootstrap --topic {topic}`, "
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


class GoldenCandidateError(ValueError):
    """A golden-set candidate triple was not in the expected shape.

    Raised (never swallowed) when the LLM's synthesis response does not parse into
    a ``{question, reference_answer, citations}`` object during
    :func:`~knotica.evals.golden.bootstrap`, or when a human-edited accepted
    candidate is missing its question/answer at :func:`~knotica.evals.golden.freeze`
    time. Subclasses :class:`ValueError` to match the codebase's parse-error
    convention (an adapter catches it into the house error envelope).
    """


class GoldenSetFloorWarning(UserWarning):
    """A frozen golden set has fewer than :data:`EVAL_MIN_GOLDEN` records.

    Emitted (not raised) by :func:`~knotica.evals.golden.freeze` -- the human is
    the gate, so a small set still freezes; the scalar is just noisier until more
    pairs are added.
    """

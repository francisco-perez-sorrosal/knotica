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

The module also owns the interactive write side that *produces* a golden set:

* :func:`bootstrap` reads a topic's entity pages and asks the injected LLM to
  synthesize candidate ``(question, reference_answer, citations)`` triples --
  each carrying the verbatim support quotes it was grounded in, located back to
  deterministic 1-based line ranges -- writing them to an *uncommitted* review
  staging file for a human to edit and accept; it never writes ``golden.jsonl``
  and never commits.
* :func:`freeze` turns the human-accepted candidates into ``QARecord``s and
  writes the frozen ``golden.jsonl`` + sibling ``MANIFEST.json`` through one
  :class:`~knotica.core.transaction.VaultTransaction` (one commit), after
  verifying the set is disjoint from the flywheel trainset.

``dspy`` is imported **lazily**, inside :func:`to_example` only, so ``import
knotica.evals.golden`` never pulls the eval dependency group onto an unrelated
import path (for example the MCP cold start); ``anthropic`` never enters this
module at all (the LLM seam is the injected :class:`~knotica.evals.llm.LLMClient`,
whose real implementation defers its own heavy import).
"""

import hashlib
import json
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from knotica.core.errors import ErrorCode, KnoticaError, KnoticaWarning
from knotica.core.links import iter_page_paths
from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.page import Page, read_page
from knotica.core.records import QARecord, body_sha256, parse_qa_jsonl
from knotica.core.scrub import scrub
from knotica.core.transaction import VaultTransaction
from knotica.evals.llm import LLMClient, Message
from knotica.store import VaultStore

if TYPE_CHECKING:  # `dspy` lives in the optional eval group; import it for types only.
    import dspy

__all__ = [
    "EVAL_MIN_GOLDEN",
    "GOLDEN_SPLIT",
    "FreezeResult",
    "GoldenCandidateError",
    "GoldenManifest",
    "GoldenSetContaminationError",
    "GoldenSetError",
    "GoldenSetFloorWarning",
    "GoldenSetIntegrityError",
    "GoldenSetMissingError",
    "bootstrap",
    "entity_pages",
    "freeze",
    "golden_dataset_path",
    "golden_manifest_path",
    "golden_staging_path",
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
#: The *uncommitted* review scratchpad :func:`bootstrap` writes synthetic
#: candidates to -- a sibling of ``golden.jsonl`` a human edits before
#: :func:`freeze` promotes the accepted subset into the frozen set.
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

    Sibling of ``golden.jsonl``; :func:`bootstrap` writes generated candidates
    here for a human to review, and it is deliberately never committed (it is not
    the frozen eval set -- :func:`freeze` produces that from the accepted subset).
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


# =========================================================================== #
# Write side: synthetic bootstrap -> review staging -> human-reviewed freeze
#
# The read side above verifies a golden set; this side *produces* one, in two
# human-gated stages that mirror the design's generate -> review -> freeze flow.
# The two stages differ deliberately in how they touch the vault:
#
# * :func:`bootstrap` never mutates the vault through the transaction path -- it
#   writes an *uncommitted* review scratchpad via a raw filesystem write to the
#   store's on-disk root (the same "store.root as a filesystem handle" pattern
#   the runner uses for in-process ripgrep search, for an operation the
#   ``VaultStore`` protocol deliberately does not offer: an un-committed write).
#   Its signature carries no ``vault_root`` precisely because it must not commit.
# * :func:`freeze` writes the frozen ``golden.jsonl`` + ``MANIFEST.json`` through
#   exactly one :class:`~knotica.core.transaction.VaultTransaction` (one commit,
#   the single-writer invariant), which is why it takes ``vault_root``.
# =========================================================================== #

#: ``source`` field the frozen ``MANIFEST.json`` records for a bootstrapped set --
#: the dataset-level *provenance* marker. Distinct from the per-record
#: ``QARecord.source`` (:data:`_RECORD_SOURCE`): the records read as ordinary
#: human-curated examples (no schema change), while the manifest carries the
#: "these began as synthetic candidates" provenance the records do not.
_MANIFEST_SOURCE = "synthetic"

#: The frozen ``QARecord.source`` enum value: human review-and-freeze *is* a
#: curation act, so the frozen enum is reused rather than migrated for a new
#: ``synthetic`` value. Equal to ``curate_example.py``'s source by design.
_RECORD_SOURCE = "curate_example"

#: The ``QARecord.verdict`` a frozen golden pair carries: an accepted reference
#: answer is a good one by definition (the human vouched for it).
_RECORD_VERDICT = "good"

#: The ``QARecord.model`` a frozen golden record carries. The record reads as a
#: human-curated example (mirroring ``curate_example.py``'s ``"unknown"``); the
#: synthetic-generation provenance lives in the manifest, not the record.
_RECORD_MODEL = "unknown"

#: The :class:`~knotica.core.transaction.VaultTransaction` op name + title for a
#: freeze commit. Reuses ``curate_example``'s op grammar (``QARecord.source``
#: already carries the provenance distinction, so no new op grammar is needed).
_FREEZE_OP = "curate_example"
_FREEZE_TITLE = "freeze golden set"

#: Answer-token budget for one synthesis call. A module default here; the packaged
#: eval constants live in ``evals.config`` and the caller passes ``snapshot``.
_BOOTSTRAP_MAX_TOKENS = 1024

#: Candidate JSON keys (the shape both the LLM emits and :func:`freeze` reads).
_QUESTION_KEY = "question"
_ANSWER_KEY = "reference_answer"
_CITATIONS_KEY = "citations"
_PAGES_KEY = "pages_used"

#: The model-supplied synthesis field carrying 1-3 short verbatim excerpts the
#: reference answer is grounded in; the parser turns these into located
#: :data:`_SUPPORT_KEY` provenance entries. Absent when the model omits it.
_SUPPORT_QUOTES_KEY = "support_quotes"

#: The candidate key carrying the located provenance spans (the review-app
#: contract). Omitted entirely when the model returned no usable quote -- the
#: app treats absence and an empty list identically via ``candidate.get(...)``.
_SUPPORT_KEY = "support"

#: Field names of one located/unlocated support entry. A located entry carries
#: all five; an unlocated one carries only quote/page/verified (``verified``
#: ``False``, no line numbers) -- never a guessed range.
_SUPPORT_QUOTE_KEY = "quote"
_SUPPORT_PAGE_KEY = "page"
_SUPPORT_LINE_START_KEY = "line_start"
_SUPPORT_LINE_END_KEY = "line_end"
_SUPPORT_VERIFIED_KEY = "verified"

#: The topic's schema overlay -- structural, not an entity page, so it is excluded
#: from bootstrap generation (mirrors ``harness``'s content-page rule; kept a local
#: constant per the convention of not importing a sibling module's private symbol).
_SCHEMA_OVERLAY_FILENAME = "SCHEMA.md"

_CODE_FENCE = "```"

#: The packaged synthesis prompt -- code, not vault content, so it is stable and
#: hashable (a generation run is reproducible for a fixed page set + snapshot).
_BOOTSTRAP_SYSTEM_PROMPT = (
    "You are helping bootstrap a held-out evaluation set for a knowledge wiki.\n"
    "\n"
    "You will be given one wiki entity page (its frontmatter and body). Read it and "
    "write ONE high-quality question-and-answer pair that a correct answer to this "
    "wiki should get right:\n"
    "\n"
    "- The question must be answerable **only** from this page -- a specific, factual "
    "question about the entity, not a vague or yes/no one.\n"
    "- The reference answer must be grounded strictly in the page; do not add outside "
    "knowledge.\n"
    "- List the citation keys the answer relies on: the bare keys of the stored "
    "sources the page cites (its `sources` frontmatter values), such that "
    "`sources/<topic>/<key>.md` holds that source. Use an empty list if the page "
    "cites no stored source.\n"
    "- List 1 to 3 SHORT support quotes: verbatim excerpts copied "
    "character-for-character from the page above that the reference answer is "
    "grounded in. Copy each one exactly as it appears (do not paraphrase, "
    "summarize, re-wrap, or fix typos) and keep each to a single sentence or "
    "phrase.\n"
    "\n"
    "Respond with a single JSON object and nothing else, of exactly this shape:\n"
    '{"question": "<one question>", "reference_answer": "<grounded answer>", '
    '"citations": ["<source-key>", ...], '
    '"support_quotes": ["<verbatim excerpt>", ...]}\n'
    "\n"
    "Do not wrap the JSON in code fences or add any prose around it."
)


class GoldenCandidateError(ValueError):
    """A golden-set candidate triple was not in the expected shape.

    Raised (never swallowed) when the LLM's synthesis response does not parse into
    a ``{question, reference_answer, citations}`` object during :func:`bootstrap`,
    or when a human-edited accepted candidate is missing its question/answer at
    :func:`freeze` time. Subclasses :class:`ValueError` to match the codebase's
    parse-error convention (an adapter catches it into the house error envelope).
    """


class GoldenSetFloorWarning(UserWarning):
    """A frozen golden set has fewer than :data:`EVAL_MIN_GOLDEN` records.

    Emitted (not raised) by :func:`freeze` -- the human is the gate, so a small
    set still freezes; the scalar is just noisier until more pairs are added.
    """


@dataclass(frozen=True, kw_only=True)
class FreezeResult:
    """The outcome of a completed :func:`freeze`.

    ``manifest`` is the sibling ``MANIFEST.json`` as written (its ``sha256``
    content-addresses the frozen ``golden.jsonl`` bytes); ``below_floor`` is
    ``True`` when the set fell under :data:`EVAL_MIN_GOLDEN` (the warning that was
    also emitted); ``warnings`` carries any secret-scrub findings from the write.
    """

    manifest: GoldenManifest
    dataset_path: str
    manifest_path: str
    commit_sha: str
    changed: bool
    below_floor: bool
    warnings: tuple[KnoticaWarning, ...]


def bootstrap(
    store: VaultStore,
    topic: str,
    llm_client: LLMClient,
    snapshot: str,
) -> list[dict[str, object]]:
    """Synthesize golden-set candidates from a topic's entity pages (the generate stage).

    For each of the topic's entity pages, asks the injected LLM (at
    ``temperature=0`` with ``snapshot``) to synthesize one candidate
    ``(question, reference_answer, citations)`` triple grounded in that page,
    plus verbatim support quotes located back to deterministic 1-based line
    ranges (an optional ``support`` list) so a reviewer can see and deep-link the
    evidence. The candidates are written to the *uncommitted* review staging file
    (:func:`golden_staging_path`) for a human to edit and accept, and also
    returned so the caller can surface them. This never writes ``golden.jsonl``
    and never commits -- only the human-gated :func:`freeze` does that.

    Args:
        store: The vault storage backend (must expose an on-disk ``root``).
        topic: The topic whose entity pages seed the candidates.
        llm_client: The injected LLM seam; tests pass a ``FakeLLMClient`` for a
            zero-network run.
        snapshot: The exact dated model snapshot to synthesize with (the caller
            passes the pinned worker/strong snapshot from ``evals.config``).

    Returns:
        The generated candidate dicts, one per entity page, in page order.

    Raises:
        GoldenCandidateError: If a synthesis response does not parse into the
            candidate shape.
    """
    candidates = [
        _synthesize_candidate(llm_client, snapshot, topic, page)
        for page in entity_pages(store, topic)
    ]
    _write_staging(store, topic, candidates)
    return candidates


def freeze(
    store: VaultStore,
    vault_root: str | PurePath,
    topic: str,
    accepted: Sequence[Mapping[str, object]],
) -> FreezeResult:
    """Freeze human-accepted candidates into the topic's held-out golden set.

    Builds a :class:`~knotica.core.records.QARecord` from each accepted candidate
    (``source: curate_example``), verifies the set is disjoint from the flywheel
    trainset **before** writing anything, then writes ``golden.jsonl`` and its
    sibling ``MANIFEST.json`` (``sha256`` content-addressing the frozen bytes,
    ``split: held_out``) through exactly one
    :class:`~knotica.core.transaction.VaultTransaction` -- one commit. Freezing
    fewer than :data:`EVAL_MIN_GOLDEN` records still succeeds but emits a
    :class:`GoldenSetFloorWarning` (the human is the gate).

    Args:
        store: The vault storage backend (the same vault ``vault_root`` names).
        vault_root: The already-resolved vault root the transaction commits on.
        topic: The topic the golden set belongs to.
        accepted: The human-reviewed candidate dicts to freeze (each carrying at
            least a ``question`` and ``reference_answer``; ``citations`` and
            ``pages_used`` default to empty when absent).

    Returns:
        A :class:`FreezeResult` with the written manifest, the commit sha, and any
        secret-scrub / below-floor findings.

    Raises:
        GoldenCandidateError: If an accepted candidate lacks a question/answer.
        GoldenSetContaminationError: If a frozen question also appears in the
            topic's flywheel ``qa.jsonl`` -- nothing is written.
    """
    records = [_build_golden_record(topic, candidate) for candidate in accepted]
    # Held-out-split guard at freeze time (not only at read time): a contaminated
    # set is refused before any byte is written, so nothing is committed.
    verify_disjoint_from_trainset(store, topic, records)

    below_floor = len(records) < EVAL_MIN_GOLDEN
    if below_floor:
        warnings.warn(_floor_message(topic, len(records)), GoldenSetFloorWarning, stacklevel=2)

    golden_body, manifest = _frozen_bytes_and_manifest(records)
    dataset_path = golden_dataset_path(topic)
    manifest_path = golden_manifest_path(topic)
    with VaultTransaction(store, vault_root, _FREEZE_OP, topic, _FREEZE_TITLE) as txn:
        txn.write(dataset_path, golden_body)
        txn.write(manifest_path, _render_manifest(manifest))
    result = txn.result
    freeze_result = FreezeResult(
        manifest=manifest,
        dataset_path=dataset_path,
        manifest_path=manifest_path,
        commit_sha=result.commit_sha,
        changed=result.changed,
        below_floor=below_floor,
        warnings=result.warnings(),
    )
    if len(records) >= EVAL_MIN_GOLDEN:
        from knotica.core.baseline_probe import maybe_auto_baseline_probe

        maybe_auto_baseline_probe(store, vault_root, topic)
    return freeze_result


# --------------------------------------------------------------------------- #
# Generate-stage helpers
# --------------------------------------------------------------------------- #


def entity_pages(store: VaultStore, topic: str) -> list[Page]:
    """Read the topic's entity pages -- every content page bar the schema overlay.

    Public: the golden bootstrap and the trainset cold-start both synthesize
    from this same page set, so the definition of "entity page" stays single.
    """
    overlay = f"{topic}/{_SCHEMA_OVERLAY_FILENAME}"
    return [
        read_page(store, topic, path) for path in iter_page_paths(store, topic) if path != overlay
    ]


def _synthesize_candidate(
    llm_client: LLMClient, snapshot: str, topic: str, page: Page
) -> dict[str, object]:
    """Make one ``temperature=0`` synthesis call for ``page`` and parse the candidate."""
    completion = llm_client.complete(
        snapshot=snapshot,
        system=_BOOTSTRAP_SYSTEM_PROMPT,
        messages=[Message(role="user", content=_render_page_prompt(topic, page))],
        temperature=0.0,
        max_tokens=_BOOTSTRAP_MAX_TOKENS,
    )
    return _parse_candidate(completion.text, _page_name(topic, page), page.raw)


def _render_page_prompt(topic: str, page: Page) -> str:
    """Compose the user message: the topic and the entity page's full raw text."""
    return f"Topic: {topic}\n\nEntity page ({_page_name(topic, page)}):\n\n{page.raw.strip()}"


def _page_name(topic: str, page: Page) -> str:
    """The topic-relative page name (``agentic-systems/react.md`` -> ``react``)."""
    return page.path.removeprefix(f"{topic}/").removesuffix(".md")


def _parse_candidate(text: str, page_name: str, page_raw: str) -> dict[str, object]:
    """Parse one synthesis response into a candidate dict, or raise a typed error.

    Adds ``pages_used`` deterministically (the entity page the candidate was
    generated from) -- the model supplies only the question/answer/citations and
    the raw support quotes. Each supplied quote is located in ``page_raw`` and
    turned into a :data:`_SUPPORT_KEY` provenance entry with a deterministic,
    1-based inclusive line range (model-supplied line numbers are never trusted);
    the key is omitted when the model returned no usable quote.
    """
    payload = _load_candidate_json(text)
    question = _required_candidate_str(payload, _QUESTION_KEY)
    reference_answer = _required_candidate_str(payload, _ANSWER_KEY)
    citations = _optional_candidate_str_list(payload, _CITATIONS_KEY)
    candidate: dict[str, object] = {
        _QUESTION_KEY: question,
        _ANSWER_KEY: reference_answer,
        _CITATIONS_KEY: citations,
        _PAGES_KEY: [page_name],
    }
    support = _build_support(payload, page_name, page_raw)
    if support:
        candidate[_SUPPORT_KEY] = support
    return candidate


def _build_support(
    payload: Mapping[str, object], page_name: str, page_raw: str
) -> list[dict[str, object]]:
    """Locate each model-supplied support quote in ``page_raw`` (best-effort provenance).

    Tolerant on both axes: an absent or non-list ``support_quotes`` yields no
    entries, and a malformed individual entry (a non-string or blank quote) is
    skipped rather than raising -- provenance is a nice-to-have that must never
    fail an otherwise-good candidate. A quote that cannot be located is kept as a
    ``verified: False`` entry (never guessed, never dropped silently); only shape
    noise is discarded.
    """
    quotes = payload.get(_SUPPORT_QUOTES_KEY)
    if not isinstance(quotes, (list, tuple)):
        return []
    return [
        _support_entry(quote, page_name, page_raw)
        for quote in quotes
        if isinstance(quote, str) and quote.strip()
    ]


def _support_entry(quote: str, page_name: str, page_raw: str) -> dict[str, object]:
    """One located/unlocated provenance entry for ``quote`` (the review-app contract)."""
    span = _locate_span(page_raw, quote)
    if span is None:
        return {
            _SUPPORT_QUOTE_KEY: quote,
            _SUPPORT_PAGE_KEY: page_name,
            _SUPPORT_VERIFIED_KEY: False,
        }
    line_start, line_end = span
    return {
        _SUPPORT_QUOTE_KEY: quote,
        _SUPPORT_PAGE_KEY: page_name,
        _SUPPORT_LINE_START_KEY: line_start,
        _SUPPORT_LINE_END_KEY: line_end,
        _SUPPORT_VERIFIED_KEY: True,
    }


def _locate_span(raw: str, quote: str) -> tuple[int, int] | None:
    """Locate ``quote`` in ``raw`` and return its 1-based inclusive line range.

    A two-rung matching ladder, first-occurrence-wins on each rung: an exact
    substring hit first; then a whitespace-normalized hit (runs of whitespace and
    newlines collapsed to a single space on both sides), which recovers a quote
    the model copied across a soft-wrapped line boundary, mapped back to the real
    offsets in ``raw``. Returns ``None`` when neither rung matches -- the caller
    records that as an unverified entry rather than guessing a range.
    """
    exact = raw.find(quote)
    if exact != -1:
        return _line_range(raw, exact, exact + len(quote) - 1)
    normalized_quote = _normalize_whitespace(quote)
    if not normalized_quote:
        return None
    normalized_raw, offsets = _normalize_whitespace_with_offsets(raw)
    hit = normalized_raw.find(normalized_quote)
    if hit == -1:
        return None
    last = hit + len(normalized_quote) - 1
    return _line_range(raw, offsets[hit], offsets[last])


def _line_range(raw: str, first_index: int, last_index: int) -> tuple[int, int]:
    """The 1-based inclusive ``(start, end)`` lines spanning ``raw``'s [first, last] chars."""
    return raw.count("\n", 0, first_index) + 1, raw.count("\n", 0, last_index) + 1


def _normalize_whitespace(text: str) -> str:
    """Collapse every run of whitespace (newlines included) to one space; strip the ends."""
    return " ".join(text.split())


def _normalize_whitespace_with_offsets(raw: str) -> tuple[str, list[int]]:
    """Whitespace-normalize ``raw``, returning the result and a per-char index map.

    ``offsets[i]`` is the index in ``raw`` of the character that produced
    ``normalized[i]``; a collapsed whitespace run maps to the index of its first
    whitespace character. The map lets :func:`_locate_span` translate a
    normalized-space match back to real ``raw`` offsets for the line-range count.
    """
    normalized: list[str] = []
    offsets: list[int] = []
    in_whitespace = False
    for index, char in enumerate(raw):
        if char.isspace():
            if not in_whitespace:
                normalized.append(" ")
                offsets.append(index)
                in_whitespace = True
            continue
        normalized.append(char)
        offsets.append(index)
        in_whitespace = False
    return "".join(normalized), offsets


def _load_candidate_json(text: str) -> dict[str, object]:
    """Parse the response text (tolerating a code fence) into a JSON object."""
    try:
        payload = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError as error:
        raise GoldenCandidateError(
            f"the synthesis response was not valid JSON: {error}."
        ) from error
    if not isinstance(payload, dict):
        raise GoldenCandidateError(
            "the synthesis response must be a JSON object with "
            f"{_QUESTION_KEY!r}, {_ANSWER_KEY!r} and {_CITATIONS_KEY!r} fields, "
            f"got {type(payload).__name__}."
        )
    return payload


def _strip_code_fence(text: str) -> str:
    """Return ``text`` with a single surrounding markdown code fence removed, if present."""
    stripped = text.strip()
    if not stripped.startswith(_CODE_FENCE):
        return stripped
    lines = stripped.splitlines()
    body = lines[1:]  # drop the opening ``` (or ```json) line
    if body and body[-1].strip() == _CODE_FENCE:
        body = body[:-1]
    return "\n".join(body).strip()


def _write_staging(
    store: VaultStore, topic: str, candidates: Sequence[Mapping[str, object]]
) -> None:
    """Write the candidates to the uncommitted review staging file.

    A raw filesystem write to the store's on-disk root -- the one place the eval
    subsystem writes outside :class:`~knotica.core.transaction.VaultTransaction`,
    because the staging file is a deliberately un-committed review scratchpad and
    the ``VaultStore`` protocol offers no un-committed write.
    """
    body = "".join(json.dumps(candidate, ensure_ascii=False) + "\n" for candidate in candidates)
    path = _staging_abspath(store, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _staging_abspath(store: VaultStore, topic: str) -> Path:
    """Resolve the staging file's absolute path, refusing any escape from the vault root.

    Replicates the store's path-confinement for this one raw write (the store's
    own confinement is bypassed here), so a malformed ``topic`` can never aim the
    write outside the vault.
    """
    root = getattr(store, "root", None)
    if root is None:
        raise TypeError(
            "bootstrap needs a filesystem-rooted vault store (one exposing a `.root` "
            "path, like LocalFSStore) to write the review staging file."
        )
    resolved_root = Path(root).resolve()
    candidate = (resolved_root / golden_staging_path(topic)).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError(f"the golden staging path for topic {topic!r} escapes the vault root.")
    return candidate


# --------------------------------------------------------------------------- #
# Freeze-stage helpers
# --------------------------------------------------------------------------- #


def _build_golden_record(topic: str, candidate: Mapping[str, object]) -> QARecord:
    """Build one frozen golden ``QARecord`` from an accepted candidate dict."""
    query = _required_candidate_str(candidate, _QUESTION_KEY)
    answer = _required_candidate_str(candidate, _ANSWER_KEY)
    return QARecord(
        id=_golden_id(query, answer),
        topic=topic,
        created=datetime.now(UTC).isoformat(),
        query=query,
        pages_used=_optional_candidate_str_tuple(candidate, _PAGES_KEY),
        answer=answer,
        citations=_optional_candidate_str_tuple(candidate, _CITATIONS_KEY),
        verdict=_RECORD_VERDICT,
        corrected_answer=None,
        source=_RECORD_SOURCE,
        model=_RECORD_MODEL,
    )


def _golden_id(query: str, answer: str) -> str:
    """A deterministic record id from ``(query, answer)`` -- stable across re-freezes."""
    digest = hashlib.sha256("\x00".join((query, answer)).encode("utf-8")).hexdigest()
    return f"golden-{digest[:16]}"


def _frozen_bytes_and_manifest(records: Sequence[QARecord]) -> tuple[str, GoldenManifest]:
    """The golden.jsonl body and its content-addressing manifest for ``records``.

    The manifest's ``sha256`` is taken over the *scrubbed* form of the body -- the
    exact bytes the transaction stores after its secret scrub (identical to the raw
    body when there is nothing to redact) -- so the round-trip through :func:`load`
    stays exact even if a secret slipped into a reviewed answer.
    """
    golden_body = _jsonl_body(records)
    scrubbed_body, _spans = scrub(golden_body)
    manifest = GoldenManifest(
        sha256=body_sha256(scrubbed_body),
        version=datetime.now(UTC).strftime("%Y-%m-%d"),
        source=_MANIFEST_SOURCE,
        split=GOLDEN_SPLIT,
        size=len(records),
    )
    return golden_body, manifest


def _jsonl_body(records: Sequence[QARecord]) -> str:
    """Render the whole frozen golden set: one JSON line per record, newline-terminated."""
    return "".join(record.to_json_line() + "\n" for record in records)


def _render_manifest(manifest: GoldenManifest) -> str:
    """Serialize a :class:`GoldenManifest` to its ``MANIFEST.json`` text (trailing newline)."""
    payload = {
        "sha256": manifest.sha256,
        "version": manifest.version,
        "source": manifest.source,
        "split": manifest.split,
        "size": manifest.size,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _floor_message(topic: str, size: int) -> str:
    """The below-floor warning text (names the shortfall and that it is not a block)."""
    return (
        f"The golden set frozen for topic '{topic}' has {size} record(s), below the "
        f"recommended floor of {EVAL_MIN_GOLDEN}. The eval scalar will be noisier until "
        "more reviewed pairs are frozen; this is a warning, not a hard block."
    )


# --------------------------------------------------------------------------- #
# Candidate-field boundary parsing (accepted dicts arrive human-edited)
# --------------------------------------------------------------------------- #


def _required_candidate_str(candidate: Mapping[str, object], key: str) -> str:
    """Return a required non-empty string candidate field, typed-error otherwise."""
    value = candidate.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GoldenCandidateError(
            f"a golden candidate must carry a non-empty string {key!r}, got {value!r}."
        )
    return value


def _optional_candidate_str_list(candidate: Mapping[str, object], key: str) -> list[str]:
    """Return an optional list-of-strings candidate field (default ``[]``), typed-error otherwise."""
    return list(_optional_candidate_str_tuple(candidate, key))


def _optional_candidate_str_tuple(candidate: Mapping[str, object], key: str) -> tuple[str, ...]:
    """Return an optional list-of-strings candidate field as a tuple (default ``()``)."""
    value = candidate.get(key, [])
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise GoldenCandidateError(
            f"a golden candidate's {key!r} must be a list of strings, got {value!r}."
        )
    return tuple(value)

"""Filing a gap no eval produced -- the ``reported`` and ``retracted`` origins.

The gap queue's third and fourth writers. ``core.gap_classifier`` files the gaps a
regression *measured*; this module files the two kinds that carry no measurement:
one a human reported conversationally (:func:`report_gap`), one a guillotine
verdict left behind when it weakened a claim (:func:`file_retracted_gap`). Both
compose the same ``genuine_gap``/``open`` :class:`GapRecord` with empty eval
evidence and write it through the classifier's own ``write_gap_records`` path, so
its ``(qa_id, fault_class)`` open-dedup drops a repeat -- a chatty client cannot
spam the queue.

The ``qa_id`` is content-addressed from the proposer text under a per-origin
prefix, so identical text collides (that is what makes the dedup work) while a
``reported`` and a ``retracted`` gap of byte-identical text stay distinct.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from knotica.core.gap_classifier import FaultClass, write_gap_records
from knotica.core.gapfill.queue_io import _invalid, _open_genuine_gaps, _utc_now_iso
from knotica.core.page import page_path
from knotica.core.records import (
    GAP_ORIGIN_REPORTED,
    GAP_ORIGIN_RETRACTED,
    GapEvidence,
    GapRecord,
)
from knotica.core.topics import require_topic
from knotica.store import VaultStore

#: Sentinel eval-provenance fields on a reported gap: it was not produced by the
#: regression classifier (``origin="reported"`` carries that signal) and belongs
#: to no eval generation, so both read as 0 rather than a fabricated version/gen.
_NO_CLASSIFIER = 0
_NO_GENERATION = 0
#: Per-origin prefix for a synthetic ``qa_id`` derived from proposer text (no
#: golden record backs it) -- mirrors ``evals.golden``'s ``golden-<hash>`` scheme.
#: The prefix keeps a reported gap and a retracted gap distinct even when their
#: source text is byte-identical (different provenance must not dedup together).
_ORIGIN_QA_ID_PREFIX: Mapping[str, str] = {
    GAP_ORIGIN_REPORTED: "reported-",
    GAP_ORIGIN_RETRACTED: "retracted-",
}


@dataclass(frozen=True)
class ReportedGapResult:
    """The outcome of one :func:`report_gap`, for the tool envelope to render.

    ``written`` is ``False`` when an open gap with the same deterministic ``qa_id``
    already existed -- the write is a dedup no-op (composes with
    ``write_gap_records``' own ``(qa_id, fault_class)`` dedup), so a repeated
    identical report never appends a duplicate record.
    """

    topic: str
    gap_id: str
    qa_id: str
    question: str
    fault_class: str
    status: str
    origin: str
    reason: str | None
    reference_pages: tuple[str, ...]
    written: bool


def report_gap(
    store: VaultStore,
    root: str | Path,
    topic: str,
    question: str,
    *,
    reason: str | None = None,
    reference_pages: Sequence[str] = (),
    clock: Callable[[], str] | None = None,
) -> ReportedGapResult:
    """File one conversationally reported ``genuine_gap`` into the P1 queue.

    The client-as-brain calls this when a wiki query is answered poorly and the
    user confirms the gap. Constructs a ``genuine_gap``/``open`` :class:`GapRecord`
    with ``origin="reported"``, a ``qa_id`` derived deterministically from the
    question text (so identical reports collide), and empty eval-evidence fields
    (a reported gap carries no per-id score). Writes via the existing
    ``write_gap_records`` path in its own ``VaultTransaction`` -- whose
    ``(qa_id, fault_class)`` open-dedup drops a repeat of the same question, so a
    chatty client cannot spam the queue. ``reason`` is advisory context surfaced
    in the result and persisted verbatim (after stripping) as the record's
    ``reported_reason`` field. ``clock`` injects the
    ``detected_at`` stamp for deterministic tests. Raises a typed ``KnoticaError``
    on an empty/blank question (never fabricates content).
    """
    cleaned_question = question.strip()
    if not cleaned_question:
        raise _invalid(
            "a reported gap requires a non-empty question",
            "Pass the actual wiki query the user could not get answered.",
        )
    return _file_synthetic_gap(
        store,
        root,
        topic,
        cleaned_question,
        origin=GAP_ORIGIN_REPORTED,
        reason=reason,
        reference_pages=reference_pages,
        clock=clock,
    )


def file_retracted_gap(
    store: VaultStore,
    root: str | Path,
    topic: str,
    claim: str,
    *,
    verdict: str,
    report_path: str,
    reference_pages: Sequence[str] = (),
    clock: Callable[[], str] | None = None,
) -> ReportedGapResult:
    """File one ``origin="retracted"`` gap for a claim a guillotine verdict weakened.

    Called by the guillotine apply path after a RETRACT / DEMOTE / DISPUTE /
    DELETE_UNSUPPORTED_SYNTHESIS commit lands: the weakened claim text becomes the
    gap question verbatim (that knowledge now needs re-sourcing) and
    ``reported_reason`` records the verdict name + the guillotine report path. The
    ``qa_id`` is derived deterministically from the claim (shared with
    ``report_gap``) under a distinct ``retracted-`` prefix, so re-applying the same
    verdict dedups but a same-text *reported* gap stays separate. Writes via the
    existing ``write_gap_records`` path in its own ``VaultTransaction``. Raises a
    typed ``KnoticaError`` on an empty/blank claim (the caller isolates failures).
    """
    cleaned_claim = claim.strip()
    if not cleaned_claim:
        raise _invalid(
            "a retracted gap requires a non-empty claim",
            "Pass the weakened claim text the guillotine acted on.",
        )
    return _file_synthetic_gap(
        store,
        root,
        topic,
        cleaned_claim,
        origin=GAP_ORIGIN_RETRACTED,
        reason=f"{verdict} · {report_path}",
        reference_pages=reference_pages,
        clock=clock,
    )


def _file_synthetic_gap(
    store: VaultStore,
    root: str | Path,
    topic: str,
    text: str,
    *,
    origin: str,
    reason: str | None,
    reference_pages: Sequence[str],
    clock: Callable[[], str] | None,
) -> ReportedGapResult:
    """Shared body for filing an origin-tagged synthetic ``genuine_gap`` (no eval evidence).

    ``text`` is the already-cleaned proposer text (question or weakened claim);
    the ``origin`` selects the ``qa_id`` prefix so different-provenance gaps with
    identical text never collide. The record is written through the reused
    ``write_gap_records`` path, whose ``(qa_id, fault_class)`` open-dedup drops a
    repeat, so ``written`` reports whether this call actually appended a record.
    """
    # A synthetic gap is filed AGAINST a topic, never a way to create one — an
    # unguarded report once scaffolded a stray topic the loop began tending.
    topic = require_topic(store, topic)
    stamp = clock or _utc_now_iso
    qa_id = _synthetic_qa_id(text, origin)
    fault_class = FaultClass.GENUINE_GAP
    already_open = any(gap.qa_id == qa_id for gap in _open_genuine_gaps(store, topic))
    pages = tuple(reference_pages)
    cleaned_reason = (reason.strip() or None) if reason else None
    record = _build_synthetic_gap(
        topic,
        qa_id,
        fault_class,
        text,
        pages,
        origin=origin,
        reported_reason=cleaned_reason,
        detected_at=stamp(),
        # Checked against the store, exactly as the eval path checks it -- never asserted.
        reference_pages_exist=any(store.exists(page_path(topic, page)) for page in pages),
    )
    write_gap_records(store, root, topic, [record])
    return ReportedGapResult(
        topic=topic,
        gap_id=record.gap_id,
        qa_id=qa_id,
        question=text,
        fault_class=fault_class,
        status="open",
        origin=origin,
        reason=cleaned_reason,
        reference_pages=pages,
        written=not already_open,
    )


def _build_synthetic_gap(
    topic: str,
    qa_id: str,
    fault_class: str,
    question: str,
    reference_pages: tuple[str, ...],
    *,
    origin: str,
    reported_reason: str | None,
    detected_at: str,
    reference_pages_exist: bool,
) -> GapRecord:
    """Compose an origin-tagged synthetic gap record with empty eval evidence."""
    return GapRecord(
        gap_id=_reported_gap_id(topic, qa_id, fault_class),
        topic=topic,
        qa_id=qa_id,
        fault_class=fault_class,
        status="open",
        classifier_version=_NO_CLASSIFIER,
        detected_generation=_NO_GENERATION,
        detected_at=detected_at,
        scalar_at_detection=0.0,
        baseline_scalar=0.0,
        question=question,
        reference_pages=reference_pages,
        reference_pages_exist=reference_pages_exist,
        evidence=GapEvidence(
            quality_delta=0.0,
            qa_accuracy_delta=0.0,
            citation_validity_delta=0.0,
            retrieval_trace=(),
            pages_added=(),
            pages_removed=(),
            prior_generation=_NO_GENERATION,
        ),
        manifest_ref="",
        origin=origin,
        reported_reason=reported_reason,
    )


def _synthetic_qa_id(text: str, origin: str) -> str:
    """A deterministic synthetic ``qa_id`` from proposer text (stable across calls).

    Mirrors ``evals.golden``'s content-addressed id hashing (sha256, 16-hex slug)
    so identical text collides on the same ``qa_id`` -- the property
    ``write_gap_records``' open-dedup relies on to reject a repeat. The origin's
    prefix keeps a ``reported`` and a ``retracted`` gap distinct even when their
    source text is byte-identical.
    """
    import hashlib

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{_ORIGIN_QA_ID_PREFIX[origin]}{digest[:16]}"


def _reported_gap_id(topic: str, qa_id: str, fault_class: str) -> str:
    """Stable 16-hex gap id over the identifying triple (mirrors the classifier's scheme)."""
    import hashlib

    return hashlib.sha1(f"{topic}|{qa_id}|{fault_class}".encode()).hexdigest()[:16]

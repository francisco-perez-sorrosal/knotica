"""``gaps.jsonl`` -- one knowledge-gap record per detected or reported gap.

Written by the loop's regression classifier (``measured``), the ``gap_report``
tool (``reported``) and the guillotine (``retracted``); consumed out-of-process
by the suggestion queue and the attention view.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields

from knotica.core.records.fields import (
    RecordParseError,
    _load_json_object,
    _optional_str_absent,
    _optional_str_or_default,
    _required_bool,
    _required_int,
    _required_number,
    _required_str,
    _required_str_tuple,
    _validate_enum,
    _validate_schema_version,
)

__all__ = [
    "GAP_FAULT_CLASSES",
    "GAP_ORIGIN_MEASURED",
    "GAP_ORIGIN_REPORTED",
    "GAP_ORIGIN_RETRACTED",
    "GAP_ORIGINS",
    "GAP_SCHEMA_VERSION",
    "GAP_STATUSES",
    "GapEvidence",
    "GapRecord",
    "parse_gaps_jsonl",
]

#: Current schema_version of the knowledge-gap record.
GAP_SCHEMA_VERSION = 1

#: Only knowledge-cause verdicts are ever persisted as a gap record; a prompt-cause
#: fault (generation/retrieval) routes to the arena heal and is never written here.
GAP_FAULT_CLASSES: frozenset[str] = frozenset({"genuine_gap", "dilution"})
#: Lifecycle of one gap record: P1 writes ``open``; P3/P4 flip it terminal.
GAP_STATUSES: frozenset[str] = frozenset({"open", "resolved", "dismissed"})

#: Provenance of a gap record. ``measured`` gaps are eval-proven (the loop's
#: regression classifier wrote them from a scored delta); ``reported`` gaps are
#: filed conversationally by the client-as-brain via the ``gap_report`` tool;
#: ``retracted`` gaps are filed by the guillotine when an applied verdict weakens
#: knowledge (a retract/demote/dispute/delete leaves a hole to re-source). Neither
#: ``reported`` nor ``retracted`` gaps carry per-id eval evidence (their evidence
#: fields are zero/empty by construction). Additive-only: pre-provenance records
#: parse as ``measured``.
GAP_ORIGIN_MEASURED = "measured"
GAP_ORIGIN_REPORTED = "reported"
GAP_ORIGIN_RETRACTED = "retracted"
GAP_ORIGINS: frozenset[str] = frozenset(
    {GAP_ORIGIN_MEASURED, GAP_ORIGIN_REPORTED, GAP_ORIGIN_RETRACTED}
)


@dataclass(frozen=True, kw_only=True)
class GapEvidence:
    """The advisory, detection-time snapshot attached to one gap record.

    A frozen snapshot of the score deltas and retrieval-trace set-diffs that
    justified the verdict -- mirrors :class:`MetricsComponents`'s nested-object
    precedent. Values are stored verbatim from the eval manifest's per-id delta
    and per-example trace; a consumer may rank on them but must not assume they
    still hold at read time (the vault moves on).
    """

    quality_delta: float
    qa_accuracy_delta: float
    citation_validity_delta: float
    retrieval_trace: tuple[str, ...]
    pages_added: tuple[str, ...]
    pages_removed: tuple[str, ...]
    prior_generation: int


@dataclass(frozen=True, kw_only=True)
class GapRecord:
    """One knowledge-gap record, a single ``gaps.jsonl`` line.

    Written by the loop's regression classifier for every ``genuine_gap`` or
    ``dilution`` verdict, consumed out-of-process by the P3 suggestion queue.
    ``reference_pages`` and ``evidence.retrieval_trace`` are stored verbatim from
    ``QARecord.pages_used`` / the manifest trace (no re-derivation), so the P3
    page-name join holds. Parsing tolerates unknown extra fields and probes
    ``schema_version`` first (dec-006 record-schema-freeze discipline).

    Unknown top-level fields are not merely tolerated on read -- they are
    **carried** on :attr:`extra` and re-emitted verbatim by
    :meth:`to_json_line`, so a full-file rewrite (the drain's answered-in-vault
    stamp and the dismiss/reopen decision path both rewrite every line)
    round-trips a field this version does not know about instead of erasing it.
    That is what makes "additive-only evolution" true across versions rather
    than only on ingress -- the same contract :class:`SuggestionRecord` carries,
    stated identically because the two families are rewritten by the same code.
    """

    gap_id: str
    schema_version: int = GAP_SCHEMA_VERSION
    topic: str
    qa_id: str
    fault_class: str
    status: str
    classifier_version: int
    detected_generation: int
    detected_at: str
    scalar_at_detection: float
    baseline_scalar: float
    question: str
    reference_pages: tuple[str, ...]
    reference_pages_exist: bool
    evidence: GapEvidence
    manifest_ref: str
    origin: str = GAP_ORIGIN_MEASURED
    #: Proposer-supplied context for a non-``measured`` gap: the ``reason`` a
    #: reporter gave (``gap_report``) or the guillotine verdict + report path
    #: (``retracted``). ``None`` on ``measured`` gaps and pre-feature records.
    #: Additive-only optional field (schema stays v1); kept on the gap only —
    #: never threaded onto the derived suggestion.
    reported_reason: str | None = None
    #: The reason a human gave for the most recent ``dismiss``/``reopen``
    #: decision (``core.gapfill.apply_gap_decision``) -- the gap lifecycle's
    #: mirror of ``SuggestionRecord.decided_reason``. ``None`` on a gap that has
    #: never been through a human decision, and on pre-feature records.
    #: Additive-only optional field (schema stays v1).
    decided_reason: str | None = None
    #: Drain-time stamp: the ISO-8601 UTC instant a gap-fill drain found this
    #: gap's *entire* non-empty candidate yield already stored in the vault --
    #: the gap is answered by sources the vault holds, so the fault is
    #: retrieval/linking, not acquisition. Cleared back to ``None`` by the same
    #: drain path the moment a drain stages a suggestion for the gap. ``None``
    #: on a gap no drain ever found inert, and on pre-feature records; the
    #: attention view reads it as a plain record field so the signal costs no
    #: discovery work (dec-092). Additive-only optional field (schema stays v1).
    answered_in_vault_at: str | None = None
    #: Top-level fields this version does not model, carried verbatim from the
    #: parsed line so a rewrite re-emits them (see the class docstring). Never
    #: contains a known field name -- :meth:`from_json_line` partitions on
    #: :data:`_GAP_KNOWN_FIELDS`.
    extra: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_enum("fault_class", self.fault_class, GAP_FAULT_CLASSES)
        _validate_enum("status", self.status, GAP_STATUSES)
        _validate_enum("origin", self.origin, GAP_ORIGINS)

    def to_json_line(self) -> str:
        """Serialize to one JSON line (no trailing newline), fields in schema order."""
        payload = {
            "schema_version": self.schema_version,
            "gap_id": self.gap_id,
            "topic": self.topic,
            "qa_id": self.qa_id,
            "fault_class": self.fault_class,
            "status": self.status,
            "classifier_version": self.classifier_version,
            "detected_generation": self.detected_generation,
            "detected_at": self.detected_at,
            "scalar_at_detection": self.scalar_at_detection,
            "baseline_scalar": self.baseline_scalar,
            "question": self.question,
            "reference_pages": list(self.reference_pages),
            "reference_pages_exist": self.reference_pages_exist,
            "evidence": {
                "quality_delta": self.evidence.quality_delta,
                "qa_accuracy_delta": self.evidence.qa_accuracy_delta,
                "citation_validity_delta": self.evidence.citation_validity_delta,
                "retrieval_trace": list(self.evidence.retrieval_trace),
                "pages_added": list(self.evidence.pages_added),
                "pages_removed": list(self.evidence.pages_removed),
                "prior_generation": self.evidence.prior_generation,
            },
            "manifest_ref": self.manifest_ref,
            "origin": self.origin,
            "reported_reason": self.reported_reason,
            "decided_reason": self.decided_reason,
            "answered_in_vault_at": self.answered_in_vault_at,
        }
        # Unknown fields trail the schema-ordered block so the known prefix of
        # every line stays byte-stable regardless of what a newer writer added.
        payload.update({key: value for key, value in self.extra.items() if key not in payload})
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str) -> "GapRecord":
        """Parse one ``gaps.jsonl`` line; unknown extra fields are tolerated."""
        data = _load_json_object(line, record="gaps.jsonl")
        evidence = data.get("evidence")
        if not isinstance(evidence, dict):
            raise RecordParseError(
                f"gaps.jsonl record field 'evidence' must be an object, got {evidence!r}"
            )
        return cls(
            gap_id=_required_str(data, "gap_id", record="gaps.jsonl"),
            schema_version=_required_int(data, "schema_version", record="gaps.jsonl"),
            topic=_required_str(data, "topic", record="gaps.jsonl"),
            qa_id=_required_str(data, "qa_id", record="gaps.jsonl"),
            fault_class=_required_str(data, "fault_class", record="gaps.jsonl"),
            status=_required_str(data, "status", record="gaps.jsonl"),
            classifier_version=_required_int(data, "classifier_version", record="gaps.jsonl"),
            detected_generation=_required_int(data, "detected_generation", record="gaps.jsonl"),
            detected_at=_required_str(data, "detected_at", record="gaps.jsonl"),
            scalar_at_detection=_required_number(data, "scalar_at_detection", record="gaps.jsonl"),
            baseline_scalar=_required_number(data, "baseline_scalar", record="gaps.jsonl"),
            question=_required_str(data, "question", record="gaps.jsonl"),
            reference_pages=_required_str_tuple(data, "reference_pages", record="gaps.jsonl"),
            reference_pages_exist=_required_bool(
                data, "reference_pages_exist", record="gaps.jsonl"
            ),
            evidence=GapEvidence(
                quality_delta=_required_number(evidence, "quality_delta", record="gaps.jsonl"),
                qa_accuracy_delta=_required_number(
                    evidence, "qa_accuracy_delta", record="gaps.jsonl"
                ),
                citation_validity_delta=_required_number(
                    evidence, "citation_validity_delta", record="gaps.jsonl"
                ),
                retrieval_trace=_required_str_tuple(
                    evidence, "retrieval_trace", record="gaps.jsonl"
                ),
                pages_added=_required_str_tuple(evidence, "pages_added", record="gaps.jsonl"),
                pages_removed=_required_str_tuple(evidence, "pages_removed", record="gaps.jsonl"),
                prior_generation=_required_int(evidence, "prior_generation", record="gaps.jsonl"),
            ),
            manifest_ref=_required_str(data, "manifest_ref", record="gaps.jsonl"),
            origin=_optional_str_or_default(
                data, "origin", GAP_ORIGIN_MEASURED, record="gaps.jsonl"
            ),
            reported_reason=_optional_str_absent(data, "reported_reason", record="gaps.jsonl"),
            decided_reason=_optional_str_absent(data, "decided_reason", record="gaps.jsonl"),
            answered_in_vault_at=_optional_str_absent(
                data, "answered_in_vault_at", record="gaps.jsonl"
            ),
            extra={key: value for key, value in data.items() if key not in _GAP_KNOWN_FIELDS},
        )


#: The top-level keys this version models, derived from the dataclass itself so
#: adding a field can never leave it double-counted as an "unknown" extra.
#: ``extra`` is the carrier, not a wire field, so it is excluded.
_GAP_KNOWN_FIELDS: frozenset[str] = frozenset(spec.name for spec in dataclass_fields(GapRecord)) - {
    "extra"
}


def parse_gaps_jsonl(text: str) -> list[GapRecord]:
    """Parse a full ``gaps.jsonl`` body; blank lines are skipped.

    Errors carry the 1-based line number of the offending record.
    """
    records: list[GapRecord] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(GapRecord.from_json_line(line))
        except (RecordParseError, ValueError) as error:
            raise RecordParseError(f"gaps.jsonl line {line_number}: {error}") from error
    return records

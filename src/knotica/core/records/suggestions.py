"""``suggestions.jsonl`` -- one gap x candidate join, the human-approval queue.

Staged by the gap-fill drain, decided by a human, and stamped by the ingest
gate. The record carries the gap fields it renders verbatim so a card needs no
join back to ``gaps.jsonl``.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields

from knotica.core.records.fields import (
    RecordParseError,
    _load_json_object,
    _optional_object_absent,
    _optional_str,
    _optional_str_absent,
    _required_int,
    _required_object,
    _required_str,
    _required_str_tuple,
    _validate_enum,
    _validate_schema_version,
)

__all__ = [
    "SUGGESTION_SCHEMA_VERSION",
    "SUGGESTION_STATUSES",
    "SuggestionRecord",
    "parse_suggestions_jsonl",
]

#: Current schema_version of the suggestion record.
SUGGESTION_SCHEMA_VERSION = 1

#: Lifecycle of one suggestion record. The discovery writer stages ``pending``;
#: the human gate flips ``approved``/``rejected``/``deferred`` in place; the
#: interactive ingest client flips ``ingested``. A bare tagged string (not a
#: ``StrEnum``) so an out-of-process reader round-trips it without enum coercion.
SUGGESTION_STATUSES: frozenset[str] = frozenset(
    {"pending", "approved", "rejected", "deferred", "ingested"}
)


@dataclass(frozen=True, kw_only=True)
class SuggestionRecord:
    """One gap-fill suggestion, a single ``suggestions.jsonl`` line.

    Joins a P1 ``genuine_gap`` (``gap_id``/``qa_id``/``question``/
    ``reference_pages`` copied verbatim for zero-join card rendering) to one
    ranked P2 discovered source. The candidate is stored as an **opaque JSON
    object** -- the verbatim ``SourceCandidate.to_record()`` payload -- *not* a
    typed ``SourceCandidate``: this record lives on the MCP cold-start path, so
    typing the field would drag ``discovery/`` onto that path and break the
    ``mcp_server`` isolation boundary. The candidate carries its own inner
    ``schema_version``, so the outer record and the nested candidate version
    independently.

    ``status`` is a bare tagged string read out-of-process (round-trips without
    enum coercion). Only the outer ``schema_version`` and ``status`` enum are
    validated; the nested candidate is validated only as "is a JSON object".

    Unknown top-level fields are not merely tolerated on read -- they are
    **carried** on :attr:`extra` and re-emitted verbatim by
    :meth:`to_json_line`, so a full-file rewrite (the drain and the dismiss
    cascade both rewrite every line) round-trips a field this version does not
    know about instead of erasing it. That is what makes "additive-only
    evolution" true across versions rather than only on ingress.
    """

    schema_version: int = SUGGESTION_SCHEMA_VERSION
    suggestion_id: str
    topic: str
    gap_id: str
    qa_id: str
    fault_class: str
    question: str
    reference_pages: tuple[str, ...]
    rank: int
    query_text: str
    candidate: dict[str, object]
    status: str
    proposed_at: str
    decided_at: str | None
    decided_reason: str | None
    ingested_at: str | None
    detected_generation: int
    #: Provenance carried from the originating gap (``measured``/``reported``);
    #: ``None`` on pre-provenance suggestions, so a consumer can distinguish
    #: eval-proven from conversationally reported. Additive-only optional field.
    gap_origin: str | None = None
    #: The gate's verdict on this suggestion's ingested candidate, stamped once
    #: a ``source`` candidate has been evaluated: ``{"verdict": "merged" |
    #: "refused", "scalar": float, "baseline_scalar": float, "ref": str,
    #: "reason": str | None, "regressed_questions": list | None}``. ``None``
    #: before the candidate is gated and on every pre-P4 record. Stored as an
    #: opaque JSON object (mirrors ``candidate``) -- validated only as "is a
    #: JSON object or null". Additive-only optional field (schema stays v1).
    gate_outcome: dict[str, object] | None = None
    #: Top-level fields this version does not model, carried verbatim from the
    #: parsed line so a rewrite re-emits them (see the class docstring). Never
    #: contains a known field name -- :meth:`from_json_line` partitions on
    #: :data:`_SUGGESTION_KNOWN_FIELDS`.
    extra: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_enum("status", self.status, SUGGESTION_STATUSES)
        if not isinstance(self.candidate, dict):
            raise ValueError(f"candidate must be a JSON object, got {self.candidate!r}")
        if self.gate_outcome is not None and not isinstance(self.gate_outcome, dict):
            raise ValueError(
                f"gate_outcome must be a JSON object or null, got {self.gate_outcome!r}"
            )

    def to_json_line(self) -> str:
        """Serialize to one JSON line (no trailing newline), fields in schema order."""
        payload = {
            "schema_version": self.schema_version,
            "suggestion_id": self.suggestion_id,
            "topic": self.topic,
            "gap_id": self.gap_id,
            "qa_id": self.qa_id,
            "fault_class": self.fault_class,
            "question": self.question,
            "reference_pages": list(self.reference_pages),
            "rank": self.rank,
            "query_text": self.query_text,
            "candidate": self.candidate,
            "status": self.status,
            "proposed_at": self.proposed_at,
            "decided_at": self.decided_at,
            "decided_reason": self.decided_reason,
            "ingested_at": self.ingested_at,
            "detected_generation": self.detected_generation,
            "gap_origin": self.gap_origin,
            "gate_outcome": self.gate_outcome,
        }
        # Unknown fields trail the schema-ordered block so the known prefix of
        # every line stays byte-stable regardless of what a newer writer added.
        payload.update({key: value for key, value in self.extra.items() if key not in payload})
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str) -> "SuggestionRecord":
        """Parse one ``suggestions.jsonl`` line; unknown extra fields are tolerated."""
        data = _load_json_object(line, record="suggestions.jsonl")
        return cls(
            schema_version=_required_int(data, "schema_version", record="suggestions.jsonl"),
            suggestion_id=_required_str(data, "suggestion_id", record="suggestions.jsonl"),
            topic=_required_str(data, "topic", record="suggestions.jsonl"),
            gap_id=_required_str(data, "gap_id", record="suggestions.jsonl"),
            qa_id=_required_str(data, "qa_id", record="suggestions.jsonl"),
            fault_class=_required_str(data, "fault_class", record="suggestions.jsonl"),
            question=_required_str(data, "question", record="suggestions.jsonl"),
            reference_pages=_required_str_tuple(
                data, "reference_pages", record="suggestions.jsonl"
            ),
            rank=_required_int(data, "rank", record="suggestions.jsonl"),
            query_text=_required_str(data, "query_text", record="suggestions.jsonl"),
            candidate=_required_object(data, "candidate", record="suggestions.jsonl"),
            status=_required_str(data, "status", record="suggestions.jsonl"),
            proposed_at=_required_str(data, "proposed_at", record="suggestions.jsonl"),
            decided_at=_optional_str(data, "decided_at", record="suggestions.jsonl"),
            decided_reason=_optional_str(data, "decided_reason", record="suggestions.jsonl"),
            ingested_at=_optional_str(data, "ingested_at", record="suggestions.jsonl"),
            detected_generation=_required_int(
                data, "detected_generation", record="suggestions.jsonl"
            ),
            gap_origin=_optional_str_absent(data, "gap_origin", record="suggestions.jsonl"),
            gate_outcome=_optional_object_absent(data, "gate_outcome", record="suggestions.jsonl"),
            extra={
                key: value for key, value in data.items() if key not in _SUGGESTION_KNOWN_FIELDS
            },
        )


#: The top-level keys this version models, derived from the dataclass itself so
#: adding a field can never leave it double-counted as an "unknown" extra.
#: ``extra`` is the carrier, not a wire field, so it is excluded.
_SUGGESTION_KNOWN_FIELDS: frozenset[str] = frozenset(
    spec.name for spec in dataclass_fields(SuggestionRecord)
) - {"extra"}


def parse_suggestions_jsonl(text: str) -> list[SuggestionRecord]:
    """Parse a full ``suggestions.jsonl`` body; blank lines are skipped.

    Errors carry the 1-based line number of the offending record.
    """
    records: list[SuggestionRecord] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(SuggestionRecord.from_json_line(line))
        except (RecordParseError, ValueError) as error:
            raise RecordParseError(f"suggestions.jsonl line {line_number}: {error}") from error
    return records

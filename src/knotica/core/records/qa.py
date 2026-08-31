"""``qa.jsonl`` -- the curated-example record.

One line per curated example: the query, the pages the answer drew on, the
answer, its citations, and the human verdict. Written by ``curate_example`` and
read by the eval harness and the trainset builder.
"""

import json
from dataclasses import dataclass

from knotica.core.records.fields import (
    RecordParseError,
    _load_json_object,
    _optional_str,
    _required_int,
    _required_str,
    _required_str_tuple,
    _validate_enum,
    _validate_schema_version,
)

__all__ = [
    "QA_SCHEMA_VERSION",
    "QA_SOURCES",
    "QA_VERDICTS",
    "QARecord",
    "parse_qa_jsonl",
]

#: Current schema_version of the curated-example record.
QA_SCHEMA_VERSION = 1

QA_VERDICTS: frozenset[str] = frozenset({"good", "bad", "corrected"})
#: ``seed_train`` has no producer anymore (the demo seeder was removed); it stays
#: accepted so vaults that ran it keep parsing — frozen record shapes, dec-006.
QA_SOURCES: frozenset[str] = frozenset({"curate_example", "distillation", "seed_train"})


@dataclass(frozen=True, kw_only=True)
class QARecord:
    """One curated-example record, a single ``qa.jsonl`` line.

    ``pages_used`` and ``citations`` are tuples (immutable data); the JSON
    representation uses arrays. ``corrected_answer`` is ``None`` unless the
    verdict warranted a correction.
    """

    id: str
    schema_version: int = QA_SCHEMA_VERSION
    topic: str
    created: str
    query: str
    pages_used: tuple[str, ...]
    answer: str
    citations: tuple[str, ...]
    verdict: str
    corrected_answer: str | None
    source: str
    model: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_enum("verdict", self.verdict, QA_VERDICTS)
        _validate_enum("source", self.source, QA_SOURCES)

    def to_json_line(self) -> str:
        """Serialize to one JSON line (no trailing newline), fields in schema order."""
        payload = {
            "id": self.id,
            "schema_version": self.schema_version,
            "topic": self.topic,
            "created": self.created,
            "query": self.query,
            "pages_used": list(self.pages_used),
            "answer": self.answer,
            "citations": list(self.citations),
            "verdict": self.verdict,
            "corrected_answer": self.corrected_answer,
            "source": self.source,
            "model": self.model,
        }
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str) -> "QARecord":
        """Parse one ``qa.jsonl`` line; unknown extra fields are tolerated."""
        data = _load_json_object(line, record="qa.jsonl")
        return cls(
            id=_required_str(data, "id", record="qa.jsonl"),
            schema_version=_required_int(data, "schema_version", record="qa.jsonl"),
            topic=_required_str(data, "topic", record="qa.jsonl"),
            created=_required_str(data, "created", record="qa.jsonl"),
            query=_required_str(data, "query", record="qa.jsonl"),
            pages_used=_required_str_tuple(data, "pages_used", record="qa.jsonl"),
            answer=_required_str(data, "answer", record="qa.jsonl"),
            citations=_required_str_tuple(data, "citations", record="qa.jsonl"),
            verdict=_required_str(data, "verdict", record="qa.jsonl"),
            corrected_answer=_optional_str(data, "corrected_answer", record="qa.jsonl"),
            source=_required_str(data, "source", record="qa.jsonl"),
            model=_required_str(data, "model", record="qa.jsonl"),
        )


def parse_qa_jsonl(text: str) -> list[QARecord]:
    """Parse a full ``qa.jsonl`` body; blank lines are skipped.

    Errors carry the 1-based line number of the offending record.
    """
    records: list[QARecord] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(QARecord.from_json_line(line))
        except (RecordParseError, ValueError) as error:
            raise RecordParseError(f"qa.jsonl line {line_number}: {error}") from error
    return records

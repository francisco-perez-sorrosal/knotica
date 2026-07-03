"""Frozen machine-record formats -- typed records + parse/serialize, no I/O.

Implements the five record shapes frozen by the vault constitution (root
``SCHEMA.md``, section "Machine-record schemas"): the ``qa.jsonl`` curated
example, the ``metrics.jsonl`` eval record, the ``log.md`` entry line, the
commit-message subject, and the source-provenance frontmatter. The constitution
is the single source of truth for field sets and grammars; this module encodes
them without restating their prose.

Evolution is additive-only: the JSONL and frontmatter records each carry their
own ``schema_version``, and parsers tolerate unknown extra fields (a future
record version adds optional fields, never renames). The two line formats (log
entry, commit message) carry no inline version -- they are versioned by the
constitution's own ``schema_version``.

Records here are pure data plus (de)serialization. File placement, appending,
and committing belong to the operations/transaction layer; the digest helper
(:func:`body_sha256`) implements the constitution's body-only hashing
convention for the storing layer to call.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from knotica.core.page import parse_page, serialize_frontmatter

__all__ = [
    "COMMIT_SUBJECT_RE",
    "LOG_ENTRY_RE",
    "METRICS_SCHEMA_VERSION",
    "PROVENANCE_SCHEMA_VERSION",
    "QA_SCHEMA_VERSION",
    "QA_SOURCES",
    "QA_VERDICTS",
    "SOURCE_TYPES",
    "CommitSubject",
    "LogEntry",
    "MetricsComponents",
    "MetricsRecord",
    "QARecord",
    "RecordParseError",
    "SourceProvenance",
    "body_sha256",
    "format_commit_subject",
    "format_log_entry",
    "parse_commit_subject",
    "parse_log_entries",
    "parse_qa_jsonl",
    "parse_source_document",
    "render_source_document",
]

#: Current schema_version of each self-versioned record kind.
QA_SCHEMA_VERSION = 1
METRICS_SCHEMA_VERSION = 1
PROVENANCE_SCHEMA_VERSION = 1

QA_VERDICTS: frozenset[str] = frozenset({"good", "bad", "corrected"})
QA_SOURCES: frozenset[str] = frozenset({"curate_example", "distillation"})
SOURCE_TYPES: frozenset[str] = frozenset({"html", "pdf", "markdown", "text"})

#: Log-entry H2 line: ``## [YYYY-MM-DD] <op> | <topic> | <title>``.
LOG_ENTRY_RE = re.compile(
    r"^## \[(?P<date>\d{4}-\d{2}-\d{2})\] (?P<op>[a-z_]+) \| (?P<topic>.+?) \| (?P<title>.+)$"
)

#: Commit subject: ``knotica(<op>): <topic> — <title>`` (em-dash, surrounding spaces).
COMMIT_SUBJECT_RE = re.compile(r"^knotica\((?P<op>[a-z_]+)\): (?P<topic>.+?) — (?P<title>.+)$")

_OP_RE = re.compile(r"^[a-z_]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_SOURCE_TYPE_VALUE = "source"
_LOG_TITLE_SEPARATOR = " | "
_COMMIT_TITLE_SEPARATOR = " — "


class RecordParseError(ValueError):
    """Record content does not conform to the constitution's frozen shape."""


# ---------------------------------------------------------------------------
# qa.jsonl -- curated examples
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# metrics.jsonl -- per-generation eval history (shape only; producer is later)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class MetricsComponents:
    """The ``components`` breakdown of one eval scalar."""

    qa_accuracy: float
    citation_validity: float
    lint_violations: float
    token_cost: float


@dataclass(frozen=True, kw_only=True)
class MetricsRecord:
    """One eval-history record, a single ``metrics.jsonl`` line.

    The shape is frozen now so the eval harness appends against a stable
    contract; nothing in this codebase writes the file yet.
    """

    schema_version: int = METRICS_SCHEMA_VERSION
    topic: str
    timestamp: str
    generation: int
    harness_version: str
    scalar: float
    components: MetricsComponents
    n_examples: int
    corpus_ref: str
    artifact_ref: str | None

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        if self.generation < 0:
            raise ValueError(f"generation must be >= 0, got {self.generation}")
        if not self.corpus_ref.startswith("git:"):
            raise ValueError(f"corpus_ref must be a 'git:<sha>' reference, got {self.corpus_ref!r}")

    def to_json_line(self) -> str:
        """Serialize to one JSON line (no trailing newline), fields in schema order."""
        payload = {
            "schema_version": self.schema_version,
            "topic": self.topic,
            "timestamp": self.timestamp,
            "generation": self.generation,
            "harness_version": self.harness_version,
            "scalar": self.scalar,
            "components": {
                "qa_accuracy": self.components.qa_accuracy,
                "citation_validity": self.components.citation_validity,
                "lint_violations": self.components.lint_violations,
                "token_cost": self.components.token_cost,
            },
            "n_examples": self.n_examples,
            "corpus_ref": self.corpus_ref,
            "artifact_ref": self.artifact_ref,
        }
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str) -> "MetricsRecord":
        """Parse one ``metrics.jsonl`` line; unknown extra fields are tolerated."""
        data = _load_json_object(line, record="metrics.jsonl")
        components = data.get("components")
        if not isinstance(components, dict):
            raise RecordParseError(
                f"metrics.jsonl record field 'components' must be an object, got {components!r}"
            )
        return cls(
            schema_version=_required_int(data, "schema_version", record="metrics.jsonl"),
            topic=_required_str(data, "topic", record="metrics.jsonl"),
            timestamp=_required_str(data, "timestamp", record="metrics.jsonl"),
            generation=_required_int(data, "generation", record="metrics.jsonl"),
            harness_version=_required_str(data, "harness_version", record="metrics.jsonl"),
            scalar=_required_number(data, "scalar", record="metrics.jsonl"),
            components=MetricsComponents(
                qa_accuracy=_required_number(components, "qa_accuracy", record="metrics.jsonl"),
                citation_validity=_required_number(
                    components, "citation_validity", record="metrics.jsonl"
                ),
                lint_violations=_required_number(
                    components, "lint_violations", record="metrics.jsonl"
                ),
                token_cost=_required_number(components, "token_cost", record="metrics.jsonl"),
            ),
            n_examples=_required_int(data, "n_examples", record="metrics.jsonl"),
            corpus_ref=_required_str(data, "corpus_ref", record="metrics.jsonl"),
            artifact_ref=_optional_str(data, "artifact_ref", record="metrics.jsonl"),
        )


# ---------------------------------------------------------------------------
# log.md entry -- one H2 line per mutating operation, optional page bullets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogEntry:
    """One operation-log entry: the H2 line plus its touched-page bullets."""

    date: str
    op: str
    topic: str
    title: str
    pages: tuple[str, ...] = ()


def format_log_entry(entry: LogEntry) -> str:
    """Render an entry in the frozen grammar, one trailing newline included."""
    if not _DATE_RE.fullmatch(entry.date):
        raise ValueError(f"log-entry date must be YYYY-MM-DD, got {entry.date!r}")
    _validate_op(entry.op)
    _validate_slot("topic", entry.topic, forbidden=_LOG_TITLE_SEPARATOR)
    _validate_slot("title", entry.title)
    lines = [f"## [{entry.date}] {entry.op} | {entry.topic} | {entry.title}"]
    for page in entry.pages:
        _validate_slot("touched page path", page)
        lines.append(f"- {page}")
    return "\n".join(lines) + "\n"


def parse_log_entries(text: str) -> list[LogEntry]:
    """Parse every frozen-grammar entry in a ``log.md`` body, with its bullets.

    Lines inside fenced code blocks are skipped: the template's ``log.md``
    header quotes the grammar and an example inside fences, and those must
    never count as real operation entries. Bullets attach to the most recent
    entry line; any other non-blank line detaches subsequent bullets.
    """
    entries: list[LogEntry] = []
    pending: tuple[re.Match[str], list[str]] | None = None
    in_fence = False

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            match, pages = pending
            entries.append(LogEntry(pages=tuple(pages), **match.groupdict()))
            pending = None

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = LOG_ENTRY_RE.match(line)
        if match:
            flush()
            pending = (match, [])
            continue
        if not line.strip():
            continue
        if pending is not None and line.startswith("- "):
            pending[1].append(line[2:].strip())
        else:
            flush()
    flush()
    return entries


# ---------------------------------------------------------------------------
# Commit message -- knotica(<op>): <topic> — <title>
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommitSubject:
    """A parsed knotica commit subject."""

    op: str
    topic: str
    title: str


def format_commit_subject(op: str, topic: str, title: str) -> str:
    """Render a commit subject in the frozen grammar (no trailing newline)."""
    _validate_op(op)
    _validate_slot("topic", topic, forbidden=_COMMIT_TITLE_SEPARATOR)
    _validate_slot("title", title)
    return f"knotica({op}): {topic}{_COMMIT_TITLE_SEPARATOR}{title}"


def parse_commit_subject(subject: str) -> CommitSubject | None:
    """Parse a commit subject; ``None`` when it is not in the knotica grammar.

    Non-knotica subjects are normal in a shared vault history (manual edits,
    merges), so a mismatch is data, not an error.
    """
    match = COMMIT_SUBJECT_RE.match(subject)
    return CommitSubject(**match.groupdict()) if match else None


# ---------------------------------------------------------------------------
# Source-provenance frontmatter + the body-only digest convention
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class SourceProvenance:
    """The frontmatter record of one immutably stored source.

    The fixed ``type: source`` marker is implied by this class and rendered
    automatically; ``sha256`` is the body-only digest (:func:`body_sha256`).
    """

    schema_version: int = PROVENANCE_SCHEMA_VERSION
    topic: str
    citation_key: str
    retrieved: str
    origin_url: str
    sha256: str
    source_type: str
    ingested_by: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_enum("source_type", self.source_type, SOURCE_TYPES)
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError(f"sha256 must be a 64-char lowercase hex digest, got {self.sha256!r}")

    def to_frontmatter(self) -> str:
        """Render the frontmatter block (both fences, trailing newline), schema order."""
        return serialize_frontmatter(
            {
                "schema_version": self.schema_version,
                "type": _SOURCE_TYPE_VALUE,
                "topic": self.topic,
                "citation_key": self.citation_key,
                "retrieved": self.retrieved,
                "origin_url": self.origin_url,
                "sha256": self.sha256,
                "source_type": self.source_type,
                "ingested_by": self.ingested_by,
            }
        )

    @classmethod
    def from_fields(cls, fields: Mapping[str, object]) -> "SourceProvenance":
        """Build from parsed frontmatter fields; unknown extra fields are tolerated."""
        marker = fields.get("type")
        if marker != _SOURCE_TYPE_VALUE:
            raise RecordParseError(
                f"provenance record field 'type' must be '{_SOURCE_TYPE_VALUE}', got {marker!r}"
            )
        return cls(
            schema_version=_required_int(fields, "schema_version", record="provenance"),
            topic=_required_str(fields, "topic", record="provenance"),
            citation_key=_required_str(fields, "citation_key", record="provenance"),
            retrieved=_required_str(fields, "retrieved", record="provenance"),
            origin_url=_required_str(fields, "origin_url", record="provenance"),
            sha256=_required_str(fields, "sha256", record="provenance"),
            source_type=_required_str(fields, "source_type", record="provenance"),
            ingested_by=_required_str(fields, "ingested_by", record="provenance"),
        )


def render_source_document(provenance: SourceProvenance, body: str) -> str:
    """Compose a stored-source file: frontmatter, one blank separator line, body.

    The blank line is load-bearing -- the ``sha256`` convention hashes exactly
    the bytes after it, so :func:`parse_source_document` must recover ``body``
    byte-for-byte from the rendered text.
    """
    return provenance.to_frontmatter() + "\n" + body


def parse_source_document(text: str) -> tuple[SourceProvenance, str]:
    """Split a stored source into its provenance record and hashable body.

    The returned body excludes the single blank separator line after the
    frontmatter block, so ``body_sha256(body)`` reproduces the recorded
    digest for a conforming document.
    """
    frontmatter, error, body = parse_page(text)
    if frontmatter is None:
        detail = error or "text does not start with a frontmatter block"
        raise RecordParseError(f"source document has no parseable frontmatter: {detail}")
    return SourceProvenance.from_fields(frontmatter), body.removeprefix("\n")


def body_sha256(body: str) -> str:
    """Hex digest of a source's markdown body, per the constitution's convention.

    Hashes the UTF-8 bytes of the content stored after the provenance
    frontmatter block's trailing blank line, trailing newline included -- i.e.
    the ``content`` a caller stores, before any frontmatter is prepended.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Shared field validation (boundary parsing helpers)
# ---------------------------------------------------------------------------


def _validate_schema_version(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"schema_version must be an integer >= 1, got {value!r}")


def _validate_enum(field: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ValueError(f"{field} must be one of {'|'.join(sorted(allowed))}, got {value!r}")


def _validate_op(op: str) -> None:
    if not _OP_RE.fullmatch(op):
        raise ValueError(f"op must be lowercase letters/underscores, got {op!r}")


def _validate_slot(name: str, value: str, *, forbidden: str | None = None) -> None:
    """Reject slot values that would break the line grammar's round-trip."""
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{name} must be a single line, got {value!r}")
    if forbidden is not None and forbidden in value:
        raise ValueError(f"{name} must not contain {forbidden!r}, got {value!r}")


def _load_json_object(line: str, *, record: str) -> dict[str, object]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError as error:
        raise RecordParseError(f"{record} record is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise RecordParseError(f"{record} record must be a JSON object, got {type(data).__name__}")
    return data


def _required_field(data: Mapping[str, object], key: str, *, record: str) -> object:
    if key not in data:
        raise RecordParseError(f"{record} record is missing required field {key!r}")
    return data[key]


def _required_str(data: Mapping[str, object], key: str, *, record: str) -> str:
    value = _required_field(data, key, record=record)
    if not isinstance(value, str):
        raise RecordParseError(f"{record} record field {key!r} must be a string, got {value!r}")
    return value


def _optional_str(data: Mapping[str, object], key: str, *, record: str) -> str | None:
    value = _required_field(data, key, record=record)
    if value is not None and not isinstance(value, str):
        raise RecordParseError(
            f"{record} record field {key!r} must be a string or null, got {value!r}"
        )
    return value


def _required_int(data: Mapping[str, object], key: str, *, record: str) -> int:
    value = _required_field(data, key, record=record)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RecordParseError(f"{record} record field {key!r} must be an integer, got {value!r}")
    return value


def _required_number(data: Mapping[str, object], key: str, *, record: str) -> float:
    value = _required_field(data, key, record=record)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecordParseError(f"{record} record field {key!r} must be a number, got {value!r}")
    return float(value)


def _required_str_tuple(data: Mapping[str, object], key: str, *, record: str) -> tuple[str, ...]:
    value = _required_field(data, key, record=record)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RecordParseError(
            f"{record} record field {key!r} must be an array of strings, got {value!r}"
        )
    return tuple(value)

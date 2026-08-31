"""The two per-operation line grammars: the ``log.md`` entry and the commit subject.

One mutating operation emits both -- an entry appended to the vault's operation
log and the subject of the single git commit that operation makes -- so the two
grammars change together and share the slot validation that keeps each rendered
line round-trippable.
"""

import re
from dataclasses import dataclass

__all__ = [
    "COMMIT_SUBJECT_RE",
    "LOG_ENTRY_RE",
    "CommitSubject",
    "LogEntry",
    "format_commit_subject",
    "format_log_entry",
    "parse_commit_subject",
    "parse_log_entries",
]

#: Log-entry H2 line: ``## [YYYY-MM-DD] <op> | <topic> | <title>``.
LOG_ENTRY_RE = re.compile(
    r"^## \[(?P<date>\d{4}-\d{2}-\d{2})\] (?P<op>[a-z_]+) \| (?P<topic>.+?) \| (?P<title>.+)$"
)

#: Commit subject: ``knotica(<op>): <topic> — <title>`` (em-dash, surrounding spaces).
COMMIT_SUBJECT_RE = re.compile(r"^knotica\((?P<op>[a-z_]+)\): (?P<topic>.+?) — (?P<title>.+)$")

_OP_RE = re.compile(r"^[a-z_]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_LOG_TITLE_SEPARATOR = " | "
_COMMIT_TITLE_SEPARATOR = " — "


@dataclass(frozen=True)
class LogEntry:
    """One operation-log entry: the H2 line plus its touched-page bullets."""

    date: str
    op: str
    topic: str
    title: str
    pages: tuple[str, ...] = ()


def format_log_entry(entry: LogEntry) -> str:
    """Render one native OKF log block (date heading + bullet), trailing newline included."""
    from knotica.okf.log_fmt import format_operation_log_entry

    if not _DATE_RE.fullmatch(entry.date):
        raise ValueError(f"log-entry date must be YYYY-MM-DD, got {entry.date!r}")
    _validate_op(entry.op)
    _validate_slot("topic", entry.topic, forbidden=_LOG_TITLE_SEPARATOR)
    _validate_slot("title", entry.title)
    for page in entry.pages:
        _validate_slot("touched page path", page)
    okf_entry = format_operation_log_entry(
        entry_date=entry.date,
        op=entry.op,
        topic=entry.topic,
        title=entry.title,
        pages=entry.pages,
    )
    return f"## {entry.date}\n* **{okf_entry.kind}**: {okf_entry.body}\n"


def parse_log_entries(text: str) -> list[LogEntry]:
    """Parse every log entry in a ``log.md`` body, oldest first.

    Accepts native OKF date-grouped bullets and legacy Knotica operation
    headings. Fenced code blocks are skipped.
    """
    from knotica.okf.log_fmt import okf_entry_to_knotica_fields, parse_log_entries as parse_okf

    knotica_entries: list[LogEntry] = []
    for okf_entry in reversed(parse_okf(text)):
        date_value, op, topic, title, pages = okf_entry_to_knotica_fields(okf_entry)
        knotica_entries.append(
            LogEntry(date=date_value, op=op, topic=topic, title=title, pages=pages)
        )
    return knotica_entries


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

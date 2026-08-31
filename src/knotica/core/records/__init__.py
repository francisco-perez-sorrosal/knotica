"""Frozen machine-record formats -- typed records + parse/serialize, no I/O.

Implements the record shapes frozen by the vault constitution (root
``SCHEMA.md``, section "Machine-record schemas"): the ``qa.jsonl`` curated
example, the ``metrics.jsonl`` eval record, the ``gaps.jsonl`` knowledge gap,
the ``suggestions.jsonl`` gap-fill suggestion, the ``log.md`` entry line, the
commit-message subject, and the source-provenance frontmatter. The constitution
is the single source of truth for field sets and grammars; this package encodes
them without restating their prose.

One module per record family; this ``__init__`` is the import surface, so every
consumer keeps writing ``from knotica.core.records import GapRecord`` and never
names a submodule.

* :mod:`~knotica.core.records.fields` -- the boundary parsing helpers every
  family's ``from_json_line`` parses through, and :class:`RecordParseError`.
* :mod:`~knotica.core.records.qa` -- ``qa.jsonl``, the curated example.
* :mod:`~knotica.core.records.metrics` -- ``metrics.jsonl``, one eval-history
  record per scored generation.
* :mod:`~knotica.core.records.gaps` -- ``gaps.jsonl``, one knowledge gap.
* :mod:`~knotica.core.records.suggestions` -- ``suggestions.jsonl``, one
  gap x candidate join.
* :mod:`~knotica.core.records.op_lines` -- the two per-operation line grammars
  (log entry, commit subject).
* :mod:`~knotica.core.records.source` -- source-provenance frontmatter and the
  body-only digest convention.

Evolution is additive-only: the JSONL and frontmatter records each carry their
own ``schema_version``, and parsers tolerate unknown extra fields (a future
record version adds optional fields, never renames). The two record families a
full-file rewriter re-serializes -- :class:`GapRecord` and
:class:`SuggestionRecord` -- go further and *carry* those unknown fields on an
``extra`` mapping so the rewrite re-emits them; tolerance on ingress alone would
let a routine drain erase them. The two line formats (log entry, commit message)
carry no inline version -- they are versioned by the constitution's own
``schema_version``.

Records here are pure data plus (de)serialization. File placement, appending,
and committing belong to the operations/transaction layer; the digest helper
(:func:`body_sha256`) implements the constitution's body-only hashing
convention for the storing layer to call.
"""

from knotica.core.records.fields import RecordParseError
from knotica.core.records.gaps import (
    GAP_FAULT_CLASSES,
    GAP_ORIGIN_MEASURED,
    GAP_ORIGIN_REPORTED,
    GAP_ORIGIN_RETRACTED,
    GAP_ORIGINS,
    GAP_SCHEMA_VERSION,
    GAP_STATUSES,
    GapEvidence,
    GapRecord,
    parse_gaps_jsonl,
)
from knotica.core.records.metrics import (
    METRICS_SCHEMA_VERSION,
    MetricsComponents,
    MetricsRecord,
)
from knotica.core.records.op_lines import (
    COMMIT_SUBJECT_RE,
    LOG_ENTRY_RE,
    CommitSubject,
    LogEntry,
    format_commit_subject,
    format_log_entry,
    parse_commit_subject,
    parse_log_entries,
)
from knotica.core.records.qa import (
    QA_SCHEMA_VERSION,
    QA_SOURCES,
    QA_VERDICTS,
    QARecord,
    parse_qa_jsonl,
)
from knotica.core.records.source import (
    PROVENANCE_SCHEMA_VERSION,
    SOURCE_TYPES,
    SourceProvenance,
    body_sha256,
    parse_source_document,
    render_source_document,
)
from knotica.core.records.suggestions import (
    SUGGESTION_SCHEMA_VERSION,
    SUGGESTION_STATUSES,
    SuggestionRecord,
    parse_suggestions_jsonl,
)

__all__ = [
    "COMMIT_SUBJECT_RE",
    "GAP_FAULT_CLASSES",
    "GAP_ORIGINS",
    "GAP_ORIGIN_MEASURED",
    "GAP_ORIGIN_REPORTED",
    "GAP_ORIGIN_RETRACTED",
    "GAP_SCHEMA_VERSION",
    "GAP_STATUSES",
    "LOG_ENTRY_RE",
    "METRICS_SCHEMA_VERSION",
    "PROVENANCE_SCHEMA_VERSION",
    "QA_SCHEMA_VERSION",
    "QA_SOURCES",
    "QA_VERDICTS",
    "SOURCE_TYPES",
    "SUGGESTION_SCHEMA_VERSION",
    "SUGGESTION_STATUSES",
    "CommitSubject",
    "GapEvidence",
    "GapRecord",
    "LogEntry",
    "MetricsComponents",
    "MetricsRecord",
    "QARecord",
    "RecordParseError",
    "SourceProvenance",
    "SuggestionRecord",
    "body_sha256",
    "format_commit_subject",
    "format_log_entry",
    "parse_commit_subject",
    "parse_gaps_jsonl",
    "parse_log_entries",
    "parse_qa_jsonl",
    "parse_source_document",
    "parse_suggestions_jsonl",
    "render_source_document",
]

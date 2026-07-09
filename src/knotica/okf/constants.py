"""OKF constants -- reserved files, field sets, type inference."""

from __future__ import annotations

from typing import Final

#: OKF reserved markdown filenames (not concept documents).
RESERVED_FILENAMES: frozenset[str] = frozenset({"index.md", "log.md"})

#: OKF-recommended frontmatter fields kept in default export.
OKF_RECOMMENDED_FIELDS: tuple[str, ...] = (
    "type",
    "title",
    "description",
    "resource",
    "tags",
    "timestamp",
)

#: Knotica extension fields preserved in native vault and default export.
KNOTICA_EXTENSION_FIELDS: tuple[str, ...] = (
    "topic",
    "created",
    "updated",
    "confidence",
    "sources",
    "status",
    "supersedes",
    "superseded_by",
    "schema_version",
    "citation_key",
    "origin_url",
    "source_type",
    "retrieved",
    "sha256",
    "ingested_by",
    "knotica_schema_version",
)

#: Fields retained in ``--pure`` export (plus citation_key when useful).
PURE_EXPORT_FIELDS: frozenset[str] = frozenset(
    {
        "type",
        "title",
        "description",
        "resource",
        "tags",
        "timestamp",
        "citation_key",
    }
)

#: Path-pattern hints for ``type`` inference when missing (Knotica taxonomy).
PATH_TYPE_HINTS: tuple[tuple[str, str], ...] = (
    ("sources/", "source"),
    ("references/", "reference"),
    ("/SCHEMA.md", "schema"),
    ("SCHEMA.md", "schema"),
    ("START_HERE.md", "guide"),
    ("reports/", "report"),
    ("playbooks/", "playbook"),
    ("metrics/", "metric"),
    ("apis/", "api"),
    ("datasets/", "dataset"),
    ("tables/", "table"),
)

#: Undo Title Case types from an earlier repair pass -> Knotica ``type`` values.
TITLE_CASE_TYPE_UNDO: dict[str, str] = {
    "Concept": "concept",
    "Paper": "paper",
    "Method": "method",
    "Benchmark": "benchmark",
    "System": "system",
    "Tool": "tool",
    "Entity": "entity",
    "Reference": "source",
    "Schema": "schema",
    "Guide": "guide",
    "Report": "report",
    "Playbook": "playbook",
    "Metric": "metric",
    "API Endpoint": "api",
    "Dataset": "dataset",
    "Table": "table",
}

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}
)

RFC3339_DATE_ONLY_RE: Final[str] = r"^\d{4}-\d{2}-\d{2}$"
RFC3339_DATETIME_RE: Final[str] = (
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

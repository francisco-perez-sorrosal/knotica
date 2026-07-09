"""OKF frontmatter validation, inference, and normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from knotica.core.page import parse_page, serialize_frontmatter
from knotica.okf.constants import (
    PATH_TYPE_HINTS,
    PURE_EXPORT_FIELDS,
    RESERVED_FILENAMES,
    TITLE_CASE_TYPE_UNDO,
)
from knotica.okf.datetime_fmt import best_timestamp, is_rfc3339, normalize_timestamp

_SLUG_WORD_RE = re.compile(r"[-_]+")


@dataclass(frozen=True)
class FrontmatterFinding:
    """One frontmatter check or repair finding."""

    path: str
    severity: str  # error | warning | info
    code: str
    message: str


@dataclass(frozen=True)
class NormalizedFrontmatter:
    """A concept document's normalized frontmatter plus repair metadata."""

    fields: dict[str, object]
    warnings: tuple[str, ...]
    changed: bool


def is_reserved_file(path: str) -> bool:
    """Return whether ``path`` is an OKF reserved file (index.md or log.md)."""
    return PurePosixPath(path).name in RESERVED_FILENAMES


def is_concept_file(path: str) -> bool:
    """Return whether ``path`` is a non-reserved markdown concept document."""
    return path.endswith(".md") and not is_reserved_file(path)


def infer_type(path: str) -> str:
    """Infer ``type`` from vault path patterns (Knotica taxonomy)."""
    normalized = path.replace("\\", "/")
    for prefix, type_value in PATH_TYPE_HINTS:
        if prefix in normalized or normalized.endswith(prefix):
            return type_value
    return "concept"


def normalize_type_value(path: str, fields: dict[str, object]) -> tuple[str | None, list[str]]:
    """Ensure a single Knotica ``type``; strip legacy ``knotica_kind`` if present."""
    warnings: list[str] = []
    legacy_kind = fields.pop("knotica_kind", None)
    if isinstance(legacy_kind, str) and legacy_kind.strip():
        warnings.append(f"removed knotica_kind; using {legacy_kind.strip()!r} as type")
        return legacy_kind.strip().lower(), warnings

    raw_type = fields.get("type")
    if not isinstance(raw_type, str) or not raw_type.strip():
        return None, warnings

    stripped = raw_type.strip()
    if stripped in TITLE_CASE_TYPE_UNDO:
        knotica_type = TITLE_CASE_TYPE_UNDO[stripped]
        if knotica_type == "source" and stripped == "Reference" and not path.startswith("sources/"):
            knotica_type = "reference"
        warnings.append(f"normalized type {stripped!r} -> {knotica_type!r}")
        return knotica_type, warnings

    return stripped, warnings


def title_from_filename(path: str) -> str:
    """Convert ``agent-memory-frontiers.md`` -> ``Agent Memory Frontiers``."""
    stem = PurePosixPath(path).stem
    return " ".join(word.capitalize() for word in _SLUG_WORD_RE.split(stem) if word)


def infer_title(fields: dict[str, object], body: str, path: str) -> str | None:
    """Infer ``title`` from frontmatter, H1, or filename."""
    existing = fields.get("title")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return title_from_filename(path)


def infer_description(fields: dict[str, object], body: str) -> str | None:
    """Infer ``description`` from frontmatter or first paragraph after H1."""
    existing = fields.get("description")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    past_h1 = False
    for line in body.splitlines():
        if line.startswith("# "):
            past_h1 = True
            continue
        if not past_h1:
            continue
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return None


def normalize_concept_frontmatter(
    path: str,
    raw_text: str,
    *,
    pure: bool = False,
) -> NormalizedFrontmatter:
    """Normalize a concept document's frontmatter for OKF compatibility."""
    frontmatter, error, body = parse_page(raw_text)
    warnings: list[str] = []
    if error is not None:
        return NormalizedFrontmatter(fields={}, warnings=(error,), changed=False)
    fields: dict[str, object] = dict(frontmatter or {})
    original = dict(fields)

    normalized_type, type_warnings = normalize_type_value(path, fields)
    warnings.extend(type_warnings)
    if normalized_type:
        fields["type"] = normalized_type
    else:
        inferred = infer_type(path)
        fields["type"] = inferred
        warnings.append(f"added type: {inferred}")

    if "title" not in fields or not str(fields.get("title", "")).strip():
        title = infer_title(fields, body, path)
        if title:
            fields["title"] = title
            warnings.append(f"inferred title: {title}")

    if "description" not in fields:
        description = infer_description(fields, body)
        if description:
            fields["description"] = description

    origin = fields.get("origin_url")
    if isinstance(origin, str) and origin.strip() and not fields.get("resource"):
        fields["resource"] = origin.strip()
        warnings.append("mapped origin_url to resource")

    ts, ts_warning = best_timestamp(fields)
    if ts:
        if "timestamp" not in fields or not is_rfc3339(str(fields["timestamp"])):
            fields["timestamp"] = ts
            if ts_warning:
                warnings.append(ts_warning)

    for date_field in ("created", "updated", "retrieved"):
        if date_field in fields:
            normalized, date_warn = normalize_timestamp(fields[date_field])
            if normalized and normalized != fields[date_field]:
                fields[date_field] = normalized
                if date_warn:
                    warnings.append(f"{date_field}: {date_warn}")

    if pure:
        fields = {k: v for k, v in fields.items() if k in PURE_EXPORT_FIELDS}

    changed = fields != original or "knotica_kind" in (frontmatter or {})
    return NormalizedFrontmatter(fields=fields, warnings=tuple(warnings), changed=changed)


def render_concept_document(path: str, raw_text: str, *, pure: bool = False) -> str:
    """Return normalized concept document text (frontmatter + body)."""
    normalized = normalize_concept_frontmatter(path, raw_text, pure=pure)
    _, error, body = parse_page(raw_text)
    if error is not None:
        return raw_text
    return serialize_frontmatter(normalized.fields) + body


def check_concept_frontmatter(path: str, raw_text: str) -> list[FrontmatterFinding]:
    """Validate one concept file for native OKF compatibility."""
    findings: list[FrontmatterFinding] = []
    if not is_concept_file(path):
        return findings
    if not raw_text.startswith("---"):
        findings.append(
            FrontmatterFinding(
                path=path,
                severity="error",
                code="missing-frontmatter",
                message="concept file lacks YAML frontmatter",
            )
        )
        return findings
    frontmatter, error, _body = parse_page(raw_text)
    if error is not None:
        findings.append(
            FrontmatterFinding(
                path=path,
                severity="error",
                code="invalid-yaml",
                message=error,
            )
        )
        return findings
    if frontmatter is None:
        findings.append(
            FrontmatterFinding(
                path=path,
                severity="error",
                code="missing-frontmatter",
                message="concept file lacks YAML frontmatter",
            )
        )
        return findings
    type_value = frontmatter.get("type")
    if not isinstance(type_value, str) or not type_value.strip():
        findings.append(
            FrontmatterFinding(
                path=path,
                severity="error",
                code="missing-type",
                message="concept frontmatter lacks non-empty type",
            )
        )
    if "knotica_kind" in frontmatter:
        findings.append(
            FrontmatterFinding(
                path=path,
                severity="warning",
                code="deprecated-knotica-kind",
                message="knotica_kind is deprecated; use type only (run knotica okf repair)",
            )
        )
    for recommended in ("title", "description", "tags", "timestamp"):
        if recommended not in frontmatter:
            findings.append(
                FrontmatterFinding(
                    path=path,
                    severity="warning",
                    code=f"missing-{recommended}",
                    message=f"recommended OKF field {recommended!r} is absent",
                )
            )
    origin = frontmatter.get("origin_url")
    if (
        isinstance(origin, str)
        and origin.strip()
        and not frontmatter.get("resource")
        and path.startswith("sources/")
    ):
        findings.append(
            FrontmatterFinding(
                path=path,
                severity="warning",
                code="missing-resource",
                message="source-like file has origin_url but no resource",
            )
        )
    return findings


def check_index_file(path: str, raw_text: str) -> list[FrontmatterFinding]:
    """Validate index.md has no frontmatter."""
    if PurePosixPath(path).name != "index.md":
        return []
    if raw_text.startswith("---"):
        return [
            FrontmatterFinding(
                path=path,
                severity="error",
                code="index-has-frontmatter",
                message="index.md must not contain YAML frontmatter",
            )
        ]
    return []

"""Source-provenance frontmatter and the body-only digest convention.

A stored source is one file: the provenance frontmatter block, one blank
separator line, then the immutable body. The blank line is load-bearing --
:func:`body_sha256` hashes exactly the bytes after it, so the render/parse pair
must recover the body byte-for-byte.
"""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass

from knotica.core.page import parse_page, serialize_frontmatter
from knotica.core.records.fields import (
    RecordParseError,
    _required_int,
    _required_str,
    _validate_enum,
    _validate_schema_version,
)

__all__ = [
    "PROVENANCE_SCHEMA_VERSION",
    "SOURCE_TYPES",
    "SourceProvenance",
    "body_sha256",
    "parse_source_document",
    "render_source_document",
]

#: Current schema_version of the provenance frontmatter record.
PROVENANCE_SCHEMA_VERSION = 1

SOURCE_TYPES: frozenset[str] = frozenset({"html", "pdf", "markdown", "text"})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_TYPE_VALUE = "source"


def _is_source_type_marker(fields: Mapping[str, object]) -> bool:
    marker = fields.get("type")
    if marker == _SOURCE_TYPE_VALUE:
        return True
    # Transitional: accept Title Case from an earlier OKF repair pass.
    if marker in {"Reference", "reference"}:
        return True
    return False


@dataclass(frozen=True, kw_only=True)
class SourceProvenance:
    """The frontmatter record of one immutably stored source.

    Uses ``type: source`` (valid OKF — open taxonomy). ``sha256`` is the
    body-only digest (:func:`body_sha256`).
    """

    schema_version: int = PROVENANCE_SCHEMA_VERSION
    topic: str
    citation_key: str
    retrieved: str
    origin_url: str
    sha256: str
    source_type: str
    ingested_by: str
    title: str | None = None

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_enum("source_type", self.source_type, SOURCE_TYPES)
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError(f"sha256 must be a 64-char lowercase hex digest, got {self.sha256!r}")

    def to_frontmatter(self) -> str:
        """Render provenance frontmatter with OKF-recommended fields."""
        fields: dict[str, object] = {
            "schema_version": self.schema_version,
            "type": _SOURCE_TYPE_VALUE,
            "topic": self.topic,
            "citation_key": self.citation_key,
            "retrieved": self.retrieved,
            "timestamp": self.retrieved,
            "origin_url": self.origin_url,
            "resource": self.origin_url,
            "sha256": self.sha256,
            "source_type": self.source_type,
            "ingested_by": self.ingested_by,
        }
        if self.title:
            fields["title"] = self.title
        return serialize_frontmatter(fields)

    @classmethod
    def from_fields(cls, fields: Mapping[str, object]) -> "SourceProvenance":
        """Build from parsed frontmatter fields; unknown extra fields are tolerated."""
        if not _is_source_type_marker(fields):
            raise RecordParseError(
                f"provenance record field 'type' must be 'source', got {fields.get('type')!r}"
            )
        title = fields.get("title")
        return cls(
            schema_version=_required_int(fields, "schema_version", record="provenance"),
            topic=_required_str(fields, "topic", record="provenance"),
            citation_key=_required_str(fields, "citation_key", record="provenance"),
            retrieved=_required_str(fields, "retrieved", record="provenance"),
            origin_url=_required_str(fields, "origin_url", record="provenance"),
            sha256=_required_str(fields, "sha256", record="provenance"),
            source_type=_required_str(fields, "source_type", record="provenance"),
            ingested_by=_required_str(fields, "ingested_by", record="provenance"),
            title=title if isinstance(title, str) and title.strip() else None,
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

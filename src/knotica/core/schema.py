"""Schema resolution -- root constitution + topic overlay -> resolved schema.

Resolution mirrors prompt resolution (``core.prompts``): defaults live at the
vault root, per-topic refinement lives in the topic directory. Pure per-call
functions over the :class:`~knotica.store.VaultStore` protocol -- nothing is
cached, deliberately (same rationale as ``core.config``: a schema edited after
boot must take effect on the next call; an mtime-cache is the sanctioned later
optimization, not MVP).

Merge semantics
---------------
The resolved schema is a **deterministic markdown concatenation**: root
constitution first, topic overlay second, each under a provenance header
naming its file and ``schema_version``. The overlay *extends and refines* the
root where the constitution allows it (e.g. defining the topic's entity types
and page template); by the constitution's own rule it never contradicts the
root. This module does **not** detect contradictions -- root-vs-overlay
contradiction detection is a lint concern (``core.lint``); the merge here is
mechanical and total, with the overlay's refinements reading last so they
take precedence where the constitution delegates.

A topic without an overlay resolves to the root constitution alone (new
topics start with an empty overlay -- divergence is earned); the merged
document says so explicitly, so a consumer never wonders whether a layer was
silently dropped.
"""

from dataclasses import dataclass

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.page import TopicNotFoundError, parse_page
from knotica.store import VaultStore

__all__ = [
    "ROOT_SCHEMA_PATH",
    "ResolvedSchema",
    "SchemaLayer",
    "overlay_path",
    "read_root_schema",
    "read_topic_overlay",
    "resolve_schema",
    "validated_topic",
]

#: Vault-relative path of the root constitution.
ROOT_SCHEMA_PATH = "SCHEMA.md"

#: Filename of a topic's schema overlay inside its directory.
_OVERLAY_FILENAME = "SCHEMA.md"


@dataclass(frozen=True)
class SchemaLayer:
    """One schema file (root constitution or topic overlay), parsed.

    ``schema_version`` is the integer from the file's frontmatter, or ``None``
    when the frontmatter is absent, unparseable, or carries no integer version
    -- resolution never fails on a malformed layer; flagging it is lint's job.
    ``body`` is the markdown after the frontmatter block, stripped; ``raw`` is
    the file content exactly as stored.
    """

    path: str
    schema_version: int | None
    body: str
    raw: str


@dataclass(frozen=True)
class ResolvedSchema:
    """The effective schema for one topic: root ⊕ optional overlay, merged.

    ``merged`` is the deterministic concatenation served as the resolved-schema
    resource (see the module docstring for the merge semantics). The individual
    layers stay addressable for diffing (root-only and overlay-only consumers).
    """

    topic: str
    root: SchemaLayer
    overlay: SchemaLayer | None
    merged: str

    @property
    def schema_version(self) -> int | None:
        """The effective vault schema version -- the root constitution's."""
        return self.root.schema_version


def validated_topic(topic: str) -> str:
    """Return ``topic`` stripped, or raise ``ValueError`` if not topic-shaped.

    A topic is a bare top-level directory name: non-empty, no path separators,
    not dot-prefixed (dot-folders are never topics).
    """
    cleaned = topic.strip()
    if not cleaned:
        raise ValueError("Topic must not be empty.")
    if "/" in cleaned or "\\" in cleaned or cleaned.startswith("."):
        raise ValueError(f"Topic must be a bare top-level directory name, got: {topic!r}")
    return cleaned


def overlay_path(topic: str) -> str:
    """Return the vault-relative path of ``topic``'s schema overlay."""
    return f"{validated_topic(topic)}/{_OVERLAY_FILENAME}"


def read_root_schema(store: VaultStore) -> SchemaLayer:
    """Read the root constitution.

    A vault without a root ``SCHEMA.md`` is not an initialized knotica vault
    (config resolution checks the same file), so its absence raises the typed
    ``NOT_CONFIGURED`` error rather than a bare file error -- adapters render
    it into the uniform unconfigured contract.
    """
    if not store.exists(ROOT_SCHEMA_PATH):
        raise KnoticaError(
            code=ErrorCode.NOT_CONFIGURED,
            message=(
                "Schema resolution failed because the vault has no root"
                f" {ROOT_SCHEMA_PATH} (the constitution) -- the vault is not"
                " an initialized knotica vault."
            ),
        )
    return _load_layer(store, ROOT_SCHEMA_PATH)


def read_topic_overlay(store: VaultStore, topic: str) -> SchemaLayer | None:
    """Read ``topic``'s schema overlay, or ``None`` when the topic has none.

    Raises :class:`~knotica.core.page.TopicNotFoundError` when the topic
    directory itself is absent -- a missing *overlay* is a normal state
    (divergence is earned), a missing *topic* is a caller error.
    """
    cleaned = validated_topic(topic)
    if not store.exists(cleaned):
        raise TopicNotFoundError(cleaned)
    path = f"{cleaned}/{_OVERLAY_FILENAME}"
    if not store.exists(path):
        return None
    return _load_layer(store, path)


def resolve_schema(store: VaultStore, topic: str) -> ResolvedSchema:
    """Resolve the effective schema for ``topic``: root ⊕ overlay, merged."""
    cleaned = validated_topic(topic)
    root = read_root_schema(store)
    overlay = read_topic_overlay(store, cleaned)
    return ResolvedSchema(
        topic=cleaned,
        root=root,
        overlay=overlay,
        merged=_merged_markdown(cleaned, root, overlay),
    )


def _load_layer(store: VaultStore, path: str) -> SchemaLayer:
    """Read and parse one schema file into a :class:`SchemaLayer`."""
    raw = store.read_text(path)
    frontmatter, _error, body = parse_page(raw)
    version = frontmatter.get("schema_version") if frontmatter is not None else None
    return SchemaLayer(
        path=path,
        schema_version=version if isinstance(version, int) else None,
        body=body.strip(),
        raw=raw,
    )


def _merged_markdown(topic: str, root: SchemaLayer, overlay: SchemaLayer | None) -> str:
    """Concatenate the layers under provenance headers (deterministic)."""
    parts = [
        (
            f"<!-- Resolved schema for topic '{topic}': root constitution ⊕ topic overlay."
            " The overlay extends and refines the root; it never contradicts it"
            " (contradictions are lint violations). -->"
        ),
        _layer_header("root constitution", root),
        root.body,
    ]
    if overlay is None:
        parts.append(
            f"<!-- Topic '{topic}' has no schema overlay;"
            " the root constitution applies unchanged. -->"
        )
    else:
        parts.append(_layer_header("topic overlay", overlay))
        parts.append(overlay.body)
    return "\n\n".join(parts) + "\n"


def _layer_header(role: str, layer: SchemaLayer) -> str:
    """Provenance header naming the layer's role, file, and schema version."""
    version = "unknown" if layer.schema_version is None else str(layer.schema_version)
    return f"<!-- Layer: {role} ({layer.path}, schema_version {version}) -->"

"""``store_source`` -- immutable raw-source persistence with provenance frontmatter.

Persists a client-fetched source into ``sources/<topic>/<citation_key>.md`` with
a :class:`~knotica.core.records.SourceProvenance` frontmatter block and exactly
one commit. Sources are immutable: a re-store with identical content is a no-op
success; a re-store under the same ``citation_key`` with *different* content
fails with ``SOURCE_EXISTS``. Because the provenance carries a fresh ``retrieved``
timestamp, idempotency is decided by comparing the stored body against the new
(scrubbed) content *before* building the document -- never by the transaction's
byte comparison, which the timestamp would always defeat.
"""

from datetime import UTC, datetime
from pathlib import PurePath

from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.records import (
    SourceProvenance,
    body_sha256,
    parse_source_document,
    render_source_document,
)
from knotica.core.operations.candidate_scope import resolve_candidate_scope
from knotica.core.scrub import scrub
from knotica.core.text_reflow import reflow_pdf_markdown
from knotica.core.transaction import VaultTransaction
from knotica.core.vcs import VaultVcs
from knotica.store import VaultStore

#: Root directory under which all immutable sources are stored (reserved top-level name).
_SOURCES_DIR = "sources"

#: Recorded provenance for who ingested the source (the deterministic tool, not the client's LLM).
_INGESTED_BY = "knotica"

#: Default original format when a caller omits it (the client converts to markdown first).
_DEFAULT_SOURCE_TYPE = "markdown"


def store_source(
    store: VaultStore,
    vault_root: str | PurePath,
    topic: str,
    citation_key: str,
    title: str,
    content: str,
    source_url: str,
    source_type: str = _DEFAULT_SOURCE_TYPE,
    candidate: str = "",
) -> dict[str, object]:
    """Persist a raw source immutably under ``sources/<topic>/<citation_key>.md``.

    Args:
        store: The vault storage backend.
        vault_root: The already-resolved vault root (operations are config-agnostic).
        topic: Topic the source belongs to (provenance + directory).
        citation_key: Filename stem under ``sources/<topic>/``.
        title: Human-readable source title for the commit subject and log entry.
        content: The source content as markdown/text (client already converted it).
        source_url: Origin URL recorded in provenance.
        source_type: Original format (``html`` / ``pdf`` / ``markdown`` / ``text``).
        candidate: Empty for a normal default-branch store (byte-identical to
            omitting it), or the WIP branch handle of an open ingest session --
            the store then lands on that candidate's private worktree, never the
            default branch.

    Returns:
        A success envelope with pointer ``{path, commit_sha, changed}`` (plus any
        secret-scrub warnings), or a ``SOURCE_EXISTS`` failure envelope.
    """
    try:
        write_store, work_dir = resolve_candidate_scope(store, vault_root, candidate)
        commit_root = work_dir if work_dir is not None else vault_root
        path = _source_path(topic, citation_key)
        prepared_body = _prepare_source_body(content, source_type)
        scrubbed_body, _spans = scrub(prepared_body)
        conflict = _idempotency_check(write_store, commit_root, path, scrubbed_body)
        if conflict is not None:
            return conflict
        provenance = _build_provenance(topic, citation_key, source_url, source_type, scrubbed_body)
        document = render_source_document(provenance, scrubbed_body)
        return _commit_source(write_store, vault_root, topic, title, path, document, work_dir)
    except KnoticaError as error:
        return error.envelope()


def _prepare_source_body(content: str, source_type: str) -> str:
    """Normalize extracted source text before scrubbing and persistence."""
    if source_type.strip().lower() == "pdf":
        return reflow_pdf_markdown(content)
    return content


def _source_path(topic: str, citation_key: str) -> str:
    """Build the vault-relative source path, rejecting empty or path-bearing keys."""
    cleaned_topic = topic.strip()
    cleaned_key = citation_key.strip()
    if not cleaned_topic or not cleaned_key:
        raise ValueError("store_source requires a non-empty topic and citation_key.")
    if "/" in cleaned_key or "\\" in cleaned_key or cleaned_key.startswith("."):
        raise ValueError(f"citation_key must be a bare filename stem, got: {citation_key!r}")
    return f"{_SOURCES_DIR}/{cleaned_topic}/{cleaned_key}.md"


def _idempotency_check(
    store: VaultStore, commit_root: str | PurePath, path: str, scrubbed_body: str
) -> dict[str, object] | None:
    """Return a no-op success or a ``SOURCE_EXISTS`` failure when the key is taken; else ``None``.

    A source with identical stored content is a no-op success (no transaction);
    a source with the same key but different content is a hard immutability
    failure. An absent key returns ``None`` -- the caller proceeds to write.
    ``commit_root`` is the git root whose ``HEAD`` the no-op pointer reports --
    the worktree for a candidate-scoped store, the canonical vault otherwise.
    """
    if not store.exists(path):
        return None
    _provenance, existing_body = parse_source_document(store.read_text(path))
    if existing_body == scrubbed_body:
        pointer = {"path": path, "commit_sha": VaultVcs(commit_root).head_sha(), "changed": False}
        return ok(pointer)
    return err(
        ErrorCode.SOURCE_EXISTS,
        f"store_source failed because '{path}' already exists with different content; "
        "sources are immutable.",
    )


def _build_provenance(
    topic: str, citation_key: str, source_url: str, source_type: str, scrubbed_body: str
) -> SourceProvenance:
    """Assemble the provenance record; ``sha256`` is the digest of the stored (scrubbed) body."""
    return SourceProvenance(
        topic=topic.strip(),
        citation_key=citation_key.strip(),
        retrieved=datetime.now(UTC).isoformat(),
        origin_url=source_url,
        sha256=body_sha256(scrubbed_body),
        source_type=source_type,
        ingested_by=_INGESTED_BY,
    )


def _commit_source(
    store: VaultStore,
    vault_root: str | PurePath,
    topic: str,
    title: str,
    path: str,
    document: str,
    work_dir: PurePath | None,
) -> dict[str, object]:
    """Open the transaction, declare the source write, and envelope the result.

    ``work_dir`` redirects the commit onto a candidate worktree's branch when
    set (the lock still brackets the canonical ``vault_root``); ``None`` is the
    default-branch path.
    """
    with VaultTransaction(
        store, vault_root, "store_source", topic, title, work_dir=work_dir
    ) as txn:
        txn.write(path, document)
    result = txn.result
    pointer = {"path": path, "commit_sha": result.commit_sha, "changed": result.changed}
    return ok(pointer, warnings=result.warnings())

"""``write_page`` -- the transactional fat-write, with optional root-index upsert.

Validates the target and frontmatter, then opens one
:class:`~knotica.core.transaction.VaultTransaction` that writes the page and --
when an ``index_entry`` is supplied -- upserts this page's line in the root
catalog (``index.md``) inside the *same* commit. Reserved bookkeeping files
(``index.md`` / ``log.md`` / ``SCHEMA.md``) are never valid page targets; the
catalog is maintained only as a side effect here, so consistency cannot be
forgotten (page and index are one call). Idempotent by result-state: identical
page content *and* index line make no commit and return ``changed=False``.
"""

from pathlib import PurePath

from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.index_catalog import INDEX_PATH, upsert_index_bullet
from knotica.core.lint import RESERVED_TOP_LEVEL_NAMES
from knotica.core.page import (
    FieldProblem,
    TopicNotFoundError,
    normalize_page_name,
    page_path,
    parse_page,
    validate_frontmatter,
)
from knotica.core.schema import resolve_schema, validated_topic
from knotica.core.transaction import VaultTransaction
from knotica.store import VaultStore


def write_page(
    store: VaultStore,
    vault_root: str | PurePath,
    topic: str,
    page: str,
    content: str,
    summary: str,
    *,
    index_entry: str | None = None,
) -> dict[str, object]:
    """Create or replace one wiki page atomically (scrub + write + commit + log).

    Args:
        store: The vault storage backend.
        vault_root: The already-resolved vault root (operations are config-agnostic).
        topic: Owning topic; must already exist.
        page: Page name or topic-relative path; never a reserved bookkeeping file.
        content: Full markdown body including YAML frontmatter.
        summary: One-line change summary for the commit subject and log entry.
        index_entry: Optional catalog line text; when set, this page's root
            ``index.md`` line is upserted in the same commit.

    Returns:
        A success envelope with pointer ``{path, commit_sha, changed}`` (plus any
        secret-scrub warnings), or a typed failure envelope.
    """
    try:
        cleaned_topic = validated_topic(topic)
        target = _validate_page_target(page)
        resolve_schema(store, cleaned_topic)
        _validate_content_frontmatter(content)
        path = page_path(cleaned_topic, page)
        return _commit_page(
            store, vault_root, cleaned_topic, target, path, content, summary, index_entry
        )
    except _WritePageRejected as rejected:
        return rejected.envelope
    except TopicNotFoundError as error:
        return err(ErrorCode.TOPIC_NOT_FOUND, str(error))
    except KnoticaError as error:
        return error.envelope()


class _WritePageRejected(Exception):
    """Internal signal carrying a pre-built failure envelope for a rejected input."""

    def __init__(self, envelope: dict[str, object]) -> None:
        super().__init__()
        self.envelope = envelope


def _validate_page_target(page: str) -> str:
    """Return the normalized page path, or reject a reserved / malformed target."""
    try:
        normalized = normalize_page_name(page)
    except ValueError as error:
        raise _WritePageRejected(
            err(
                ErrorCode.RESERVED_NAME,
                f"write_page failed because the page name is not a valid page path: {error}",
            )
        ) from error
    basename = normalized.rsplit("/", 1)[-1]
    if basename in RESERVED_TOP_LEVEL_NAMES:
        raise _WritePageRejected(
            err(
                ErrorCode.RESERVED_NAME,
                f"write_page failed because '{basename}' is a reserved bookkeeping file that is "
                "maintained only as a side effect, never written directly. "
                f"Reserved names: {', '.join(sorted(RESERVED_TOP_LEVEL_NAMES))}.",
            )
        )
    return normalized


def _validate_content_frontmatter(content: str) -> None:
    """Reject content whose frontmatter is absent, unparseable, or non-conforming."""
    frontmatter, parse_error, _body = parse_page(content)
    if frontmatter is None:
        detail = parse_error or "the content has no leading YAML frontmatter block"
        raise _WritePageRejected(
            err(
                ErrorCode.INVALID_FRONTMATTER,
                f"write_page failed because the page frontmatter could not be read: {detail}.",
            )
        )
    problems = validate_frontmatter(frontmatter)
    if problems:
        raise _WritePageRejected(
            err(
                ErrorCode.INVALID_FRONTMATTER,
                "write_page failed because the page frontmatter does not conform to the schema: "
                + _describe_problems(problems)
                + ".",
                fix="Add or fix these frontmatter fields: " + _describe_problems(problems) + ".",
            )
        )


def _describe_problems(problems: list[FieldProblem]) -> str:
    """Render field problems as a compact ``field: problem`` list."""
    return "; ".join(f"{problem.field}: {problem.problem}" for problem in problems)


def _commit_page(
    store: VaultStore,
    vault_root: str | PurePath,
    topic: str,
    normalized_page: str,
    path: str,
    content: str,
    summary: str,
    index_entry: str | None,
) -> dict[str, object]:
    """Open the transaction, declare the page (and optional index) write, envelope the result."""
    with VaultTransaction(store, vault_root, "write_page", topic, summary) as txn:
        txn.write(path, content)
        if index_entry:
            existing_index = store.read_text(INDEX_PATH) if store.exists(INDEX_PATH) else ""
            txn.write(
                INDEX_PATH,
                upsert_index_bullet(
                    existing_index,
                    vault_path=path,
                    index_entry=index_entry,
                    section=topic,
                ),
            )
    result = txn.result
    pointer = {"path": path, "commit_sha": result.commit_sha, "changed": result.changed}
    return ok(pointer, warnings=result.warnings())

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

from knotica.core.config import resolve
from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.lint import INDEX_PATH, RESERVED_TOP_LEVEL_NAMES
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
from knotica.store import LocalFSStore, VaultStore


def _resolved_vault(
    store: VaultStore | None, vault_root: str | PurePath | None
) -> tuple[VaultStore, str | PurePath]:
    """Return an explicit ``(store, vault_root)`` pair, resolving the configured vault when omitted.

    Adapters pass a pre-resolved vault (config-agnostic path); callers that pass
    neither get the configured default vault resolved per call (honoring
    ``$KNOTICA_CONFIG``), which raises ``NOT_CONFIGURED`` when the vault is unset.
    """
    if store is not None and vault_root is not None:
        return store, vault_root
    root = resolve().path
    return (store or LocalFSStore(root)), (vault_root if vault_root is not None else root)


def write_page(
    topic: str,
    page: str,
    content: str,
    summary: str,
    *,
    index_entry: str | None = None,
    store: VaultStore | None = None,
    vault_root: str | PurePath | None = None,
) -> dict[str, object]:
    """Create or replace one wiki page atomically (scrub + write + commit + log).

    Args:
        topic: Owning topic; must already exist.
        page: Page name or topic-relative path; never a reserved bookkeeping file.
        content: Full markdown body including YAML frontmatter.
        summary: One-line change summary for the commit subject and log entry.
        index_entry: Optional catalog line text; when set, this page's root
            ``index.md`` line is upserted in the same commit.
        store: Vault storage backend. Omit to resolve the configured default vault.
        vault_root: Resolved vault root. Omit to resolve from config alongside ``store``.

    Returns:
        A success envelope with pointer ``{path, commit_sha, changed}`` (plus any
        secret-scrub warnings), or a typed failure envelope.
    """
    try:
        vault_store, root = _resolved_vault(store, vault_root)
        cleaned_topic = validated_topic(topic)
        target = _validate_page_target(page)
        resolve_schema(vault_store, cleaned_topic)
        _validate_content_frontmatter(content)
        path = page_path(cleaned_topic, page)
        return _commit_page(
            vault_store, root, cleaned_topic, target, path, content, summary, index_entry
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
            stem = normalized_page[: -len(".md")]
            existing_index = store.read_text(INDEX_PATH) if store.exists(INDEX_PATH) else ""
            txn.write(INDEX_PATH, _upsert_index_entry(existing_index, topic, stem, index_entry))
    result = txn.result
    pointer = {"path": path, "commit_sha": result.commit_sha, "changed": result.changed}
    return ok(pointer, warnings=result.warnings())


def _upsert_index_entry(index_text: str, topic: str, page_stem: str, index_entry: str) -> str:
    """Replace this page's catalog bullet (keyed by wikilink), or add it, preserving all others.

    The bullet is a single line: ``- [[<topic>/<page_stem>]] — <index_entry>``.
    An existing entry -- even one wrapped across continuation lines -- is replaced
    in place; a new entry is appended under the topic's ``### <topic>`` section
    when present, otherwise at the end of the file.
    """
    wikilink = f"[[{topic}/{page_stem}]]"
    new_bullet = f"- {wikilink} — {index_entry}"
    lines = index_text.splitlines()
    block = _find_bullet_block(lines, wikilink)
    if block is not None:
        start, end = block
        updated = lines[:start] + [new_bullet] + lines[end:]
    else:
        insert_at = _topic_section_insert_point(lines, topic)
        updated = lines[:insert_at] + [new_bullet] + lines[insert_at:]
    return "\n".join(updated).rstrip("\n") + "\n"


def _find_bullet_block(lines: list[str], wikilink: str) -> tuple[int, int] | None:
    """Return the ``[start, end)`` line span of the bullet for ``wikilink``, if present."""
    for index, line in enumerate(lines):
        if line.lstrip().startswith("- ") and wikilink in line:
            end = index + 1
            while end < len(lines) and _is_continuation(lines[end]):
                end += 1
            return index, end
    return None


def _is_continuation(line: str) -> bool:
    """Whether ``line`` continues the preceding bullet (indented, not a new bullet/heading)."""
    stripped = line.lstrip()
    return bool(stripped) and not stripped.startswith("- ") and not stripped.startswith("#")


def _topic_section_insert_point(lines: list[str], topic: str) -> int:
    """Line index to insert a new bullet: after the topic section's content, or end of file."""
    header = f"### {topic}"
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == header)
    except StopIteration:
        return len(lines)
    end = start + 1
    while end < len(lines) and not lines[end].lstrip().startswith("#"):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end

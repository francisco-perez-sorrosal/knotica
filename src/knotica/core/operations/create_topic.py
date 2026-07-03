"""``create_topic`` -- deterministic topic scaffolding as one commit.

Scaffolds a new topic's whole starting skeleton -- an empty ``SCHEMA.md``
overlay that inherits the root constitution (divergence is earned, so the
overlay starts empty), the hidden ``.knotica/`` state (an empty
``datasets/qa.jsonl`` plus empty ``prompts/`` and ``compiled/`` directories),
and the topic's ``index.md`` catalog section -- inside a single
:class:`~knotica.core.transaction.VaultTransaction`. ``metrics.jsonl`` is *not*
created here: it is a lazy eval artifact whose absence means "not yet
evaluated". Idempotent by existence: an existing topic returns
``existed=True`` and makes no commit (and never re-scaffolds, so an earned
overlay is preserved). A reserved top-level name fails fast with
``RESERVED_NAME``.
"""

from pathlib import PurePath

from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.lint import INDEX_PATH, RESERVED_TOP_LEVEL_NAMES
from knotica.core.page import serialize_frontmatter
from knotica.core.schema import overlay_path, validated_topic
from knotica.core.transaction import VaultTransaction
from knotica.core.vcs import VaultVcs
from knotica.store import VaultStore

#: schema_version stamped on a new topic's (empty) overlay. Overlays are
#: self-versioned so they evolve additively alongside the root constitution.
_OVERLAY_SCHEMA_VERSION = 1

#: Hidden per-topic state directory (datasets, prompts, compiled artifacts).
_KNOTICA_DIR = ".knotica"


def qa_dataset_path(topic: str) -> str:
    """Vault-relative path of ``topic``'s curated-example dataset (the DSPy flywheel).

    Single source of truth for the topic's ``qa.jsonl`` location -- both the
    scaffolding here and the ``curate_example`` append target derive from it.
    """
    return f"{topic}/{_KNOTICA_DIR}/datasets/qa.jsonl"


def create_topic(
    store: VaultStore,
    vault_root: str | PurePath,
    topic: str,
    description: str | None = None,
) -> dict[str, object]:
    """Scaffold a new topic (or report it already existed) as one atomic commit.

    Args:
        store: The vault storage backend.
        vault_root: The already-resolved vault root (operations are config-agnostic).
        topic: New topic name; must not collide with a reserved top-level name.
        description: Optional one-line description for the topic's catalog section.

    Returns:
        A success envelope with pointer ``{topic, path, commit_sha, existed}``
        (plus any secret-scrub warnings), or a typed failure envelope.
    """
    stripped = topic.strip()
    if stripped in RESERVED_TOP_LEVEL_NAMES:
        return err(
            ErrorCode.RESERVED_NAME,
            f"create_topic failed because '{stripped}' is a reserved top-level name. "
            f"Reserved names: {', '.join(sorted(RESERVED_TOP_LEVEL_NAMES))}.",
        )
    try:
        cleaned = validated_topic(topic)
    except ValueError as error:
        return err(ErrorCode.RESERVED_NAME, f"create_topic failed because {error}")

    if store.exists(cleaned):
        return ok(
            {
                "topic": cleaned,
                "path": cleaned,
                "commit_sha": VaultVcs(vault_root).head_sha(),
                "existed": True,
            }
        )
    return _scaffold_topic(store, vault_root, cleaned, description)


def _scaffold_topic(
    store: VaultStore,
    vault_root: str | PurePath,
    topic: str,
    description: str | None,
) -> dict[str, object]:
    """Buffer every scaffolding write into one transaction and envelope the result."""
    try:
        with VaultTransaction(store, vault_root, "create_topic", topic, _title(topic)) as txn:
            txn.write(overlay_path(topic), _overlay_content(topic))
            txn.write(qa_dataset_path(topic), "")
            txn.write(f"{topic}/{_KNOTICA_DIR}/prompts/.gitkeep", "")
            txn.write(f"{topic}/{_KNOTICA_DIR}/compiled/.gitkeep", "")
            existing_index = store.read_text(INDEX_PATH) if store.exists(INDEX_PATH) else ""
            txn.write(INDEX_PATH, _appended_topic_section(existing_index, topic, description))
    except KnoticaError as error:
        return error.envelope()
    result = txn.result
    pointer = {
        "topic": topic,
        "path": topic,
        "commit_sha": result.commit_sha,
        "existed": False,
    }
    return ok(pointer, warnings=result.warnings())


def _title(topic: str) -> str:
    """Human-readable title slot for the commit subject and log entry."""
    return f"new topic {topic}"


def _overlay_content(topic: str) -> str:
    """Render the empty starting overlay: schema_version frontmatter + inherit note.

    The overlay starts empty because divergence from the root constitution is
    earned -- a new topic resolves to the root alone until it grows conventions
    worth recording here.
    """
    frontmatter = serialize_frontmatter({"schema_version": _OVERLAY_SCHEMA_VERSION})
    body = (
        f"# SCHEMA -- {topic} overlay\n"
        "\n"
        f"This overlay extends the root constitution (root `SCHEMA.md`) for the `{topic}` "
        "topic. It starts empty -- divergence is earned. Add entity types, page templates, "
        "and topic conventions here only when this topic genuinely needs to refine the root. "
        "It never contradicts the root; contradictions are lint violations.\n"
    )
    return frontmatter + "\n" + body


def _appended_topic_section(index_text: str, topic: str, description: str | None) -> str:
    """Append a ``### <topic>`` catalog section to the root index, preserving all else.

    A new topic has no pages yet, so the section carries only its heading and an
    optional one-line description; page bullets are added later by ``write_page``.
    """
    description_line = (
        description.strip() if description and description.strip() else _NO_PAGES_NOTE
    )
    section = f"### {topic}\n\n{description_line}"
    if not index_text.strip():
        return section + "\n"
    return index_text.rstrip("\n") + "\n\n" + section + "\n"


#: Placeholder shown under a freshly created topic that has no pages yet.
_NO_PAGES_NOTE = "*(new topic -- no pages yet)*"

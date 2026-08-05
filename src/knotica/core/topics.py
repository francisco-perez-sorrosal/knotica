"""Topic identity against the store — is this name a topic, and does it exist?

``core.vault_layout`` is the single declaration of *path* classification: given a
vault-relative string, which folder family holds it. That module is a
zero-dependency leaf by design (it imports nothing from ``knotica``), so it
cannot answer the other half of the question — whether the name corresponds to a
directory that is actually there. Answering that needs the store, so it lives
here instead, one layer up: this module owns every predicate that decides
"is this a topic?" by *reading* the vault.

Two predicates, deliberately not folded into one:

* :func:`is_topic` — an **enumeration filter**. Callers walk ``list_dir("")`` and
  keep the entries that are visible, non-reserved directories. It never raises:
  a name that is not there is simply not a topic.
* :func:`require_topic` — a **boundary assertion** on caller-supplied input. It
  normalizes the string and raises
  :class:`~knotica.core.page.TopicNotFoundError` when it does not name an
  existing topic directory. It rejects nested paths (``a/b``) rather than
  reserved names, because the input it guards is a topic *argument*, not a
  directory entry already known to sit at the vault root.

:func:`topic_directories` is the enumeration those two imply: the one walk of the
vault root that every "which topics are there?" caller shares. It lives here
rather than at each call site because a wrapper around a consolidated predicate
is still a copy of the policy -- three modules held their own, and one of them
had drifted onto an inline re-implementation that never reached ``is_topic`` at
all (``td-040``).

Every consumer -- ``core.status``, ``core.lint``, ``core.vault_metadata_tree``,
``core.datasets_inventory``, ``core.golden_review``, ``mcp_server.tools_read``,
``service.manager`` -- shares these three, so a search result can never disagree
with the rest of the codebase about what counts as a topic.
"""

from __future__ import annotations

from knotica.core.page import TopicNotFoundError
from knotica.core.vault_layout import RESERVED_TOP_LEVEL_NAMES
from knotica.store import VaultStore

__all__ = ["is_topic", "require_topic", "topic_directories"]


def is_topic(store: VaultStore, name: str) -> bool:
    """Whether a top-level entry is a topic: a visible, non-reserved directory.

    Total over any string: a name that does not exist, or that exists but is a
    file, is simply not a topic. This never raises, so it is safe to use as a
    filter over names that were not sourced from ``list_dir("")``.
    """
    if name.startswith(".") or name in RESERVED_TOP_LEVEL_NAMES:
        return False
    if not store.exists(name):
        return False
    try:
        store.list_dir(name)
    except (NotADirectoryError, FileNotFoundError):
        return False
    return True


def topic_directories(store: VaultStore) -> list[str]:
    """Every topic directory at the vault root, in lexicographic order.

    The single enumeration of "which topics does this vault hold?". Order is
    deterministic without a second sort: ``VaultStore.list_dir`` is contractually
    sorted, so filtering it preserves that order.
    """
    return [name for name in store.list_dir("") if is_topic(store, name)]


def require_topic(store: VaultStore, topic: str) -> str:
    """Normalize a caller-supplied topic argument, or raise ``TopicNotFoundError``.

    Strips surrounding whitespace and slashes and rejects anything that is empty
    or nested (``a/b``) before checking that the directory is present.
    """
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    if not store.exists(cleaned):
        raise TopicNotFoundError(cleaned)
    try:
        store.list_dir(cleaned)
    except (NotADirectoryError, FileNotFoundError) as exc:
        raise TopicNotFoundError(cleaned) from exc
    return cleaned

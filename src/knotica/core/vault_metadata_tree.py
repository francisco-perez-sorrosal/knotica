"""Deterministic vault metadata tree — ``.knotica`` substrate + optional root overlays.

Read-only walk over what exists on disk. Used by the dashboard Vault pane and the
``vault_metadata_tree`` MCP tool. Never lists wiki content pages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from knotica.core.lint import RESERVED_TOP_LEVEL_NAMES
from knotica.core.page import TopicNotFoundError
from knotica.core.schema import validated_topic
from knotica.store import VaultStore

__all__ = ["gather_vault_metadata_tree"]

_ROOT_METADATA_FILES = ("SCHEMA.md", "log.md")
_TOPIC_OVERLAY = "SCHEMA.md"


def gather_vault_metadata_tree(
    store: VaultStore,
    vault_path: Path,
    *,
    topic: str = "",
) -> dict[str, Any]:
    """Build a nested metadata tree for the vault (optionally scoped to one topic)."""
    cleaned = topic.strip().strip("/")
    if cleaned:
        try:
            validated_topic(cleaned)
        except ValueError as exc:
            raise TopicNotFoundError(cleaned) from exc
        if not _is_topic(store, cleaned):
            raise TopicNotFoundError(cleaned)
        topic_names = [cleaned]
    else:
        topic_names = _topic_directories(store)

    children: list[dict[str, Any]] = []
    for rel in _ROOT_METADATA_FILES:
        if store.exists(rel):
            children.append(_node_from_path(store, vault_path, rel))

    if store.exists(".knotica"):
        children.append(_walk_dir(store, vault_path, ".knotica"))

    for name in topic_names:
        branch = _topic_branch(store, vault_path, name)
        if branch["children"]:
            children.append(branch)

    return {
        "schema_version": 1,
        "topic": cleaned or None,
        "children": children,
    }


def _topic_branch(store: VaultStore, vault_path: Path, topic: str) -> dict[str, Any]:
    topic_children: list[dict[str, Any]] = []
    overlay = f"{topic}/{_TOPIC_OVERLAY}"
    if store.exists(overlay):
        topic_children.append(_node_from_path(store, vault_path, overlay))

    knotica = f"{topic}/.knotica"
    if store.exists(knotica):
        topic_children.append(_walk_dir(store, vault_path, knotica))

    return {
        "name": topic,
        "path": topic,
        "kind": "dir",
        "scope": "topic",
        "exists": True,
        "children": topic_children,
    }


def _walk_dir(store: VaultStore, vault_path: Path, rel_dir: str) -> dict[str, Any]:
    name = rel_dir.rsplit("/", 1)[-1]
    children: list[dict[str, Any]] = []
    for entry in store.list_dir(rel_dir):
        child_rel = f"{rel_dir}/{entry}"
        if _looks_like_dir(store, child_rel):
            children.append(_walk_dir(store, vault_path, child_rel))
        else:
            children.append(_node_from_path(store, vault_path, child_rel))
    return {
        "name": name,
        "path": rel_dir,
        "kind": "dir",
        "exists": True,
        "children": children,
    }


def _node_from_path(store: VaultStore, vault_path: Path, rel_path: str) -> dict[str, Any]:
    name = rel_path.rsplit("/", 1)[-1]
    node: dict[str, Any] = {
        "name": name,
        "path": rel_path,
        "kind": "file",
        "exists": True,
    }
    abs_path = vault_path / rel_path
    try:
        stat = abs_path.stat()
    except OSError:
        return node
    node["size"] = stat.st_size
    node["mtime"] = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
    return node


def _looks_like_dir(store: VaultStore, rel_path: str) -> bool:
    try:
        store.list_dir(rel_path)
    except NotADirectoryError:
        return False
    except FileNotFoundError:
        return False
    return True


def _topic_directories(store: VaultStore) -> list[str]:
    return [name for name in sorted(store.list_dir("")) if _is_topic(store, name)]


def _is_topic(store: VaultStore, name: str) -> bool:
    if name.startswith(".") or name in RESERVED_TOP_LEVEL_NAMES:
        return False
    if not store.exists(name):
        return False
    try:
        store.list_dir(name)
    except (NotADirectoryError, FileNotFoundError):
        return False
    return True

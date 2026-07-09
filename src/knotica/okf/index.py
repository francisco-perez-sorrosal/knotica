"""Vault index for OKF link resolution and bundle operations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from knotica.core.links import iter_page_paths
from knotica.core.page import parse_page
from knotica.okf.frontmatter import is_reserved_file
from knotica.store import VaultStore


@dataclass
class VaultIndex:
    """Central vault index for resolution and export."""

    bundle_root: str
    concept_paths: set[str] = field(default_factory=set)
    reserved_paths: set[str] = field(default_factory=set)
    by_concept_id: dict[str, str] = field(default_factory=dict)
    by_basename: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    by_title: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    by_h1: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    frontmatter_by_path: dict[str, dict[str, object]] = field(default_factory=dict)
    body_by_path: dict[str, str] = field(default_factory=dict)


def build_vault_index(
    store: VaultStore,
    *,
    overrides: dict[str, str] | None = None,
) -> VaultIndex:
    """Scan the vault once and build a resolution index."""
    index = VaultIndex(bundle_root=str(store.root))
    overrides = overrides or {}
    for path in iter_page_paths(store):
        raw = overrides.get(path, store.read_text(path))
        frontmatter, _error, body = parse_page(raw)
        index.body_by_path[path] = body
        if is_reserved_file(path):
            index.reserved_paths.add(path)
            index.concept_paths.add(path)
            index.by_concept_id[path.removesuffix(".md")] = path
            basename = PurePosixPath(path).stem
            index.by_basename[basename].append(path)
            continue
        if not path.endswith(".md"):
            continue
        index.concept_paths.add(path)
        concept_id = path.removesuffix(".md")
        index.by_concept_id[concept_id] = path
        basename = PurePosixPath(path).stem
        index.by_basename[basename].append(path)
        if frontmatter:
            index.frontmatter_by_path[path] = frontmatter
            title = frontmatter.get("title")
            if isinstance(title, str) and title.strip():
                index.by_title[title.strip().lower()].append(path)
        for line in body.splitlines():
            if line.startswith("# "):
                index.by_h1[line[2:].strip().lower()].append(path)
                break
    return index


def topic_root_for_path(path: str) -> str | None:
    """Return the topic directory for a vault path, if any."""
    parts = PurePosixPath(path).parts
    if not parts:
        return None
    if parts[0] == "sources" and len(parts) > 2:
        return parts[1]
    if parts[0] not in {"sources", ".knotica", ".git"} and "/" not in path:
        return None
    if len(parts) >= 2 and parts[0] not in {"sources", "reports"}:
        return parts[0]
    return parts[0] if parts[0] not in {"sources", "reports", "index.md", "log.md"} else None

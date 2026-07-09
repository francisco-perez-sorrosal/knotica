"""Repair stored PDF sources with hard-wrapped extraction text."""

from __future__ import annotations

from dataclasses import replace
from pathlib import PurePath

from knotica.core.errors import KnoticaError, ok
from knotica.core.links import iter_page_paths
from knotica.core.records import (
    body_sha256,
    parse_source_document,
    render_source_document,
)
from knotica.core.text_reflow import reflow_pdf_markdown
from knotica.core.transaction import VaultTransaction
from knotica.store import VaultStore

__all__ = ["reflow_stored_source_document", "repair_pdf_sources"]


def reflow_stored_source_document(text: str) -> tuple[str, bool]:
    """Reflow one stored source document body; return updated file text when changed."""
    provenance, body = parse_source_document(text)
    if provenance.source_type.strip().lower() != "pdf":
        return text, False
    reflowed = reflow_pdf_markdown(body)
    if reflowed == body:
        return text, False
    updated = replace(provenance, sha256=body_sha256(reflowed))
    return render_source_document(updated, reflowed), True


def repair_pdf_sources(store: VaultStore, vault_root: str | PurePath) -> dict[str, object]:
    """Reflow every stored PDF source in the vault and commit changed files."""
    changed_paths: list[str] = []
    for path in iter_page_paths(store):
        if not path.startswith("sources/"):
            continue
        raw = store.read_text(path)
        try:
            provenance, _body = parse_source_document(raw)
        except Exception:
            continue
        if provenance.source_type.strip().lower() != "pdf":
            continue
        updated, changed = reflow_stored_source_document(raw)
        if not changed:
            continue
        try:
            with VaultTransaction(
                store,
                vault_root,
                op="repair",
                topic=provenance.topic,
                title=f"reflow PDF source {provenance.citation_key}",
            ) as tx:
                tx.write(path, updated)
        except KnoticaError as error:
            return error.envelope()
        changed_paths.append(path)

    return ok({"changed": bool(changed_paths), "paths": changed_paths})

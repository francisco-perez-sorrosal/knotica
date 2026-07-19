"""``guillotine`` -- claim trial artifacts and optional patch application."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path, PurePath

from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.gapfill import file_retracted_gap
from knotica.core.index_catalog import INDEX_PATH, REPORTS_SECTION, upsert_index_bullet
from knotica.core.transaction import VaultTransaction
from knotica.guillotine.models import GuillotineResult, Verdict
from knotica.guillotine.patch import apply_patches_to_contents
from knotica.guillotine.report import (
    artifact_paths_for,
    guillotine_index_entry,
    render_report_json,
    render_report_markdown,
)
from knotica.store import VaultStore

__all__ = ["apply_guillotine", "persist_guillotine_artifacts"]

#: Applied verdicts that weaken existing knowledge -- each leaves a hole the wiki
#: can no longer answer, so it files a ``retracted`` gap into the P1 queue for
#: re-sourcing. KEEP / QUALIFY / QUARANTINE_SOURCE do not weaken and file nothing.
_WEAKENING_VERDICTS: frozenset[Verdict] = frozenset(
    {
        Verdict.RETRACT,
        Verdict.DEMOTE,
        Verdict.DISPUTE,
        Verdict.DELETE_UNSUPPORTED_SYNTHESIS,
    }
)


def _artifact_bundle(
    result: GuillotineResult,
    diff_text: str,
    *,
    dry_run: bool,
    commit_sha: str | None = None,
) -> tuple[str, str, str, str, str]:
    """Render report markdown, diff text, and JSON sidecar for one trial."""
    artifacts = artifact_paths_for(result)
    generated_at = datetime.now(UTC)
    report_md = render_report_markdown(
        result,
        artifacts.diff_path,
        dry_run=dry_run,
        commit_sha=commit_sha,
        generated_at=generated_at,
        report_path=artifacts.report_path,
    )
    json_payload = render_report_json(
        result,
        artifacts.report_path,
        artifacts.diff_path,
        artifacts.json_path,
        dry_run=dry_run,
        generated_at=generated_at,
    )
    json_text = json.dumps(json_payload, ensure_ascii=False, indent=2) + "\n"
    return (
        artifacts.report_path,
        artifacts.diff_path,
        artifacts.json_path,
        report_md,
        json_text,
    )


def _write_guillotine_transaction(
    store: VaultStore,
    vault_root: str | PurePath,
    result: GuillotineResult,
    *,
    summary: str,
    report_path: str,
    diff_path: str,
    json_path: str,
    report_md: str,
    diff_text: str,
    json_text: str,
    dry_run: bool,
    page_updates: dict[str, str] | None = None,
) -> dict[str, object]:
    """Commit guillotine artifacts, optional wiki patches, and catalog index line."""
    index_entry = guillotine_index_entry(result, dry_run=dry_run)
    try:
        with VaultTransaction(
            store, vault_root, op="guillotine", topic=result.topic, title=summary
        ) as tx:
            for path, content in (page_updates or {}).items():
                tx.write(path, content)
            tx.write(report_path, report_md)
            tx.write(diff_path, diff_text)
            tx.write(json_path, json_text)
            existing_index = store.read_text(INDEX_PATH) if store.exists(INDEX_PATH) else ""
            tx.write(
                INDEX_PATH,
                upsert_index_bullet(
                    existing_index,
                    vault_path=report_path,
                    index_entry=index_entry,
                    section=REPORTS_SECTION,
                ),
            )
        result_state = tx.result
    except KnoticaError as error:
        return error.envelope()

    return ok(
        {
            "report_path": report_path,
            "diff_path": diff_path,
            "json_path": json_path,
            "commit_sha": result_state.commit_sha,
            "changed": result_state.changed,
            "touched_paths": list(result_state.touched_paths),
            "recommendation": result.recommendation.value,
            "risk_score": result.risk_score,
        },
        warnings=result_state.warnings(),
    )


def persist_guillotine_artifacts(
    store: VaultStore,
    vault_root: str | PurePath,
    result: GuillotineResult,
    diff_text: str,
    *,
    summary: str,
) -> dict[str, object]:
    """Write guillotine report artifacts in one vault transaction (dry-run mode)."""
    report_path, diff_path, json_path, report_md, json_text = _artifact_bundle(
        result, diff_text, dry_run=True
    )
    return _write_guillotine_transaction(
        store,
        vault_root,
        result,
        summary=summary,
        report_path=report_path,
        diff_path=diff_path,
        json_path=json_path,
        report_md=report_md,
        diff_text=diff_text,
        json_text=json_text,
        dry_run=True,
    )


def apply_guillotine(
    store: VaultStore,
    vault_root: str | PurePath,
    result: GuillotineResult,
    diff_text: str,
    *,
    summary: str,
) -> dict[str, object]:
    """Apply synthesized page patches and commit artifacts in one transaction."""
    if not result.patches:
        return err(
            ErrorCode.RESERVED_NAME,
            "apply_guillotine failed because there are no patches to apply.",
        )
    if any(patch.path.startswith("sources/") for patch in result.patches):
        return err(
            ErrorCode.RESERVED_NAME,
            "apply_guillotine refused to modify immutable raw source files.",
        )

    report_path, diff_path, json_path, report_md, json_text = _artifact_bundle(
        result, diff_text, dry_run=False
    )
    file_contents = {patch.path: store.read_text(patch.path) for patch in result.patches}
    updated = apply_patches_to_contents(file_contents, list(result.patches))
    envelope = _write_guillotine_transaction(
        store,
        vault_root,
        result,
        summary=summary,
        report_path=report_path,
        diff_path=diff_path,
        json_path=json_path,
        report_md=report_md,
        diff_text=diff_text,
        json_text=json_text,
        dry_run=False,
        page_updates=updated,
    )
    _maybe_file_retracted_gap(store, vault_root, result, report_path, envelope)
    return envelope


def _maybe_file_retracted_gap(
    store: VaultStore,
    vault_root: str | PurePath,
    result: GuillotineResult,
    report_path: str,
    envelope: dict[str, object],
) -> None:
    """File a ``retracted`` gap when an applied weakening verdict left a hole.

    Runs only after a successful apply commit (never on an error envelope) and
    only for a weakening verdict. The gap is written in its **own**
    ``VaultTransaction`` (separate from the guillotine artifact commit) and is
    **failure-isolated**: a gap-write failure surfaces as a warning on the
    envelope but never fails the guillotine apply, which has already committed.
    """
    if "error" in envelope or result.recommendation not in _WEAKENING_VERDICTS:
        return
    try:
        file_retracted_gap(
            store,
            Path(vault_root),
            result.topic,
            result.claim,
            verdict=result.recommendation.value,
            report_path=report_path,
        )
    except Exception as error:  # noqa: BLE001 — gap filing must never fail an applied retraction
        warning = {
            "code": "gap_write_skipped",
            "message": (
                f"{result.recommendation.value} was applied, but filing the "
                f"retracted-knowledge gap failed: {error}"
            ),
            "fix": (
                "The claim change committed successfully; re-file the gap with "
                "gap_report if the wiki still cannot answer this."
            ),
        }
        warnings = envelope.get("warnings")
        if isinstance(warnings, list):
            warnings.append(warning)
        else:
            envelope["warnings"] = [warning]

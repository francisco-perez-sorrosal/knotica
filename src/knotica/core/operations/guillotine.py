"""``guillotine`` -- claim trial artifacts and optional patch application."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import PurePath

from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.transaction import VaultTransaction
from knotica.guillotine.models import GuillotineResult
from knotica.guillotine.patch import apply_patches_to_contents
from knotica.guillotine.report import (
    artifact_paths_for,
    build_report,
    render_report_json,
    render_report_markdown,
)
from knotica.store import VaultStore

__all__ = ["apply_guillotine", "persist_guillotine_artifacts"]


def persist_guillotine_artifacts(
    store: VaultStore,
    result: GuillotineResult,
    diff_text: str,
) -> dict[str, object]:
    """Write guillotine report artifacts without applying page patches or committing."""
    artifacts = artifact_paths_for(result)
    generated_at = datetime.now(UTC)
    report_md = render_report_markdown(
        result,
        artifacts.diff_path,
        dry_run=True,
        commit_sha=None,
        generated_at=generated_at,
    )
    json_payload = render_report_json(
        result,
        artifacts.report_path,
        artifacts.diff_path,
        artifacts.json_path,
        dry_run=True,
        generated_at=generated_at,
    )
    store.write_text_atomic(artifacts.report_path, report_md)
    store.write_text_atomic(artifacts.diff_path, diff_text)
    store.write_text_atomic(
        artifacts.json_path, json.dumps(json_payload, ensure_ascii=False, indent=2) + "\n"
    )
    report = build_report(result, artifacts, dry_run=True)
    return ok(
        {
            "report_path": artifacts.report_path,
            "diff_path": artifacts.diff_path,
            "json_path": artifacts.json_path,
            "recommendation": report.recommendation.value,
            "risk_score": report.risk_score,
        }
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

    artifacts = artifact_paths_for(result)
    file_contents = {patch.path: store.read_text(patch.path) for patch in result.patches}
    updated = apply_patches_to_contents(file_contents, list(result.patches))
    generated_at = datetime.now(UTC)
    report_md = render_report_markdown(
        result,
        artifacts.diff_path,
        dry_run=False,
        commit_sha=None,
        generated_at=generated_at,
    )
    json_payload = render_report_json(
        result,
        artifacts.report_path,
        artifacts.diff_path,
        artifacts.json_path,
        dry_run=False,
        generated_at=generated_at,
    )

    try:
        with VaultTransaction(
            store, vault_root, op="guillotine", topic=result.topic, title=summary
        ) as tx:
            for path, content in updated.items():
                tx.write(path, content)
            tx.write(artifacts.report_path, report_md)
            tx.write(artifacts.diff_path, diff_text)
            tx.write(
                artifacts.json_path,
                json.dumps(json_payload, ensure_ascii=False, indent=2) + "\n",
            )
        result_state = tx.result
    except KnoticaError as error:
        return error.envelope()

    return ok(
        {
            "report_path": artifacts.report_path,
            "diff_path": artifacts.diff_path,
            "json_path": artifacts.json_path,
            "commit_sha": result_state.commit_sha,
            "changed": result_state.changed,
            "touched_paths": list(result_state.touched_paths),
            "recommendation": result.recommendation.value,
            "risk_score": result.risk_score,
        },
        warnings=result_state.warnings(),
    )

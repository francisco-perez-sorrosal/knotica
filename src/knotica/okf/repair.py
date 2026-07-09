"""OKF vault repair -- restore native OKF compatibility in the active vault."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from knotica.core.links import iter_page_paths
from knotica.core.page import parse_page, serialize_frontmatter
from knotica.okf.datetime_fmt import now_rfc3339
from knotica.okf.check import check_vault
from knotica.okf.frontmatter import (
    is_concept_file,
    is_reserved_file,
    normalize_concept_frontmatter,
    render_concept_document,
)
from knotica.okf.log_fmt import canonicalize_log
from knotica.store import VaultStore


@dataclass(frozen=True)
class RepairOptions:
    """Options controlling OKF repair."""

    apply: bool = False
    force: bool = False
    reports_dir: Path | None = None


@dataclass
class RepairResult:
    """Outcome of an OKF repair run."""

    status: str
    dry_run: bool
    files_changed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    report_path: str | None = None
    commit_sha: str | None = None


def repair_vault(store: VaultStore, options: RepairOptions) -> RepairResult:
    """Repair the active vault for native OKF compatibility."""
    vault_root = Path(store.root).resolve()
    result = RepairResult(status="OK", dry_run=not options.apply)

    if options.apply and not options.force and _git_dirty(vault_root):
        raise ValueError("git working tree is dirty; commit or stash changes, or pass --force")

    planned: dict[str, str] = {}

    for path in sorted(iter_page_paths(store)):
        raw = store.read_text(path)
        if is_concept_file(path):
            normalized = normalize_concept_frontmatter(path, raw)
            if normalized.changed or normalized.warnings:
                new_content = render_concept_document(path, raw)
                if new_content != raw:
                    planned[path] = new_content
                    result.warnings.extend(f"{path}: {w}" for w in normalized.warnings)
        elif path.endswith("index.md") and raw.startswith("---"):
            _, _err, body = parse_page(raw)
            preamble = "# Index\n\n<!-- frontmatter removed by okf repair -->\n\n"
            planned[path] = preamble + body.lstrip()
            result.warnings.append(f"{path}: removed accidental frontmatter")
        elif path.endswith("log.md"):
            canonical = canonicalize_log(raw)
            if canonical != raw:
                planned[path] = canonical
                if "newest last" in raw or "```" in raw.split("## ", 1)[0]:
                    result.warnings.append(f"{path}: canonicalized OKF log preamble")

    result.files_changed = sorted(planned.keys())

    if options.apply:
        for path, content in planned.items():
            store.write_text_atomic(path, content)
        report_dir = options.reports_dir or (vault_root / "reports" / "okf")
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{date.today().isoformat()}-okf-repair.md"
        report_body = _render_report(result, vault_root)
        report_frontmatter = {
            "type": "report",
            "title": "OKF Repair Report",
            "timestamp": now_rfc3339(),
            "topic": "okf",
            "tags": ["okf", "repair"],
        }
        report_path.write_text(
            serialize_frontmatter(report_frontmatter) + report_body,
            encoding="utf-8",
        )
        result.report_path = str(report_path)
        if _git_available(vault_root):
            commit_paths = [*result.files_changed, str(report_path.relative_to(vault_root))]
            _git_add_commit(vault_root, commit_paths)
            result.commit_sha = _git_head(vault_root)
    else:
        result.status = "DRY-RUN"

    post = check_vault(store, overrides=planned if not options.apply else None)
    if post.failed:
        result.status = "FAILED"
    return result


def _render_report(result: RepairResult, vault_root: Path) -> str:
    lines = [
        "# OKF Repair Report",
        "",
        f"- Vault: `{vault_root}`",
        f"- Mode: {'apply' if not result.dry_run else 'dry-run'}",
        f"- Files changed: {len(result.files_changed)}",
        "",
    ]
    if result.files_changed:
        lines.append("## Changed files")
        lines.extend(f"- `{path}`" for path in result.files_changed)
        lines.append("")
    if result.warnings:
        lines.append("## Warnings")
        lines.extend(f"- {warning}" for warning in result.warnings)
        lines.append("")
    if result.commit_sha:
        lines.append(f"## Rollback\n\n```bash\ngit revert {result.commit_sha}\n```\n")
    return "\n".join(lines)


def _git_dirty(root: Path) -> bool:
    if not _git_available(root):
        return False
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(proc.stdout.strip())


def _git_available(root: Path) -> bool:
    return (root / ".git").exists()


def _git_add_commit(root: Path, paths: list[str]) -> None:
    if not paths:
        return
    subprocess.run(["git", "add", *paths], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "knotica(okf): repair native OKF compatibility"],
        cwd=root,
        check=True,
    )


def _git_head(root: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() or None

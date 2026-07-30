"""OKF vault repair -- restore native OKF compatibility in the active vault."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.links import iter_page_paths
from knotica.core.page import parse_page, serialize_frontmatter
from knotica.core.transaction import LOG_PATH, VaultTransaction
from knotica.core.vcs import VaultVcs
from knotica.okf.datetime_fmt import now_rfc3339
from knotica.okf.check import check_vault
from knotica.okf.frontmatter import (
    is_concept_file,
    normalize_concept_frontmatter,
    render_concept_document,
)
from knotica.okf.log_fmt import canonicalize_log
from knotica.store import LocalFSStore, VaultStore

#: Commit-subject / log-entry slots for the repair operation. ``repair`` is the
#: op name the OKF log layer already knows (``log_fmt._REPAIR_OPS``), so the
#: entry renders with kind ``Repair`` and round-trips back to ``repair``.
_REPAIR_OP = "repair"
_REPAIR_TOPIC = "okf"
_REPAIR_TITLE = "native OKF compatibility"

#: Vault-relative directory the dated repair report lands in.
_REPORTS_DIR = "reports/okf"


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
    """Repair the active vault for native OKF compatibility.

    On apply, every write (repaired pages, the canonicalized ``log.md``, and the
    dated report) and the single git commit go through one
    :class:`~knotica.core.transaction.VaultTransaction`, so the run is
    flock-guarded and serializes with every other vault writer.
    """
    # store.root is a LocalFSStore concretion, not on the VaultStore protocol
    # (td-019 cluster D); every production caller resolves a LocalFSStore.
    assert isinstance(store, LocalFSStore), "repair_vault requires a LocalFSStore-backed vault"
    vault_root = Path(store.root).resolve()
    result = RepairResult(status="OK", dry_run=not options.apply)

    if options.apply and not options.force and _git_dirty(vault_root):
        raise ValueError("git working tree is dirty; commit or stash changes, or pass --force")

    planned = _plan_repairs(store, result)
    result.files_changed = sorted(planned.keys())

    if options.apply:
        _apply_repairs(store, vault_root, options, result, planned)
    else:
        result.status = "DRY-RUN"

    post = check_vault(store, overrides=planned if not options.apply else None)
    if post.failed:
        result.status = "FAILED"
    return result


def _plan_repairs(store: LocalFSStore, result: RepairResult) -> dict[str, str]:
    """The full new content of every page this run would rewrite, by vault path."""
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
        elif path.endswith(LOG_PATH):
            canonical = canonicalize_log(raw)
            if canonical != raw:
                planned[path] = canonical
                if "newest last" in raw or "```" in raw.split("## ", 1)[0]:
                    result.warnings.append(f"{path}: canonicalized OKF log preamble")
    return planned


def _apply_repairs(
    store: LocalFSStore,
    vault_root: Path,
    options: RepairOptions,
    result: RepairResult,
    planned: dict[str, str],
) -> None:
    """Write the plan and the report, and commit, through one vault transaction."""
    _require_committable_vault(vault_root)
    report_relpath = _report_relpath(vault_root, options.reports_dir)
    report_content = _render_report_document(result, vault_root)

    with VaultTransaction(
        store, vault_root, _REPAIR_OP, _REPAIR_TOPIC, _REPAIR_TITLE
    ) as transaction:
        for path, content in planned.items():
            if path == LOG_PATH:
                # The transaction owns log.md: the canonicalized log is the base
                # its own operation entry prepends to, not a competing write.
                transaction.rewrite_log(content)
            else:
                transaction.write(path, content)
        transaction.write(report_relpath, report_content)

    result.report_path = str(vault_root / report_relpath)
    result.commit_sha = transaction.result.commit_sha


def _require_committable_vault(vault_root: Path) -> None:
    """Refuse to apply a repair to a vault that has no git repository to commit into."""
    if _git_available(vault_root):
        return
    raise KnoticaError(
        ErrorCode.GIT_ERROR,
        f"The vault at {vault_root} is not a git repository, so an OKF repair cannot "
        "be committed. Every mutating vault operation is one git commit; applying "
        "without one would leave the repair with no audit trail or rollback point.",
        fix="Run `git init` in the vault (or `knotica init`), then re-run the repair.",
    )


def _report_relpath(vault_root: Path, reports_dir: Path | None) -> str:
    """Vault-relative path of today's repair report; rejects a target outside the vault.

    The report is committed vault content written through the transaction, so it
    must be addressable as a vault-relative path. An override pointing outside
    the vault is refused rather than silently ignored or silently uncommitted.
    """
    filename = f"{date.today().isoformat()}-okf-repair.md"
    if reports_dir is None:
        return f"{_REPORTS_DIR}/{filename}"
    resolved = Path(reports_dir).resolve()
    if not resolved.is_relative_to(vault_root):
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"The repair report directory {resolved} is outside the vault at {vault_root}. "
            "The report is committed vault content, so it has to live inside the vault.",
            fix=f"Pass a directory inside the vault, or omit it to use `{_REPORTS_DIR}/`.",
        )
    return f"{resolved.relative_to(vault_root).as_posix()}/{filename}"


def _render_report_document(result: RepairResult, vault_root: Path) -> str:
    """The full report page: report frontmatter plus the rendered body."""
    frontmatter = {
        "type": "report",
        "title": "OKF Repair Report",
        "timestamp": now_rfc3339(),
        "topic": "okf",
        "tags": ["okf", "repair"],
    }
    return serialize_frontmatter(frontmatter) + _render_report_body(result, vault_root)


def _render_report_body(result: RepairResult, vault_root: Path) -> str:
    # No rollback section: the report is rendered before the commit that
    # contains it, so the commit sha cannot appear inside it -- the sha hashes a
    # tree that already includes this file. `RepairResult.commit_sha` carries it.
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
    return "\n".join(lines)


def _git_dirty(root: Path) -> bool:
    """Whether the vault work tree has uncommitted changes (untracked included).

    The same ``git status --porcelain`` predicate the pre-transaction version
    shelled out for, now read through the read-only git surface.
    """
    if not _git_available(root):
        return False
    return VaultVcs(root).is_dirty()


def _git_available(root: Path) -> bool:
    return (root / ".git").exists()

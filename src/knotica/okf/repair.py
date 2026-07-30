"""OKF vault repair -- restore native OKF compatibility in the active vault."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.page import parse_page, serialize_frontmatter
from knotica.core.transaction import LOG_PATH, VaultTransaction
from knotica.core.vcs import VaultVcs
from knotica.okf.datetime_fmt import now_rfc3339
from knotica.okf.check import check_vault
from knotica.core.links import iter_scored_page_paths
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

#: Vault-relative directory the dated repair report lands in. Under ``.knotica/``
#: (committed, per the vault template's own gitignore) rather than a vault-root
#: ``reports/`` -- dot-prefixed so ``iter_page_paths`` skips it (no re-sweep of
#: the report as a concept file on the next run, td-022) and so it is not a
#: reserved top-level name that would otherwise surface as a phantom topic in
#: enumeration (``family_of``/``topic_of``, ``_topic_directories``).
_REPORTS_DIR = ".knotica/reports/okf"

#: Where reports landed before the ``.knotica/`` fix -- a vault that has
#: already run ``okf repair`` may still carry leftovers here. Every run
#: reclaims its own prior output at this exact subpath into ``_REPORTS_DIR``;
#: nothing else under a vault-root ``reports/`` is ever touched.
_LEGACY_REPORTS_DIR = "reports/okf"


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
    #: Paths that needed repair but were declined because git shows them as
    #: dirty (modified, staged, or untracked) -- the user's in-flight work,
    #: not this run's business. See ``_plan_repairs`` (td-021).
    skipped_dirty: list[str] = field(default_factory=list)
    #: ``(old_path, new_path)`` pairs for pre-fix reports reclaimed from the
    #: legacy ``reports/okf/`` location into ``_REPORTS_DIR``. See
    #: ``_plan_relocations`` (td-022 migration).
    relocated_reports: list[tuple[str, str]] = field(default_factory=list)


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
    dirty_paths = _dirty_paths(vault_root)

    if options.apply and not options.force and dirty_paths:
        raise ValueError("git working tree is dirty; commit or stash changes, or pass --force")

    planned = _plan_repairs(store, result, dirty_paths)
    relocations = _plan_relocations(store, dirty_paths, result)
    result.files_changed = sorted(planned.keys())
    result.skipped_dirty.sort()
    result.relocated_reports = sorted(relocations.items())
    has_work = bool(planned) or bool(relocations) or bool(result.skipped_dirty)

    if options.apply:
        # A vault with nothing to repair, relocate, or report a skip for is a
        # true no-op: no report, no commit. Without this guard every apply
        # would still write a freshly-timestamped report and commit it, so a
        # vault that needs nothing would never reach a fixed point (the exact
        # symptom td-022 tracked).
        if has_work:
            _apply_repairs(store, vault_root, options, result, planned, relocations)
    else:
        result.status = "DRY-RUN"

    post = check_vault(store, overrides=planned if not options.apply else None)
    if post.failed:
        result.status = "FAILED"
    return result


def _plan_repairs(
    store: LocalFSStore, result: RepairResult, dirty_paths: frozenset[str]
) -> dict[str, str]:
    """The full new content of every page this run would rewrite, by vault path.

    A path in ``dirty_paths`` (modified, staged, or untracked) is the user's
    in-flight work, not this run's business: it is declined and recorded on
    ``result.skipped_dirty`` rather than rewritten, whether or not the change
    it needs would otherwise be legitimate. Applies unconditionally -- on a
    clean tree ``dirty_paths`` is empty, so this is a no-op (td-021).
    """
    planned: dict[str, str] = {}
    for path in sorted(iter_scored_page_paths(store)):
        raw = store.read_text(path)
        if is_concept_file(path):
            normalized = normalize_concept_frontmatter(path, raw)
            if normalized.changed or normalized.warnings:
                new_content = render_concept_document(path, raw)
                if new_content != raw:
                    if _skip_dirty(path, dirty_paths, result):
                        continue
                    planned[path] = new_content
                    result.warnings.extend(f"{path}: {w}" for w in normalized.warnings)
        elif path.endswith("index.md") and raw.startswith("---"):
            if _skip_dirty(path, dirty_paths, result):
                continue
            _, _err, body = parse_page(raw)
            preamble = "# Index\n\n<!-- frontmatter removed by okf repair -->\n\n"
            planned[path] = preamble + body.lstrip()
            result.warnings.append(f"{path}: removed accidental frontmatter")
        elif path.endswith(LOG_PATH):
            canonical = canonicalize_log(raw)
            if canonical != raw:
                if _skip_dirty(path, dirty_paths, result):
                    continue
                planned[path] = canonical
                if "newest last" in raw or "```" in raw.split("## ", 1)[0]:
                    result.warnings.append(f"{path}: canonicalized OKF log preamble")
    return planned


def _skip_dirty(path: str, dirty_paths: frozenset[str], result: RepairResult) -> bool:
    """Record and decline ``path`` when it is dirty; a no-op on a clean path."""
    if path not in dirty_paths:
        return False
    result.skipped_dirty.append(path)
    return True


def _plan_relocations(
    store: LocalFSStore, dirty_paths: frozenset[str], result: RepairResult
) -> dict[str, str]:
    """Old-vault-path -> new-vault-path for pre-fix reports under ``_LEGACY_REPORTS_DIR``.

    Reclaims exactly this tool's own prior output (every ``*.md`` file directly
    under the legacy ``reports/okf/`` directory) into ``_REPORTS_DIR`` -- never
    any other content a user may have filed under a vault-root ``reports/``. A
    dirty candidate is declined and reported the same way a dirty page is
    (``_skip_dirty``); applies unconditionally, so a vault with no legacy
    reports is a no-op.
    """
    relocations: dict[str, str] = {}
    if not store.exists(_LEGACY_REPORTS_DIR):
        return relocations
    for name in sorted(store.list_dir(_LEGACY_REPORTS_DIR)):
        if not name.endswith(".md"):
            continue
        old_path = f"{_LEGACY_REPORTS_DIR}/{name}"
        if _skip_dirty(old_path, dirty_paths, result):
            continue
        relocations[old_path] = f"{_REPORTS_DIR}/{name}"
    return relocations


def _apply_repairs(
    store: LocalFSStore,
    vault_root: Path,
    options: RepairOptions,
    result: RepairResult,
    planned: dict[str, str],
    relocations: dict[str, str],
) -> None:
    """Write the plan, relocate legacy reports, write the new report, and commit
    -- all through one vault transaction."""
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
        for old_path, new_path in relocations.items():
            transaction.write(new_path, store.read_text(old_path))
            transaction.delete(old_path)
        transaction.write(report_relpath, report_content)

    if relocations:
        _prune_empty_legacy_dirs(vault_root)

    result.report_path = str(vault_root / report_relpath)
    result.commit_sha = transaction.result.commit_sha


def _prune_empty_legacy_dirs(vault_root: Path) -> None:
    """Remove ``reports/okf/`` and ``reports/`` if relocation left them empty.

    Git does not track empty directories, so this is plain filesystem
    cleanup -- no commit needed. Only these two exact directories are ever
    considered; a ``reports/`` holding anything else (its own leftover
    ``*.md``, or unrelated user content) survives untouched.
    """
    legacy_dir = vault_root / _LEGACY_REPORTS_DIR
    if legacy_dir.is_dir() and not any(legacy_dir.iterdir()):
        legacy_dir.rmdir()
    reports_root = vault_root / "reports"
    if reports_root.is_dir() and not any(reports_root.iterdir()):
        reports_root.rmdir()


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
    if result.skipped_dirty:
        lines.append(f"## Skipped (uncommitted): {len(result.skipped_dirty)}")
        lines.append(
            "These paths needed repair but were declined because git shows them as "
            "uncommitted. Commit or stash them, then re-run repair."
        )
        lines.extend(f"- `{path}`" for path in result.skipped_dirty)
        lines.append("")
    if result.relocated_reports:
        lines.append(f"## Relocated reports: {len(result.relocated_reports)}")
        lines.append(
            "Pre-fix reports found under the legacy `reports/okf/` location were "
            "moved to their new home."
        )
        lines.extend(f"- `{old}` -> `{new}`" for old, new in result.relocated_reports)
        lines.append("")
    return "\n".join(lines)


def _dirty_paths(root: Path) -> frozenset[str]:
    """Vault-relative paths with uncommitted git state -- one status call.

    Modified, staged, and untracked paths are all the user's in-flight work.
    Sourced from a single ``git status --porcelain`` invocation (not a
    per-path check) so a large vault costs one subprocess, not N.
    """
    if not _git_available(root):
        return frozenset()
    entries = VaultVcs(root).list_dirty_entries()
    return frozenset(str(entry["path"]) for entry in entries)


def _git_available(root: Path) -> bool:
    return (root / ".git").exists()

"""Deterministic vault health checks — shared by ``knotica doctor`` and MCP.

Mechanical, LLM-free. Semantic lint stays client-orchestrated. This module owns
the check rows and JSON payload; the CLI renders them for humans/hooks, and the
dashboard Vault pane consumes the same payload via ``doctor_run``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from knotica.core.config import MangledVaultName, detect_mangled_vault_names
from knotica.core.lint import LintCheck, Violation, lint_vault
from knotica.core.schema import read_root_schema
from knotica.core.vcs import GitError, VaultVcs
from knotica.store import VaultStore

__all__ = [
    "DOCTOR_JSON_SCHEMA_VERSION",
    "CheckRow",
    "build_doctor_payload",
    "build_fix_guidance",
    "run_doctor_checks",
]

DOCTOR_JSON_SCHEMA_VERSION = 1

_PASS = "PASS"
_WARN = "WARN"
_FAIL = "FAIL"

_LINK_CHECKS = frozenset(
    {LintCheck.LINK_UNRESOLVED, LintCheck.LINK_DOT_PATH, LintCheck.LINK_BARE_SCHEMA}
)
_STRUCTURE_CHECKS = frozenset(
    {
        LintCheck.FRONTMATTER_MISSING,
        LintCheck.FRONTMATTER_MALFORMED,
        LintCheck.FRONTMATTER_FIELD,
        LintCheck.FRONTMATTER_BLOCK_SCALAR,
        LintCheck.SCHEMA_VERSION_MISSING,
        LintCheck.OVERLAY_VERSION_CONFLICT,
        LintCheck.INDEX_MISSING_ENTRY,
        LintCheck.LOG_MISSING_PATH,
        LintCheck.PAGE_ORPHANED,
    }
)
_CITATION_CHECKS = frozenset({LintCheck.CITATION_UNRESOLVED})


@dataclass(frozen=True, slots=True)
class CheckRow:
    """One health-check result: a semantic status, a name, and remediation."""

    status: str
    name: str
    message: str
    remediation: str | None = None


def run_doctor_checks(
    store: VaultStore,
    vault_path: Path,
    *,
    config_detail: str,
    quick: bool = False,
) -> list[CheckRow]:
    """Assemble the ordered check rows; ``quick`` keeps only the fast subset."""
    rows = [
        CheckRow(_PASS, "config", config_detail),
        _schema_row(store),
    ]
    if quick:
        return rows

    rows.append(_vault_names_row())
    violations = lint_vault(store)
    rows.append(_reserved_names_row(violations))
    rows.append(_links_row(violations))
    rows.append(_citations_row(violations))
    rows.append(_structure_row(violations))
    rows.extend(_git_rows(vault_path))
    rows.append(_mcp_row())
    rows.append(_uv_row())
    return rows


def build_doctor_payload(
    vault_path: Path | None,
    rows: list[CheckRow],
    *,
    quick: bool = False,
    include_fix: bool = False,
) -> dict[str, Any]:
    """Stable machine envelope (same shape as ``knotica doctor --json``).

    When ``include_fix`` is true (CLI ``--fix`` / MCP ``fix=true``), attach
    scoped git-restore guidance if the work tree is dirty — never auto-runs it.
    """
    summary = _summary(rows)
    failed = any(row.status == _FAIL for row in rows)
    exit_code = 1 if failed else 0
    payload: dict[str, Any] = {
        "schema_version": DOCTOR_JSON_SCHEMA_VERSION,
        "vault": str(vault_path) if vault_path is not None else None,
        "quick": bool(quick),
        "ok": not failed,
        "exit_code": exit_code,
        "checks": [
            {
                "name": row.name,
                "status": row.status,
                "message": row.message,
                "remediation": row.remediation,
            }
            for row in rows
        ],
        "summary": summary,
        "fix_guidance": None,
    }
    if include_fix:
        payload["fix_guidance"] = build_fix_guidance(vault_path, rows)
    return payload


def build_fix_guidance(vault_path: Path | None, rows: list[CheckRow]) -> dict[str, Any] | None:
    """Scoped restore guidance for a dirty tree — same as ``knotica doctor --fix``.

    Returns ``None`` when there is nothing to surface (clean tree / no vault).
    Never executes restore; the caller only displays the commands.
    """
    dirty = any(row.name == "git" and row.status != _PASS for row in rows)
    if not dirty or vault_path is None:
        return None
    return {
        "kind": "scoped_git_restore",
        "summary": (
            "Uncommitted changes in the work tree — restore only the paths you "
            "intend to discard (never `git restore .`)."
        ),
        "commands": [
            "knotica doctor repair --dry-run",
            "knotica doctor repair --apply --paths <path>...",
            "knotica doctor repair --apply --all-tracked",
        ],
        "note": (
            "Path-scoped only so concurrent Obsidian edits are not clobbered. "
            "`--fix` is guidance; `doctor repair --apply` performs the restore."
        ),
    }


def _schema_row(store: VaultStore) -> CheckRow:
    root = read_root_schema(store)
    if root.schema_version is None:
        return CheckRow(
            _WARN,
            "schema",
            "root SCHEMA.md declares no integer schema_version",
            "add `schema_version: <n>` to SCHEMA.md frontmatter",
        )
    return CheckRow(_PASS, "schema", f"root SCHEMA.md v{root.schema_version} resolves clean")


def _vault_names_row() -> CheckRow:
    """Report vault names a dot silently nested into sub-tables, with the repair.

    Report-only, by the surface's own convention: ``doctor repair --apply``
    means one thing here -- a path-scoped ``git restore`` inside the *vault* --
    and ``config.toml`` lives outside the vault and outside that repo, so a
    config rewrite has no home behind that flag. The remediation is the exact
    replacement text instead, which is copy-pasteable and leaves the user in
    control of a file that is theirs.
    """
    mangled = detect_mangled_vault_names()
    if not mangled:
        return CheckRow(_PASS, "vault names", "every configured vault name is a TOML bare key")
    names = ", ".join(entry.name for entry in mangled)
    return CheckRow(
        _WARN,
        "vault names",
        (
            f"{len(mangled)} vault name(s) written before the bare-key guard nested "
            f"into sub-tables and now read back as phantom vaults: {names}"
        ),
        _mangled_vault_repair(mangled),
    )


def _mangled_vault_repair(mangled: list[MangledVaultName]) -> str:
    """The literal TOML to paste over each mangled entry in ``config.toml``.

    The repair renames to a **bare** key rather than quoting the dotted one.
    Quoting would make the file read correctly and then break on the next
    mutation: :func:`~knotica.core.config_write._emit_table` still rejects a
    dotted vault name outright (a vault name is typed back as a ``--vault``
    flag and passed as a tool argument), so ``[vaults."my.name"]`` would raise
    ``INVALID_ARGUMENT`` the next time any vault is added. A repair that arms a
    later failure is not a repair.
    """
    blocks = "\n\n".join(
        f'[vaults.{entry.name.replace(".", "-")}]\npath = "{entry.path or "<the vault path>"}"'
        for entry in mangled
    )
    return (
        "edit ~/.config/knotica/config.toml by hand: delete the nested "
        "[vaults...] header(s) and write one flat table per vault under a name "
        "with no dot (a dotted name is what nested them) —\n"
        f"{blocks}\n"
        "— then point `default_vault` at the new name if it named an old one."
    )


def _reserved_names_row(violations: list[Violation]) -> CheckRow:
    hits = [v for v in violations if v.check == LintCheck.RESERVED_TOP_LEVEL_NAME]
    if not hits:
        return CheckRow(_PASS, "reserved names", "no topic collides with a reserved name")
    names = ", ".join(v.path for v in hits)
    return CheckRow(
        _FAIL,
        "reserved names",
        f"{len(hits)} reserved-name collision(s): {names}",
        hits[0].fix,
    )


def _links_row(violations: list[Violation]) -> CheckRow:
    hits = [v for v in violations if v.check in _LINK_CHECKS]
    if not hits:
        return CheckRow(_PASS, "links", "all wikilinks resolve")
    return CheckRow(
        _WARN,
        "links",
        f"{len(hits)} unresolved wikilink(s) ({_locations(hits)})",
        "fix or remove the dangling wikilinks; run `knotica status` for the full lint",
    )


def _citations_row(violations: list[Violation]) -> CheckRow:
    hits = [v for v in violations if v.check in _CITATION_CHECKS]
    if not hits:
        return CheckRow(_PASS, "citations", "every cited source is stored")
    return CheckRow(
        _WARN,
        "citations",
        f"{len(hits)} citation(s) to unstored sources ({_locations(hits)})",
        "store the cited source(s) before citing, or fix the citation key — "
        "for a long paper, store each cited section as its own chunk",
    )


def _structure_row(violations: list[Violation]) -> CheckRow:
    hits = [v for v in violations if v.check in _STRUCTURE_CHECKS]
    if not hits:
        return CheckRow(_PASS, "structure", "frontmatter, index, and log are consistent")
    return CheckRow(
        _WARN,
        "structure",
        f"{len(hits)} mechanical violation(s) ({_locations(hits)})",
        hits[0].fix,
    )


def _git_rows(vault_path: Path) -> list[CheckRow]:
    try:
        vcs = VaultVcs(vault_path)
        branch = vcs.current_branch()
        dirty = vcs.is_dirty()
        unpushed = vcs.unpushed_count()
    except GitError as error:
        return [CheckRow(_WARN, "git", f"git state unavailable: {error}")]
    return [_git_tree_row(branch, dirty, vault_path), _git_remote_row(unpushed, vault_path)]


def _git_tree_row(branch: str | None, dirty: bool, vault_path: Path) -> CheckRow:
    where = f"on {branch}" if branch is not None else "detached HEAD"
    if not dirty:
        return CheckRow(_PASS, "git", f"clean tree, {where}")
    return CheckRow(
        _WARN,
        "git",
        f"uncommitted changes in the work tree ({where})",
        "run `knotica doctor repair --dry-run` then "
        "`knotica doctor repair --apply --paths …` "
        "(or `knotica doctor --fix` for the command list)",
    )


def _git_remote_row(unpushed: int | None, vault_path: Path) -> CheckRow:
    if unpushed is None:
        return CheckRow(_PASS, "git remote", "no upstream configured")
    if unpushed == 0:
        return CheckRow(_PASS, "git remote", "up to date with upstream")
    return CheckRow(
        _WARN,
        "git remote",
        f"{unpushed} unpushed commit(s)",
        f"git -C {vault_path} push",
    )


def _mcp_row() -> CheckRow:
    if shutil.which("claude") is not None:
        return CheckRow(_PASS, "mcp", "claude CLI present (register via /knotica:setup)")
    return CheckRow(
        _WARN,
        "mcp",
        "claude CLI not found on PATH",
        "install Claude Code and register with `/knotica:setup` or `knotica init`",
    )


def _uv_row() -> CheckRow:
    uvx = shutil.which("uvx")
    if uvx is not None:
        return CheckRow(_PASS, "uv", f"uvx present ({uvx})")
    return CheckRow(
        _WARN,
        "uv",
        "uvx not found on PATH",
        "install uv -- https://docs.astral.sh/uv/getting-started/installation/",
    )


def _locations(violations: list[Violation], limit: int = 3) -> str:
    shown = violations[:limit]
    parts = [f"{v.path}:{v.line}" if v.line is not None else v.path for v in shown]
    if len(violations) > limit:
        parts.append(f"+{len(violations) - limit} more")
    return ", ".join(parts)


def _summary(rows: list[CheckRow]) -> dict[str, int]:
    return {
        "pass": sum(row.status == _PASS for row in rows),
        "warn": sum(row.status == _WARN for row in rows),
        "fail": sum(row.status == _FAIL for row in rows),
    }

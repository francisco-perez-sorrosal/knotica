"""``knotica doctor`` -- deterministic mechanical health checks.

``doctor`` is the deterministic pre/post harness guard whose exit code gates the
hooks: it **never invokes an LLM** (semantic checks -- contradictions, staleness
-- belong to ``/knotica:lint``, client-run). Every check is a pure read over the
resolved vault: config three-state distinction, schema resolution, reserved-name
collisions, mechanical link/structure violations (``core.lint``), git work-tree
state, MCP registration presence, and ``uvx`` availability.

Output discipline (``cli.common``): the checklist/JSON is the payload on stdout;
every diagnostic goes to stderr. Exit ``0`` when nothing FAILed (warnings are
allowed), ``1`` when a check FAILED, ``3`` when the vault is unconfigured -- the
config-nudge SessionStart hook keys on ``doctor --quick`` returning ``3``.

``doctor`` reads git state read-only (``core.vcs``); it never mutates the vault.
``--fix`` surfaces the exact *scoped* remediation command rather than executing a
rollback -- an unscoped restore would clobber concurrent Obsidian edits, which
the vault's path-scoped-safety invariant forbids.
"""

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path

from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_SUCCESS,
    Console,
    Status,
    common_parent,
    console_from_args,
    unconfigured,
)
from knotica.core.config import ConfigState, diagnose
from knotica.core.lint import LintCheck, Violation, lint_vault
from knotica.core.schema import read_root_schema
from knotica.core.vcs import GitError, VaultVcs
from knotica.store import LocalFSStore, VaultStore

__all__ = ["DOCTOR_JSON_SCHEMA_VERSION", "configure", "run"]

#: Stable version of the ``--json`` envelope (consumers branch on this).
DOCTOR_JSON_SCHEMA_VERSION = 1

#: Link-graph lint checks surfaced under the "links" row (WARN, never a FAIL).
_LINK_CHECKS = frozenset(
    {LintCheck.LINK_UNRESOLVED, LintCheck.LINK_DOT_PATH, LintCheck.LINK_BARE_SCHEMA}
)
#: Remaining mechanical lint checks surfaced under the "structure" row.
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
#: Source-citation integrity, surfaced under its own "citations" row (WARN).
_CITATION_CHECKS = frozenset({LintCheck.CITATION_UNRESOLVED})


@dataclass(frozen=True, slots=True)
class CheckRow:
    """One health-check result: a semantic status, a name, and remediation."""

    status: str
    name: str
    message: str
    remediation: str | None = None


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``doctor`` subcommand and its flags."""
    parser = subparsers.add_parser(
        "doctor",
        parents=[common_parent()],
        help="run deterministic mechanical health checks",
        description="Run deterministic health checks; the exit code gates the hooks.",
    )
    parser.add_argument("--quick", action="store_true", help="run the SessionStart subset only")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--fix", action="store_true", help="show scoped rollback remediation")
    return parser


def run(args: argparse.Namespace) -> int:
    """Resolve config fresh, run the checks, render, and return the exit code."""
    console = console_from_args(args)
    diagnosis = diagnose()
    if diagnosis.state == ConfigState.UNCONFIGURED:
        return _report_unconfigured(console, args)
    if diagnosis.vault is None:
        rows = [_config_row(diagnosis.state, diagnosis.detail, diagnosis.remediation)]
        return _render(console, args, vault_path=None, rows=rows)

    vault = diagnosis.vault
    store = LocalFSStore(vault.path)
    rows = _run_checks(store, vault.path, config_detail=diagnosis.detail, quick=args.quick)
    return _render(console, args, vault_path=vault.path, rows=rows)


def _report_unconfigured(console: Console, args: argparse.Namespace) -> int:
    """Emit the unconfigured result -- JSON envelope or the shared stderr message."""
    if args.json:
        console.data(
            _json_dumps(
                {
                    "schema_version": DOCTOR_JSON_SCHEMA_VERSION,
                    "vault": None,
                    "quick": bool(args.quick),
                    "ok": False,
                    "exit_code": 3,
                    "state": ConfigState.UNCONFIGURED.value,
                    "checks": [],
                    "summary": {"pass": 0, "warn": 0, "fail": 0},
                }
            )
        )
    return unconfigured(console)


def _run_checks(
    store: VaultStore, vault_path: Path, *, config_detail: str, quick: bool
) -> list[CheckRow]:
    """Assemble the ordered check rows; ``--quick`` keeps only the fast subset."""
    rows = [
        CheckRow(Status.PASS, "config", config_detail),
        _schema_row(store),
    ]
    if quick:
        return rows

    violations = lint_vault(store)
    rows.append(_reserved_names_row(violations))
    rows.append(_links_row(violations))
    rows.append(_citations_row(violations))
    rows.append(_structure_row(violations))
    rows.extend(_git_rows(vault_path))
    rows.append(_mcp_row())
    rows.append(_uv_row())
    return rows


def _schema_row(store: VaultStore) -> CheckRow:
    """Resolve the root constitution and report its declared schema version."""
    root = read_root_schema(store)
    if root.schema_version is None:
        return CheckRow(
            Status.WARN,
            "schema",
            "root SCHEMA.md declares no integer schema_version",
            "add `schema_version: <n>` to SCHEMA.md frontmatter",
        )
    return CheckRow(Status.PASS, "schema", f"root SCHEMA.md v{root.schema_version} resolves clean")


def _reserved_names_row(violations: list[Violation]) -> CheckRow:
    """A topic colliding with a reserved bookkeeping name is a FAIL."""
    hits = [v for v in violations if v.check == LintCheck.RESERVED_TOP_LEVEL_NAME]
    if not hits:
        return CheckRow(Status.PASS, "reserved names", "no topic collides with a reserved name")
    names = ", ".join(v.path for v in hits)
    return CheckRow(
        Status.FAIL,
        "reserved names",
        f"{len(hits)} reserved-name collision(s): {names}",
        hits[0].fix,
    )


def _links_row(violations: list[Violation]) -> CheckRow:
    """Unresolved wikilinks are a WARN with the offending file:line locations."""
    hits = [v for v in violations if v.check in _LINK_CHECKS]
    if not hits:
        return CheckRow(Status.PASS, "links", "all wikilinks resolve")
    return CheckRow(
        Status.WARN,
        "links",
        f"{len(hits)} unresolved wikilink(s) ({_locations(hits)})",
        "fix or remove the dangling wikilinks; run `knotica status` for the full lint",
    )


def _citations_row(violations: list[Violation]) -> CheckRow:
    """Pages citing a source not stored in the vault are a WARN (unverifiable claims)."""
    hits = [v for v in violations if v.check in _CITATION_CHECKS]
    if not hits:
        return CheckRow(Status.PASS, "citations", "every cited source is stored")
    return CheckRow(
        Status.WARN,
        "citations",
        f"{len(hits)} citation(s) to unstored sources ({_locations(hits)})",
        "store the cited source(s) before citing, or fix the citation key — "
        "for a long paper, store each cited section as its own chunk",
    )


def _structure_row(violations: list[Violation]) -> CheckRow:
    """Remaining mechanical violations (frontmatter, index, orphans) are a WARN."""
    hits = [v for v in violations if v.check in _STRUCTURE_CHECKS]
    if not hits:
        return CheckRow(Status.PASS, "structure", "frontmatter, index, and log are consistent")
    return CheckRow(
        Status.WARN,
        "structure",
        f"{len(hits)} mechanical violation(s) ({_locations(hits)})",
        hits[0].fix,
    )


def _git_rows(vault_path: Path) -> list[CheckRow]:
    """Read-only git work-tree and upstream state (never mutates the vault)."""
    try:
        vcs = VaultVcs(vault_path)
        branch = vcs.current_branch()
        dirty = vcs.is_dirty()
        unpushed = vcs.unpushed_count()
    except GitError as error:
        return [CheckRow(Status.WARN, "git", f"git state unavailable: {error}")]
    return [_git_tree_row(branch, dirty, vault_path), _git_remote_row(unpushed, vault_path)]


def _git_tree_row(branch: str | None, dirty: bool, vault_path: Path) -> CheckRow:
    """The work-tree row: clean → PASS, uncommitted changes → WARN."""
    where = f"on {branch}" if branch is not None else "detached HEAD"
    if not dirty:
        return CheckRow(Status.PASS, "git", f"clean tree, {where}")
    return CheckRow(
        Status.WARN,
        "git",
        f"uncommitted changes in the work tree ({where})",
        f"run `knotica doctor --fix` for the scoped restore command"
        f" (or review with `git -C {vault_path} status`)",
    )


def _git_remote_row(unpushed: int | None, vault_path: Path) -> CheckRow:
    """The upstream row: no upstream or up-to-date → PASS, ahead → WARN."""
    if unpushed is None:
        return CheckRow(Status.PASS, "git remote", "no upstream configured")
    if unpushed == 0:
        return CheckRow(Status.PASS, "git remote", "up to date with upstream")
    return CheckRow(
        Status.WARN,
        "git remote",
        f"{unpushed} unpushed commit(s)",
        f"git -C {vault_path} push",
    )


def _mcp_row() -> CheckRow:
    """Presence of the ``claude`` CLI (plugin-registration channel), best-effort."""
    if shutil.which("claude") is not None:
        return CheckRow(Status.PASS, "mcp", "claude CLI present (register via /knotica:setup)")
    return CheckRow(
        Status.WARN,
        "mcp",
        "claude CLI not found on PATH",
        "install Claude Code and register with `/knotica:setup` or `knotica init`",
    )


def _uv_row() -> CheckRow:
    """Presence of ``uvx`` -- the launcher the plugin uses to run the server."""
    uvx = shutil.which("uvx")
    if uvx is not None:
        return CheckRow(Status.PASS, "uv", f"uvx present ({uvx})")
    return CheckRow(
        Status.WARN,
        "uv",
        "uvx not found on PATH",
        "install uv -- https://docs.astral.sh/uv/getting-started/installation/",
    )


def _config_row(state: ConfigState, detail: str, remediation: str) -> CheckRow:
    """The config row for the ``CONFIGURED_NO_VAULT`` state (a FAIL)."""
    return CheckRow(Status.FAIL, "config", f"{state.value}: {detail}", remediation or None)


def _locations(violations: list[Violation], limit: int = 3) -> str:
    """Render a short ``path:line`` list for the first few violations."""
    shown = violations[:limit]
    parts = [f"{v.path}:{v.line}" if v.line is not None else v.path for v in shown]
    if len(violations) > limit:
        parts.append(f"+{len(violations) - limit} more")
    return ", ".join(parts)


def _render(
    console: Console, args: argparse.Namespace, *, vault_path: Path | None, rows: list[CheckRow]
) -> int:
    """Render the rows (JSON or human checklist) and return the exit code."""
    exit_code = EXIT_ERROR if any(r.status == Status.FAIL for r in rows) else EXIT_SUCCESS
    if args.json:
        console.data(_json_payload(vault_path, rows, args.quick, exit_code))
        return exit_code
    _render_human(console, vault_path, rows, exit_code)
    if args.fix:
        _render_fix_guidance(console, vault_path, rows)
    return exit_code


def _json_payload(
    vault_path: Path | None, rows: list[CheckRow], quick: bool, exit_code: int
) -> str:
    """Build the stable ``--json`` envelope for the checklist."""
    counts = _summary(rows)
    return _json_dumps(
        {
            "schema_version": DOCTOR_JSON_SCHEMA_VERSION,
            "vault": str(vault_path) if vault_path is not None else None,
            "quick": bool(quick),
            "ok": exit_code == EXIT_SUCCESS,
            "exit_code": exit_code,
            "checks": [
                {
                    "name": r.name,
                    "status": r.status,
                    "message": r.message,
                    "remediation": r.remediation,
                }
                for r in rows
            ],
            "summary": counts,
        }
    )


def _render_human(
    console: Console, vault_path: Path | None, rows: list[CheckRow], exit_code: int
) -> None:
    """Print the aligned PASS/WARN/FAIL checklist to stdout with a summary footer."""
    header = "knotica doctor"
    if vault_path is not None:
        header = f"{header}    vault: {vault_path}"
    console.data(header)
    console.data("")
    width = max((len(r.name) for r in rows), default=0)
    for row in rows:
        glyph = console.status_glyph(row.status)
        console.data(f"  {glyph}  {row.name.ljust(width)}  {row.message}")
        if row.remediation is not None and row.status != Status.PASS:
            console.data(f"        {' ' * width}  -> {row.remediation}")
    console.data("")
    counts = _summary(rows)
    trailer = " — see remediations above." if exit_code != EXIT_SUCCESS or counts["warn"] else "."
    console.data(f"{counts['pass']} pass, {counts['warn']} warn, {counts['fail']} fail{trailer}")


def _render_fix_guidance(console: Console, vault_path: Path | None, rows: list[CheckRow]) -> None:
    """Surface the *scoped* restore command for a dirty tree, never auto-run it.

    An unscoped ``git restore`` would clobber concurrent foreign (Obsidian)
    edits, which the vault's path-scoped-safety invariant forbids -- so ``--fix``
    prints the command for the user to review and run against the knotica paths.
    """
    dirty = any(row.name == "git" and row.status != Status.PASS for row in rows)
    if not dirty or vault_path is None:
        return
    console.data("")
    console.data("--fix: review the uncommitted knotica-owned paths, then restore them scoped:")
    console.data(f"  git -C {vault_path} status        # inspect what changed")
    console.data(
        f"  git -C {vault_path} restore -- <path>...   # scoped rollback (never `restore .`)"
    )


def _summary(rows: list[CheckRow]) -> dict[str, int]:
    """Count rows per semantic status."""
    return {
        "pass": sum(r.status == Status.PASS for r in rows),
        "warn": sum(r.status == Status.WARN for r in rows),
        "fail": sum(r.status == Status.FAIL for r in rows),
    }


def _json_dumps(payload: dict[str, object]) -> str:
    """Serialize a JSON payload deterministically (imported lazily on demand)."""
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)

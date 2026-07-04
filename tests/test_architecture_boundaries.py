"""Static import-boundary fitness test: the vault has exactly one writer.

The single-writer invariant is what guarantees "one git commit per effective
mutating operation" for every surface at once: adapters (``cli``, ``mcp_server``)
must never mutate the vault directly, and exactly one module -- ``core.transaction``
-- may call the mutating git surface. This is enforced *statically* here (AST scan
over ``src/knotica``) so a regression is caught at test time, not in production.

What the boundary forbids inside every ``cli/`` and ``mcp_server/`` module:

- importing ``subprocess`` or using ``os.system`` / ``os.popen`` (an adapter has
  no business shelling out to git) -- with one exemption: ``cli/init.py`` may
  shell out to bootstrap a fresh vault (``git init`` + initial commit), which is
  SETUP of a not-yet-a-vault directory, not mutation of a live vault;
- importing ``knotica.core.lock`` (the vault flock is the transaction's to take);
- calling the mutating store methods ``write_text_atomic`` / ``delete``;
- calling the mutating ``VaultVcs`` methods ``commit_paths`` / ``rollback_paths``.

What it deliberately permits (must never be flagged): the read-only ``VaultVcs``
methods (``head_sha`` / ``current_branch`` / ``unpushed_count`` / ``is_dirty`` /
``root``) that ``doctor`` and ``status`` legitimately use -- reading git *state*
does not threaten the single-writer invariant -- and calling ``core.operations.*``
/ ``core.transaction`` from the adapters.

The scan matches mutating operations by call-method name, which is robust to
aliasing (``from knotica.core.vcs import VaultVcs`` then ``.commit_paths`` is
still caught). Known blind spot: an unrelated ``.delete(...)`` call on some
non-store object in an adapter would also be flagged -- acceptable, since the
single-writer intent is that adapters perform no mutation of any kind, and no
such call exists on the current tree.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "knotica"

#: Packages that adapt an external surface (MCP, CLI) onto the deterministic core.
#: Everything under these must route mutation through ``core.transaction``.
ADAPTER_PACKAGES = ("cli", "mcp_server")

#: The only module permitted to call the mutating git surface.
SOLE_WRITER = SRC_ROOT / "core" / "transaction.py"

#: Adapter modules exempt from the shell-out clause ONLY. ``cli/init.py`` shells
#: out to git for one-time repo bootstrap (``git init`` + the initial commit of a
#: fresh vault) -- that is SETUP of a not-yet-a-vault directory, not mutation of a
#: live vault, so it predates the single-writer transaction path. It remains fully
#: subject to every other clause (no core.lock import, no store/VaultVcs mutation).
SHELL_OUT_EXEMPT = frozenset({"cli/init.py"})

#: Store methods that mutate the filesystem -- forbidden to the adapters.
MUTATING_STORE_METHODS = frozenset({"write_text_atomic", "delete"})

#: ``VaultVcs`` methods that mutate git history -- adapters must not call them,
#: and only ``core.transaction`` may across the whole codebase.
MUTATING_VCS_METHODS = frozenset({"commit_paths", "rollback_paths"})

#: ``VaultVcs`` methods that only *read* git state -- explicitly permitted in
#: the adapters (``doctor``/``status`` surface branch/dirty/unpushed info).
READ_ONLY_VCS_METHODS = frozenset(
    {"head_sha", "current_branch", "unpushed_count", "is_dirty", "root"}
)

#: ``os`` attributes that shell out -- forbidden to the adapters.
OS_SHELL_ATTRS = frozenset({"system", "popen"})


def _module_label(path: Path) -> str:
    """Repo-relative POSIX label for a source file (stable in failure messages)."""
    return path.relative_to(SRC_ROOT).as_posix()


def _adapter_files() -> Iterator[Path]:
    """Every ``.py`` file under the adapter packages, in stable order."""
    for package in ADAPTER_PACKAGES:
        yield from sorted((SRC_ROOT / package).rglob("*.py"))


def _all_source_files() -> Iterator[Path]:
    """Every ``.py`` file under ``src/knotica``, in stable order."""
    yield from sorted(SRC_ROOT.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _called_method_names(tree: ast.Module) -> list[tuple[str, int]]:
    """Attribute-call method names with line numbers, e.g. ``obj.foo()`` -> ``foo``.

    Matching by the trailing attribute name is aliasing-robust: it catches the
    call regardless of how the receiver was imported or bound.
    """
    calls: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            calls.append((node.func.attr, node.func.lineno))
    return calls


def _imports_subprocess(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "subprocess" or a.name.startswith("subprocess.") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "subprocess" or module.startswith("subprocess."):
                return True
    return False


def _os_shell_lines(tree: ast.Module) -> list[int]:
    """Lines using ``os.system`` / ``os.popen`` (attribute or ``from os import``)."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in OS_SHELL_ATTRS
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ):
            lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and (node.module or "") == "os":
            if any(a.name in OS_SHELL_ATTRS for a in node.names):
                lines.append(node.lineno)
    return lines


def _imports_core_lock(tree: ast.Module) -> bool:
    """Whether the module imports ``knotica.core.lock`` in any form (incl. relative)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.endswith("core.lock") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.endswith("core.lock"):
                return True
            if module.endswith("core") and any(a.name == "lock" for a in node.names):
                return True
    return False


def test_adapters_do_not_shell_out_for_git() -> None:
    violations: list[str] = []
    for path in _adapter_files():
        if _module_label(path) in SHELL_OUT_EXEMPT:
            continue
        tree = _parse(path)
        if _imports_subprocess(tree):
            violations.append(f"{_module_label(path)} imports subprocess")
        for line in _os_shell_lines(tree):
            violations.append(f"{_module_label(path)}:{line} uses os.system/os.popen")
    assert not violations, (
        "cli/ and mcp_server/ must not shell out for git (single writer is "
        f"core.transaction): {violations}"
    )


def test_adapters_do_not_import_the_vault_lock() -> None:
    violations = [
        _module_label(path) for path in _adapter_files() if _imports_core_lock(_parse(path))
    ]
    assert not violations, (
        "cli/ and mcp_server/ must not import knotica.core.lock; the vault flock "
        f"belongs to the transaction: {violations}"
    )


def test_adapters_do_not_call_mutating_store_methods() -> None:
    violations: list[str] = []
    for path in _adapter_files():
        for name, line in _called_method_names(_parse(path)):
            if name in MUTATING_STORE_METHODS:
                violations.append(f"{_module_label(path)}:{line} calls {name}()")
    assert not violations, (
        "cli/ and mcp_server/ must not write the vault directly (no "
        f"write_text_atomic/delete): {violations}"
    )


def test_adapters_do_not_call_mutating_vcs_methods() -> None:
    violations: list[str] = []
    for path in _adapter_files():
        for name, line in _called_method_names(_parse(path)):
            if name in MUTATING_VCS_METHODS:
                violations.append(f"{_module_label(path)}:{line} calls {name}()")
    assert not violations, (
        "cli/ and mcp_server/ must not commit or roll back the vault (no "
        f"commit_paths/rollback_paths): {violations}"
    )


def test_core_transaction_is_the_only_caller_of_mutating_vcs_methods() -> None:
    callers: set[str] = set()
    for path in _all_source_files():
        for name, _line in _called_method_names(_parse(path)):
            if name in MUTATING_VCS_METHODS:
                callers.add(_module_label(path))
    assert callers == {_module_label(SOLE_WRITER)}, (
        "commit_paths/rollback_paths must be called only by core/transaction.py; "
        f"found callers: {sorted(callers)}"
    )


def test_adapters_may_read_git_state() -> None:
    # Non-vacuity guard: the boundary permits read-only VaultVcs use, and at
    # least one adapter (doctor/status) actually relies on it. A future rewrite
    # that over-broadly banned all VaultVcs use in the adapters would fail here.
    readers: set[str] = set()
    for path in _adapter_files():
        for name, _line in _called_method_names(_parse(path)):
            if name in READ_ONLY_VCS_METHODS:
                readers.add(_module_label(path))
    assert readers, (
        "expected at least one cli/mcp_server module to read git state via "
        "read-only VaultVcs methods -- the boundary permits this"
    )

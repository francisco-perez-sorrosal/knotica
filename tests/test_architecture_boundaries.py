"""Static import-boundary fitness test: the vault has exactly one writer.

The single-writer invariant is what guarantees "one git commit per effective
mutating operation" for every surface at once: the adapters (``cli``,
``mcp_server``) and the headless eval subsystem (``evals``) must never mutate the
vault directly, and exactly one module -- ``core.transaction`` -- may call the
mutating git surface. ``evals`` joins the bound set because it writes
``metrics.jsonl`` through ``VaultTransaction`` on a clone, so it is subject to the
same single-writer rule as the adapters. This is enforced *statically* here (AST
scan over ``src/knotica``) so a regression is caught at test time, not in
production.

What the boundary forbids inside every ``cli/``, ``mcp_server/``, and ``evals/``
module:

- importing ``subprocess`` or using ``os.system`` / ``os.popen`` (an adapter has
  no business shelling out to git) -- with two exemptions: ``cli/init.py`` may
  shell out to bootstrap a fresh vault (``git init`` + initial commit), which is
  SETUP of a not-yet-a-vault directory, not mutation of a live vault; and
  ``cli/service.py`` imports ``subprocess`` only to catch/type-check the
  ``subprocess.CalledProcessError`` that ``knotica.service.manager``'s
  injectable-runner OS-service-manager calls (``launchctl``/``systemctl``) can
  raise -- an entirely different external system than git, never the vault's
  git history nor ``core.transaction``'s single-writer path;
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

A third check closes a gap in the above: matching by *store method name* is
blind to a raw filesystem bypass -- ``Path(...).write_text(...)`` writes the
vault exactly as effectively as ``store.write_text_atomic(...)`` without ever
calling a name the scan above looks for. ``test_adapters_do_not_perform_raw_
filesystem_writes`` detects ``Path.write_text``/``write_bytes``/``unlink``
(bare attribute name, same aliasing-robust idiom), ``os.replace``/``os.remove``
and ``shutil.copy*``/``shutil.move`` (receiver-checked on the module name,
since "replace"/"remove"/"copy"/"move" are too generic to match by bare
attribute name alone), and ``open()``/``Path.open()`` calls whose mode is a
*literal* string containing ``w``, ``a``, or ``x`` (a dynamically computed
mode is an accepted, documented blind spot). Two allowlists carve out the
writes that are genuinely not vault mutations: entire modules whose own
design excludes vault coupling, and individual ``module::function`` pairs
whose target is a specific gitignored runtime-state path (verified against
``vault-template/.gitignore``) or an external application's own config file.
See the allowlist constants below for the reasoning behind each entry.

Scope: this raw-write check covers the same ``ADAPTER_PACKAGES`` as the checks
above (``cli``, ``mcp_server``, ``evals``) -- not the wider tree. ``core/``
hosts the sole writer itself plus a family of legitimate non-vault operational
writers (locks, heartbeats, progress files, config, caches) that were never
part of "the vault" in the git-committed-content sense; folding it in would
require a much larger allowlist unrelated to this gap. ``guillotine/``,
``discovery/``, ``dashboard/``, ``programs/``, and ``search/`` perform no raw
filesystem writes at all today (verified by grep), so widening the scan to
include them would not currently change its outcome.

A second, independent boundary: ``search/`` may depend on ``knotica.core``
only through ``core.vault_layout``, the zero-dependency folder-family leaf
(see ``core/__init__.py``'s boundary docstring). ``vault_layout`` imports
nothing from ``knotica``, so any layer can depend on it without creating a
cycle -- but any *other* ``knotica.core.*`` import from ``search/`` would
give ``search`` a transitive dependency on the config/schema layer, which is
exactly the cycle risk the leaf exists to avoid. This is checked separately
from the single-writer scan above: it is an import-direction rule, not a
mutation rule, so a module could satisfy one and violate the other.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "knotica"

#: Packages that adapt an external surface (MCP, CLI) or drive a headless
#: subsystem (``evals``) on top of the deterministic core. Everything under these
#: must route mutation through ``core.transaction``: ``evals`` appends
#: ``metrics.jsonl`` via ``VaultTransaction`` on a clone, so it is bound by the
#: same single-writer rule as the adapters -- ``clone_to`` is a read/checkout
#: method (absent from ``MUTATING_VCS_METHODS``), so cloning the corpus is allowed.
ADAPTER_PACKAGES = ("cli", "mcp_server", "evals")

#: The only module permitted to call the mutating git surface.
SOLE_WRITER = SRC_ROOT / "core" / "transaction.py"

#: Adapter modules exempt from the shell-out clause ONLY. ``cli/init.py`` shells
#: out to git for one-time repo bootstrap (``git init`` + the initial commit of a
#: fresh vault) -- that is SETUP of a not-yet-a-vault directory, not mutation of a
#: live vault, so it predates the single-writer transaction path. ``cli/service.py``
#: imports ``subprocess`` only to catch/type-check ``subprocess.CalledProcessError``
#: from ``knotica.service.manager``'s OS-service-manager (launchd/systemd) calls --
#: an unrelated external system, never git. Both remain fully subject to every
#: other clause (no core.lock import, no store/VaultVcs mutation).
SHELL_OUT_EXEMPT = frozenset({"cli/init.py", "cli/service.py"})

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

#: The one ``core`` submodule ``search/`` may depend on -- see the module
#: docstring for why (zero-dependency leaf, no cycle risk).
CORE_LEAF_EXEMPTION = "vault_layout"

#: Path methods that mutate the filesystem directly -- matched by trailing
#: attribute name (also catches ``os.unlink``, which shares the same name).
RAW_WRITE_PATH_METHODS = frozenset({"write_text", "write_bytes", "unlink"})

#: ``os`` attributes that mutate the filesystem -- receiver-checked (module
#: name only, no aliasing), since "replace"/"remove" are too generic to match
#: by bare attribute name (``str.replace``, ``list.remove`` would collide).
RAW_WRITE_OS_ATTRS = frozenset({"replace", "remove"})

#: ``shutil`` attributes that copy or move files -- receiver-checked for the
#: same reason as ``RAW_WRITE_OS_ATTRS``.
RAW_WRITE_SHUTIL_ATTRS = frozenset({"copy", "copy2", "copyfile", "copytree", "move"})

#: Characters whose presence in a literal ``open``/``Path.open`` mode string
#: marks the call as a write. A read-write mode not containing any of these
#: (e.g. ``"r+"``) is an accepted blind spot -- none exist in the scanned
#: packages today (verified by grep).
_WRITE_MODE_CHARS = frozenset("wax")

#: Whole modules exempt from the raw-write scan below -- every write in the
#: module targets a location outside the vault's git-tracked content, so none
#: of it is subject to the single-writer transaction path.
RAW_WRITE_MODULE_ALLOWLIST = frozenset(
    {
        # Stdlib-only response cache; "no vault coupling" is a load-bearing
        # design constraint stated in the module's own docstring. The on-disk
        # backing lives under a constructor-supplied storage_root -- never
        # vault content.
        "evals/cache.py",
    }
)

#: ``module::function`` entries exempt from the raw-write scan -- narrower
#: than the module allowlist above because the rest of the module IS subject
#: to the single-writer rule.
RAW_WRITE_FUNCTION_ALLOWLIST = frozenset(
    {
        # Writes the golden-bootstrap review scratchpad at the gitignored
        # `.knotica/datasets/golden.staging.jsonl` -- deliberately never
        # committed (a human reviews it before the accept step freezes it via
        # VaultTransaction).
        "evals/golden.py::_write_staging",
        # Backs up and patches the Claude Desktop app's own config file --
        # external application state, never vault content.
        "cli/init.py::_patch_desktop",
        # Mints a single-use nonce under the gitignored `.knotica/locks/`
        # directory -- the same runtime-lock class the vault flock
        # (core/lock.py) uses, never vault content.
        "mcp_server/tools_vault.py::_mint_nonce",
        # Consumes (and deletes) the same gitignored nonce file minted above.
        "mcp_server/tools_vault.py::_consume_nonce",
    }
)


def _module_label(path: Path) -> str:
    """Repo-relative POSIX label for a source file (stable in failure messages)."""
    return path.relative_to(SRC_ROOT).as_posix()


def _adapter_files() -> Iterator[Path]:
    """Every ``.py`` file under the adapter packages, in stable order."""
    for package in ADAPTER_PACKAGES:
        yield from sorted((SRC_ROOT / package).rglob("*.py"))


def _search_files() -> Iterator[Path]:
    """Every ``.py`` file under ``search/``, in stable order."""
    yield from sorted((SRC_ROOT / "search").rglob("*.py"))


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


def _module_attr_write_lines(
    tree: ast.Module, module_name: str, attrs: frozenset[str]
) -> list[tuple[str, int]]:
    """``(attribute-name, line)`` for ``<module_name>.<attr>(...)`` calls, ``attr in attrs``.

    Receiver-checked (module name only, no aliasing) -- mirrors ``_os_shell_lines``.
    """
    lines: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in attrs:
            continue
        if isinstance(node.func.value, ast.Name) and node.func.value.id == module_name:
            lines.append((node.func.attr, node.func.lineno))
    return lines


def _literal_open_mode(node: ast.Call, is_builtin: bool) -> str | None:
    """The literal string mode argument of an ``open``/``Path.open`` call, if any."""
    positional_index = 1 if is_builtin else 0
    if len(node.args) > positional_index:
        arg = node.args[positional_index]
        return arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return keyword.value.value if isinstance(keyword.value.value, str) else None
    return None


def _write_mode_open_lines(tree: ast.Module) -> list[tuple[str, int]]:
    """Lines calling ``open(...)`` or ``<expr>.open(...)`` with a literal write mode.

    Covers builtin ``open`` and ``Path.open`` alike. A call with no mode argument
    at all defaults to read (``"r"``) and is correctly not flagged.
    """
    lines: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        is_builtin_open = isinstance(node.func, ast.Name) and node.func.id == "open"
        is_path_open = isinstance(node.func, ast.Attribute) and node.func.attr == "open"
        if not (is_builtin_open or is_path_open):
            continue
        mode = _literal_open_mode(node, is_builtin_open)
        if mode is not None and any(char in mode for char in _WRITE_MODE_CHARS):
            lines.append(("open", node.lineno))
    return lines


def _raw_write_calls(tree: ast.Module) -> list[tuple[str, int]]:
    """``(description, line)`` for every raw filesystem-write call detectable in ``tree``.

    See the module docstring's third-check paragraph for what this covers and why.
    """
    calls: list[tuple[str, int]] = [
        (name, line) for name, line in _called_method_names(tree) if name in RAW_WRITE_PATH_METHODS
    ]
    for attr, line in _module_attr_write_lines(tree, "os", RAW_WRITE_OS_ATTRS):
        calls.append((f"os.{attr}", line))
    for attr, line in _module_attr_write_lines(tree, "shutil", RAW_WRITE_SHUTIL_ATTRS):
        calls.append((f"shutil.{attr}", line))
    calls.extend(_write_mode_open_lines(tree))
    return calls


def _enclosing_function(tree: ast.Module, line: int) -> str | None:
    """Name of the innermost function/method enclosing ``line``, or ``None`` at module scope."""
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.lineno <= line <= (node.end_lineno or node.lineno):
            if best is None or node.lineno > best.lineno:
                best = node
    return best.name if best else None


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


def _forbidden_core_imports(tree: ast.Module) -> list[tuple[str, int]]:
    """``(offending-import, line)`` for every ``knotica.core.*`` import that
    does not target ``knotica.core.vault_layout``.

    Handles ``import knotica.core.x``, ``from knotica.core.x import y``,
    ``from knotica.core import x``, and the relative equivalents
    (``from ..core.x import y``, ``from ..core import x``) -- ``ast.Import
    From.level`` counts the leading dots separately from ``.module``, so a
    relative import with ``module == "core"`` and ``level > 0`` resolves the
    same way as the absolute ``knotica.core`` form.
    """
    violations: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.extend(_plain_core_import_violations(node))
        elif isinstance(node, ast.ImportFrom):
            violations.extend(_from_core_import_violations(node))
    return violations


def _is_exempt_submodule(submodule: str) -> bool:
    """Whether a ``knotica.core`` submodule path is the permitted leaf (or below it)."""
    return submodule == CORE_LEAF_EXEMPTION or submodule.startswith(f"{CORE_LEAF_EXEMPTION}.")


def _plain_core_import_violations(node: ast.Import) -> list[tuple[str, int]]:
    """Offenders in ``import knotica.core`` / ``import knotica.core.x`` form."""
    offenders: list[tuple[str, int]] = []
    for alias in node.names:
        name = alias.name
        if name != "knotica.core" and not name.startswith("knotica.core."):
            continue
        if name.startswith(f"knotica.core.{CORE_LEAF_EXEMPTION}"):
            continue
        offenders.append((name, node.lineno))
    return offenders


def _from_core_import_violations(node: ast.ImportFrom) -> list[tuple[str, int]]:
    """Offenders in every ``from …`` form, absolute and relative.

    Includes ``from knotica import core`` / ``from .. import core``, which bind
    the package itself: that hands the module every submodule via attribute
    access, so it defeats the boundary just as directly as naming a submodule.
    Dynamic access (``importlib.import_module``) is out of reach of an AST gate
    and is not attempted here.
    """
    module = node.module or ""
    relative_core = node.level > 0 and (module == "core" or module.startswith("core."))
    if module.startswith("knotica.core.") or (relative_core and module.startswith("core.")):
        if _is_exempt_submodule(module.rsplit("core.", 1)[1]):
            return []
        return [(f"{module}.{alias.name}", node.lineno) for alias in node.names]
    if module == "knotica.core" or (relative_core and module == "core"):
        return [
            (f"{module}.{alias.name}", node.lineno)
            for alias in node.names
            if alias.name != CORE_LEAF_EXEMPTION
        ]
    if module == "knotica" or (node.level > 0 and not module):
        return [
            ("{}.core".format(module or "."), node.lineno)
            for alias in node.names
            if alias.name == "core"
        ]
    return []


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


def test_adapters_do_not_perform_raw_filesystem_writes() -> None:
    violations: list[str] = []
    for path in _adapter_files():
        label = _module_label(path)
        if label in RAW_WRITE_MODULE_ALLOWLIST:
            continue
        tree = _parse(path)
        for description, line in _raw_write_calls(tree):
            qualified = f"{label}::{_enclosing_function(tree, line)}"
            if qualified in RAW_WRITE_FUNCTION_ALLOWLIST:
                continue
            violations.append(f"{label}:{line} calls {description}(...) directly")
    assert not violations, (
        "cli/, mcp_server/, and evals/ must not perform raw filesystem writes "
        "(Path.write_text/write_bytes/unlink, os.replace/os.remove, "
        "shutil.copy*/move, or open(..., 'w'|'a'|'x')) -- these bypass "
        "core.transaction's single-writer path just as effectively as calling "
        f"write_text_atomic directly would: {violations}"
    )


def test_raw_write_scanner_detects_the_allowlisted_golden_staging_write() -> None:
    # Non-vacuity guard: proves the scanner actually sees `_write_staging`'s
    # write_text call -- the function allowlist above is suppressing a real
    # detection, not filtering nothing. Without this, a future rename of
    # `_write_staging` that silently stopped the allowlist entry from matching
    # would leave the allowlist entry dead and this guard would be the only
    # thing to notice.
    tree = _parse(SRC_ROOT / "evals" / "golden.py")
    detected = {name for name, _line in _raw_write_calls(tree)}
    assert "write_text" in detected, (
        "expected the raw-write scanner to detect golden.py's _write_staging "
        "write_text call -- if this fails, the allowlist entry "
        "'evals/golden.py::_write_staging' matches nothing"
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


def test_search_depends_on_core_only_through_the_vault_layout_leaf() -> None:
    violations: list[str] = []
    for path in _search_files():
        for target, line in _forbidden_core_imports(_parse(path)):
            violations.append(f"{_module_label(path)}:{line} imports {target}")
    assert not violations, (
        "search/ may depend on knotica.core only through core.vault_layout -- the "
        "zero-dependency leaf that every layer can safely import without creating a "
        "cycle (core/__init__.py's boundary docstring). Any other knotica.core "
        f"import from search/ risks a future cycle back into core: {violations}"
    )


def test_search_actually_depends_on_vault_layout() -> None:
    # Non-vacuity guard: the boundary exists to *permit* search/ -> core.vault_layout,
    # and search/ relies on it today for topic/family classification. Without this,
    # a future rewrite that severed the edge entirely (or renamed the leaf without
    # updating search/) would leave the assertion above vacuously true.
    importers: set[str] = set()
    for path in _search_files():
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
                f"core.{CORE_LEAF_EXEMPTION}"
            ):
                importers.add(_module_label(path))
    assert importers, (
        "expected at least one search/ module to import knotica.core.vault_layout -- "
        "the boundary exists specifically to permit this dependency"
    )

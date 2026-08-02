"""``knotica init`` -- fallback-channel setup wizard.

The CLI twin of ``/knotica:setup``: it scaffolds a vault from the packaged
template, bootstraps its git repository, writes ``config.toml``, registers the
MCP server with the ``claude`` CLI (and, on ``--desktop``, patches the Claude
Desktop config), then verifies/warms ``uvx``. Every external write is
**idempotent and reversible** -- re-running ``init`` is safe, the Desktop patch
is additive and backed up (``.bak``) before it touches anything, and nothing is
ever written outside the target vault, the config file, or the Desktop config.

Output discipline (``cli.common``): the final summary is the payload on stdout;
every progress line and warning goes to stderr.

**Git bootstrap exemption (documented).** Standing up a *new* repository (``git
init`` + one initial commit over the freshly copied template) is one-time repo
setup, not ongoing vault mutation, so it does not go through the ``core``
single-writer seam (``core.transaction``/``core.vcs``) and never imports
``core.lock``. The bootstrap is confined to this module via a narrow
:func:`subprocess.run` wrapper. ``config.toml`` is written outside the vault, so
it is a plain file write, not a vault mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_MISUSE,
    EXIT_SUCCESS,
    Console,
    common_parent,
    console_from_args,
)
from knotica.core import config_write, vault_scaffold
from knotica.core.config import config_file_path
from knotica.core.errors import KnoticaError
from knotica.core.template import TEMPLATE_DIRNAME

#: ``knotica.cli.desktop`` reuses this module's Desktop-config seam so the
#: launch argv and the additive patch have exactly one definition.
__all__ = [
    "MCP_SERVER_NAME",
    "configure",
    "desktop_config_path",
    "mcp_from_source",
    "patch_desktop",
    "run",
    "warm_launch",
]

#: Config vault name written by the wizard (the schema's ``default_vault``).
_DEFAULT_VAULT_NAME = "main"
#: Default vault filesystem path offered under ``--yes`` / interactive default.
_DEFAULT_VAULT_PATH = "~/dev/data/knotica"
#: Name the MCP server is registered under (claude CLI + Desktop config).
MCP_SERVER_NAME = "knotica"
#: Env override for the Desktop config path (test hook; never bind $HOME early).
_DESKTOP_CONFIG_ENV_VAR = "KNOTICA_DESKTOP_CONFIG"
#: Env override for the MCP ``--from`` source (test hook / power-user escape).
_MCP_FROM_ENV_VAR = "KNOTICA_MCP_FROM"
#: Timeout for every bootstrap subprocess call.
_SUBPROCESS_TIMEOUT_SECONDS = 120.0
#: Name of the PEP 621 extra carrying the headless LLM dependencies (query /
#: compile / Arena). The single source of truth for what those dependencies are
#: is ``pyproject.toml``; every launch path requests them by this name so no
#: caller re-states the package list or its version bounds.
_EVALS_EXTRA = "evals"


class _InitError(Exception):
    """A fatal wizard failure carrying the process exit code to return."""

    def __init__(self, message: str, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class _Inputs:
    """The resolved wizard inputs (from flags, defaults, or prompts)."""

    vault_path: Path
    topic: str | None
    remote: str
    desktop: bool


def configure(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the ``init`` subcommand and its flags."""
    parser = subparsers.add_parser(
        "init",
        parents=[common_parent()],
        help="scaffold a vault and write config.toml (setup wizard)",
        description="Scaffold a knotica vault, register the MCP server, and pre-warm.",
    )
    parser.add_argument("--yes", action="store_true", help="accept all defaults (non-interactive)")
    parser.add_argument("--vault", metavar="PATH", help="filesystem path for the new vault")
    parser.add_argument("--topic", metavar="NAME", help="seed an initial topic")
    parser.add_argument(
        "--remote",
        choices=("none", "gh-private"),
        default="none",
        help="create a git remote (default: none)",
    )
    parser.add_argument("--desktop", action="store_true", help="patch Claude Desktop config")
    return parser


def run(args: argparse.Namespace) -> int:
    """Resolve inputs, scaffold the vault, wire the channels, and summarize."""
    console = console_from_args(args)
    try:
        inputs = _resolve_inputs(console, args)
        _scaffold_and_wire(console, inputs)
    except _InitError as failure:
        console.error(str(failure))
        return failure.exit_code
    _print_summary(console, inputs)
    return EXIT_SUCCESS


def _scaffold_and_wire(console: Console, inputs: _Inputs) -> None:
    """Run every wizard stage in order (config resolved fresh, never cached)."""
    from_source = mcp_from_source()
    result = _run_scaffold(inputs.vault_path, inputs.topic)
    _report_scaffold(console, inputs, result)
    _setup_remote(console, inputs.vault_path, inputs.remote)
    _write_config(console, _DEFAULT_VAULT_NAME, inputs.vault_path)
    _register_mcp(console, from_source)
    if inputs.desktop:
        patch_desktop(console, from_source)
    warm_launch(console, from_source)


def _resolve_inputs(console: Console, args: argparse.Namespace) -> _Inputs:
    """Resolve vault/topic/remote/desktop from flags, defaults, or prompts."""
    interactive = not args.yes and not args.no_input and sys.stdin.isatty()
    vault_path = _resolve_vault_path(console, args, interactive)
    topic = args.topic
    remote = args.remote
    desktop = args.desktop
    if interactive:
        topic = _prompt(console, "Seed a topic (blank to skip)", args.topic or "") or None
        remote = _prompt(console, "Remote (none|gh-private)", args.remote) or "none"
        desktop = _prompt_yes_no(console, "Patch Claude Desktop config?", args.desktop)
    if topic is not None and topic in vault_scaffold.RESERVED_TOPIC_NAMES:
        raise _InitError(
            f"init failed because '{topic}' is a reserved name and cannot be a topic. "
            "To fix: choose a different --topic (kebab-case or lowercase)."
        )
    return _Inputs(vault_path=vault_path, topic=topic, remote=remote, desktop=desktop)


def _resolve_vault_path(console: Console, args: argparse.Namespace, interactive: bool) -> Path:
    """Resolve the target vault path; fail fast (exit 2) when it is unobtainable."""
    if args.vault:
        return _expand(args.vault)
    if args.no_input:
        raise _InitError(
            "init failed because no --vault was given and --no-input forbids prompting. "
            "To fix: pass --vault <path>.",
            EXIT_MISUSE,
        )
    if args.yes:
        return _expand(_DEFAULT_VAULT_PATH)
    if not interactive:
        raise _InitError(
            "init failed because no --vault was given and stdin is not a terminal. "
            "To fix: pass --vault <path> or --yes.",
            EXIT_MISUSE,
        )
    return _expand(_prompt(console, "Vault path", _DEFAULT_VAULT_PATH) or _DEFAULT_VAULT_PATH)


def _run_scaffold(vault_path: Path, topic: str | None) -> vault_scaffold.ScaffoldResult:
    """Scaffold the vault via :mod:`knotica.core.vault_scaffold`, in wizard grammar.

    Translates the shared scaffolder's plain-fact ``KnoticaError`` into the
    wizard's three-part ``_InitError`` -- the scaffolder itself is adapter-
    agnostic (also used by ``vault action=create``) and never raises in CLI
    grammar.
    """
    try:
        return vault_scaffold.scaffold_vault(vault_path, topic=topic)
    except KnoticaError as failure:
        raise _InitError(
            f"init failed because {failure.message} To fix: {failure.fix}"
        ) from failure


def _report_scaffold(
    console: Console, inputs: _Inputs, result: vault_scaffold.ScaffoldResult
) -> None:
    """Emit the same user-facing progress lines the inline scaffold used to print."""
    if result.created:
        console.info(f"copied vault template → {result.path}")
    else:
        console.info(f"vault already scaffolded at {result.path} — leaving contents untouched")
    if inputs.topic is not None:
        console.info(f"seeded topic '{inputs.topic}'")
    if result.committed:
        console.info("created initial commit")
    else:
        console.info("nothing to commit — vault already committed")


def _setup_remote(console: Console, vault_path: Path, remote: str) -> None:
    """Create an optional private GitHub remote (best-effort; never fatal)."""
    if remote != "gh-private":
        return
    if shutil.which("gh") is None:
        console.warn("gh CLI not found — skipping private remote creation")
        return
    result = _run(
        [
            "gh",
            "repo",
            "create",
            vault_path.name,
            "--private",
            "--source",
            str(vault_path),
            "--remote",
            "origin",
        ],
        check=False,
    )
    if result.returncode != 0:
        console.warn(f"could not create private remote (gh): {result.stderr.strip()}")
    else:
        console.info(f"created private GitHub remote 'origin' for {vault_path.name}")


def _write_config(console: Console, vault_name: str, vault_path: Path) -> None:
    """Write ``config.toml`` additively -- preserves any pre-existing vaults."""
    path = config_file_path()
    config_write.upsert_vault(path, vault_name, vault_path, make_default=True)
    console.info(f"wrote config → {path}")


def _register_mcp(console: Console, from_source: str) -> None:
    """Register the MCP server with the ``claude`` CLI (skip if absent)."""
    claude = shutil.which("claude")
    if claude is None:
        console.info(
            "claude CLI not found — skipping `claude mcp add` (register via /knotica:setup)"
        )
        return
    result = _run(
        [
            claude,
            "mcp",
            "add",
            MCP_SERVER_NAME,
            "--",
            "uvx",
            "--from",
            from_source,
            "knotica",
            "mcp",
        ],
        check=False,
    )
    if result.returncode == 0:
        console.info(f"registered MCP server '{MCP_SERVER_NAME}' with claude")
        return
    combined = f"{result.stderr}\n{result.stdout}".lower()
    if "already exists" in combined:
        console.info(f"MCP server '{MCP_SERVER_NAME}' already registered with claude")
    else:
        console.warn(f"`claude mcp add` failed: {result.stderr.strip() or result.stdout.strip()}")


def patch_desktop(console: Console, from_source: str) -> None:
    """Additively patch the Desktop config with an absolute ``uv`` / ``uvx`` launch.

    Local repo checkouts use editable ``uv run --directory … --extra evals`` so
    Desktop picks up worktree changes without a stale ``uvx`` wheel cache.
    Published installs fall back to ``uvx --refresh --from …``.

    Backs the existing file up to ``.bak`` before writing, and merges only the
    knotica server entry -- every pre-existing server and key is preserved.
    """
    try:
        command, args = _desktop_knotica_launch(from_source, "mcp")
    except _InitError as error:
        console.warn(str(error))
        return
    path = desktop_config_path()
    console.info(f"target file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            console.warn(f"Desktop config at {path} is not valid JSON — leaving it untouched")
            return
    else:
        console.info("no Desktop config yet — creating it")

    servers = existing.setdefault("mcpServers", {})
    prior = servers.get(MCP_SERVER_NAME)
    prior = prior if isinstance(prior, dict) else None
    entry: dict[str, object] = {"command": command, "args": args}
    prior_env = prior.get("env") if prior else None
    if isinstance(prior_env, dict):
        entry["env"] = prior_env

    if prior == entry:
        console.info(f"mcpServers.{MCP_SERVER_NAME} is already current — nothing to change")
        return

    # Back up only when something will actually change: an unconditional copy
    # would overwrite a good .bak with an identical file on every no-op run,
    # quietly destroying the one snapshot a user might need to roll back to.
    if path.is_file():
        backup = path.with_name(path.name + ".bak")
        shutil.copy2(path, backup)
        console.info(f"backed up previous config → {backup}")

    _report_desktop_changes(console, prior, command, args, entry.get("env"), servers)
    servers[MCP_SERVER_NAME] = entry
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    console.info(f"wrote mcpServers.{MCP_SERVER_NAME} in {path}")


def _report_desktop_changes(
    console: Console,
    prior: dict[str, Any] | None,
    command: str,
    args: list[str],
    env: object,
    servers: dict[str, Any],
) -> None:
    """Narrate exactly what the patch changes, before it is written.

    A config patch is an edit to a file the user did not open, so the log is the
    only record of it. Name the key that changes, the before/after of each field,
    what is carried over, and what is left alone -- enough to audit or undo the
    write without diffing the file.
    """
    verb = "updating" if prior else "adding"
    console.info(f"{verb} key: mcpServers.{MCP_SERVER_NAME}")
    prior_command = prior.get("command") if prior else None
    if prior and prior_command != command:
        console.info(f"  command: {prior_command} → {command}")
    else:
        console.info(f"  command: {command}")
    prior_args = prior.get("args") if prior else None
    if prior and prior_args != args:
        console.info(f"  args (was): {' '.join(str(a) for a in prior_args or [])}")
    console.info(f"  args: {' '.join(args)}")
    if isinstance(env, dict) and env:
        # Names only -- these are credentials.
        console.info(f"  env: preserved {', '.join(sorted(env))}")
    untouched = sorted(name for name in servers if name != MCP_SERVER_NAME)
    if untouched:
        console.info(f"  left untouched: {', '.join(untouched)}")


def _is_local_repo_source(from_source: str) -> bool:
    """True when ``from_source`` is a checkout directory (editable ``uv run``)."""
    path = Path(from_source).expanduser()
    return path.is_dir() and (path / "pyproject.toml").is_file()


def _local_repo_run_args(from_source: str, *knotica_argv: str) -> list[str]:
    """``uv run --directory <repo> --extra evals knotica …`` argv tail.

    ``--extra`` (not ``--group``): the eval dependencies are a PEP 621 extra, and
    the byte-identical PEP 735 group that once aliased it is gone. A config
    written before that removal still carries the old group flag and now fails to
    launch with "Group `evals` is not defined"; ``knotica desktop install``
    rewrites it.
    """
    # Pre-migration configs carry `--group evals` here. allow-stale-invocation
    repo = str(Path(from_source).expanduser().resolve())
    return ["run", "--directory", repo, "--extra", _EVALS_EXTRA, "knotica", *knotica_argv]


def _desktop_knotica_launch(from_source: str, subcommand: str) -> tuple[str, list[str]]:
    """Return ``(command, args)`` for Desktop to launch ``knotica <subcommand>``."""
    if _is_local_repo_source(from_source):
        uv = shutil.which("uv")
        if uv is None:
            raise _InitError(
                "init failed because `uv` is not installed. "
                "To fix: install uv and re-run `knotica init --desktop`."
            )
        return uv, _local_repo_run_args(from_source, subcommand)
    uvx = shutil.which("uvx")
    if uvx is None:
        raise _InitError(
            "init failed because `uvx` is not installed. "
            "To fix: install uv and re-run `knotica init --desktop`."
        )
    return uvx, _uvx_knotica_args(from_source, subcommand, include_evals=True, refresh=True)


def _uvx_knotica_args(
    from_source: str,
    subcommand: str,
    *,
    include_evals: bool = False,
    refresh: bool = False,
) -> list[str]:
    """Build ``uvx`` argv for a knotica subcommand.

    Desktop headless tools (``query``, compile, Arena) need the eval dependencies,
    which are absent from the base wheel ``uvx --from`` resolves.
    ``include_evals=True`` requests them as the ``evals`` **extra**
    (``--from '<source>[evals]'``) rather than naming packages with ``--with``:
    the extra carries the full dependency specification, version bounds included,
    so this path cannot drift from ``pyproject.toml``. Naming packages by hand
    silently dropped the ``litellm`` platform bound and left Desktop installs
    building a Rust sdist on macOS. The lean Claude Code plugin path is unchanged
    -- it resolves ``--from <source>`` with no extra.
    ``refresh=True`` forces a rebuild so local ``--from`` edits are not masked
    by a stale cached wheel (notably headless retrieval helpers).
    """
    args: list[str] = []
    if refresh:
        args.append("--refresh")
    args.extend(["--from", f"{from_source}[{_EVALS_EXTRA}]" if include_evals else from_source])
    args.extend(["knotica", subcommand])
    return args


def warm_launch(console: Console, from_source: str) -> None:
    """Verify launch tooling and warm the Desktop resolution cache (best-effort)."""
    try:
        command, args = _desktop_knotica_launch(from_source, "--version")
    except _InitError as error:
        console.warn(str(error))
        return
    label = "uv run" if _is_local_repo_source(from_source) else "uvx"
    console.info(f"warming {label} environment (first resolution can take ~25s)…")
    result = _run([command, *args], check=False)
    if result.returncode != 0:
        console.warn(f"{label} warm-up did not complete: {result.stderr.strip()}")
    else:
        console.info(f"{label} ready: {result.stdout.strip()}")


def _print_summary(console: Console, inputs: _Inputs) -> None:
    """Emit the final summary to stdout, ending with the Obsidian next step."""
    console.data(f"knotica vault ready at {inputs.vault_path}")
    console.data(f"config written to {config_file_path()}")
    console.data("next step: open the folder as a vault in Obsidian")


# --- small helpers -----------------------------------------------------------


def _expand(raw: str) -> Path:
    """Expand ``$ENV`` and ``~`` and resolve to an absolute path."""
    return Path(os.path.expandvars(raw)).expanduser().resolve()


def _repo_root_from(start: Path) -> str | None:
    """Walk ``start``'s parents for a knotica repo root (pyproject.toml + template dir)."""
    for parent in [start, *start.parents]:
        if (parent / "pyproject.toml").is_file() and (parent / TEMPLATE_DIRNAME).is_dir():
            return str(parent)
    return None


def mcp_from_source() -> str:
    """Resolve the MCP ``--from`` source: env > source checkout > package name.

    The source-checkout probe checks two signals, since either can miss the repo
    root depending on how ``knotica`` was invoked: ``__file__``'s parents (works
    when running via ``uv run`` from the checkout) and the current working
    directory's parents (works when running via a ``uv tool install``'d binary
    from inside the checkout — ``__file__`` then resolves into the isolated tool
    venv, not the repo, per the README's own documented install sequence).
    """
    override = os.environ.get(_MCP_FROM_ENV_VAR)
    if override:
        return override
    return (
        _repo_root_from(Path(__file__).resolve().parent) or _repo_root_from(Path.cwd()) or "knotica"
    )


def desktop_config_path() -> Path:
    """Desktop config location: ``$KNOTICA_DESKTOP_CONFIG`` > macOS default."""
    override = os.environ.get(_DESKTOP_CONFIG_ENV_VAR)
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def _run(argv: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, mapping a checked failure to a three-part ``_InitError``."""
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=check,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        raise _InitError(
            f"init failed because `{' '.join(argv[:2])}` exited {error.returncode}"
            f" ({detail}). To fix: resolve the error above and re-run `knotica init`."
        ) from error
    except FileNotFoundError as error:
        raise _InitError(
            f"init failed because `{argv[0]}` is not installed. "
            f"To fix: install it and re-run `knotica init`."
        ) from error


def _prompt(console: Console, label: str, default: str) -> str:
    """Prompt on stderr with a default; return the entered value or the default."""
    suffix = f" [{default}]" if default else ""
    print(f"{label}{suffix}: ", end="", file=console.err, flush=True)
    answer = input().strip()
    return answer or default


def _prompt_yes_no(console: Console, label: str, default: bool) -> bool:
    """Prompt for a yes/no answer on stderr, defaulting to ``default``."""
    answer = _prompt(console, f"{label} (y/n)", "y" if default else "n").lower()
    return answer.startswith("y")

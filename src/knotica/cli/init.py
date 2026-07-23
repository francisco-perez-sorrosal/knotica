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

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_MISUSE,
    EXIT_SUCCESS,
    Console,
    common_parent,
    console_from_args,
)
from knotica.core.config import config_file_path
from knotica.core.template import TEMPLATE_DIRNAME, TemplateNotFoundError
from knotica.core.template import packaged_template_path as _locate_template

__all__ = ["configure", "packaged_template_path", "run"]

#: Config vault name written by the wizard (the schema's ``default_vault``).
_DEFAULT_VAULT_NAME = "main"
#: Default vault filesystem path offered under ``--yes`` / interactive default.
_DEFAULT_VAULT_PATH = "~/dev/data/knotica"
#: Name the MCP server is registered under (claude CLI + Desktop config).
_MCP_SERVER_NAME = "knotica"
#: Env override for the Desktop config path (test hook; never bind $HOME early).
_DESKTOP_CONFIG_ENV_VAR = "KNOTICA_DESKTOP_CONFIG"
#: Env override for the MCP ``--from`` source (test hook / power-user escape).
_MCP_FROM_ENV_VAR = "KNOTICA_MCP_FROM"
#: Timeout for every bootstrap subprocess call.
_SUBPROCESS_TIMEOUT_SECONDS = 120.0
#: Headless LLM packages injected into Desktop's uvx launch (query / compile / Arena).
_UVX_EVALS_PACKAGES = ("anthropic", "dspy")
#: Top-level names a topic may never collide with (root constitution).
_RESERVED_TOPIC_NAMES = frozenset(
    {"sources", "index.md", "log.md", "SCHEMA.md", "START_HERE.md", ".knotica", ".git"}
)

_EMPTY_OVERLAY = """\
---
schema_version: 1
---

# SCHEMA — {topic} overlay

Empty overlay: this topic starts with no divergence from the root constitution
(root `SCHEMA.md`). Add entity types and page conventions here as the topic
earns them.
"""


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


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
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


def packaged_template_path() -> Path:
    """Locate the packaged ``vault-template``, as a wizard-grammar error on miss.

    Thin wrapper over :func:`knotica.core.template.packaged_template_path` (the
    single reusable locator) that translates a missing template into the
    wizard's three-part ``_InitError``.
    """
    try:
        return _locate_template()
    except TemplateNotFoundError as missing:
        raise _InitError(
            f"init failed because {missing}. "
            "To fix: reinstall knotica so the template ships with the wheel."
        ) from missing


def _scaffold_and_wire(console: Console, inputs: _Inputs) -> None:
    """Run every wizard stage in order (config resolved fresh, never cached)."""
    from_source = _mcp_from_source()
    _scaffold_vault(console, inputs.vault_path)
    if inputs.topic is not None:
        _seed_topic(console, inputs.vault_path, inputs.topic)
    _git_bootstrap(console, inputs.vault_path)
    _setup_remote(console, inputs.vault_path, inputs.remote)
    _write_config(console, _DEFAULT_VAULT_NAME, inputs.vault_path)
    _register_mcp(console, from_source)
    if inputs.desktop:
        _patch_desktop(console, from_source)
    _warm_uvx(console, from_source, include_evals=inputs.desktop)


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
    if topic is not None and topic in _RESERVED_TOPIC_NAMES:
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


def _scaffold_vault(console: Console, vault_path: Path) -> None:
    """Copy the packaged template into ``vault_path`` (idempotent, never clobbers)."""
    if vault_path.exists() and any(vault_path.iterdir()):
        if (vault_path / "SCHEMA.md").is_file():
            console.info(f"vault already scaffolded at {vault_path} — leaving contents untouched")
            return
        raise _InitError(
            f"init failed because {vault_path} is not empty and is not a knotica vault. "
            "To fix: choose an empty --vault path, or remove the directory first."
        )
    template = packaged_template_path()
    shutil.copytree(template, vault_path, dirs_exist_ok=True)
    console.info(f"copied vault template → {vault_path}")


def _seed_topic(console: Console, vault_path: Path, topic: str) -> None:
    """Create a minimal empty-overlay topic (idempotent -- skips if present)."""
    schema = vault_path / topic / "SCHEMA.md"
    if schema.is_file():
        console.info(f"topic '{topic}' already present — skipping")
        return
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_text(_EMPTY_OVERLAY.format(topic=topic), encoding="utf-8")
    console.info(f"seeded topic '{topic}'")


def _git_bootstrap(console: Console, vault_path: Path) -> None:
    """Initialize the vault repo and make the initial commit (idempotent).

    New-repo setup only -- distinct from vault mutation, so it never touches the
    ``core`` single-writer seam. Re-running is safe: ``init`` is skipped when a
    repo exists and the commit is skipped when there is nothing to commit.
    """
    if not (vault_path / ".git").exists():
        _git(console, vault_path, "init", "-q")
        console.info("initialized git repository")
    _git(console, vault_path, "add", "-A")
    if not _git(console, vault_path, "status", "--porcelain").stdout.strip():
        console.info("nothing to commit — vault already committed")
        return
    commit = ["commit", "-q", "-m", "Initialize knotica vault (knotica init)"]
    if not _has_git_identity(console, vault_path):
        commit = ["-c", "user.name=knotica", "-c", "user.email=knotica@localhost", *commit]
    _git(console, vault_path, *commit)
    console.info("created initial commit")


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
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_config(path)
    data["schema_version"] = 1
    data["default_vault"] = vault_name
    vaults = data.setdefault("vaults", {})
    vaults[vault_name] = {"path": str(vault_path)}
    _atomic_write(path, _dump_config_toml(data))
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
            _MCP_SERVER_NAME,
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
        console.info(f"registered MCP server '{_MCP_SERVER_NAME}' with claude")
        return
    combined = f"{result.stderr}\n{result.stdout}".lower()
    if "already exists" in combined:
        console.info(f"MCP server '{_MCP_SERVER_NAME}' already registered with claude")
    else:
        console.warn(f"`claude mcp add` failed: {result.stderr.strip() or result.stdout.strip()}")


def _patch_desktop(console: Console, from_source: str) -> None:
    """Additively patch the Desktop config with an absolute ``uv`` / ``uvx`` launch.

    Local repo checkouts use editable ``uv run --directory … --group evals`` so
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
    path = _desktop_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if path.is_file():
        backup = path.with_name(path.name + ".bak")
        shutil.copy2(path, backup)
        console.info(f"backed up Desktop config → {backup}")
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            console.warn(f"Desktop config at {path} is not valid JSON — leaving it untouched")
            return
    servers = existing.setdefault("mcpServers", {})
    entry: dict[str, object] = {"command": command, "args": args}
    prior_env = servers.get(_MCP_SERVER_NAME, {}).get("env")
    if isinstance(prior_env, dict):
        entry["env"] = prior_env
    servers[_MCP_SERVER_NAME] = entry
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    console.info(f"patched Desktop config → {path} (additive; server '{_MCP_SERVER_NAME}')")


def _is_local_repo_source(from_source: str) -> bool:
    """True when ``from_source`` is a checkout directory (editable ``uv run``)."""
    path = Path(from_source).expanduser()
    return path.is_dir() and (path / "pyproject.toml").is_file()


def _local_repo_run_args(from_source: str, *knotica_argv: str) -> list[str]:
    """``uv run --directory <repo> --group evals knotica …`` argv tail."""
    repo = str(Path(from_source).expanduser().resolve())
    return ["run", "--directory", repo, "--group", "evals", "knotica", *knotica_argv]


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

    Desktop headless tools (``query``, compile, Arena) need ``anthropic`` and
    ``dspy``, which live in the PEP 735 ``evals`` group and are not part of the
    base wheel ``uvx --from`` resolves. ``include_evals=True`` adds ``--with``
    for each package without pulling them onto the lean Claude Code plugin path.
    ``refresh=True`` forces a rebuild so local ``--from`` edits are not masked
    by a stale cached wheel (notably headless retrieval helpers).
    """
    args: list[str] = []
    if refresh:
        args.append("--refresh")
    args.extend(["--from", from_source])
    if include_evals:
        for package in _UVX_EVALS_PACKAGES:
            args.extend(["--with", package])
    args.extend(["knotica", subcommand])
    return args


def _warm_uvx(console: Console, from_source: str, *, include_evals: bool = False) -> None:
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


def _mcp_from_source() -> str:
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


def _desktop_config_path() -> Path:
    """Desktop config location: ``$KNOTICA_DESKTOP_CONFIG`` > macOS default."""
    override = os.environ.get(_DESKTOP_CONFIG_ENV_VAR)
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically: temp file in the same dir + rename.

    ``config.toml`` is merged additively (every pre-existing vault is preserved),
    so a torn write must never leave a truncated file that drops those vaults.
    Writing a sibling temp file and ``os.replace``-ing it (an atomic rename on
    the same filesystem) guarantees readers see either the old file or the fully
    written new one -- never a partial merge.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _read_config(path: Path) -> dict:
    """Read the existing config table, or an empty table if absent/invalid."""
    if not path.is_file():
        return {}
    import tomllib

    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
        return {}


def _dump_config_toml(data: dict) -> str:
    """Serialize the config table additively.

    Handles three shapes: top-level scalars, the special ``[vaults.<name>]``
    nested-table family, and any other dict-valued top-level key rendered as a
    flat ``[<key>]`` table (e.g. ``[loop]``, ``[models]``, ``[gapfill]``).
    Every writer that reads via :func:`_read_config` and writes back through
    this function preserves sibling sections it never touched -- callers only
    mutate the one dict key they own.
    """
    lines: list[str] = []
    for key, value in data.items():
        if key == "vaults" or isinstance(value, dict):
            continue
        if isinstance(value, (str, int, float, bool)):
            lines.append(f"{key} = {_toml_scalar(value)}")
    for name, entry in data.get("vaults", {}).items():
        lines.append("")
        lines.append(f"[vaults.{name}]")
        for key, value in entry.items():
            lines.append(f"{key} = {_toml_scalar(value)}")
    for key, value in data.items():
        if key == "vaults" or not isinstance(value, dict):
            continue
        lines.append("")
        lines.append(f"[{key}]")
        for sub_key, sub_value in value.items():
            lines.append(f"{sub_key} = {_toml_scalar(sub_value)}")
    return "\n".join(lines) + "\n"


def _toml_scalar(value: str | int | float | bool) -> str:
    """Render a scalar as a TOML value (bool before int/float -- ``bool`` is an int)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value)  # basic string with correct escaping


def _has_git_identity(console: Console, vault_path: Path) -> bool:
    """Return whether git has a committer identity configured for this repo."""
    result = _git(console, vault_path, "config", "user.email", check=False)
    return bool(result.stdout.strip())


def _git(
    console: Console, vault_path: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a ``git -C <vault>`` bootstrap command, surfacing failures cleanly."""
    console.debug(f"git -C {vault_path} {' '.join(args)}")
    return _run(["git", "-C", str(vault_path), *args], check=check)


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

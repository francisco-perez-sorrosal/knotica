"""Behavioral tests for ``knotica prompt <op>`` — the alias-injection surface.

``knotica prompt`` renders the vault-resolved operation body so that the CLI
alias and the MCP prompt handler share ONE source of truth: the vault
``.knotica/prompts/`` files. These tests pin that single-source contract by
running the command as a subprocess and asserting its stdout equals the body the
shared ``core.prompts`` resolver produces for the same operation and topic — the
same bytes ``test_prompts.py`` and the MCP prompt band assert on. They also pin
the CLI conventions the surface promises: success exits 0, misuse exits 2, and an
unconfigured invocation exits 3 with the uniform not-configured remediation on
stderr (mirroring the MCP ``NOT_CONFIGURED`` grammar).

Subprocess env inherits the test's already-redirected ``os.environ`` (the config
fixtures point HOME/``KNOTICA_CONFIG`` at a tmp vault, or clear them for the
unconfigured cases), so no real user config is ever read.

RED until the CLI ``prompt`` command lands: the placeholder entry point ignores
the argument and prints a version banner.
"""

import os
import subprocess
import sys
from pathlib import Path

from knotica.core.prompts import override_prompt_path, resolve_prompt
from knotica.store import LocalFSStore
from test_errors import assert_names_both_setup_paths

SEED_TOPIC = "agentic-systems"


def _cli(*args: str) -> list[str]:
    console = Path(sys.executable).with_name("knotica")
    if console.exists():
        return [str(console), *args]
    return [
        sys.executable,
        "-c",
        "import sys; from knotica.cli import main; sys.exit(main())",
        *args,
    ]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    return subprocess.run(
        _cli(*args),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _resolver_body(template_vault: Path, operation: str, topic: str = "") -> str:
    return resolve_prompt(LocalFSStore(template_vault), operation, topic).body


def _matches_body(stdout: str, body: str) -> bool:
    """The rendered body, allowing at most one trailing newline from ``print``."""
    return stdout == body or stdout == body + "\n"


# ---------------------------------------------------------------------------
# Single source of truth: CLI output == shared resolver output
# ---------------------------------------------------------------------------


def test_prompt_output_equals_the_shared_resolver_body(vault_config: Path, template_vault: Path):
    result = _run("prompt", "ingest")

    assert result.returncode == 0, result.stderr
    body = _resolver_body(template_vault, "ingest")
    assert _matches_body(result.stdout, body), (
        "knotica prompt must render the exact resolver body — the vault prompts "
        f"are the single source of truth; got {result.stdout!r}"
    )


def test_prompt_renders_the_earned_topic_override(vault_config: Path, template_vault: Path):
    """A topic override written to the vault is what the CLI serves for that
    topic — same precedence the resolver applies (override wins once earned)."""
    override_body = "# Ingest — evolved for this topic\n\nRefined protocol.\n"
    override = template_vault / override_prompt_path("ingest", SEED_TOPIC)
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text(override_body, encoding="utf-8")

    result = _run("prompt", "ingest", "--topic", SEED_TOPIC)

    assert result.returncode == 0, result.stderr
    assert _matches_body(result.stdout, override_body)


def test_prompt_accepts_the_source_flag_and_still_serves_the_resolver_body(
    vault_config: Path, template_vault: Path, tmp_path: Path
):
    """``--source`` supplies operation context but must not alter the rendered
    body: the prompt surface stays byte-identical to the shared resolver."""
    source = tmp_path / "paper.md"
    source.write_text("# A source\n", encoding="utf-8")

    result = _run("prompt", "ingest", "--source", str(source))

    assert result.returncode == 0, result.stderr
    body = _resolver_body(template_vault, "ingest")
    assert _matches_body(result.stdout, body)


# ---------------------------------------------------------------------------
# CLI conventions: exit codes and the unconfigured mirror
# ---------------------------------------------------------------------------


def test_unknown_operation_is_misuse_and_exits_two(vault_config: Path):
    """A bad operation word is argument misuse — the documented misuse code is
    2, distinct from a runtime failure (1) or an unconfigured vault (3)."""
    result = _run("prompt", "not-an-operation")

    assert result.returncode == 2, (
        f"misuse must exit 2 (got {result.returncode}); stderr: {result.stderr!r}"
    )


def test_unconfigured_prompt_exits_three_and_mirrors_the_setup_remediation(
    unconfigured_env: Path,
):
    """With no config anywhere, the CLI prompt reports the uniform not-configured
    remediation on stderr (naming BOTH the plugin and CLI setup paths, mirroring
    the MCP ``NOT_CONFIGURED`` grammar) and exits with the documented code 3."""
    result = _run("prompt", "ingest")

    assert result.returncode == 3, (
        f"unconfigured must exit 3 (got {result.returncode}); stderr: {result.stderr!r}"
    )
    assert_names_both_setup_paths(result.stderr)

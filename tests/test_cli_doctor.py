"""Behavioral tests for ``knotica doctor`` — the deterministic health harness.

``doctor`` runs mechanical, LLM-free checks over the resolved vault and reports a
PASS/WARN/FAIL row per check with a specific remediation, gating the hooks
through its exit code. These tests pin that contract *behaviorally*, from the
documented interface (INTERFACE_DESIGN §4.3 human mockup + §4.4 exit codes),
never from implementation internals:

- a healthy fixture vault produces no FAIL rows and exits 0 (warnings are still
  a clean exit per §4.4);
- one corruption per deterministic, vault-scoped check drives exactly that row
  off PASS and surfaces its specific remediation; a FAIL row forces exit 1,
  warnings-only stays exit 0 — the §4.4 rule encoded directly;
- ``--quick`` is a strict subset of the full check set;
- ``--json`` is stable machine output (parses, round-trips, carries the check
  labels and status tokens);
- an unconfigured vault exits 3 with the uniform not-configured remediation on
  stderr (mirroring the MCP ``NOT_CONFIGURED`` grammar).

Rows are parsed from the human output rather than JSON keys because the human
mockup (§4.3) is the literal contract; assertions match documented labels and
remediation grammar by substring, so an impl free to phrase details stays green
as long as it honors the contract. The environment-dependent checks (``mcp``
plugin registration, ``uvx`` presence) are deliberately *not* pinned here — they
depend on the host, not the vault, and cannot be corrupted deterministically in
a subprocess; the healthy case therefore asserts "no FAIL", not "every row
PASS", which is what §4.4 actually promises.

RED until the ``doctor`` command lands: the registered stub raises
``NotImplementedError`` (empty stdout, exit 1), so every behavioral assertion
below fails until the paired ``doctor`` implementation commits.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from knotica.cli import _register_commands
from knotica.cli.common import UNCONFIGURED_MESSAGE
from support.vault import run_git

_STATUS_TOKENS = ("PASS", "WARN", "FAIL")


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
    return subprocess.run(_cli(*args), capture_output=True, text=True, env=env, timeout=60)


# ---------------------------------------------------------------------------
# Human-output row parsing (the §4.3 mockup is the literal contract)
# ---------------------------------------------------------------------------


def _check_rows(stdout: str) -> list[tuple[str, str]]:
    """Every check line as ``(status, remainder)`` — a line whose stripped form
    opens with a PASS/WARN/FAIL glyph is a check row."""
    rows: list[tuple[str, str]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        for token in _STATUS_TOKENS:
            if stripped.startswith(token):
                rows.append((token, stripped[len(token) :].strip()))
                break
    return rows


def _statuses_for(stdout: str, label: str) -> set[str]:
    """Statuses of every row whose remainder mentions ``label`` (case-insensitive)."""
    return {status for status, rest in _check_rows(stdout) if label.lower() in rest.lower()}


def _has_fail(stdout: str) -> bool:
    return any(status == "FAIL" for status, _ in _check_rows(stdout))


def _leading_labels(stdout: str) -> set[str]:
    """The first word of each check row's remainder — a stable per-check key."""
    return {rest.split()[0].lower() for _, rest in _check_rows(stdout) if rest.split()}


def _string_values(obj: object) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for value in obj.values() for s in _string_values(value)]
    if isinstance(obj, list):
        return [s for item in obj for s in _string_values(item)]
    return []


# ---------------------------------------------------------------------------
# Corruption helpers — each drives exactly one deterministic vault-scoped check
# ---------------------------------------------------------------------------


def _strip_schema_version(vault: Path) -> None:
    schema = vault / "SCHEMA.md"
    kept = [
        line
        for line in schema.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("schema_version")
    ]
    schema.write_text("\n".join(kept) + "\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: drop root schema_version")


def _reserved_name_collision(vault: Path) -> None:
    """Turn a reserved top-level file into a reserved-named *directory* — the one
    shape ``_check_reserved_names`` flags (a directory claiming a reserved name)."""
    reserved = vault / "START_HERE.md"
    reserved.unlink()
    reserved.mkdir()
    (reserved / "page.md").write_text("# collides\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: reserved-name directory collision")


def _broken_wikilink(vault: Path) -> None:
    page = vault / "agentic-systems" / "agent-memory.md"
    page.write_text(
        page.read_text(encoding="utf-8") + "\n\nSee [[zzz-missing-target]].\n",
        encoding="utf-8",
    )
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: introduce unresolved wikilink")


def _dirty_tree(vault: Path) -> None:
    """Leave an uncommitted change — a dirty working tree (no commit)."""
    (vault / "agentic-systems" / "agent-memory.md").write_text(
        "uncommitted edit\n", encoding="utf-8"
    )


def _one_unpushed_commit(vault: Path, remote: Path) -> None:
    run_git(remote.parent, "init", "--bare", remote.name)
    run_git(vault, "remote", "add", "origin", str(remote))
    run_git(vault, "push", "-u", "origin", "HEAD")
    (vault / "agentic-systems" / "ahead.md").write_text("# ahead\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: local commit not yet pushed")


# ---------------------------------------------------------------------------
# Healthy vault
# ---------------------------------------------------------------------------


def test_healthy_vault_has_no_failing_check_and_exits_zero(
    vault_config: Path, template_vault: Path
):
    """A clean fixture vault yields no FAIL row and a clean exit. Warnings are
    permitted (host-dependent ``mcp``/``uvx`` rows may warn) — §4.4 makes a
    warning-only run exit 0, so the invariant is 'no FAIL', not 'all PASS'."""
    result = _run("doctor")

    assert result.returncode == 0, f"healthy vault must exit 0; stderr: {result.stderr!r}"
    assert not _has_fail(result.stdout), (
        f"healthy vault must not FAIL any check; rows: {_check_rows(result.stdout)!r}"
    )
    for label in ("config", "schema", "reserved", "link", "git"):
        assert _statuses_for(result.stdout, label) == {"PASS"}, (
            f"deterministic vault-scoped check {label!r} must PASS on a healthy vault; "
            f"saw {_statuses_for(result.stdout, label)!r}"
        )


# ---------------------------------------------------------------------------
# One corruption per deterministic, vault-scoped check
# ---------------------------------------------------------------------------


def test_bad_config_is_not_a_clean_exit_and_names_the_vault_problem(
    isolated_home: Path, template_vault: Path, monkeypatch: pytest.MonkeyPatch
):
    """A present config whose vault path does not resolve is not healthy: doctor
    must exit non-zero and name the configuration/vault problem."""
    config_dir = isolated_home / ".config" / "knotica"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.toml"
    missing = template_vault.parent / "does-not-exist"
    config_path.write_text(
        f'schema_version = 1\ndefault_vault = "main"\n\n[vaults.main]\npath = "{missing}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KNOTICA_CONFIG", str(config_path))

    result = _run("doctor")

    assert result.returncode != 0, "an unresolvable vault must not exit 0"
    combined = (result.stdout + result.stderr).lower()
    assert "vault" in combined or "config" in combined


def test_unresolved_schema_flags_the_schema_check_with_remediation(
    vault_config: Path, template_vault: Path
):
    _strip_schema_version(template_vault)

    result = _run("doctor")

    assert _statuses_for(result.stdout, "schema") & {"WARN", "FAIL"}, (
        "a schema with no schema_version must drive the schema row off PASS"
    )
    combined = result.stdout.lower()
    assert "schema_version" in combined or "migrate" in combined, (
        "the schema row must carry its specific remediation (add schema_version / migrate)"
    )
    assert result.returncode == (1 if _has_fail(result.stdout) else 0)


def test_reserved_name_collision_flags_the_reserved_row_with_rename_remediation(
    vault_config: Path, template_vault: Path
):
    _reserved_name_collision(template_vault)

    result = _run("doctor")

    assert _statuses_for(result.stdout, "reserved") & {"WARN", "FAIL"}, (
        "a reserved-named directory must drive the reserved-names row off PASS"
    )
    assert "rename" in result.stdout.lower(), (
        "the reserved row must carry its specific remediation (rename the directory)"
    )
    assert result.returncode == (1 if _has_fail(result.stdout) else 0)


def test_broken_wikilink_flags_the_links_row_as_unresolved(
    vault_config: Path, template_vault: Path
):
    _broken_wikilink(template_vault)

    result = _run("doctor")

    assert _statuses_for(result.stdout, "link") & {"WARN", "FAIL"}, (
        "an unresolved wikilink must drive the links row off PASS"
    )
    assert "unresolved" in result.stdout.lower(), (
        "the links row must name the unresolved-wikilink condition"
    )
    assert result.returncode == (1 if _has_fail(result.stdout) else 0)


def test_page_citing_an_unstored_source_flags_the_citations_row(
    vault_config: Path, template_vault: Path
):
    page = template_vault / "agentic-systems" / "agent-memory.md"
    page.write_text(
        page.read_text() + "\n\nA later survey expands this (nobody2099ghost §3).\n",
        encoding="utf-8",
    )

    result = _run("doctor")

    assert _statuses_for(result.stdout, "citations") & {"WARN", "FAIL"}, (
        "citing a source the vault does not hold must drive the citations row off PASS"
    )
    assert "unstored" in result.stdout.lower(), (
        "the citations row must name the unstored-source condition"
    )
    assert result.returncode == (1 if _has_fail(result.stdout) else 0)


def test_dirty_working_tree_flags_a_git_row_and_offers_fix(
    vault_config: Path, template_vault: Path
):
    _dirty_tree(template_vault)

    result = _run("doctor")

    assert _statuses_for(result.stdout, "git") & {"WARN", "FAIL"}, (
        "an uncommitted change must drive a git row off PASS"
    )
    assert "--fix" in result.stdout, (
        "a dirty tree is rollback-able — doctor must point at `doctor --fix`"
    )
    assert result.returncode == (1 if _has_fail(result.stdout) else 0)


def test_unpushed_commit_flags_a_git_row_with_push_remediation(
    vault_config: Path, template_vault: Path, tmp_path: Path
):
    _one_unpushed_commit(template_vault, tmp_path / "remote.git")

    result = _run("doctor")

    assert _statuses_for(result.stdout, "git") & {"WARN", "FAIL"}, (
        "a commit ahead of the remote must drive a git row off PASS"
    )
    assert "push" in result.stdout.lower(), (
        "the unpushed row must carry its specific remediation (git push)"
    )
    assert result.returncode == (1 if _has_fail(result.stdout) else 0)


# ---------------------------------------------------------------------------
# --quick subset, --json stability, unconfigured exit 3
# ---------------------------------------------------------------------------


def test_quick_is_a_strict_subset_of_the_full_check_set(vault_config: Path, template_vault: Path):
    """``--quick`` is the SessionStart subset: fewer checks, all drawn from the
    full set — never a check the full run does not have."""
    full = _leading_labels(_run("doctor").stdout)
    quick = _leading_labels(_run("doctor", "--quick").stdout)

    assert len(full) >= 5, "the full run must exercise the documented check set"
    assert quick, "--quick must still run some checks"
    assert quick <= full, f"--quick checks {quick - full!r} are not in the full set"
    assert len(quick) < len(full), "--quick must be a *strict* subset (a smaller set)"


def test_json_output_is_stable_machine_readable(vault_config: Path, template_vault: Path):
    """``--json`` is a script-parseable rendering: it parses, round-trips, and
    carries the same checks and status tokens as the human surface."""
    result = _run("doctor", "--json")

    payload = json.loads(result.stdout)
    assert json.loads(json.dumps(payload)) == payload, "JSON output must round-trip"

    values = " ".join(_string_values(payload))
    assert any(token in values for token in _STATUS_TOKENS), (
        "the JSON must carry per-check status tokens"
    )
    for label in ("config", "schema", "git"):
        assert label in values.lower(), f"the JSON must name the {label!r} check"


def test_unconfigured_vault_exits_three_with_the_setup_remediation(
    unconfigured_env: Path,
):
    """No config anywhere → the uniform not-configured remediation on stderr and
    the documented exit code 3 (mirrors the MCP ``NOT_CONFIGURED`` grammar)."""
    result = _run("doctor")

    assert result.returncode == 3, (
        f"unconfigured must exit 3 (got {result.returncode}); stderr: {result.stderr!r}"
    )
    assert UNCONFIGURED_MESSAGE in result.stderr


# ---------------------------------------------------------------------------
# doctor repair — real path-scoped restore (not --fix guidance)
# ---------------------------------------------------------------------------


def test_doctor_repair_dry_run_lists_dirty_paths(vault_config: Path, template_vault: Path):
    del vault_config
    (template_vault / "SCHEMA.md").write_text(
        (template_vault / "SCHEMA.md").read_text(encoding="utf-8") + "\n# dirty\n",
        encoding="utf-8",
    )
    result = _run("doctor", "repair", "--dry-run", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mode"] == "dry-run"
    assert "SCHEMA.md" in payload["tracked_paths"]


def test_doctor_repair_apply_restores_selected_path(vault_config: Path, template_vault: Path):
    del vault_config
    target = template_vault / "SCHEMA.md"
    original = target.read_text(encoding="utf-8")
    target.write_text(original + "\n# dirty-apply\n", encoding="utf-8")

    result = _run("doctor", "repair", "--apply", "--paths", "SCHEMA.md", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mode"] == "apply"
    assert "SCHEMA.md" in payload["restored"]
    assert target.read_text(encoding="utf-8") == original


def test_doctor_fix_points_at_repair_not_restore_dot(vault_config: Path, template_vault: Path):
    del vault_config
    (template_vault / "SCHEMA.md").write_text(
        (template_vault / "SCHEMA.md").read_text(encoding="utf-8") + "\n# dirty\n",
        encoding="utf-8",
    )
    result = _run("doctor", "--fix", "--json")
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    guidance = payload.get("fix_guidance")
    assert guidance is not None
    assert guidance["kind"] == "scoped_git_restore"
    joined = " ".join(guidance["commands"])
    assert "doctor repair" in joined
    assert "git restore ." not in joined


def test_importing_doctor_never_pulls_in_an_llm_client() -> None:
    """ "Runs mechanical, LLM-free checks" survives only as prose in this module's docstring.

    `doctor` is the command a confused user runs first, often before any
    credential is configured, and `--quick` is advertised as cheap. If a check
    ever reached for the LLM the cost would land on the user silently -- and a
    behavioral test cannot prove the absence of a call on paths it does not
    take, so this pins the stronger structural property: the module cannot call
    what it cannot import.
    """
    script = (
        "import sys\n"
        "import knotica.core.doctor, knotica.cli.doctor\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m.split('.')[0] in {'anthropic', 'dspy'} or m.endswith('evals.llm')\n"
        ")\n"
        "assert not leaked, leaked\n"
        "print('DOCTOR_LLM_FREE_OK')\n"
    )

    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)

    assert result.returncode == 0, (
        f"doctor must not import an LLM client; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "DOCTOR_LLM_FREE_OK" in result.stdout


def test_the_check_set_includes_the_mcp_registration_row(vault_config: Path) -> None:
    """`core/doctor.py` emits an `mcp` row that no test pinned.

    It is host-dependent, so it may PASS or WARN and the assertions elsewhere
    deliberately tolerate either -- which is exactly why its *presence* needs
    pinning separately: a row allowed to warn is a row whose disappearance
    nothing would notice.
    """
    result = _run("doctor", "--json")

    rows = json.loads(result.stdout)["checks"]
    names = [row["name"] for row in rows]

    assert "mcp" in names, f"doctor must report an mcp registration row; got {names}"


# ---------------------------------------------------------------------------
# common_parent() placement — the whole nested-subparser surface
# ---------------------------------------------------------------------------
#
# `doctor repair` is one of the nested subparsers walked below, and it is the
# riskiest of them (a required --dry-run|--apply group guarding a vault
# mutation), so the registry-wide flag-placement guard lives beside it rather
# than in a file of its own. The walk is derived from the *live* parser tree,
# never a hardcoded list, so a subcommand added later cannot silently regress.


def _root_parser() -> argparse.ArgumentParser:
    """Build the live parser tree the way ``knotica.cli.main`` builds it."""
    parser = argparse.ArgumentParser(prog="knotica")
    _register_commands(parser.add_subparsers(dest="command", metavar="<command>"))
    return parser


def _subparser_actions(parser: argparse.ArgumentParser) -> list[argparse._SubParsersAction]:
    return [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]


def _nested_sites() -> list[tuple[str, str, argparse.ArgumentParser]]:
    """Every ``<command> <subcommand>`` pair in the tree, discovered not listed."""
    sites = []
    for action in _subparser_actions(_ROOT):
        for command, command_parser in action.choices.items():
            for nested in _subparser_actions(command_parser):
                for subcommand, nested_parser in nested.choices.items():
                    sites.append((command, subcommand, nested_parser))
    return sites


def _required_argv(parser: argparse.ArgumentParser) -> list[str]:
    """Synthesize the minimum argv a nested subparser needs to parse at all.

    Several nested subcommands demand `--topic`/`--output`/`--branch` or a
    required mutually-exclusive branch; without them argparse exits 2 before it
    can tell us anything about flag placement.
    """
    argv: list[str] = []
    actions = [a for a in parser._actions if a.option_strings and a.required]
    for group in parser._mutually_exclusive_groups:
        if group.required:
            actions.append(group._group_actions[0])
    for action in actions:
        argv.append(action.option_strings[0])
        if action.nargs != 0:
            argv.append(str(next(iter(action.choices))) if action.choices else "1")
    return argv


_ROOT = _root_parser()
_NESTED_SITES = _nested_sites()
_SITE_IDS = [f"{command} {subcommand}" for command, subcommand, _ in _NESTED_SITES]

#: ``common_parent()``'s four flags, as (option string, namespace dest).
_COMMON_FLAGS = [
    ("--quiet", "quiet"),
    ("--verbose", "verbose"),
    ("--no-color", "no_color"),
    ("--no-input", "no_input"),
]


def test_the_parser_tree_actually_has_nested_subcommands_to_walk() -> None:
    """The walk below is only a guard if it found something to guard."""
    assert len(_NESTED_SITES) >= 13, (
        f"expected the nested-subparser walk to find the known sites; got {_SITE_IDS}"
    )


@pytest.mark.parametrize(("flag", "dest"), _COMMON_FLAGS, ids=[f for f, _ in _COMMON_FLAGS])
@pytest.mark.parametrize(("command", "subcommand", "parser"), _NESTED_SITES, ids=_SITE_IDS)
def test_a_common_flag_reads_the_same_before_or_after_the_nested_subcommand_name(
    command: str, subcommand: str, parser: argparse.ArgumentParser, flag: str, dest: str
) -> None:
    """`knotica okf --quiet check` and `knotica okf check --quiet` must agree.

    Two failure modes are pinned at once. A nested subparser with no
    `common_parent()` rejects the post-name form outright (exit 2). One that
    attaches the plain `common_parent()` accepts both and silently drops the
    pre-name form, because argparse copies the nested parser's own defaults
    over the command-level namespace.
    """
    required = _required_argv(parser)

    before = _ROOT.parse_args([command, flag, subcommand, *required])
    after = _ROOT.parse_args([command, subcommand, flag, *required])

    assert getattr(before, dest) is True, (
        f"`knotica {command} {flag} {subcommand}` must set {dest}; the nested "
        f"subparser's default clobbered the command-level value"
    )
    assert getattr(after, dest) is True, f"`knotica {command} {subcommand} {flag}` must set {dest}"


@pytest.mark.parametrize(("command", "subcommand", "parser"), _NESTED_SITES, ids=_SITE_IDS)
def test_omitting_the_common_flags_leaves_them_false_at_every_nested_subcommand(
    command: str, subcommand: str, parser: argparse.ArgumentParser
) -> None:
    """Suppressing the nested *default* must not suppress the attribute itself.

    The command-level parser still carries a real `False` default, so every
    reader -- `console_from_args`'s getattr fallback and the direct `args.verbose`
    reads in `cli/okf.py` alike -- keeps seeing a boolean.
    """
    args = _ROOT.parse_args([command, subcommand, *_required_argv(parser)])

    assert (args.quiet, args.verbose, args.no_color, args.no_input) == (
        False,
        False,
        False,
        False,
    ), f"`knotica {command} {subcommand}` with no flags must leave all four off"


def test_doctor_repair_still_requires_a_dry_run_or_apply_branch() -> None:
    """The flag surface may not weaken the gate on a vault-mutating subcommand."""
    with pytest.raises(SystemExit) as exit_info:
        _ROOT.parse_args(["doctor", "repair"])

    assert exit_info.value.code == 2

"""Tests for `scripts/test_group.py`, the topology-derived scoped-test runner.

The runner is dev tooling, so it lives outside `src/knotica/` and outside every
topology group -- which is exactly why it needs its own coverage. It decides
which tests a scoped run executes, so a silent regression in it shrinks that
scope without anything failing: the run still reports green, just over fewer
tests.

The completeness cases below are the ones that matter. `--check` validates the
group blocks it parsed; only the cross-check against the `## Subsystems` table
can notice that the parse itself dropped half the file.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "test_group.py"

GROUP_BLOCK = """\
### `{gid}`

```yaml
id: {gid}
title: {gid} title
subsystems:
  - "src/knotica/{gid}/"
tier: integration
selectors:
  - strategy: {strategy}
    arg:
      - {arg}
file_dependencies:
  - "src/knotica/**"
integration_boundaries: []
parallel_safe: true
shared_fixture_scope: per-suite
shared_state: tmp_path
```
"""


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    """Load the runner as a module -- `scripts/` is not an importable package."""
    spec = importlib.util.spec_from_file_location("topology_runner", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_topology(
    table_ids: list[str],
    block_ids: list[str],
    arg: str = "tests/probe/test_probe.py",
    strategy: str = "pytest-globs",
    extra_note: str = "",
) -> str:
    """Render a synthetic topology whose table and blocks are free to disagree."""
    rows = "\n".join(f"| `src/knotica/{gid}/` | `{gid}` | why |" for gid in table_ids)
    blocks = "\n".join(GROUP_BLOCK.format(gid=gid, arg=arg, strategy=strategy) for gid in block_ids)
    return (
        "# Test Topology\n\n"
        "## Subsystems\n\n"
        "| Component | Group | Why |\n|---|---|---|\n"
        f"{rows}\n\n"
        f"### Note 1 — a note\n\n{extra_note}\n\n"
        "## Test Groups\n\n"
        f"{blocks}\n"
    )


@pytest.fixture
def check_synthetic(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., int]:
    """Point the runner at a throwaway topology and repo root, then run `--check`."""
    topology = tmp_path / "TEST_TOPOLOGY.md"
    monkeypatch.setattr(runner, "TOPOLOGY_PATH", topology)
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    probe = tmp_path / "tests" / "probe"
    probe.mkdir(parents=True)
    (probe / "test_probe.py").write_text("")

    def check(**kwargs: object) -> int:
        topology.write_text(build_topology(**kwargs))
        return int(runner.cmd_check(runner.load_groups()))

    return check


def test_declared_group_ids_names_every_group_in_the_committed_topology(runner: ModuleType) -> None:
    declared = runner.declared_group_ids(runner.TOPOLOGY_PATH.read_text(encoding="utf-8"))
    parsed = {str(group["id"]) for group in runner.load_groups()}

    assert declared == parsed, "the Subsystems table and the group blocks disagree"


def test_declared_group_ids_ignores_group_shaped_tables_in_the_notes(runner: ModuleType) -> None:
    # Note 2's subtraction table also leads with a backticked group id; counting
    # it would inflate the declared set and mask a genuinely missing block.
    text = build_topology(
        ["real-group"],
        ["real-group"],
        extra_note="| Group | Modules |\n|---|---|\n| `phantom-group` | `a.py` |",
    )

    assert runner.declared_group_ids(text) == {"real-group"}


def test_check_passes_on_the_committed_topology(runner: ModuleType) -> None:
    assert runner.cmd_check(runner.load_groups()) == 0


def test_check_rejects_a_subsystems_group_that_has_no_block(
    check_synthetic: Callable[..., int], capsys
) -> None:
    # The fail-open guard: a parser regression matching fewer blocks used to
    # report "OK -- N groups" and read as a pass.
    exit_code = check_synthetic(table_ids=["kept", "dropped"], block_ids=["kept"])

    assert exit_code == 1
    assert "dropped" in capsys.readouterr().err


def test_check_rejects_a_block_the_subsystems_table_does_not_name(
    check_synthetic: Callable[..., int], capsys
) -> None:
    exit_code = check_synthetic(table_ids=["kept"], block_ids=["kept", "stowaway"])

    assert exit_code == 1
    assert "stowaway" in capsys.readouterr().err


def test_check_rejects_a_selector_arg_that_does_not_exist(
    check_synthetic: Callable[..., int], capsys
) -> None:
    exit_code = check_synthetic(table_ids=["g"], block_ids=["g"], arg="tests/probe/test_absent.py")

    assert exit_code == 1
    assert "does not exist" in capsys.readouterr().err


def test_check_rejects_a_wildcard_selector_arg(check_synthetic: Callable[..., int], capsys) -> None:
    # pytest does not expand globs itself, so a wildcard silently selects
    # nothing under shell=False while the group still reports a pass.
    exit_code = check_synthetic(table_ids=["g"], block_ids=["g"], arg="tests/probe/test_*.py")

    assert exit_code == 1
    assert "wildcard" in capsys.readouterr().err


def test_selector_args_refuses_an_unregistered_strategy(runner: ModuleType) -> None:
    group = {"id": "g", "selectors": [{"strategy": "pytest-markers", "arg": ["tests/"]}]}

    with pytest.raises(SystemExit) as raised:
        runner.selector_args(group)

    assert "pytest-markers" in str(raised.value)


def test_count_test_files_expands_a_directory_argument(runner: ModuleType) -> None:
    # A directory arg stands for many files; counting args instead would
    # disagree with the topology's own runtime table.
    nested = runner.count_test_files(["tests/core/notes/"])
    single = runner.count_test_files(["tests/core/notes/test_anchor.py"])

    assert single == 1
    assert nested > single


def test_running_an_unknown_group_reports_the_valid_ids(runner: ModuleType, capsys) -> None:
    exit_code = runner.cmd_run(runner.load_groups(), "no-such-group", [])

    assert exit_code == 2
    assert "vault-substrate" in capsys.readouterr().err

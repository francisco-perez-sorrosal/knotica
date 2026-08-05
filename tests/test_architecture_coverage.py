"""Tests for `scripts/check_architecture_coverage.py`, the architecture-doc gate.

The defect this gate exists to catch is not a typo -- it is a *timing* failure.
Three `core/` modules landed after the pass that last touched the architecture
documents, and nothing re-read those documents afterwards, so they described a
tree that no longer existed (td-038). `make verify` gated the test topology and
the ADR corpus; the one artifact pair with no gate is the one that lagged.

So every case below drives a *bad* tree or a *bad* document and asserts the check
rejects it. The happy-path case is deliberately last and deliberately thin: that
the checker passes on a healthy repo proves nothing about whether it bites.

The fixtures build a miniature `src/knotica/` tree rather than reading the real
one, so a case pins the checker's behaviour and not today's module count.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_architecture_coverage.py"

INVENTORY_HEADER = "| Package | Modules |\n|---|---|\n"


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    """Load the checker as a module -- `scripts/` is not an importable package."""
    spec = importlib.util.spec_from_file_location("architecture_coverage", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(
    checker: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., int]:
    """Build a throwaway repo -- a package tree plus two documents -- and check it."""
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(checker, "PACKAGE_ROOT", tmp_path / "src" / "knotica")

    def run(
        packages: dict[str, list[str]],
        inventory: dict[str, int],
        *,
        design_extra: str = "",
        architecture_extra: str = "",
    ) -> int:
        for package, modules in packages.items():
            directory = tmp_path / package
            directory.mkdir(parents=True, exist_ok=True)
            for module in modules:
                (directory / module).write_text("")

        rows = "".join(f"| `{path}` | {count} |\n" for path, count in inventory.items())
        design = tmp_path / ".ai-state" / "DESIGN.md"
        design.parent.mkdir(parents=True, exist_ok=True)
        design.write_text(f"## 3. Components\n\n{INVENTORY_HEADER}{rows}\n{design_extra}\n")

        architecture = tmp_path / "docs" / "architecture.md"
        architecture.parent.mkdir(parents=True, exist_ok=True)
        architecture.write_text(f"# Architecture Guide\n\n{architecture_extra}\n")

        monkeypatch.setattr(checker, "DESIGN", design)
        monkeypatch.setattr(checker, "ARCHITECTURE", architecture)
        return int(checker.main())

    return run


def test_rejects_a_module_added_without_the_published_count_moving(
    repo: Callable[..., int],
) -> None:
    # The td-038 shape exactly: a module lands, the count keeps its old value.
    exit_code = repo(
        {"src/knotica/core": ["__init__.py", "lint.py", "topics.py"]},
        {"src/knotica/core/": 2},
    )

    assert exit_code == 1


def test_rejects_a_module_deleted_without_the_published_count_moving(
    repo: Callable[..., int],
) -> None:
    exit_code = repo(
        {"src/knotica/core": ["__init__.py", "lint.py"]},
        {"src/knotica/core/": 3},
    )

    assert exit_code == 1


def test_rejects_a_package_absent_from_the_inventory_table(
    repo: Callable[..., int],
) -> None:
    # A whole package can arrive unrecorded, not just a module inside one.
    exit_code = repo(
        {
            "src/knotica/core": ["__init__.py"],
            "src/knotica/discovery": ["__init__.py", "youcom.py"],
        },
        {"src/knotica/core/": 1},
    )

    assert exit_code == 1


def test_rejects_an_inventory_row_for_a_package_that_is_gone(
    repo: Callable[..., int],
) -> None:
    exit_code = repo(
        {"src/knotica/core": ["__init__.py"]},
        {"src/knotica/core/": 1, "src/knotica/removed/": 4},
    )

    assert exit_code == 1


def test_rejects_a_design_citation_that_does_not_resolve_on_disk(
    repo: Callable[..., int],
) -> None:
    exit_code = repo(
        {"src/knotica/core": ["__init__.py"]},
        {"src/knotica/core/": 1},
        design_extra="The renamed module lives at `src/knotica/core/deleted.py` today.",
    )

    assert exit_code == 1


def test_rejects_an_architecture_guide_citation_that_does_not_resolve_on_disk(
    repo: Callable[..., int],
) -> None:
    # The guide's header promises every path was re-checked on disk; this is the
    # check that holds it to the promise.
    exit_code = repo(
        {"src/knotica/core": ["__init__.py"]},
        {"src/knotica/core/": 1},
        architecture_extra="| `src/knotica/core/vanished.py` | gone |",
    )

    assert exit_code == 1


def test_accepts_a_glob_citation_that_matches_at_least_one_file(
    repo: Callable[..., int],
) -> None:
    # `src/knotica/core/loop*.py` is a real citation in DESIGN.md. Truncating the
    # path at the `*` would report a dangling reference that is not one.
    exit_code = repo(
        {"src/knotica/core": ["__init__.py", "loop.py", "loop_state.py"]},
        {"src/knotica/core/": 3},
        design_extra="As-built: `src/knotica/core/loop*.py`.",
    )

    assert exit_code == 0


def test_rejects_a_glob_citation_that_matches_nothing(repo: Callable[..., int]) -> None:
    exit_code = repo(
        {"src/knotica/core": ["__init__.py"]},
        {"src/knotica/core/": 1},
        design_extra="As-built: `src/knotica/core/absent*.py`.",
    )

    assert exit_code == 1


def test_counts_a_package_by_every_py_file_including_dunder_init(
    repo: Callable[..., int],
) -> None:
    # The convention the okf/, guillotine/, and service/ rows already used before
    # this checker existed: `__init__.py` is one of the modules.
    exit_code = repo(
        {"src/knotica/okf": ["__init__.py", "check.py", "export.py"]},
        {"src/knotica/okf/": 3},
    )

    assert exit_code == 0


def test_ignores_a_directory_holding_no_python_at_all(repo: Callable[..., int]) -> None:
    # `service/templates/` holds unit templates, not modules; demanding an
    # inventory row for it would be a finding about nothing.
    exit_code = repo(
        {
            "src/knotica/service": ["__init__.py", "manager.py"],
            "src/knotica/service/templates": [],
        },
        {"src/knotica/service/": 2},
    )

    assert exit_code == 0


def test_accepts_a_tree_whose_counts_and_citations_all_agree(
    repo: Callable[..., int],
) -> None:
    exit_code = repo(
        {
            "src/knotica/core": ["__init__.py", "lint.py"],
            "src/knotica/store": ["__init__.py"],
        },
        {"src/knotica/core/": 2, "src/knotica/store/": 1},
        design_extra="The single writer is `src/knotica/core/lint.py`.",
        architecture_extra="Storage primitives: `src/knotica/store/`.",
    )

    assert exit_code == 0

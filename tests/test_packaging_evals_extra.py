"""Packaging fitness test: the eval dependencies are declared exactly once.

``anthropic`` / ``dspy`` / their version bounds were once written down in three
places at the same time -- a PEP 621 extra, a byte-identical PEP 735
dependency-group aliasing it, and a hand-maintained package tuple that built
Desktop's ``uvx --with`` argv. Nothing enforced that the three agreed, and they
did not: a ``litellm`` platform bound added to the first two never reached the
third, so Desktop installs kept resolving a litellm with no macOS wheel and
failed building a Rust sdist.

These tests pin the post-consolidation shape: the extra is the single
declaration, and every launch path requests it *by name* so the bounds travel
with it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
EXTRA_NAME = "evals"

#: Text surfaces that instruct a human or a machine how to install the extra.
#: ``.ai-state/`` is excluded on purpose -- superseded ADRs are historical
#: records that must keep describing the world as it was. ``app.html`` is a
#: build artifact regenerated from ``dashboard/src``.
_INSTRUCTION_GLOBS = (
    "src/knotica/**/*.py",
    "tests/**/*.py",
    "dashboard/src/**/*.tsx",
    "dashboard/src/**/*.ts",
    "docs/**/*.md",
    "commands/**/*.md",
    "*.md",
    "Makefile",
    "pyproject.toml",
)
_STALE_INVOCATION = "--group evals"
#: Opt-out marker for the one legitimate reason to write the stale invocation: a
#: migration note telling a reader what their *existing* broken config looks like.
#: Naming the exact string is the point there -- a reader greps their config for
#: it. Every other occurrence is an instruction into a dead end.
_MIGRATION_NOTE_MARKER = "allow-stale-invocation"


def _pyproject() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _instruction_files() -> list[Path]:
    seen: list[Path] = []
    for pattern in _INSTRUCTION_GLOBS:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if path.is_file() and "app.html" not in path.name:
                seen.append(path)
    return seen


def test_evals_is_declared_as_an_extra() -> None:
    extras = _pyproject().get("project", {}).get("optional-dependencies", {})  # type: ignore[union-attr]
    assert EXTRA_NAME in extras, (
        "the eval dependencies must be a PEP 621 extra so they ship in the wheel's "
        "metadata -- that is what makes `pip install knotica[evals]`, "
        "`uv tool install --from '.[evals]'` and `uvx --from '<src>[evals]'` resolvable "
        f"by a consumer who only has the package; got extras={sorted(extras)}"
    )


def test_no_dependency_group_shadows_the_evals_extra() -> None:
    groups = _pyproject().get("dependency-groups", {})
    assert EXTRA_NAME not in groups, (
        "a `dependency-groups.evals` entry re-declares dependencies the extra already "
        "owns. Two hand-synced copies drift silently -- nothing can enforce their "
        "equality, and the last drift shipped a Desktop install that could not build. "
        f"Declare the eval dependencies once, in the extra; got groups={sorted(groups)}"
    )


def test_the_extra_carries_its_own_version_bounds() -> None:
    # Non-vacuity guard: the tests above are satisfiable by an *empty* extra. The
    # point of consolidating is that the single declaration is the one carrying
    # the bounds, so assert it actually specifies them.
    extras = _pyproject()["project"]["optional-dependencies"]  # type: ignore[index]
    specs = extras[EXTRA_NAME]
    unbounded = [spec for spec in specs if not any(op in spec for op in ("<", ">", "==", "~="))]
    assert specs and not unbounded, (
        "every requirement in the evals extra must carry a version bound -- the extra "
        "is the only place a bound can live now that the duplicate declarations are "
        f"gone; unbounded={unbounded}"
    )


def test_no_instruction_still_tells_anyone_to_use_the_removed_group() -> None:
    offenders: list[str] = []
    for path in _instruction_files():
        if "test_packaging_evals_extra" in path.name:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            if _STALE_INVOCATION in line and _MIGRATION_NOTE_MARKER not in line:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}")
    assert not offenders, (
        "`uv sync --group evals` is now a hard error ('Group `evals` is not defined in "
        "the project's dependency-groups table'), so any surviving instruction sends a "
        f"reader into a dead end; use `--extra evals` (or mark a migration note with "
        f"'{_MIGRATION_NOTE_MARKER}'): {offenders}"
    )


_HEADLESS_COMMAND = REPO_ROOT / "commands" / "headless.md"


def test_headless_command_requests_the_evals_extra_instead_of_hand_listing_packages() -> None:
    """``/knotica:headless on`` built its ``claude mcp add`` argv with a hand-listed
    ``--with anthropic --with dspy`` -- the exact anti-pattern dec-067 deleted everywhere
    else (the retired Desktop ``_UVX_EVALS_PACKAGES`` tuple). Naming packages instead of the
    extra drops ``litellm<1.92`` silently, so a macOS ``/knotica:headless on`` resolves a
    litellm with no wheel and falls back to a Rust sdist build. This command's launch line
    is prose, not code the packaging fitness tests above already scan for shape, so it needs
    its own guard.
    """
    text = _HEADLESS_COMMAND.read_text(encoding="utf-8")
    assert f"[{EXTRA_NAME}]" in text, (
        f"the enable path must request the extra by name (`[{EXTRA_NAME}]`) so its version "
        "bounds -- notably `litellm<1.92` -- travel with the install"
    )
    assert "--with anthropic" not in text and "--with dspy" not in text, (
        "hand-listing the eval packages via `--with` drops the extra's version bounds and "
        "reintroduces the macOS sdist/Rust-toolchain build failure dec-067 fixed elsewhere"
    )


# The two tests below police `commands/guillotine.md`, not the evals extra -- they live here
# rather than in a new file because this module is the `plugin-layer` test-topology group's
# only file that already parses `commands/**/*.md` prose for shipped-vs-documented drift, and
# a new test file would need its own `.ai-state/TEST_TOPOLOGY.md` entry.
_GUILLOTINE_COMMAND = REPO_ROOT / "commands" / "guillotine.md"


def test_guillotine_command_takes_topic_as_an_argument_not_a_hardcoded_default() -> None:
    """The command's shell invocation once baked in ``--topic agentic-systems`` and told the
    reader to "replace ... if needed" -- a command that only works against one vault's topic
    name, exactly what ``commands/CLAUDE.md`` forbids. Topic must be a documented argument.
    """
    text = _GUILLOTINE_COMMAND.read_text(encoding="utf-8")
    assert "--topic agentic-systems" not in text, (
        "the shipped invocation must not hardcode a vault-specific topic default"
    )
    argument_hint_line = next(
        line for line in text.splitlines() if line.startswith("argument-hint:")
    )
    assert "topic" in argument_hint_line.lower(), (
        "argument-hint must document topic as a takeable argument rather than leaving it "
        "implicit in a hardcoded shell command"
    )


def test_guillotine_command_description_does_not_overpromise_a_rewrite() -> None:
    """The description once promised "a reversible retraction patch". Per
    ``guillotine/patch.py``, the guillotine never rewrites wiki-page prose -- it renders a
    diff that is evidence for human review and is never applied to a page.
    """
    text = _GUILLOTINE_COMMAND.read_text(encoding="utf-8")
    description_line = next(line for line in text.splitlines() if line.startswith("description:"))
    assert "reversible retraction" not in description_line.lower(), (
        "the guillotine never edits wiki pages -- calling its diff a 'reversible retraction "
        "patch' overstates what ships"
    )
    assert "review" in description_line.lower(), (
        "the description should say the diff is for human review, matching the "
        "never-applied contract in guillotine/patch.py"
    )

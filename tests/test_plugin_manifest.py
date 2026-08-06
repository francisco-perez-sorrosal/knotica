"""Contract test for the shipped `.mcp.json` — the plugin's launch line.

This file is every Claude Desktop and Claude Code user's first contact with
knotica: the plugin loader reads it to learn how to start the server. Until
now nothing asserted it. The manifest was correct, and a spec re-derivation
recorded that it was verified *by inspection* — which is exactly the state
that lets an edit ship undetected, because the next reader has no way to tell
a checked claim from an unchecked one.

Two properties, and they fail in opposite directions:

- **The launch line is exact.** `uvx --from ${CLAUDE_PLUGIN_ROOT} knotica mcp`
  resolves the server from the plugin directory the loader substitutes. A
  reordered or dropped argument does not degrade gracefully — `uvx` either
  resolves a different package or none, so the user gets a server that fails
  to start rather than one that misbehaves subtly.
- **`alwaysLoad` is absent.** Setting it would start the server for every
  session whether or not the user is doing wiki work, which contradicts the
  cold-start discipline the import-boundary tests exist to protect. Its
  absence is a deliberate choice and therefore worth pinning; a key that is
  *supposed* to be missing is the kind nobody notices being added.

The file has since taken on two more shipped-declaration couplings that fail the
same silent way: the autofix hub's workflow-name array, and the release version
carried by three files at once.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_MANIFEST = REPO_ROOT / ".mcp.json"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
AUTOFIX_WORKFLOW = WORKFLOWS_DIR / "ci-autofix.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"
PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
UV_LOCK = REPO_ROOT / "uv.lock"

#: The launch line the plugin loader must find, argument for argument.
EXPECTED_COMMAND = "uvx"
EXPECTED_ARGS = ["--from", "${CLAUDE_PLUGIN_ROOT}", "knotica", "mcp"]


def _manifest() -> dict:
    return json.loads(MCP_MANIFEST.read_text(encoding="utf-8"))


def test_the_manifest_is_present_and_parses_as_json() -> None:
    # A malformed manifest is indistinguishable from a missing plugin at the
    # loader, so parse failure is its own finding rather than a side effect of
    # the assertions below.
    assert MCP_MANIFEST.is_file(), f"{MCP_MANIFEST} must ship with the repo"

    assert isinstance(_manifest(), dict)


def test_the_server_launches_from_the_plugin_root_via_uvx() -> None:
    server = _manifest()["mcpServers"]["knotica"]

    assert server["command"] == EXPECTED_COMMAND
    assert server["args"] == EXPECTED_ARGS, (
        "the launch line is positional -- uvx resolves --from's value as the "
        "package source, so a reordered or dropped argument starts the wrong "
        "thing or nothing at all"
    )


def test_the_manifest_declares_exactly_one_server_named_knotica() -> None:
    # A second entry would start a second server against the same vault, and
    # the single-writer rule is enforced per process, not per vault.
    assert list(_manifest()["mcpServers"]) == ["knotica"]


def test_the_manifest_never_sets_always_load() -> None:
    # Checked over the raw bytes rather than the parsed object: the key would
    # be a regression wherever it appeared in the tree, and reading it back
    # from one expected nesting level would miss it one level over.
    raw = MCP_MANIFEST.read_text(encoding="utf-8")

    assert "alwaysLoad" not in raw, (
        "alwaysLoad would start the server for every session regardless of "
        "whether the user is doing wiki work; its absence is deliberate"
    )


# --- the autofix hub's workflow_run coupling -------------------------------
#
# `ci-autofix.yml` triggers on `workflow_run` with a hardcoded array of
# workflow *names*. GitHub Actions requires that literal -- the trigger cannot
# reference a workflow file -- so the coupling is by string, and a string
# coupling that nothing checks is a string coupling that rots. It already did:
# the array named "CI" for months while no workflow declared that name, so half
# the trigger silently never fired and no signal said so. Renaming a `name:`
# field is the obvious way to reintroduce it, and renaming is exactly the kind
# of edit that looks harmless.


def _declared_workflow_names() -> set[str]:
    """Every `name:` declared by a workflow file, as GitHub resolves them."""
    names = set()
    for path in sorted(WORKFLOWS_DIR.glob("*.y*ml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        declared = document.get("name")
        if isinstance(declared, str):
            names.add(declared)
    return names


def _autofix_watched_names() -> list[str]:
    """The workflow names `ci-autofix.yml` waits on."""
    document = yaml.safe_load(AUTOFIX_WORKFLOW.read_text(encoding="utf-8")) or {}
    # PyYAML's safe loader is YAML 1.1, where a bare `on` key parses as the
    # boolean True rather than the string "on". GitHub Actions spells the
    # trigger block `on:`, so both spellings have to be tried -- reading only
    # "on" yields an empty trigger and every assertion below passes vacuously.
    triggers = document.get("on") or document.get(True) or {}
    watched = (triggers.get("workflow_run") or {}).get("workflows") or []
    return [name for name in watched if isinstance(name, str)]


def test_the_autofix_hub_still_parses_as_watching_something() -> None:
    # Guards the guard. Every assertion about the watched set is vacuous if the
    # parse silently yields nothing -- which is precisely what the YAML 1.1 `on`
    # gotcha above does when someone "simplifies" the fallback away.
    assert _autofix_watched_names(), (
        "parsed no watched workflows out of ci-autofix.yml; the trigger block "
        "did not parse, so the coupling test below proves nothing"
    )


def test_every_workflow_the_autofix_hub_watches_actually_exists() -> None:
    dangling = sorted(set(_autofix_watched_names()) - _declared_workflow_names())

    assert not dangling, (
        f"ci-autofix.yml waits on {dangling}, and no workflow in "
        f".github/workflows/ declares that name. The hub will never fire for "
        f"those runs, silently. Either restore the workflow's `name:` field or "
        f"drop the entry from its `workflow_run.workflows` array -- the two "
        f"are coupled by string literal and nothing else keeps them in step."
    )


# --- the release version, carried by three files ---------------------------
#
# One release moves `pyproject.toml`, the `knotica` stanza of `uv.lock`, and
# `.claude-plugin/plugin.json` together, and each drifts in its own silent way:
#
#   - pyproject vs uv.lock: CI runs `uv sync --locked`, so a lock left behind
#     does not mis-report a version, it fails the build outright -- but only
#     after the release commit is already tagged and pushed.
#   - pyproject vs plugin.json: Claude Code reads the plugin `version` as a
#     CACHE KEY. A stale one is invisible here and total for the user: every
#     installed copy keeps serving the old plugin while this repo looks correct.
#
# `cz bump --check-consistency` checks this at bump time. That is one moment, on
# a CI runner, on a tree nobody reads. These run on every `make verify`, which is
# where a hand-edit to any of the three would actually be caught.


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _lockfile_version(package: str) -> str | None:
    """The version `uv.lock` records for one package, or None if it is absent."""
    lock = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    return next(
        (entry["version"] for entry in lock.get("package", []) if entry.get("name") == package),
        None,
    )


def test_the_declared_version_is_the_same_in_all_three_files() -> None:
    declared = _pyproject()["project"]["version"]

    assert json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))["version"] == declared, (
        "the plugin manifest version is Claude Code's cache key -- while it "
        "disagrees with pyproject.toml, installed copies keep serving the "
        "version it names and nothing surfaces the gap to their users"
    )
    assert _lockfile_version("knotica") == declared, (
        "uv.lock records this project's own version, and CI runs "
        "`uv sync --locked` -- so this disagreement fails the build rather "
        "than mis-reporting anything. Run `uv lock` to resolve it"
    )


def test_the_bump_is_still_configured_to_write_the_lockfile() -> None:
    # The single load-bearing line of the commitizen config. `pep621` -- the
    # obvious-looking choice, and the one a config copied from a non-uv project
    # would carry -- writes pyproject.toml alone and leaves uv.lock stale, which
    # turns `main` red on the release commit itself. Nothing else in the config
    # fails this way, so this is the one key worth pinning to a test rather than
    # to a comment.
    commitizen = _pyproject()["tool"]["commitizen"]

    assert commitizen["version_provider"] == "uv", (
        "only the `uv` provider updates uv.lock alongside pyproject.toml; any "
        "other provider bumps one and leaves the other stale"
    )
    assert "version" not in commitizen, (
        "the `uv` provider reads [project].version, so a `version` key here is "
        "a second copy that nothing keeps in step -- delete it rather than "
        "syncing it by hand"
    )

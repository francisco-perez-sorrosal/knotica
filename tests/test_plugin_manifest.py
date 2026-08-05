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
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_MANIFEST = REPO_ROOT / ".mcp.json"

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

"""Knotica: an AI-maintained, compounding knowledge wiki over an Obsidian vault.

Package layout (hexagonal, single-mutation-core; dependency arrows point inward
toward ``store``):

- ``knotica.core`` -- vault semantics and the vault's ONLY writer
  (``core.transaction``); all mutating operations route through
  ``core.operations``.
- ``knotica.store`` -- storage protocol + local filesystem backend (stdlib only).
- ``knotica.search`` -- read-only search protocol + ripgrep backend.
- ``knotica.cli`` -- thin CLI adapter (console entry point ``knotica``).
- ``knotica.mcp_server`` -- thin MCP adapter (named to avoid shadowing the
  ``mcp`` SDK package).
"""

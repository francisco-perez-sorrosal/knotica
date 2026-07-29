"""Vault semantics -- the hexagonal core and the vault's only writer.

Owns config resolution, schema resolution (root + topic overlay), the page and
wikilink models, lint, the frozen record formats, git (``vcs``), locking
(``lock``), secret scrubbing (``scrub``), and the single mutation path: every
mutating operation (MCP tool, CLI command, future headless loop) routes through
``core.operations.*``, which opens the one ``core.transaction.VaultTransaction``
-- flock, atomic writes, log append, scrub, exactly one git commit.

Boundary: depends only on the ``knotica.store``/``knotica.search`` protocols and
the stdlib. The adapters (``knotica.cli``, ``knotica.mcp_server``) depend on this
package -- never the reverse. The single exception is ``vault_layout``, the
zero-dependency folder-family leaf: it imports nothing from ``knotica``, so
``knotica.search`` depends on it (for the topic/family a search result reports)
without creating a cycle. Any *other* inbound edge into this package is a
layering violation.
"""

"""MCP adapter -- the FastMCP server exposing knotica's deterministic tool surface.

Named ``mcp_server`` (not ``mcp``) so this package never shadows the official
``mcp`` SDK it builds on. Thin and stateless: per-call config resolution, zero
vault access at startup, errors carried in result content (never transport
exceptions). Reads delegate to ``knotica.core`` read functions; mutations go
ONLY through ``knotica.core.operations.*`` -- this package never writes the
vault directly (enforced by the import-boundary fitness test).
"""

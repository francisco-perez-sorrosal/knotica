"""Search boundary -- ``SearchBackend`` protocol and the ripgrep backend.

Read-only full-text search over the vault, returning pointer results (topic,
path, snippet, score) with stateless opaque-cursor pagination. Never writes the
vault; no git/log/schema knowledge. Swappable behind the protocol (future
embedding-based backends).
"""

"""Storage boundary -- ``VaultStore`` protocol and the local filesystem backend.

Pure storage primitives: atomic temp+rename writes, reads, existence checks,
listing, deletion. Knows nothing about git, logs, schemas, or records; stdlib
only. Innermost layer: anything may depend on ``store``; ``store`` depends on
nothing else in knotica. Writing the vault through this package from outside
``knotica.core.transaction`` is forbidden (enforced by the import-boundary
fitness test).
"""

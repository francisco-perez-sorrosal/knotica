"""The vault's mutating operations -- the only callers of ``core.transaction``.

Each operation is thin choreography over :class:`~knotica.core.transaction.VaultTransaction`:
it validates its inputs, opens exactly one transaction, declares its writes, and
exits. The transaction owns lock/scrub/commit/rollback and the one-commit-per-
effective-mutation invariant; the operations own boundary validation, the index
upsert logic, and provenance construction. Adapters (MCP tools, CLI) call these
operations and never the transaction directly.

Every operation returns the shared result envelope (``ok`` / ``err`` from
:mod:`knotica.core.errors`): a success envelope carries a pointer plus any scrub
warnings; a failure envelope carries a typed error. Operations are
config-agnostic -- callers resolve the vault root per call and pass it in.
"""

from knotica.core.operations.create_topic import create_topic
from knotica.core.operations.curate_example import curate_example
from knotica.core.operations.guillotine import apply_guillotine, persist_guillotine_artifacts
from knotica.core.operations.migrate import migrate
from knotica.core.operations.store_source import store_source
from knotica.core.operations.write_page import write_page

__all__ = [
    "apply_guillotine",
    "create_topic",
    "curate_example",
    "migrate",
    "persist_guillotine_artifacts",
    "store_source",
    "write_page",
]

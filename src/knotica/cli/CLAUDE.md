# `cli/` — the `knotica` entry point

A self-registering registry. **`__init__.py::COMMAND_NAMES` is the single declaration of the subcommand set** — 15 of them: `init`, `desktop`, `mcp`, `doctor`, `status`, `migrate`, `prompt`, `guillotine`, `okf`, `eval`, `datasets`, `compile`, `loop`, `gapfill`, `service`. Read it there; never trust a copy in a doc or a test.

## Output discipline (`common.py`)

**stdout carries data, stderr carries every message.** A `--json` payload must land on stdout with nothing else mixed in, so a caller can pipe it. Informational lines, warnings, and errors all go to stderr.

Exit codes are the deterministic branch — never signal failure by printing:

| Code | Meaning |
|---|---|
| `EXIT_SUCCESS` (0) | Success; a check may have warned but nothing failed |
| `EXIT_ERROR` (1) | A check FAILED or the operation failed |
| `EXIT_MISUSE` (2) | Bad arguments (argparse emits this too) |
| `EXIT_NOT_CONFIGURED` (3) | No `config.toml` / vault — mirrors the tool-level `NOT_CONFIGURED` |
| `EXIT_MIGRATION_AVAILABLE` (4) | `migrate --check` only; up-to-date is `EXIT_SUCCESS` |
| `EXIT_NO_GOLDEN_SET` (5) | `eval` only; the topic has no golden set |

## Adding a subcommand

1. Add the name to `COMMAND_NAMES` and write the module beside its siblings.
2. Delegate to `core/` — the CLI is an adapter and owns no vault-write logic, exactly like `mcp_server/`.
3. Attach the shared flags — `parents=[common_parent()]` on a **top-level** parser, `parents=[common_parent(nested=True)]` on a **nested** one (`okf check`, `doctor repair`, `service status`, …). The asymmetry is load-bearing, not cosmetic: `nested=True` sets `default=argparse.SUPPRESS`, and without it argparse parses the subcommand into a fresh namespace with defaults applied and then copies every key onto the parent — silently clobbering a flag the user typed *before* the subcommand name. Plain `common_parent()` on a nested parser is how `knotica service --quiet status` came to parse as `quiet=False`. Do not "simplify" the two forms into one.
4. Route unconfigured state through the shared `unconfigured(console)` helper rather than inventing a message.
5. If the command spends money, gate it behind an explicit flag or confirmation — the CLI has no two-phase nonce, so the flag *is* the gate.

Hidden flags exist (`--fake-scalar` on `loop` is `argparse.SUPPRESS`-ed for testing). Keep test affordances suppressed rather than documented.

User-facing reference: [`docs/reference.md`](../../../docs/reference.md).

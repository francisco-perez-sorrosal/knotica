# `mcp_server/` — the FastMCP adapter

The package is named `mcp_server`, not `mcp`, to avoid shadowing the `mcp` SDK (`dec-009`). Keep it that way.

## The surface

25 flat conversational tools, 9 action dispatchers, and `open_dashboard` — 35 registrations. The dispatchers are `loop`, `branches`, `compile`, `datasets`, `arena`, `golden`, `notes`, `vault`, `vault_health`. Full action lists: [`docs/reference.md`](../../../docs/reference.md).

## Rules for every tool

- **Resolve config per call** through `vault_ctx.with_resolved_vault`. That seam *is* the concrete form of the stateless-server invariant — there is no session state to read.
- **Return an envelope, never raise.** `envelope.py` holds the read/write mapper split; house errors map to structured codes (`NOT_CONFIGURED`, `INVALID_ARGUMENT`, `INVALID_CURSOR`, …).
- **Delegate every mutation to `core.operations.*`.** This layer is an adapter; it owns no write logic.
- **Take `topic` and `vault` explicitly.** The one sanctioned exception is `read_protocol`, which resolves the default vault per call.
- The server must boot cleanly **unconfigured** — tools return "not configured", they do not crash.

## Adding a dispatcher action

1. Extend that dispatcher's `_ACTIONS` tuple — it is the single declaration, and the dispatcher validates `action` against it, returning `INVALID_ARGUMENT` for anything else.
2. Mutating actions take `mode=dry-run|apply`. Dry-run must be genuinely side-effect-free.
3. `dispatch_telemetry.py` logs one line per invocation and one per rejected action — keep new actions on that path.
4. If the action spends money, it is **two-phase**: a bare call mints a single-use nonce and returns a preview; only a second call passing that nonce as `confirm` executes. Nonces are per-action and the nonce file is deleted unconditionally on read, so one action's nonce can never confirm another's. Do not add a billed action without this.

## Billed actions

The rule above is not dispatcher-specific — `gapfill_discover` is a **flat** tool that spends, and takes the same two phases. Use `confirm_nonce.py` (mint / consume / TTL) rather than rolling a scheme per tool; it is the shared seam `loop action=run_eval`, `loop action=run_once`, and `gapfill_discover` all mint from, each under its own `kind`.

Phase 1 must be genuinely free. It may *construct* whatever it needs to quote an honest preview — `gapfill_discover` builds the discovery service to report `provider_configured` — but it must issue no request.

**A test that passes a valid `confirm` reaches the billing boundary.** `resolve_api_key` falls back to `./.env` after the process environment, so a search key does resolve under pytest on a maintainer's machine. Stub the service in any test that confirms; do not assume the absence of a key.

## Which tools call a model

`query` (flat), plus the dispatcher actions `compile action=run`, `datasets action=bootstrap|bootstrap_train`, and `loop action=run_eval|run_once`. All of them run **in the server process** — there is no subprocess boundary. Everything else on the surface is deterministic. The arena is not a model-calling path.

When you add a model-calling path, construct the client lazily so the lean launch (`uvx --from <root> knotica mcp`, no `evals` extra) still imports cleanly and fails with a typed `NOT_CONFIGURED` rather than an `ImportError`.

## Resources and prompts

Resources: `knotica://schema/root`, `knotica://schema/topic/{topic}`, `knotica://schema/resolved/{topic}`, `knotica://index`, plus the `ui://knotica/dashboard` MCP-App resource. Prompts (`ingest`, `query`, `lint`, `curate`) register **static names with lazily-resolved bodies** — the body comes from the vault's `.knotica/prompts/` at invocation time, which is what lets an unconfigured server still advertise them.

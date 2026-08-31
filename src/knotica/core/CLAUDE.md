# `core/` — vault semantics and the single mutation path

The most load-bearing rule in the repo lives here: **every vault mutation flows through one code path.**

## The single writer

`core/transaction.py::VaultTransaction` is the only caller of `store.write_text_atomic` / `delete` **codebase-wide**, and `tests/test_architecture_boundaries.py` fails the build if that stops being true. A transaction is one atomic unit: flock → buffered secret-scrubbed writes → atomic apply → `log.md` append → exactly one git commit, path-scoped.

Consequences worth internalizing before you write code here:

- **Never call the store directly.** If you need to write, open a transaction or call an existing operation.
- **A no-op transaction makes zero commits**, not an empty one. That is the one sanctioned exception to one-commit-per-op.
- **`log.md` cannot be written as a normal target** — `_normalize_write_path` raises. A full log re-render declares itself via `VaultTransaction.rewrite_log()`, which composes with the entry the transaction appends. `index.md` is likewise maintained by the tools, from the `index_entry` a caller passes.
- `vcs.commit_paths` scopes both the `add` and the `commit` to declared paths, so an operation never sweeps up a user's unrelated dirt. Preserve that when adding an operation.

## `operations/`

One module per mutating operation. Nine open exactly one `VaultTransaction` each — `write_page`, `store_source`, `create_topic`, `curate_example`, `migrate`, `guillotine`, `capture_note`, `reanchor_note`, `reflow_sources`. Two carry none of their own: `doctor_repair`, and `promote_note`, which delegates to `curate_example` or `gapfill.report_gap`. `candidate_scope.py` routes a write onto a candidate worktree instead of the live vault.

Adding an operation: put it here, give it exactly one transaction, and let the MCP and CLI adapters delegate to it. Adapters must not grow their own write logic.

## Two distinctions that are easy to conflate

- **Topic identity vs path classification.** `topics.py` answers "is this a topic? which topics exist?" against the store. `vault_layout.py` is its pure, store-free counterpart for classifying a *path*. Reach for the one that matches what you actually have.
- **Notes stay off scored surfaces by two independent mechanisms**: omission from `SCORED_FAMILIES`, *and* explicit filters at each point of use. They are not redundant and must not be collapsed into one — removing either silently contaminates eval.

## The loop cluster

`loop.py` plus its siblings (`loop_observe`, `loop_gap_redirect`, `loop_state`, `loop_heartbeat`, `loop_progress`, `loop_factory`, `loop_promote`, `loop_retry_backoff`, `loop_attempt`, `loop_cadence_config`, `arena`, `arena_resolve`, `candidate_gate`, `branch_namespaces`, `branch_scoreboard`, `branch_delete`, `best_effort`).

- **`loop_factory.build_loop_runner` is the one construction seam.** Both real call sites — the CLI watcher and the service daemon — go through it. When a knob has to reach every runner, resolve it *in the factory*, not at each call site: a caller that forgets is how documented config silently reaches nothing.
- **`branch_namespaces.py` declares every branch prefix once.** Never hardcode a `loop/...` string elsewhere.
- Loops always operate on a clone. A guard refuses to start if the clone destination resolves to the live vault root.

Mechanism narrative for humans: [`docs/self-improvement.md`](../../../docs/self-improvement.md).

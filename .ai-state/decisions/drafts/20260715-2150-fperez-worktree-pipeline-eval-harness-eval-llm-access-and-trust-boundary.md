---
id: dec-draft-8591febf
title: Eval LLM access — direct Messages API, pinned judge, and a new knotica-owned trust boundary
status: proposed
category: architectural
date: 2026-07-15
summary: The eval harness reaches Anthropic via the direct Messages API behind a BaselineRunner interface (baseline answerer) and a pinned Opus-class judge; it authenticates with a knotica-owned ANTHROPIC_API_KEY read from the environment only. This is the first knotica-owned LLM access — a new trust boundary distinct from client-as-brain, confined to the headless eval CLI and never on the MCP server launch path.
tags: [evals, phase-2, llm, anthropic, judge, trust-boundary, security, cost-accounting, auth]
made_by: agent
agent_type: systems-architect
branch: worktree-pipeline-eval-harness
pipeline_tier: standard
affected_files: [src/knotica/evals/llm.py, src/knotica/evals/runner.py, src/knotica/evals/judge.py, src/knotica/evals/cache.py, pyproject.toml]
affected_reqs: [REQ-RUN-01, REQ-RUN-02, REQ-RUN-03, REQ-JUDGE-01, REQ-JUDGE-02, REQ-JUDGE-03, REQ-JUDGE-04]
dissent: The Claude Agent SDK (SIA's own SDK, subscription-credit capable) would avoid provisioning a knotica-owned API key and align the eval runner with the Phase-3b SIA runtime, at the cost of coarse per-run cost accounting (a credit pool hides per-run USD) and lower determinism — a poor trade for an objective function whose whole point is a stable, cost-bearing scalar.
re_affirms: dec-draft-6ea4e4f3
---

## Context

The evaluator is the **first knotica-owned LLM access** (research: zero references to any LLM SDK anywhere in `src/knotica/`; `pyproject.toml` lists only `mcp>=1.28`). Two headless LLM roles are needed: a **baseline answerer** (runs the vault's query op to produce the thing being scored) and an **LLM-as-judge** (grades QA accuracy). The runner comparison (research Q5) weighed the direct Messages API (`anthropic` 0.116.0), the Claude Agent SDK (0.2.120), and `claude -p`. The deciding constraint is the cost-penalty term: an objective function whose scalar bears a token-cost discount cannot be built on a subscription credit-pool that hides per-run USD. Separately, this access needs credentials, which raises a trust-boundary question against the locked "server-side LLM access only Phase 3a+" invariant.

## Decision

**Runner + judge = direct Anthropic Messages API** (`anthropic>=0.116`) behind an injectable `LLMClient` protocol:

- **Baseline runner** (`evals/runner.py`): a `BaselineRunner` protocol with a `MessagesApiRunner` impl that drives the clone's own `query.md` prompt (via `core.prompts.get_prompt`) and in-process `search`/`read_page`, `temperature=0`, a pinned dated **Sonnet-class** worker snapshot (recommend Sonnet 4.6), capturing exact per-call `usage`. The `BaselineRunner` seam is the Phase-3a swap point (a compiled DSPy program replaces the impl behind the same protocol).
- **Judge** (`evals/judge.py`): reference-based grading, structured output bounded to `[0,1]`, N-sample median (default N=3, odd), `temperature=0`, a pinned dated **Opus-class** snapshot (recommend Opus 4.6 — same $5/$25 as newer Opus, previous tokenizer = maximally stable/available). Response cache (`evals/cache.py`) keyed on `(judge_snapshot, judge_prompt_hash, question, candidate, reference)`. The judge prompt is packaged harness code (hashed), **not** a vault prompt — a future SIA loop must not be able to edit the instrument it is measured against.

**Trust boundary (the load-bearing reading — CONFIRMED, not objected):** the PRE_PLAN invariant "server-side LLM access only Phase 3a+" constrains the **MCP server**. The eval harness is a **separate headless CLI process** that legitimately owns an `ANTHROPIC_API_KEY`. The key is read **from the environment only** — never from `config.toml`, never stored in the vault, never resolved on the `uvx --from ${CLAUDE_PLUGIN_ROOT} knotica mcp` launch path. Absent key → a clean "eval not configured" result before any network attempt (mirrors the `unconfigured` contract). The production query op stays client-as-brain on the user's own subscription; the eval baseline runner is a distinct path with its own key. **No invariant conflict.**

Pin exact **dated** snapshots (never a floating alias); fetch the exact strings from the live model list at build time (the researcher confirmed lineup/pricing 2026-07-15 but not dated snapshot IDs — the `claude-ecosystem` snapshot lags live). Never hand-convert token counts across models (tokenizer differences); always use each call's own `usage`.

## Considered Options

### Option A — direct Messages API behind BaselineRunner + pinned judge (chosen)
- **Pros:** exact per-call `usage` → faithful `cost_usd`/token term; `temperature=0` + pinned snapshot → best determinism; cleanest Phase-3a swap (DSPy calls the same API); minimal dependency (`anthropic` only, isolated); the API-key trust boundary is anticipated by the brief.
- **Cons:** requires provisioning a knotica-owned API key (a new secret to manage); the eval path diverges from the SIA runtime's SDK.

### Option B — Claude Agent SDK (subscription-credit capable)
- **Pros:** avoids a knotica-owned key when a subscription is present; aligns with SIA's own SDK for Phase 3b.
- **Cons:** usage bundled across turns/sub-agents; subscription = credit pool with no clean per-run USD → the cost term loses fidelity; agentic multi-turn control lowers determinism; heavy (bundles the Node Claude Code CLI).

### Option C — `claude -p` headless CLI
- **Pros:** trivially available where Claude Code is installed; subscription-credit capable.
- **Cons:** coarse usage accounting (bundles turns); external Node process; agentic and opaque — the worst determinism/accounting for an objective function.

## Consequences

- **Positive:** the scalar's cost term is faithful and reproducible; the Phase-3a DSPy swap is a drop-in behind `BaselineRunner`; the judge is cache-stable on a frozen corpus; the trust boundary is explicit, minimal, and env-scoped; the judge instrument is unforgeable by the optimizer.
- **Negative:** knotica must provision and document a knotica-owned API key for eval; a second LLM-access convention (SDK) will exist in Phase 3b (SIA) — acceptable, they are different runtimes; the exact snapshot strings are a build-time lookup, not a memorized constant.

## Disconfirmation

- **Falsifier:** if per-run cost accounting turns out not to matter for keep/discard (e.g. the token-cost term is dropped from the scalar), the primary rationale for the Messages API over the subscription-credit SDKs collapses, and Option B's key-avoidance + SIA-alignment would win.
- **Steelmanned runner-up (Option B):** Phase 3b runs on the Claude Agent SDK regardless; using it for the eval runner too would mean one LLM-access convention across Phases 2–3b, no knotica-owned key to provision or secure, and — where a subscription credit exists — zero marginal API cost. If the cost term proves unnecessary or is computed from `usage` the SDK still exposes, the fidelity objection weakens and the single-runtime simplicity dominates.
- **Reversal trigger:** revisit if (a) the token-cost term is removed or can be computed faithfully from the Agent SDK's usage, or (b) maintaining a knotica-owned key becomes a real operational burden, or (c) Phase-3b forces the eval runner onto the SIA SDK anyway (collapsing the two conventions).

## Prior Decision

Re-affirms `dec-draft-6ea4e4f3` (MCP SDK = official `mcp`, cold-start-minimal) in spirit: that decision keeps the *server* dependency env minimal; this decision keeps the new LLM dependency **out of the server env entirely** (isolated to the `evals` group per `dec-draft-c2ad09bc`), so the cold-start rationale of 6ea4e4f3 is preserved, not eroded, by adding `anthropic`.

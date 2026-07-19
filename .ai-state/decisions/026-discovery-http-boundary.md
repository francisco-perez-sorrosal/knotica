---
id: dec-026
title: Discovery HTTP boundary — direct httpx REST, zero provider SDKs, env-only keys
status: accepted
category: architectural
date: 2026-07-19
summary: The discovery layer reaches every provider (Exa, you.com, OpenAlex) via direct httpx REST behind one thin shared client, adopting no provider SDK; exa-py is rejected because it drags openai + python-dotenv. Credentials resolve from the environment only, at use time, failing before the network — mirroring evals/llm.py.
tags: [gapfill, discovery, http, dependencies, httpx, sdk, trust-boundary, security, cold-start, env-credentials, phase-p2]
made_by: agent
agent_type: systems-architect
branch: worktree-hackathon-loop-ideas
pipeline_tier: standard
affected_files:
  - src/knotica/discovery/http.py
  - src/knotica/discovery/exa.py
  - src/knotica/discovery/youcom.py
  - src/knotica/discovery/openalex.py
  - src/knotica/discovery/config.py
  - pyproject.toml
dissent: Adopting only pyalex (requests-only, lightweight) for OpenAlex would give free pagination/backoff for the scholarly enricher without the openai bloat that sinks exa-py.
---

## Context

The discovery layer calls three external HTTP APIs. The knotica invariants that bear on how: (1) every dependency
must earn its place; (2) the MCP server cold-start path must stay lean (dec-013 isolates `anthropic`/`dspy` off the
wheel for exactly this reason); (3) credentials are env-only, never in `config.toml`, the vault, logs, or errors, and
resolution fails before the network (the `evals/llm.py` trust-boundary discipline). The obvious path — official
provider SDKs — was checked against these. `exa-py` 2.16.0 declares (verified on PyPI 2026-07-19):
`httpcore, httpx, openai>=1.48, pydantic, python-dotenv, requests, typing-extensions`. Adopting it pulls the entire
OpenAI SDK into knotica for a single `POST /search`. `httpx` 0.28.1 is already resolved as a **hard transitive
dependency of `mcp` 1.28** (a direct runtime dep).

## Decision

Reach every provider via **direct `httpx` REST** behind one thin shared client wrapper (`discovery/http.py`): auth
header injection, timeouts, bounded retry with exponential backoff honoring `Retry-After` and each API's rate-limit
headers, and credential handling that never logs the key. Adopt **no provider SDK** — not `exa-py`, not `pyalex`.
This adds **no new dependency** (httpx is already present). Endpoints: Exa `POST https://api.exa.ai/search`
(`x-api-key`); you.com Search REST (bearer); OpenAlex `GET /works` (keyless + `mailto` polite pool). API keys resolve
from the environment only (`KNOTICA_EXA_API_KEY`, `KNOTICA_YOUCOM_API_KEY`), **at use time**, raising a typed
`NOT_CONFIGURED` `KnoticaError` naming the exact variable **before** any HTTP client is constructed or socket opened.
`httpx` is imported lazily inside client construction so `import knotica.discovery` succeeds in the base environment,
and `mcp_server` must not transitively import `discovery` (extends the import-boundary fitness test).

## Considered Options

### Option 1 — Official provider SDKs (exa-py + pyalex)
- Pros: typed models, maintained pagination/backoff, less wire-mapping code.
- Cons: `exa-py` drags `openai` + `python-dotenv` + `requests` — a large, unrelated surface on the install path,
  directly against the dec-013 cold-start posture; two SDKs means two HTTP strategies to reason about.

### Option 2 — Direct httpx REST, one shared client (chosen)
- Pros: zero new dependencies (httpx already transitive via mcp); one HTTP strategy for all three providers; full
  control over retry/rate-limit to honor each API's documented convention; every adapter fakeable via a fake httpx
  transport + captured fixtures; credential discipline identical to `evals/llm.py`.
- Cons: we hand-maintain the wire→`SourceCandidate` mapping (small — few stable fields consumed per provider);
  relying on httpx as a transitive rather than a declared dependency.

## Consequences

- Positive: no dependency bloat; cold-start posture preserved; uniform, testable HTTP + credential handling; the
  response subsets consumed are small and stable, so hand-mapping is cheap.
- Negative: transitive reliance on httpx (mitigated: hard transitive of mcp; optional follow-up to pin it in a
  PEP-735 `[dependency-groups] gapfill` group — a no-op install since already resolved); no SDK safety net if a
  provider changes its wire shape (mitigated: one pure parse fn per adapter + a fixture test flags drift fast).
- Neutral: MVP makes no `pyproject.toml` change at all, avoiding a merge collision with the parallel P1 pipeline.

## Disconfirmation

- **Falsifier:** If a provider's response shape were volatile enough that an SDK's maintained models measurably
  reduced breakage, or if OpenAlex pagination/backoff grew genuinely complex, the SDK cost would be justified.
- **Steelmanned runner-up:** Adopt **only** `pyalex` (requests-only, lightweight — no openai bloat) for the OpenAlex
  enricher, keeping direct httpx for Exa/you.com. This buys free scholarly pagination/backoff for the one provider
  whose queries are richest, at a small, contained dependency cost.
- **Reversal trigger:** If the OpenAlex enricher's pagination/rate-limit/backoff logic in `openalex.py` exceeds what
  the shared `http.py` wrapper handles cleanly, adopt `pyalex` for the enricher only and re-evaluate this decision.

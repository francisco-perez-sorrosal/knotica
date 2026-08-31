# Knotica

AI-maintained, compounding knowledge wiki (Karpathy's llm-wiki pattern) living in an Obsidian vault, with per-topic self-improving agents: DSPy optimizes operation prompts (inner loop), SIA evolves schemas/structure (outer loop). No model weights are ever modified — the system's "weights" are its schemas and prompts.

> Process — tiers, delegation, the behavioral contract, ADR and git conventions, `.ai-work/` vs `.ai-state/` — comes from Praxion's always-loaded rules and is deliberately **not** restated here. This file carries only what is true of *this* repo.

## Canonical design

**[`.ai-state/DESIGN.md`](.ai-state/DESIGN.md) is the design canon** (invariants, rationale, design target). **[`docs/architecture.md`](docs/architecture.md) is the code-verified developer guide** (what exists now). Read the guide for current state, DESIGN for intent; update both when a structural change lands — `make verify` gates their package inventory and every `src/knotica/...` path they cite. The original pre-plan is archived at `.ai-state/design-history/PRE_PLAN.md`: historical only, never cite it as current.

Invariants — do not violate without updating `DESIGN.md` first:

- **Client-as-brain**: the MCP server exposes deterministic tools; the client's LLM does the cognitive work (ingest/query/lint) guided by vault schemas. `query` is the one *flat* tool that calls a server-side LLM; the dispatchers `compile action=run`, `datasets action=bootstrap|bootstrap_train`, and `loop action=run_eval|run_once` also call one **in the server process**, each behind a gate or two-phase confirm. The arena is not one of them — its only wired scorer is a keyword heuristic.
- **Stateless server**: no session state — the vault (git) and `~/.config/knotica/config.toml` are the only durable state, resolved per tool call. Topic is always an explicit argument, and every vault-touching tool takes an explicit `vault` (the one exception, `read_protocol`, resolves the default vault per call). The loop *daemon*'s gitignored markers under `.knotica/locks/` are outside this scope, not an exception to it — scoped once in [`DESIGN.md` § 7](.ai-state/DESIGN.md).
- **The vault is data, this repo is code**: the wiki is a separate git repo at a user-configured path. Several named vaults may be configured; the active one is switchable at runtime. Never hardcode a vault path — all access goes through `VaultStore`.
- **One git commit per mutating vault operation**, flock-guarded. A no-op transaction makes zero commits rather than an empty one.
- **Loops always work on a git clone, never the live vault**; results return as branches for human review.
- **Single source of truth for prompts**: operation prompts live in the vault (`.knotica/prompts/`, root defaults + earned topic overrides) and are simultaneously the MCP-prompt UX surface and the DSPy/SIA-evolvable substrate.

## Lane vocabulary

The six process lanes — `home`, `learn`, `answer`, `improve`, `fill`, `tend` — are declared once in `src/knotica/core/process_model.py`; every surface (MCP dispatchers, CLI groups, dashboard rails, slash-command descriptions) is a projection of that declaration.

- **Marker convention** — the identifier sense of a lane name is always written in a backtick span (`fill`, `tend`); the ordinary English word never is. All six are common words, so without this rule no reader or gate can tell a lane reference from a sentence.
- **Home / Tend / Improve discriminator** — *Home is cross-topic and actionable; Tend is mechanical and per-vault; Improve is measured and per-topic.* All three answer the same question shape; point here rather than restating the boundary.

## Project conventions

- Python 3.12+, **uv-managed** (`uv sync --extra evals`, `uv run`); src layout under `src/knotica/`.
- Dual-role repo: Python package + a Claude plugin (`.claude-plugin/`, `commands/`, `hooks/`, `skills/`, `.mcp.json`). The repo is **not** a marketplace — it ships *through* the external `francisco-perez-sorrosal/bit-agora` marketplace.
- MCP server on FastMCP; CLI entry point `knotica`. The subcommand set is declared once, in `src/knotica/cli/__init__.py::COMMAND_NAMES` — read it there rather than trusting a copy.
- Build/tooling output to `/dev/null` or `tmp/` — never commit artifacts.

Phases 0–4 ship locally (vault template, core/MCP/plugin, eval harness, DSPy compile, dashboard, the autonomous loop, gap-fill, the notes overlay, the Memory Guillotine). Phase 5 (remote/Railway) is gated on local smoothness.

## Where to look

Each directory below carries its own `CLAUDE.md` with the conventions that apply inside it — they load when you work there, so this index stays short.

| Working on | Directory guide | User-facing doc |
|---|---|---|
| Vault semantics, the single mutation path, the loop | [`src/knotica/core/`](src/knotica/core/CLAUDE.md) | [self-improvement](docs/self-improvement.md) |
| MCP tools, dispatchers, resources, prompts | [`src/knotica/mcp_server/`](src/knotica/mcp_server/CLAUDE.md) | [reference](docs/reference.md) |
| CLI subcommands and flags | [`src/knotica/cli/`](src/knotica/cli/CLAUDE.md) | [reference](docs/reference.md) |
| The web UI | [`dashboard/`](dashboard/CLAUDE.md) | [dashboard](docs/dashboard.md) |
| Plugin slash commands | [`commands/`](commands/CLAUDE.md) | [install](docs/install.md) |
| Tests | [`tests/`](tests/CLAUDE.md) | — |

Feature docs not tied to one directory: [gap-fill](docs/gap-fill.md) · [notes](docs/notes.md) · [guillotine](docs/guillotine.md) · [configuration](docs/configuration.md) · [tutorial](docs/tutorial.md) · [new knowledge base](docs/new-knowledge-base.md) · [OKF](docs/okf.md) · [Claude Desktop](docs/CLAUDE_DESKTOP.md).

## Verification

`make verify` is the canonical chain, in order: topology check → ADR health → architecture coverage → mypy → the full suite → `ruff check` → `ruff format --check`. Run it before every commit and fix at each step — the three leading checks are cheap, and a failure there makes everything below it untrustworthy.

For the inner loop, `make test-groups` lists the groups and `make test-group GROUP=<id>` runs one in seconds. Membership derives from [`.ai-state/TEST_TOPOLOGY.md`](.ai-state/TEST_TOPOLOGY.md), never restated elsewhere.

## Traps that have bitten

- **Adding or removing a module fails `make verify`** until `DESIGN.md` § 3a's package inventory matches. That is the gate working, not a spurious failure.
- **Never point a finalized ADR at a `dec-draft-<hash>` id** — the ADR health check rejects it. Drafts are *not* gated on numbering, so a hand-numbered draft passes and collides at finalize.
- **`uv sync --group evals` is a hard error.** Use `--extra evals`. A test fails the build on any surviving instruction; a deliberate migration note must carry an `allow-stale-invocation` HTML comment.
- **Quote shell extras** — `'.[evals]'`, not `.[evals]`. Unquoted brackets are a zsh glob.
- **Never bump a version by hand.** `cz bump`, dispatched through `.github/workflows/release.yml`, is the only mechanism that changes one — see [CONTRIBUTING.md](CONTRIBUTING.md#releases). A bump must move `uv.lock` with `pyproject.toml`, because CI runs `uv sync --locked`; Commitizen's `uv` version provider is what does that, and swapping it for the obvious-looking `pep621` turns `main` red on the release commit itself.
- **The dashboard's built artifact is committed.** CI rebuilds and `git diff --exit-code`s it, so a source change without a rebuild fails.
- **Docs are ground-truthed against code, not against other docs.** State defaults explicitly; a flag documented without its default is half-documented.

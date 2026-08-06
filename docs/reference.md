# Command & Tool Reference

The complete, dense lookup for every command and tool knotica ships: the `knotica` CLI, the MCP
tool surface, MCP resources and prompts, the `/knotica:*` plugin aliases, the vault's on-disk
layout, and the error codes a caller can actually hit. For a guided first run, see
[tutorial](tutorial.md); for `config.toml` and env vars, see [configuration](configuration.md).

## Table of contents

- [CLI](#cli)
- [MCP tools](#mcp-tools)
- [MCP resources and prompts](#mcp-resources-and-prompts)
- [Plugin aliases](#plugin-aliases)
- [Vault layout](#vault-layout)
- [Error envelope](#error-envelope)

## CLI

`knotica --version` prints the package version and exits 0. Bare `knotica` (no subcommand)
prints help to stderr and exits `2`. `knotica <cmd> --help` prints argparse help on stdout and
exits 0. The 15 registered subcommands, in help-listing order:

```
init, desktop, mcp, doctor, status, migrate, prompt, guillotine, okf,
eval, datasets, compile, loop, gapfill, service
```

> [!IMPORTANT]
> **stdout carries data only; stderr carries every message** (info, warnings, errors, debug,
> progress). Scripts that parse `knotica` output must read stdout, never stderr. Three exceptions:
> `guillotine`'s human-readable report (not `--json`) prints to stderr, and neither `loop` nor `okf`
> writes to stdout at all — neither has a `--json` mode.

### Exit codes

| Code | Constant | Meaning |
|---|---|---|
| 0 | `EXIT_SUCCESS` | Success — a check may have WARNed but nothing FAILed. |
| 1 | `EXIT_ERROR` | A check FAILED or the operation failed. |
| 2 | `EXIT_MISUSE` | Bad arguments — argparse also emits this. |
| 3 | `EXIT_NOT_CONFIGURED` | No `config.toml` / vault resolved. |
| 4 | `EXIT_MIGRATION_AVAILABLE` | `migrate --check` only; up-to-date is `EXIT_SUCCESS`. |
| 5 | `EXIT_NO_GOLDEN_SET` | `eval` only: topic has no golden set — run `eval --bootstrap` first. |

`guillotine` reuses the small integers as **command-local** labels: `EXIT_CLAIM_NOT_FOUND = 1`,
`EXIT_PATCH_FAILED = 3`, `EXIT_APPLY_FAILED = 4` — same values, guillotine-specific meaning.

The unconfigured message is byte-identical everywhere it appears:

```
knotica is not configured — run `/knotica:setup` (Claude Code) or `knotica init` (CLI).
```

### Flags common to every top-level subcommand

| Flag | Default | Meaning |
|---|---|---|
| `--quiet` / `-q` | off | Suppress info lines (warnings/errors still print). |
| `--verbose` / `-v` | off | Emit debug context to stderr. |
| `--no-color` | off | Disable color (also auto-off when not a TTY, `NO_COLOR` set, or `TERM=dumb`). |
| `--no-input` | off | Never prompt; fail fast if required input is missing. |

All 15 top-level parsers take these four, and so does every nested subcommand, in either position —
`knotica okf --quiet check` and `knotica okf check --quiet` mean the same thing.

`--json` is **not** common — each of these adds its own: `doctor`, `doctor repair`, `status`,
`migrate`, `guillotine`, `eval`, `datasets bootstrap-train`, `datasets freeze`, `compile`,
`compile promote`, `service status`.

### Subcommands

| Command | Mutates | Flags (default) | Notes |
|---|---|---|---|
| `init` | Yes | `--yes` off<br>`--vault PATH` prompts, or `~/dev/data/knotica` under `--yes`<br>`--topic NAME` none<br>`--remote {none,gh-private}` `none`<br>`--desktop` off | Setup wizard: scaffold template → `git init` + commit → optional GH remote → write `config.toml` → register MCP server → optional Desktop patch → warm the launch tool. Interactive prompts fire only when not `--yes`/`--no-input` and stdin is a TTY. Env overrides: `KNOTICA_DESKTOP_CONFIG`, `KNOTICA_MCP_FROM`. |
| `desktop install` | Yes | (common only) | Patches (or creates) the Desktop config's `mcpServers.knotica` entry — additive, `.bak` backup, every other server preserved, `env` carried over untouched. |
| `desktop status` | No | (common only) | Read-only. Prints `command`, `args`, `env` (names only, never values). |
| `mcp` | Serves | `--vault NAME` none<br>`--http` off<br>`--host HOST` `127.0.0.1`<br>`--port PORT` `8765` | Serves the MCP tool surface. Stdio mode blocks until disconnect; stdout is the JSON-RPC channel — no diagnostic byte reaches it. `--http` needs the server's HTTP extra (`uvicorn`). |
| `doctor` | No | `--quick` off<br>`--json` off<br>`--fix` off (read-only guidance) | Deterministic mechanical checks — never invokes an LLM. Exit `1` on any FAIL row. |
| `doctor repair` | Yes | `--dry-run` / `--apply` mutually exclusive, required<br>`--paths PATH...` required with `--apply` unless `--all-tracked`<br>`--all-tracked` off<br>`--delete-untracked` off<br>`--json` off | Path-scoped restore to HEAD, vault-lock guarded. Never `git restore .`. |
| `status` | No | `--json` off<br>`--topic NAME` none<br>`--wide` off<br>`--nudge` off | Pure read: pages/curated/unpushed counts per topic, last lint. |
| `migrate` | Yes | `--check` off (exit-code only, never writes)<br>`--dry-run` off<br>`--yes` off<br>`--json` off<br>`--topic NAME` none = whole vault | Three-way template-diff migration through one `VaultTransaction`; preserves files the vault has evolved past the template (warns, never overwrites). |
| `prompt <operation>` | No | `operation` positional, required (`ingest`\|`query`\|`lint`\|`curate`)<br>`--topic NAME` `""`<br>`--source`, `--question`, `--verdict` parity-only, not consumed by this adapter | Renders the vault-resolved operation prompt body verbatim to stdout — byte-identical with the MCP `prompts/get` handler. |
| `guillotine <claim>` | Dry-run by default | `claim` positional, required<br>`--topic NAME` required<br>`--dry-run` / `--no-dry-run` default `True`<br>`--apply` off, implies not-dry-run<br>`--verdict NAME` none = recommended (`keep`\|`qualify`\|`demote`\|`dispute`\|`retract`\|`quarantine_source`\|`delete_unsupported_synthesis`)<br>`--json` off<br>`--include-sources` / `--no-include-sources` default `True`<br>`--include-reports` off<br>`--max-results N` `50`<br>`--out PATH` reserved, not yet implemented | Claim-level memory audit + reversible retraction. `--apply` commits the verdict as applied and files a re-grounding gap — **no wiki page content is edited**. |
| `okf check` | No | `--strict` off<br>`--export-ready` off | OKF compatibility check; all output via stderr (payload-on-stderr, like `guillotine`). |
| `okf export` | Yes | `--output PATH` / `-o` required<br>`--pure` off<br>`--link-style {bundle-relative,relative}` `bundle-relative`<br>`--lossy-embeds` off<br>`--force` off<br>`--export-ready` off | Writes a portable OKF bundle to `--output`. |
| `okf repair` | Yes | `--dry-run` / `--apply` mutually exclusive, required<br>`--force` off | One git commit on `--apply`; relocates old reports, skips dirty/uncommitted files. |
| `eval` | Yes (clone's `metrics.jsonl` only) | `--topic NAME` required<br>`--ref COMMIT` source vault's HEAD<br>`--bootstrap` off (stages golden candidates, does not freeze)<br>`--json` off<br>`--max-total-tokens N` packaged ceiling<br>`--max-usd USD` packaged ceiling<br>`--judge-snapshot MODEL` pinned default<br>`--worker-snapshot MODEL` pinned default<br>`--n-judge-samples N` harness default, must be odd<br>`--num-threads N` `4` | Headless per-topic eval harness. Clones the source vault at a pinned SHA — **never touches the live vault**. Credential: `CLAUDE_CODE_OAUTH_TOKEN` preferred, falls back to `ANTHROPIC_API_KEY` (warns on fallback). |
| `datasets bootstrap-train` | Yes | `--topic NAME` required<br>`--target N` `30`<br>`--json` off | LLM-synthesizes seeded QA pairs from the topic's own pages, appends to `qa.jsonl`. |
| `datasets freeze` | Yes | `--topic NAME` required<br>`--json` off | Promotes reviewed candidates into `golden.jsonl` + `MANIFEST.json`, one commit. Refuses questions overlapping the trainset. |
| `compile` | Yes | `--topic NAME` required for the top-level run (checked manually)<br>`--json` off<br>`--no-mipro` off — skip MIPROv2, write a bootstrap artifact instead | Clone → gate on ≥30 query-train examples + a held-out golden set → MIPROv2 optimize → write `<topic>/.knotica/compiled/` → return a branch for human review. No auto-merge. |
| `compile promote` | Yes on `--apply` | `--topic NAME` required<br>`--branch NAME` required<br>`--json` off<br>`--dry-run` / `--apply` mutually exclusive, required | Human gate: merges `compile/<topic>/<sha>` into the default branch with `--no-ff`, vault-lock guarded. Refuses arbitrary branch names and dirty trees. |
| `loop` | Yes | `--topic NAME` required<br>`--vault PATH` config's resolved vault<br>`--once` off<br>`--set-baseline SCALAR`<br>`--baseline-policy {latest,best}`<br>`--rebaseline {best,latest}`<br>`--mark-observed` off<br>`--interval SECONDS` `5.0`<br>`--eval-threads N` `4`<br>`--observe-quiet SECONDS` `20.0`<br>`--push REMOTE` none<br>`--no-arena` off<br>`--no-observe` off<br>`--branch-prefix` `DEFAULT_BRANCH_PREFIX`<br>`--arena-variants JSON` none = generated variants | The self-improvement watcher (observe → gate → heal). No mode flag ⇒ watch forever (the default; there is no `--watch` flag); the five mode flags above are mutually exclusive with each other. **Never writes to stdout** — no `--json` exists here. |
| `gapfill discover` | Yes (clean no-op without a search provider) | `--topic NAME` required<br>`--vault PATH` config's resolved vault<br>`--max-gaps N` none = all | Drains open `genuine_gap` records through the configured search provider + OpenAlex, stages ranked candidates in `suggestions.jsonl`. When no `KNOTICA_YOUCOM_API_KEY` resolves — process environment first, then `./.env`, then `~/.config/knotica/.env` — prints a no-op notice and exits `0`. |
| `service install` | Yes (idempotent) | `--vault NAME` configured default<br>`--dry-run` off | Installs the OS-managed loop daemon (launchd macOS, systemd Linux — "code-complete but untested"). Rolls back the partial unit on failure. **Never runs automatically** — a live install is something you run yourself. |
| `service uninstall` | Yes | `--dry-run` off | Clean no-op if nothing installed. |
| `service status` | No | `--vault NAME` configured default<br>`--json` off | Read-only: install state + per-topic runner liveness via the heartbeat convention. |

## MCP tools

33 tools are registered on the server: 9 action-parameterized **dispatchers** and 24 flat,
fixed-behavior tools (5 read + 4 write + 15 grouped by purpose below). Every tool accepts a
`vault: str = ""` parameter (targets a configured vault by name; empty = the active one) — the two
exceptions are the `vault` dispatcher itself (no vault to target before one resolves) and
`read_protocol`, whose prompt body resolves from the active vault only.

> [!NOTE]
> All four write tools (`write_page`, `store_source`, `create_topic`, `curate_example`) and every
> mutating dispatcher action carry the same instruction: never call from detection alone — only
> after the user has explicitly confirmed the write.

### Read tools — 5, zero commits

| Tool | Params | Returns |
|---|---|---|
| `list_topics` | `vault=""` | `{topics: [{name, page_count}, ...]}` |
| `read_page` | `topic` (req), `page` (req), `vault=""` | `{topic, path, frontmatter, frontmatter_error, body, content}` |
| `search` | `query` (req), `topic=""`, `cursor=""`, `limit=10`, `families=[]`, `vault=""` | `{results, next_cursor, has_more, total_count}` |
| `list_links` | `topic` (req), `page` (req), `direction="both"`, `vault=""` | `{page, direction, out?, in?}` |
| `lint_check` | `topic=""`, `vault=""` | `{violations: [...]}` — empty list is clean, not an error |

`search`'s `families` values: `page`, `source`, `note`. Empty defaults to `page`+`source` (never
`note` — notes are private marginalia). `limit` caps at `50`; default `10`. `families` must stay
identical across a paginated walk or the cursor is invalidated.

### Write tools — 4, one git commit each

| Tool | Params | Semantics |
|---|---|---|
| `write_page` | `topic`, `page`, `content`, `summary` (req); `index_entry=""`, `candidate=""`, `vault=""` | Create/replace atomically. Idempotent (`changed=false`, no commit, on identical content). `RESERVED_NAME` when the page's basename is any of the eight [reserved top-level names](#reserved-top-level-names). |
| `store_source` | `topic`, `citation_key`, `title`, `content`, `source_url` (req); `source_type="markdown"`, `candidate=""`, `vault=""` | Persists under `sources/<topic>/`. Immutable: same content re-submit is a no-op; different content, same key → `SOURCE_EXISTS`. |
| `create_topic` | `topic` (req); `description=""`, `vault=""` | Creates topic dir, empty `SCHEMA.md` overlay, `.knotica/` state. Idempotent (`existed=true`, no commit). |
| `curate_example` | `topic`, `query`, `answer`, `verdict` (req); `pages_used=None`, `notes=""`, `vault=""` | Appends one example to `qa.jsonl`. Idempotent by content hash. |

### Other flat tools — 15, grouped by purpose

| Tool | Params | Notes |
|---|---|---|
| `query` | `question`, `topic` (req), `vault=""` | The one wiki-answer tool; grounded pages + citations. Read-only. |
| `wiki_status` | `topic=""`, `vault=""`, `view="summary"` | `view="scope"` is the cheapest read (topic names + totals) for conversational routing. |
| `metrics_read` | `topic` (req), `limit=100`, `before_generation=None`, `vault=""` | Ascending-generation window of `metrics.jsonl`. `limit` capped at `1000`. |
| `baseline_probe` | `topic` (req), `vault=""` | Persists a naive cold-start anchor (scalar `0.0`) — chart-floor only, not gate-quality. |
| `prompt_diff` | `topic` (req); `branch=""`, `base_ref=""`, `head_ref=""`, `history_id=""`, `mode="git"`, `vault=""` | `mode=git` diffs `query.md` across a branch or commit; `mode=compiled` diffs vault `query.md` vs the compiled artifact. |
| `suggestions_read` | `topic` (req); `status="pending"`, `cursor=""`, `limit=20`, `vault=""` | Paginated gap-fill queue. `status` ∈ `pending`\|`approved`\|`rejected`\|`deferred`\|`ingested`\|`all`. `limit` max `50`. |
| `suggestions_review` | `topic`, `suggestion_id`, `action` (req); `mode="dry-run"`, `reason=""`, `vault=""` | `action` ∈ `approve`\|`reject`\|`defer`\|`mark_ingested`. `reject` requires a non-empty `reason`. |
| `gap_report` | `topic`, `question` (req); `reason=""`, `reference_pages=None`, `vault=""` | Files a conversationally-reported gap (`origin=reported`). Dedups on repeat identical question. |
| `gaps_read` | `topic` (req); `status="open"`, `cursor=""`, `limit=20`, `vault=""` | Paginated P1 gap queue — the stage *before* sources exist. `status` ∈ `open`\|`resolved`\|`dismissed`\|`all` (here `all` means all three, unlike `suggestions_read`). `limit` max `50`. Returns `origin_counts` alongside `status_counts`. |
| `source_ingest_open` | `topic`, `suggestion_id` (req), `vault=""` | Opens/resumes a private candidate context for one approved suggestion. Idempotent (same handle on reopen). |
| `source_ingest_submit` | `topic`, `suggestion_id` (req); `mode="dry-run"`, `vault=""` | Finalizes candidate ingest, drives the loop gate synchronously. `mode=apply` returns `merged`\|`refused`\|`blocked`. |
| `note_capture` | `topic`, `note` (req); `quote=""`, `pages=[]`, `intent="reflection"`, `tags=[]`, `vault=""` | Writes under `notes/<topic>/`, never a wiki page. A weak/unprovable anchor degrades to `ANCHOR_DEGRADED`, never fails. |
| `ingest_progress` | `topic`, `stage`, `title` (req); `status="info"`, `detail=""`, `run_id=""`, `citation_key=""`, `vault=""` | Best-effort journal append (**not** a git commit) for the dashboard Ingest pane. |
| `ingest_activity_read` | `topic=""`, `run_id=""`, `limit=120`, `vault=""` | Reads recent ingest-activity events for the dashboard. Read-only. |
| `read_protocol` | `operation` (req, `ingest`\|`query`\|`lint`\|`curate`), `topic=""` | Returns the operation prompt body as a tool result — closes the gap for hosts without MCP-prompt support. |
| `open_dashboard` | `topic="agentic-systems"`, `vault=""` | See [dashboard](dashboard.md). Falls back to a `TextContent` URL on hosts without MCP Apps support. |

### Action dispatchers — 9

Every dispatcher validates `action` against a fixed tuple; an unrecognized action raises
`INVALID_ARGUMENT` and is recorded as mis-selection telemetry.

| Dispatcher | Actions | `mode=` dry-run/apply? | Params beyond `action`/`topic`/`vault` |
|---|---|---|---|
| `arena` | `status`, `history` | No — all read-only | `limit=20` |
| `branches` | `scoreboard`, `promote_loop`, `promote`, `delete` | Yes, on all but `scoreboard` | `branch=""`, `kind=""` |
| `compile` | `run`, `status`, `promote` | Yes, on `promote` | `branch=""`, `use_mipro=True` |
| `datasets` | `inventory`, `records`, `bootstrap`, `bootstrap_train`, `freeze` | No | `role=""`, `limit=200`, `target=30` |
| `golden` | `load`, `save` | No | `accepted_json=""` |
| `loop` | `run_once`, `set_baseline`, `baseline_policy`, `rebaseline`, `cadence`, `run_eval` | No (nonce-confirmed instead — see below) | `scalar`, `policy=""`, `mode="best"`, `eval_min_interval_hours`, `eval_window`, `eval_num_threads`, `confirm=""`, `num_threads` |
| `notes` | `list`, `read`, `drift`, `reanchor`, `detach`, `promote`, `archive` | Yes, on the 4 mutating actions | `note_id=""`, `intent="all"`, `status="all"`, `cursor=""`, `limit=20`, `anchor=0`, `page=""`, `quote=""`, `target="trainset"`, `question=""`, `answer=""`, `verdict="good"` |
| `vault` | `list`, `status`, `use`, `add`, `create` | No | `name=""` (letters, digits, `-`, `_` only), `path=""`, `make_default=False` — **no `vault` param**; this dispatcher IS the vault-selection surface |
| `vault_health` | `doctor`, `repair`, `okf_check`, `okf_repair`, `lint`, `metadata_tree` | Yes, on `repair`/`okf_repair` | `quick=False`, `fix=False`, `paths_json="[]"`, `all_tracked=False`, `delete_untracked=False`, `strict=False`, `force=False` |

**`loop`'s `run_once` and `run_eval` are two-phase billed.** Call once with no `confirm` to
mint a single-use nonce and see a cost preview (`ttl=300` seconds) — nothing is billed. Call again
passing that nonce as `confirm` to execute and bill. Nonces are keyed by `(kind, topic)` — a
`run_eval` nonce cannot confirm a `run_once` call, or vice versa. No other dispatcher or tool uses
this mechanism.

**`notes`** is the most complex dispatcher. `list`/`read`/`drift` are always read-only regardless
of `mode`. `promote` is the only action writing outside the notes layer: `target=trainset`
(curated example, needs ≥1 live grounding page), `target=gap` (note's `intent` must be
`dispute`/`gap`/`question`), or `target=golden` — **always rejected**, deferred to the `golden`
dispatcher's own `save` action.

## MCP resources and prompts

### Resources — 4 + 1

All resolve config fresh on every read (no restart needed after `/knotica:setup`); none mutate.

| URI | Mime type | Content |
|---|---|---|
| `knotica://schema/root` | `text/markdown` | Root constitution (vault `SCHEMA.md`). |
| `knotica://schema/topic/{topic}` | `text/markdown` | One topic's overlay; a placeholder note when there is none yet. |
| `knotica://schema/resolved/{topic}` | `text/markdown` | Effective merged schema (root ⊕ overlay) — what the operation prompts reference. |
| `knotica://index` | `text/markdown` | Global catalog (vault `index.md`). |
| `ui://knotica/dashboard` | `text/html;profile=mcp-app` | The MCP App dashboard — the same artifact `knotica mcp --http` serves at `GET /`. |

`log.md` is deliberately **not** exposed as a resource (append-only, unbounded).

### Prompts — 4

Static names (`prompts/list` needs zero vault access); bodies resolve lazily per `prompts/get`,
the same resolver `knotica prompt` and `read_protocol` use.

| Name | Args (all optional strings) | Purpose |
|---|---|---|
| `ingest` | `source`, `topic` | Fetch a source, place it by topic, write pages, offer to curate. |
| `query` | `question`, `topic` | Answer from the wiki with citations, offer to curate. |
| `lint` | `topic` | Mechanical checks + semantic schema review, reported by severity. |
| `curate` | `topic`, `verdict` | Save the last interaction as a curated example. |

## Plugin aliases

Every `/knotica:*` slash command in `commands/`, including `guillotine` — the one the previous
README omitted despite it being a full, shipped alias.

| Alias | Argument | Does |
|---|---|---|
| `/knotica:setup` | — | Interactive first-run wizard: scaffold a vault, wire the MCP server, pre-warm. |
| `/knotica:create` | `[name]` | Create + initialize a new knowledge base; switches to it. |
| `/knotica:use` | `[vault-name]` | Switch or inspect the active knowledge base; no arg reports status + lists configured vaults. |
| `/knotica:headless` | `[on\|off\|status]` | Enable, disable, or check server-side LLM mode (`query`, `compile`, the loop's evals — not the arena, which makes no model call). |
| `/knotica:ingest` | `<source-url> [topic]` | Fetch a source, place it by topic, write pages, log. |
| `/knotica:query` | `<question> [topic]` | Answer a question grounded in curated topic pages. |
| `/knotica:lint` | `[topic]` | Lint pages against the schema. |
| `/knotica:curate` | `[topic] [verdict]` | Curate the last interaction into compile-ready training signal. |
| `/knotica:note` | `<your note>` | Save a personal note, anchored to the passage that provoked it. Never scored. |
| `/knotica:status` | `[topic]` | Pages per topic, compile-ready count, lint state, unpushed commits. |
| `/knotica:doctor` | — | Run deterministic health checks; offer the real repair path on a dirty git row. |
| `/knotica:migrate` | `[topic]` | Preview a schema migration, then apply on confirmation. |
| `/knotica:loop` | `<topic>` | Run one self-improvement loop tick (observe → gate → heal). |
| `/knotica:guillotine` | `<claim>` | Put a wiki claim on trial: find mentions, audit evidence, generate a dry-run retraction patch. |

## Vault layout

A freshly scaffolded vault — everything `knotica init` / `vault action=create` leaves on disk:

```
vault-name/
├── .gitattributes              log.md merge=union
├── .gitignore                  see below
├── .knotica/
│   └── prompts/                root-default operation prompts: ingest, query, lint, curate
├── SCHEMA.md                   root constitution — every topic inherits this
├── START_HERE.md               in-vault orientation
├── index.md                    global catalog (no frontmatter — OKF-reserved)
├── log.md                      append-only operation log (OKF-reserved)
├── sources/<topic>/            immutable raw material, one file per citation key
└── <topic>/                    optional seeded topic — empty SCHEMA.md overlay only
```

A scaffolded vault is always **bare**: root constitution plus, optionally, one empty seeded topic
overlay — never demo content. The packaged template itself carries more than this: an
`agentic-systems` demo topic, its `sources/agentic-systems/` tree, and matching `index.md` / `log.md`
entries, all of which exist only as the test suite's fixture data and are stripped at scaffold time.
`.knotica/` itself is committed (it carries `datasets/qa.jsonl`,
`prompts/`, `compiled/`); only `.knotica/locks/`, `.knotica/ingest-activity.jsonl`,
`.knotica/worktrees/`, and the golden staging scratch file are gitignored.

### Folder families

| Family | Path shape | Scored (feeds eval scalar)? |
|---|---|---|
| `page` | `<topic>/*.md` | **Yes** |
| `source` | `sources/<topic>/<citation_key>.md` | **Yes** |
| `note` | `notes/<topic>/<file>.md` | **No — never.** Not shipped in the template; created lazily on first capture. Excluded from `search`, lint's page counts, and the loop's change detection. |

### Reserved top-level names

These 8 names may never be a topic name — the topic-discovery walk excludes them by omission
(membership check, not a maintained filter list):

```
sources, notes, index.md, log.md, SCHEMA.md, START_HERE.md, .knotica, .git
```

### What accumulates under `.knotica/` after first use

Not shipped in the template — populated by tool usage:

| Path (topic-relative) | Producer | Purpose |
|---|---|---|
| `datasets/qa.jsonl` | `curate_example` | Curated flywheel examples. |
| `datasets/golden.jsonl` + `MANIFEST.json` | `datasets freeze` | Frozen held-out golden set, tamper-evident seal. |
| `datasets/golden.staging.jsonl` | golden bootstrap | Gitignored, uncommitted LLM-synthesized candidates. |
| `gaps/gaps.jsonl` | gap classifier | Diagnosed knowledge gaps. |
| `suggestions/suggestions.jsonl` | gap-fill discovery | Candidate sources gated through approval. |
| `metrics.jsonl` | eval harness | Per-generation eval history — absent means "not yet evaluated." |
| `compiled/query_v1.json` + `MANIFEST.json` | `compile promote` | Compiled DSPy artifact, live only after a merge. |

## Error envelope

`ErrorCode` — 17 codes. 15 can appear as a hard `error` (`isError=True`); the last two ride on a
success envelope as warnings only.

| Code | Meaning | Fix |
|---|---|---|
| `NOT_CONFIGURED` | No vault resolved. | `/knotica:setup` or `knotica init`. |
| `TOPIC_NOT_FOUND` | Topic doesn't exist. | Call `list_topics`, or `create_topic`. |
| `PAGE_NOT_FOUND` | Page doesn't exist (message lists nearest matches). | Call `search` in this topic. |
| `RESERVED_NAME` | Wrote to a reserved top-level name. | Choose a different name; use `index_entry` on `write_page` for the catalog. |
| `SOURCE_EXISTS` | `store_source` citation key exists with different content. | Use a different citation key — sources are immutable. |
| `INVALID_FRONTMATTER` | Frontmatter or JSON payload malformed. | Add/fix the named fields. |
| `LOCK_BUSY` | Vault mutation lock held by another operation. | Retryable — retry in a moment. |
| `GIT_ERROR` | Git operation failed. | Run `knotica doctor`. |
| `INVALID_CURSOR` | Stale/malformed/wrong-filter pagination cursor. | Restart the search without a cursor. |
| `INVALID_ARGUMENT` | Generic bad argument (empty required field, out-of-range, bad enum). | Correct the named argument and call again. |
| `LLM_API_ERROR` | Headless LLM call failed. | Retryable for transient statuses; not for auth rejections. |
| `SEARCH_API_ERROR` | Search-provider call failed. | Retryable for transient statuses. |
| `SUGGESTION_NOT_FOUND` | `suggestion_id` not in queue. | Call `suggestions_read`. |
| `SUGGESTION_NOT_APPROVED` | Action requires `approved` status. | Approve first via `suggestions_review`. |
| `NOTE_NOT_FOUND` | `note_id` not in topic. | Call `notes(action=list)`. |
| `SECRET_SCRUBBED` | **Warning only** — write succeeded, a secret was redacted. | Review the redacted spans. |
| `ANCHOR_DEGRADED` | **Warning only** — note saved, anchor pin weak/unprovable. | Call `notes(action=read)`; re-capture naming a more specific page. |

`LOCK_BUSY`, `LLM_API_ERROR`, and `SEARCH_API_ERROR` are the retryable codes (the latter two
default `retryable=True` but can be raised `retryable=False` for non-transient failures like auth
rejections).

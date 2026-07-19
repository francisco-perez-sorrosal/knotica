# Knotica on Claude Desktop

End-to-end guide: install knotica, wire it into **Claude Desktop** (Chat), then run a real
flywheel use case on the demo topic `agentic-systems` (Agent Workflow Memory).

Knotica is **client-as-brain** for ingest, curate, and exploratory Q&A: Claude Desktop’s model
does the thinking while the knotica MCP server exposes deterministic tools over your Obsidian
vault (git). **No knotica LLM credentials** are required for those paths.

The MCP tool **`query`**, plus **compile**, **eval**, and **loop/Arena**, run **headless**
(server-side LLM). They need credentials in Desktop’s MCP `env` block — see
[Headless LLM credentials](#headless-llm-credentials-query--compile--eval).

---

## What you get in Desktop

| Surface | How it appears |
|---------|----------------|
| MCP tools | `query`, `curate_example`, `store_source`, `write_page`, `open_dashboard`, `compile_run`, … |
| MCP prompts | Operation guides (`query`, `ingest`, …) — load via the client’s prompt UI when available |
| Dashboard (MCP App) | Call `open_dashboard` — inline UI in Chat when the host supports MCP Apps |
| Vault | Separate git repo (default `~/dev/data/knotica`), opened in Obsidian for reading |

> Claude Code users: prefer the [plugin channel](../README.md#channel-1--claude-code-plugin-recommended).
> This guide is for **Claude Desktop Chat** (and the same MCP config shape on claude.ai where Apps are enabled).

---

## Prerequisites

1. **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — provides `uvx` (hard requirement).
2. **git** and **[ripgrep](https://github.com/BurntSushi/ripgrep)**.
3. **[Claude Desktop](https://claude.ai/download)** for macOS/Windows.
4. **[Obsidian](https://obsidian.md)** (optional but recommended) to browse the vault.

Confirm `uvx` is on your PATH:

```bash
command -v uvx && uvx --version
```

---

## Install and register with Claude Desktop

### Option A — Recommended (`knotica init --desktop`)

From a clone of this repo:

```bash
# Install the CLI onto your PATH
uv tool install --from . knotica

# Scaffold vault + config + Desktop MCP entry (absolute uvx path)
knotica init \
  --vault ~/dev/data/knotica \
  --topic agentic-systems \
  --remote none \
  --desktop \
  --yes
```

What this does:

1. Copies `vault-template/` into `~/dev/data/knotica` (includes the AWM demo pages).
2. `git init` on the vault.
3. Writes `~/.config/knotica/config.toml` pointing at that vault.
4. Patches Claude Desktop’s config (see below) with an **absolute** `uvx` path and
   **`--with anthropic --with dspy`** so headless `query` / compile / Arena work.
5. Pre-warms the `uvx` environment (first resolution can take ~20–30s).

Then:

1. **Fully quit and reopen Claude Desktop** (config is read at launch).
2. Open `~/dev/data/knotica` as a vault in Obsidian.
3. In Desktop → Settings → Developer (or MCP), confirm a server named **`knotica`** is connected.
4. For MCP `query`, compile, or Arena: add LLM credentials to Desktop’s MCP `env` — see
   [Headless LLM credentials](#headless-llm-credentials-query--compile--eval). Ingest/curate need none.

### Option B — Manual Desktop config

If you prefer to edit the file yourself (macOS):

`~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "knotica": {
      "command": "/ABS/PATH/TO/uvx",
      "args": [
        "--from",
        "/ABS/PATH/TO/knotica/repo",
        "--with",
        "anthropic",
        "--with",
        "dspy",
        "knotica",
        "mcp"
      ]
    }
  }
}
```

**Gotcha:** Desktop launches servers with a minimal PATH. Always use the absolute path from
`command -v uvx` — not bare `"uvx"`. After saving, fully restart Claude Desktop.

For headless `query` / compile / Arena, add an `env` block — see
[Headless LLM credentials](#headless-llm-credentials-query--compile--eval).

Logs if the server fails to start: `~/Library/Logs/Claude/mcp*.log`.

---

## Headless LLM credentials (query / compile / eval)

Claude Desktop does **not** inherit your shell environment. When knotica runs a server-side
LLM (unified MCP `query`, DSPy compile, eval harness, loop/Arena), it reads credentials from
`mcpServers.knotica.env` in Desktop’s config — or from your terminal env for CLI-only runs
(`knotica eval`, `knotica compile`).

### When credentials are needed

| Path | Needs knotica LLM credentials? | Why |
|------|----------------------------------|-----|
| Ingest (`read_protocol` → `store_source` / `write_page`) | **No** | Client LLM drives the workflow |
| Curate, lint, status, doctor, vault reads | **No** | Deterministic tools |
| Exploratory Q&A (`read_protocol` + `search` / `read_page`) | **No** | Client LLM synthesizes the answer |
| MCP tool **`query`** | **Yes** | Phase 3a unified server-side query engine |
| **`compile_run`** / dashboard Compile | **Yes** | DSPy MIPROv2 / bootstrap on a clone |
| **`knotica eval`** (CLI) | **Yes** | Baseline runner + LLM-as-judge |
| Loop / **Arena** | **Yes** | Headless prompt racing |

### Obtain credentials (env only)

**Preferred — Claude subscription (no metered spend):**

1. From a machine with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed, run:
   ```bash
   claude setup-token
   ```
2. Copy the token into Desktop config as `CLAUDE_CODE_OAUTH_TOKEN`.

**Fallback — metered API credits:**

- Set `ANTHROPIC_API_KEY` in the same `env` block (or your shell for CLI-only runs).
- Used only when `CLAUDE_CODE_OAUTH_TOKEN` is absent; knotica logs a spend warning when it falls back.

Never put credentials in `~/.config/knotica/config.toml` or the vault.

### Desktop config example (uvx + credentials)

Merge into the Option B JSON above (macOS path:
`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
"env": {
  "CLAUDE_CODE_OAUTH_TOKEN": "YOUR_TOKEN_HERE"
}
```

Or, with the metered fallback only:

```json
"env": {
  "ANTHROPIC_API_KEY": "sk-ant-..."
}
```

Credentials are read from the environment only — never `config.toml` or the vault.

### Restart and verify

1. **Fully quit** Claude Desktop (not just close the window) and reopen it — MCP config is read at launch.
2. In Chat, retry the AWM prove question via `query` (Step 2 below) or call `compile_status` after a compile.
3. If it still fails, check `~/Library/Logs/Claude/mcp*.log` for auth errors.
4. Terminal sanity check (uses your shell env, not Desktop’s):
   ```bash
   export CLAUDE_CODE_OAUTH_TOKEN="..."   # or ANTHROPIC_API_KEY
   knotica status --topic agentic-systems
   ```

A missing credential surfaces as `NOT_CONFIGURED` naming both `CLAUDE_CODE_OAUTH_TOKEN` (preferred)
and `ANTHROPIC_API_KEY`.

### Headless LLM packages (evals group)

`anthropic` and `dspy` live in the PEP 735 **`evals` dependency group** in the knotica **code repo**
(not the vault). The base wheel that `uvx --from <repo> knotica mcp` installs does **not** include
them — by design (dec-013 cold-start isolation for ingest-only paths).

For **Claude Desktop**, headless tools need those packages in the uvx environment:

| Install path | What to run |
|--------------|-------------|
| **`knotica init --desktop`** | Writes `--with anthropic --with dspy` into Desktop config automatically |
| **Manual Desktop config** | Add `"--with", "anthropic", "--with", "dspy"` to the uvx `args` (see Option B JSON above) |
| **Repo dev / CLI** (`knotica eval`, `knotica compile`) | From the knotica repo root: `uv sync --group evals` |

**Verify** the evals group is available:

```bash
# Dev worktree / repo clone — CLI and pytest
cd /path/to/knotica/repo
uv sync --group evals
uv run python -c "import anthropic; print('anthropic ok')"

# Desktop uvx path — must match your claude_desktop_config.json args
uvx --from /path/to/knotica/repo --with anthropic --with dspy python -c "import anthropic; print('anthropic ok')"
```

If `query` returns `NOT_CONFIGURED` about the eval dependency group, you have OAuth set but uvx is
missing `anthropic` — patch Desktop config (or re-run `knotica init --desktop` from the repo) and
fully restart Desktop. Do **not** run `uv sync` in the vault directory; the vault is data only.

### Verify from a terminal

```bash
knotica doctor --quick
knotica status --topic agentic-systems
```

Doctor should be green; status should list the demo topic pages.

---

## Real use case: Agent Workflow Memory (AWM)

This walkthrough uses the template’s `agentic-systems` topic (pages for AWM, workflow
induction, and agent memory, plus the source `wang2024awm`). You will:

1. Ask a grounded question in Chat.
2. Curate good answers until compile-ready.
3. Compile (DSPy MIPROv2 / bootstrap) onto a review branch.
4. Merge, re-ask, and prove the improvement in Ask / Dashboard.

### Canonical prove question (Q★)

> How does Agent Workflow Memory improve web agents without changing model weights, and what relative gains does it report on Mind2Web and WebArena?

**Grounded answer to look for:** AWM induces reusable workflows into agent memory (offline or
online) without changing model weights; relative gains **24.6% on Mind2Web** and
**51.1% on WebArena**. Citations should include `wang2024awm` / the matching pages.

---

### Step 1 — Open the dashboard in Chat

In a new Claude Desktop conversation, ask:

> Use the knotica tool `open_dashboard` with topic `agentic-systems`.

On hosts with MCP Apps, the interactive dashboard opens inline (Vault → Ask → Loop → Arena → …).
Use the chrome topic picker if needed. The flywheel chip shows Curating / Ready / Compiling / Compiled.

If Apps are unavailable, Claude will still return a text fallback. Start the HTTP mount yourself:

```bash
knotica mcp --http --port 8765
# open http://127.0.0.1:8765/?topic=agentic-systems
```

### Step 2 — Ask Q★ (Before)

Requires [headless LLM credentials](#headless-llm-credentials-query--compile--eval) in Desktop’s MCP
`env` — `query` is server-side.

In Chat (or the dashboard **Ask** pane):

> Call knotica `query` with topic `agentic-systems` and this question:
> How does Agent Workflow Memory improve web agents without changing model weights, and what relative gains does it report on Mind2Web and WebArena?

Pin the answer as **Before** in the Ask pane (or keep the reply in the chat thread). Check that
citations/pages look reasonable.

### Step 3 — Curate good examples (fuel the flywheel)

When an answer is solid:

> Save that Q&A with `curate_example` — topic `agentic-systems`, verdict `good`.

Repeat with related questions until Vault shows **compile-ready** (~30 query-style examples;
ingest-style lines do not count). The trainset grows only through this flywheel — every curated
answer is real usage, so the compiled program optimizes toward questions you actually ask:

```bash
knotica status --topic agentic-systems
```

### Step 4 — Compile

When the Vault pane shows **Ready** (or status reports `compile_ready: true`):

- Dashboard: click **Compile** on Vault, or
- Chat: ask Claude to call `compile_run` for `agentic-systems`, then poll `compile_status`.

Compile always runs on a **clone** and returns a branch named like
`compile/agentic-systems/<shortsha>` — it never merges to main for you.

Merge when the compile scalar beats baseline — **do not ask Claude to run raw git**; use the
deterministic promote tool:

- Dashboard: **Vault** or **Loop** pane → **Branch scoreboard** → select row → **Promote**
  (dry-run confirm, then apply).
- Chat: `branch_scoreboard` to compare, then `branch_promote` with `kind=compile`,
  `topic=agentic-systems`, `branch` from the scoreboard, `mode=dry-run` first, then `mode=apply`.
- Legacy single-tool: `compile_promote` with the same branch/mode args.
- CLI:

```bash
knotica compile promote --topic agentic-systems --branch compile/agentic-systems/<shortsha> --dry-run
knotica compile promote --topic agentic-systems --branch compile/agentic-systems/<shortsha> --apply
```

Legacy manual merge (terminal only, outside MCP):

```bash
cd ~/dev/data/knotica
git checkout main   # or your default branch
git merge compile/agentic-systems/<shortsha>
```

After merge, the live vault has `<topic>/.knotica/compiled/`. The next `query` call uses the
compiled engine automatically — **no second tool name**, no engine fields in the answer.

### Step 5 — Ask Q★ again (After / Prove)

Re-ask the same prove question via `query` or Ask. Compare Before vs After: numbers and
citations should be stable or clearer. On the dashboard, the story map’s **Prove** beat
completes when you pin Before and get a distinct After.

### Step 6 — Optional: Arena heal path

`knotica loop --topic agentic-systems` (terminal, outside Desktop) runs the **autonomous watcher**: it
observes new default-branch content, evals it on a clone, and — for a fresh topic — auto-freezes its
first observation as the gate baseline (no manual `--set-baseline` step). If a later observation or a
`loop/c/*` candidate fails the gate, Arena races `query.md` variants; that is the **reactive** heal path.
Compile is the **proactive** flywheel. Both prove out in Ask. The Loop pane shows the watcher as alive
(heartbeat) and, while an eval is in flight, live per-question progress.

When a topic exists but has no eval/compile scalar yet, the Loop pane **auto-anchors**
once per session at **0.0** (``naive-cold-start`` — no LLM, no train/golden scoring).
Server-side hooks also run after golden freeze / review when eligible.
Legacy measured probe lines are treated as stale and can be replaced by this zero anchor.
Do **not** freeze the loop gate from this probe — run ``knotica eval`` or compile first.

### Suggestion queue (P3 gap-fill approval)

When the loop observes a regression, the fault classifier (P1) diagnoses whether it is a genuine knowledge gap, retrieval fault, or generation fault. Genuine gaps are persisted to a queue for human approval (P3). You can also file gaps conversationally: if the wiki answers poorly and you confirm a gap, call knotica `gap_report` (topic, question text) to file a reported gap flowing into the same discovery queue. Guillotine disputes (weakened claims on `--apply`) also file retracted gaps that feed the queue.

In the dashboard **Suggestions** pane (or via `suggestions_read` / `suggestions_review` tools):
- View pending suggestions joining a diagnosed gap to a ranked source (P2 discovery)
- Approve to queue an ingest instruction (no auto-ingest)
- Reject with a reason, defer to later, or mark as ingested once you've handled it manually
- The `wiki_status.suggestions` block shows per-topic counts: `pending`, `approved_awaiting_ingest`; see gap origin (`measured`, `reported`, `retracted`) to understand source provenance

Suggestion discovery runs on-demand via `knotica gapfill discover --topic <t>` (primary) or auto-batches from loop regression hooks when configured. **Approved suggestions flow through a candidate-branch-gated ingest** (P4): call `source_ingest_open(suggestion_id)` to begin a WIP ingest on a server-managed worktree (isolated from the live vault); fetch the source and drive `store_source` / `write_page` with the returned `candidate` handle (per-call flock, one commit per write); then `source_ingest_submit(candidate, mode="dry-run")` for a lint/gate-eligibility check, followed by `mode="apply"` to publish the candidate branch and synchronously gate it — the loop evaluates and merges gap-closing sources (auto-marking the suggestion ingested with a page-subset trainset upgrade) or quarantines dilutive ones to `loop/x/*` with a per-question diff artifact (suggestion stays `approved` with a `gate_outcome` record for rework). The candidate branch is invisible to the gate until finalize; a half-built ingest is never evaluated.

### Gate policy

The watcher's baseline defends one of two policies: **latest** (default — the baseline tracks reality;
only auto-freeze and an instrument change move it) or **best** (high-water mark — a better observation
ratchets the baseline up; anything below it is a regression the arena fights). Switch it, or re-freeze
from history, from chat:

| Goal | What to tell Claude |
|------|---------------------|
| Switch policy | “Call knotica `loop_baseline_policy` with topic `agentic-systems` and policy `best`.” |
| Re-freeze from history | “Call knotica `loop_rebaseline` with topic `agentic-systems` and mode `best`.” |

From the terminal watcher: `knotica loop --topic agentic-systems --baseline-policy best` or
`--rebaseline best` (each sets/freezes and exits, no eval). On the dashboard, the Loop pane's **defend**
toggle switches policy inline, and a **Re-freeze at best** action appears once metrics history shows a
scalar above the current baseline.

Observations debounce during ingests: while an ingest run is active, the watcher holds off observing
(staleness-bounded, so a crashed ingest cannot block it forever) and, in watch mode, waits for HEAD to
stay stable for a short quiet window (`--observe-quiet`, default 20s) so a multi-commit ingest is measured
once, at its boundary — not once per commit.

### Datasets tab (train + held-out pipeline)

Open the dashboard **Datasets** pane (legacy `?pane=golden` still works). Disk filenames stay
the same; the UI shows **role · filename**:

| Role | File | Purpose |
|------|------|---------|
| Trainset | `qa.jsonl` | Compile flywheel (Ask / `curate_example`) |
| Held-out eval | `golden.jsonl` | Eval / compile gate exam set |
| Held-out seal | `MANIFEST.json` | sha256 seal for golden |
| Candidates | `golden.staging.jsonl` | Bootstrap scratch (often uncommitted) |
| Reviewed | `golden.staging.reviewed.jsonl` | Human-kept candidates → Freeze |

Pipeline: **Bootstrap → Save reviewed → Freeze** (Freeze needs Reviewed ≥ 20 and zero
trainset question overlap). MCP: `datasets_inventory`, `datasets_records`,
`datasets_bootstrap`, `datasets_freeze`, plus `golden_review_load` / `golden_review_save`.
CLI: `knotica datasets freeze --topic <name>` (and existing `knotica eval --bootstrap`).

A fresh topic's **trainset** (`qa.jsonl`) starts empty, so compile is unreachable until curation
fills it. `knotica datasets bootstrap-train --topic <name> [--target N]` (MCP: `datasets_bootstrap_train`)
cold-starts it: the LLM synthesizes query-style QA pairs grounded in the topic's own entity pages,
written with `source: seed_train`. Curated examples (`curate_example`) always displace seeded ones in
compile demo selection, so real usage progressively takes over.

---

## Everyday prompts in Desktop Chat

Claude Desktop may not surface MCP prompts as slash commands. Prefer explicit tool asks:

| Goal | What to tell Claude |
|------|---------------------|
| One-shot answer | “Call knotica `query` with topic … and question …” |
| Ingest a paper | “Load the ingest protocol (`read_protocol` / ingest guide), then `store_source` → write pages …” |
| Health | “Run knotica `doctor_run` (quick) and summarize failures.” |
| Progress | “Call `wiki_status` for topic `agentic-systems`.” |
| Compare branches | “Call `branch_scoreboard` for topic `agentic-systems`.” |
| Promote compile / loop | “Call `branch_promote` with kind, branch, mode=dry-run then apply.” |
| Dashboard | “Call `open_dashboard` for topic `agentic-systems`.” |
| Dataset inventory | “Call `datasets_inventory` for topic `agentic-systems`.” |
| Freeze held-out golden | “Call `datasets_freeze` after Reviewed ≥ 20 (or `knotica datasets freeze`).” |

For long multi-step ops (ingest), ask Claude to call `read_protocol` first so it follows the
vault’s `.knotica/prompts/*.md` rather than improvising a single tool call.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Server never connects | Confirm absolute `uvx` in Desktop config; fully restart Desktop; check `~/Library/Logs/Claude/mcp*.log` |
| `NOT_CONFIGURED` (vault) | Run `knotica init` / ensure `~/.config/knotica/config.toml` points at a real vault |
| `NOT_CONFIGURED` (`query` / compile / Arena, credentials) | Add `CLAUDE_CODE_OAUTH_TOKEN` (preferred) or `ANTHROPIC_API_KEY` to `mcpServers.knotica.env`; fully restart Desktop — see [Headless LLM credentials](#headless-llm-credentials-query--compile--eval) |
| `NOT_CONFIGURED` (`query` / compile, eval dependency group) | Add `--with anthropic --with dspy` to Desktop uvx args (or re-run `knotica init --desktop` from the code repo); see [Headless LLM packages](#headless-llm-packages-evals-group) — **not** `uv sync` in the vault |
| First call hangs ~30s | Cold `uvx` resolve — run `uvx --from <repo> knotica --version` once to warm |
| Dirty vault blocks compile | `knotica doctor` → scoped `knotica doctor repair` (never bare `git restore .`) |
| Compile not ready | Need ≥30 **query-style** curated examples + golden ≥20; grow them via `curate_example` and the golden bootstrap/review flow |
| Dashboard blank in Apps | Host may lack MCP Apps — use `knotica mcp --http` fallback URL |

---

## Related docs

- [README](../README.md) — install channels and command surface
- [PRE_PLAN](./PRE_PLAN.md) — canonical architecture
- [Dashboard README](../dashboard/README.md) — HTTP mount / build
- Vault [START_HERE](../vault-template/START_HERE.md) — in-vault orientation after init

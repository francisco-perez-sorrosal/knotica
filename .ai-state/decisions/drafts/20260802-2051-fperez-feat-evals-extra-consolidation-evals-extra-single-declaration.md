---
id: dec-draft-ca6dfe07
title: Declare the eval dependencies exactly once, as the PEP 621 evals extra
status: proposed
category: architectural
date: 2026-08-02
summary: Remove the PEP 735 dependency-group alias and the hand-written uvx --with package tuple so the evals extra is the single declaration of the headless LLM dependencies; every launch path now requests the extra by name and inherits its version bounds.
tags: [evals, packaging, pep-621, pep-735, install, uvx, desktop, drift, single-source-of-truth]
made_by: agent
agent_type: orchestrator
branch: feat-evals-extra-consolidation
pipeline_tier: standard
affected_files: [pyproject.toml, src/knotica/cli/init.py, src/knotica/evals/llm.py, tests/test_packaging_evals_extra.py, docs/CLAUDE_DESKTOP.md, docs/architecture.md, .ai-state/DESIGN.md, Makefile]
supersedes: dec-055
dissent: Removing the group deletes the only declaration form that is invisible to wheel metadata, so the cold-start isolation dec-013 prized is now enforced solely by the launch command omitting `[evals]` — a convention a future edit can break silently, where the group made the mistake unrepresentable. The counter is that dec-055 had already surrendered that guarantee by adding the extra; keeping the group afterwards bought no isolation while costing a synchronized duplicate.
---

## Context

`dec-013` placed `anthropic`+`dspy` in a PEP 735 `[dependency-groups] evals` precisely so the wheel `uvx --from <root> knotica mcp` resolves would never declare them — cold-start isolation by construction. `dec-055` then added a PEP 621 `evals` **extra** to make headless install reachable (`pip install knotica[evals]`, `uvx --from '<src>[evals]'`, `uv tool install --from '.[evals]'`) but **kept the group** as a byte-identical alias, deferring consolidation to "once the `--group evals` call sites move to `--extra`".

That deferral had a measurable cost. The dependency set ended up declared in **three** places: the extra, the group alias, and a hand-written `_UVX_EVALS_PACKAGES = ("anthropic", "dspy")` tuple in `cli/init.py` that built Desktop's `uvx --with` argv. Nothing enforced agreement — no test compared them, only a prose comment asserted "same package set".

They then diverged, with user-visible consequences. From `litellm` 1.92.0 the package moved to a Rust bridge and publishes manylinux + Windows wheels only — **no macOS wheel at any version** — so a Mac install builds the sdist and needs a Rust toolchain (1.95.0 requires `rustc >=1.94.1`, against a common installed 1.88.0). Adding the necessary `litellm<1.92` bound fixed the extra and the group, but could not reach the third copy: `--with anthropic --with dspy` names packages without bounds, so `knotica init --desktop` kept writing a launch that resolves litellm 1.95 and fails to build.

## Decision

Declare the eval dependencies **exactly once**, in `[project.optional-dependencies].evals`, and have every consumer request them **by extra name**:

- Delete `[dependency-groups].evals`.
- Replace the `--with <package>` argv construction with `--from '<source>[evals]'`, so Desktop's uvx launch resolves the extra from package metadata — bounds included.
- Migrate every instruction surface (`uv sync`, docs, dashboard strings, runtime `NOT_CONFIGURED` remediation) from `--group evals` to `--extra evals`.
- Add `tests/test_packaging_evals_extra.py` as a fitness test: the extra exists, no group shadows it, its requirements carry bounds, and no instruction anywhere still names the removed group.

`dec-013`'s cold-start isolation goal is **preserved in effect**: the lean plugin launch resolves `--from <source>` with no extra, so it still installs neither package.

## Considered Options

### Keep both, add a parity test
Cheapest, and it would have caught the drift between extra and group. But it cannot catch the third copy (the `--with` tuple is argv construction, not a dependency declaration), and it institutionalizes the duplicate rather than removing it. Rejected: a test that guards a redundancy is weaker than deleting the redundancy.

### Scope the litellm bound to macOS via an environment marker
`litellm<1.92; sys_platform == "darwin"` would let Linux CI track modern litellm. Rejected: it makes CI and local resolve different versions of the model-calling stack, and this project ties eval-score comparability to instrument consistency — a silent cross-platform version split is exactly the kind of instrument change that should never happen implicitly.

### Full consolidation onto the extra (chosen)
One declaration, one name, bounds that travel to every consumer automatically. Costs a breaking change to already-written Desktop configs and a migration note.

## Consequences

**Positive**
- The dependency set and its bounds cannot drift — there is one copy.
- Desktop's uvx launch inherits `litellm<1.92`, fixing macOS installs that the `--with` path broke.
- A new consumer surface gets bounds for free by naming the extra.
- The fitness test fails the build if a group, an unbounded requirement, or a stale `--group evals` instruction reappears.

**Negative**
- **Breaking for existing Desktop configs.** A config written before this change carries `--group evals` and now fails at launch with ``Group `evals` is not defined in the project's `dependency-groups` table``. Remedy: re-run `knotica init --desktop`, or hand-edit `--from '<repo>[evals]'`. Documented in `docs/CLAUDE_DESKTOP.md`.
- `uv sync --group evals` in anyone's muscle memory or shell history now errors.
- Cold-start isolation is now a property of the launch command rather than of the metadata (see `dissent`).

## Disconfirmation

**Falsifier.** Evidence that the extra leaks into the lean path — a plugin cold start that resolves `anthropic`/`dspy` despite `--from <source>` carrying no `[evals]` — would show the isolation `dec-013` bought was actually load-bearing on the group's absence from metadata, not merely on the launch command.

**Steelmanned runner-up.** Keeping the group plus a parity test is genuinely defensible: it preserves an isolation guarantee that no convention can break, and the drift that motivated removal was in the *third* copy, which consolidation to either form would have fixed. If a future leak materializes, restoring a group-only declaration for the lean path is the reversal.

**Reversal trigger.** Revisit if a cold-start regression traces to extra metadata, or if litellm ships macOS wheels and the bound can lift (at which point re-evaluate whether the bound still belongs in the extra at all).

## Prior Decision

`dec-055` added the extra while retaining the group, explicitly as a migration window, and named its own completion condition: consolidate once the `--group evals` call sites move to `--extra`. This decision performs that migration and closes the window. What changed since: the duplicate declarations were shown to drift in practice, not just in principle, and the drift shipped a broken macOS install path — converting a theoretical DRY concern into an observed defect.

---
id: dec-draft-cbac8bf4
title: Commitizen releases, bumped only in CI
status: proposed
category: implementation
date: 2026-08-05
summary: "Versioning adopts Commitizen with the `uv` version provider so one commit moves pyproject.toml, uv.lock and plugin.json together; `cz bump` runs only from a dispatched CI workflow, and the dashboard is deliberately left out of the version set."
tags: [versioning, releases, commitizen, uv-lock, plugin-manifest, changelog, ci, marketplace]
made_by: agent
agent_type: orchestrator
branch: feat-versioning
pipeline_tier: standard
affected_files:
  - pyproject.toml
  - uv.lock
  - .claude-plugin/plugin.json
  - .github/workflows/release.yml
  - tests/test_plugin_manifest.py
  - CONTRIBUTING.md
dissent: "Deriving the version from git tags (hatch-vcs) would remove the multi-file sync problem entirely, at the cost of the literal plugin.json version the marketplace cache key requires."
---

## Context

The project had no versioning system. `pyproject.toml`, `.claude-plugin/plugin.json` and the
external `bit-agora` marketplace entry all read `0.1.0` by hand-coincidence; there was no `v*`
tag, no `CHANGELOG.md`, and no release mechanism.

That is not cosmetic. The plugin ships through a marketplace manifest whose `version` field
Claude Code treats as a **cache key tied to a git tag**, and Claude Code serves only published
releases. With no tag ever cut, every command, skill and hook in the repo was invisible to any
installed copy — there was nothing to install. 404 of the 437 commits were already conventional,
so commit-derived bumping was available the moment a tool was configured.

One constraint dominated the design. `uv.lock` records the project's *own* version, and CI runs
`uv sync --locked` (deliberately — `--locked` asserts the lock is current where `--frozen` would
not). Measured directly: changing `[project].version` alone makes `uv lock --check` fail. So any
mechanism that bumps `pyproject.toml` without moving `uv.lock` in the same commit turns `main`
red on the release commit itself — after the tag is already cut and pushed.

## Decision

Adopt **Commitizen** with `version_provider = "uv"`, and make a dispatched CI workflow the only
thing that ever runs it.

- The `uv` provider writes `[project].version` **and** rewrites the `knotica` stanza of `uv.lock`
  via tomlkit — no resolver, no network, no `uv` binary. Measured: a one-line lock diff, and
  `uv lock --check` passes afterwards.
- `.claude-plugin/plugin.json` is a `version_files` target, so the marketplace cache key moves in
  the same commit.
- `[tool.commitizen]` carries **no** `version` key: the provider reads `[project].version`, so the
  repo holds one version string rather than two that must be hand-kept equal.
- `.github/workflows/release.yml` (`workflow_dispatch`, `increment` input) is the only sanctioned
  invocation. `update_changelog_on_bump` folds `CHANGELOG.md` into the bump commit before it is
  tagged, so the tag documents its own version.
- `major_version_zero = true` while pre-1.0.
- **`dashboard/package.json` is excluded.** It is a private build input, never published; bumping
  it would desync `dashboard/package-lock.json` (whose root version cannot be targeted safely —
  every dependency line contains the word "version") and fire the Dashboard workflow's
  `git diff --exit-code` gate for no gain.

`tests/test_plugin_manifest.py` pins the three-file agreement and the `version_provider` value on
every `make verify`, rather than trusting `cz bump --check-consistency` — which runs once, on a
runner, on a tree nobody reads.

## Considered Options

### Commitizen + `uv` provider (chosen)

Native multi-file sync including the lockfile; conventional-commit bump detection matching the
existing history; changelog generation; and the same contract Praxion's `/i-am:release` already
drives, so the command works here without a fork.

### Commitizen + `pep621` provider

The obvious-looking choice, and the one a config copied from a non-uv project carries. Writes
`pyproject.toml` alone and leaves `uv.lock` stale — the exact failure the constraint above
describes. Rejected on measurement, and now pinned against by a test.

### python-semantic-release

Comparable bump detection, but oriented around PyPI publishing (which this project does not do)
and with no native lockfile provider — the `uv.lock` problem would have to be solved by hand with
a bump hook.

### bump-my-version

No changelog generation and no commit analysis; every bump would need an explicit part argument,
discarding the conventional-commit history the repo already maintains.

### hatch-vcs / setuptools-scm (version derived from git tags)

Removes multi-file sync entirely by having no committed version string. Rejected because the
marketplace cache key requires a **literal** version in `plugin.json` in the committed tree; a
derived version cannot satisfy a consumer that reads the file rather than building the package.

## Consequences

**Positive**

- One release is one reviewable commit touching four files, then a tag — no split between the tag
  and the changelog that documents it.
- The marketplace cache key can no longer silently lag the package version; a test fails first.
- `/i-am:release` drives this repo unchanged, so there is one release procedure to learn.

**Negative**

- Bump quality now depends on commit-type discipline. A user-visible change committed as `chore:`
  is invisible twice — it moves no version and appears in no changelog. Documented in
  `CONTRIBUTING.md`, not enforced by a hook.
- The `bit-agora` marketplace manifest lives in another repository and is still bumped by hand
  after each release. The release procedure surfaces the step; nothing verifies it.
- Commitizen is pinned exactly in the workflow, so keeping it current is a maintenance task.

## Disconfirmation

**Falsifier.** A release in which `uv.lock` is *not* correctly rewritten — a lock diff larger than
the single `knotica` stanza line, or a red `main` on a bump commit — would show the provider is not
doing what was measured, and the mechanism would need a lockfile step of its own.

**Steelmanned runner-up.** `hatch-vcs` is genuinely simpler: with the version derived from the tag
there is no sync problem, no `version_files`, no consistency test, and no way for three files to
disagree. It loses only because one consumer — the marketplace — reads a committed file instead of
installed package metadata. Were the plugin ever distributed some other way, that objection
disappears and this decision should be revisited rather than defended.

**Reversal trigger.** If `plugin.json` stops being the marketplace's cache key, or the plugin stops
shipping through a marketplace manifest, the literal-version constraint is gone and the
tag-derived approach becomes the better one.

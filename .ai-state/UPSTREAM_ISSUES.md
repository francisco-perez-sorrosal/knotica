# Upstream Issues

Issues filed on third-party open-source projects. Append-only log.

| Date | Repository | Issue | Title | Status | Workaround | Filed By |
|------|-----------|-------|-------|--------|------------|----------|
| 2026-08-06 | francisco-perez-sorrosal/praxion | [#59](https://github.com/francisco-perez-sorrosal/praxion/issues/59) | Generalize /release for managed projects, and fold four verified Commitizen facts into the versioning skill | open | none needed — this repo adopted the portable half of the contract directly (`.github/workflows/release.yml` matches the `increment` input `/i-am:release` dispatches, so the command drives it once its staleness step is made conditional upstream). The staleness diagnostic runs here today via the plugin-shipped copy: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check_release_staleness.py" --repo-root .` | user |

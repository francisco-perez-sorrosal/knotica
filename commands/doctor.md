---
description: Run knotica's deterministic health checks and surface any warnings or failures.
allowed-tools:
  - Bash(knotica doctor:*)
---
Health check:
!`knotica doctor`

Report the PASS/WARN/FAIL results above. For each WARN or FAIL, state the exact
remediation command shown.

When the git row is dirty, `knotica doctor --fix` only prints guidance — it does
not restore. Offer the real repair path:

1. Preview: `knotica doctor repair --dry-run`
2. Apply selected paths: `knotica doctor repair --apply --paths <path>...`
3. Or all tracked dirty paths: `knotica doctor repair --apply --all-tracked`

Never run `git restore .`. Untracked paths need `--delete-untracked` (destructive
for those paths only). Ask before applying.

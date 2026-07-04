---
description: Run knotica's deterministic health checks and surface any warnings or failures.
allowed-tools:
  - Bash(knotica doctor:*)
---
Health check:
!`knotica doctor`

Report the PASS/WARN/FAIL results above. For each WARN or FAIL, state the exact
remediation command shown, and offer to run `knotica doctor --fix` when it lists
rollback-able items.

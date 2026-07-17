# Calibration Log

One row per completed task: tier chosen at intake vs what the work actually needed.

| Date | Task | Tier | Right call? | Actual shape | Retrospective |
|------|------|------|-------------|--------------|---------------|
| 2026-07-16 | eval-harness (PRE_PLAN Phase 2) | Standard | Yes | 33 planned steps + ~8 user-directed live-run additions; 11 new modules + CLI; 963 tests (baseline 689); 2 researchers, architect (+1 revision), planner, 16 impl/test pairs, 2 fable light-reviews, exhaustive fable verification (FAIL→rework→PASS-with-findings) | Tier fit well: the characterization-test-first gate and pre-mortem fold-ins each caught real issues (all five pre-mortem guards ended up exercised — live-vault guard, key-leak sweeps, spend ceiling fired live, instrument-drift warning, num_threads pin). Live first-runs surfaced 4 defect classes no offline test could (orphaned method via class-body dedent, raw SDK tracebacks, schema-valid-but-garbage citations → ENAMETOOLONG, mis-calibrated spend default) — budget a live-shakedown step in future instrument-building plans. Four subagent return truncations recovered via ground-truth handshake (RECOVERY_LOG.md); truncation rate suggests keeping heavyweight steps' final report short. Phase-2 criterion met: scalar 0.5707 reproduced bit-for-bit on frozen corpus git:599237b. |

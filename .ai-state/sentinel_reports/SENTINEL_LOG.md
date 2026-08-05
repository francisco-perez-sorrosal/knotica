# Sentinel Log

Historical summary metrics, one row per sentinel run. Full reports are siblings of this file in `.ai-state/sentinel_reports/`.

`Findings (C/I/S)` = Critical / Important / Suggested counts. `Ecosystem Coherence` is the system-level composite grade, distinct from the per-artifact Coherence column inside each report's scorecard.

| Timestamp | Report File | Health Grade | Artifacts | Findings (C/I/S) | Ecosystem Coherence |
|-----------|-------------|--------------|-----------|-------------------|---------------------|
| 2026-08-04 15:48:39 | SENTINEL_REPORT_2026-08-04_15-48-39.md | B | 99 | 0/5/5 | B |

## Run Notes

**2026-08-04 15:48:39 — baseline run.** First sentinel audit of this project; no prior report to diff against, so all report trend columns read `baseline`.

- **Artifact count basis (99)**: 1 `CLAUDE.md` + 1 `plugin.json` + 14 commands + 1 skill + 2 hooks + 69 ADRs (68 finalized, 1 draft) + 1 `DECISIONS_INDEX.md` + 1 spec + 2 idea ledgers + `DESIGN.md` + `docs/architecture.md` + `TEST_TOPOLOGY.md` + 2 tech-debt ledger files + `calibration_log.md` + `docs/PRE_PLAN.md`.
- **Scope**: knotica is a Praxion *consumer* project. Checks presupposing an in-repo `agents/` + `rules/` tree (BC01/BC03/BC04, X01/X05/X06/X08, EC01/EC03/EC04/EC06, GL01/GL03, V01–V04, S02/S03, C03/C05, N02, F04) are N/A-by-scope, not failures.
- **Cross-cutting signal**: all five Important findings share one signature — *present container, absent substance*. Nothing project-local validates `.ai-state/` metadata, so every DL/SH/CA finding was invisible until this audit.
- **Test topology (first-day check)**: TT01–TT05 all PASS. 18/18 `subsystems` entries resolve verbatim to `DESIGN.md` §3 Built components, 1:1, zero uncovered. TT04/TT06 skip by policy.
- **Dark dimensions**: `.ai-state/metrics_reports/` holds only `index.html`, deactivating TD01–TD04, RD01, and TT04. Running `/project-metrics` would reactivate six checks — the lowest-effort, highest-information-gain follow-up.
- **Ledger**: 4 rows filed (`td-032`–`td-035`), all `class = drift`, all LLM-judgment (no metrics thresholds available). Active rows 7 → 11.

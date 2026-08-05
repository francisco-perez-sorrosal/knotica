# Sentinel Log

Historical summary metrics, one row per sentinel run. Full reports are siblings of this file in `.ai-state/sentinel_reports/`.

`Findings (C/I/S)` = Critical / Important / Suggested counts. `Ecosystem Coherence` is the system-level composite grade, distinct from the per-artifact Coherence column inside each report's scorecard.

| Timestamp | Report File | Health Grade | Artifacts | Findings (C/I/S) | Ecosystem Coherence |
|-----------|-------------|--------------|-----------|-------------------|---------------------|
| 2026-08-04 15:48:39 | SENTINEL_REPORT_2026-08-04_15-48-39.md | B | 99 | 0/5/5 | B |
| 2026-08-05 00:06:36 | SENTINEL_REPORT_2026-08-05_00-06-36.md | B | 102 | 0/3/5 | B |

## Run Notes

**2026-08-04 15:48:39 — baseline run.** First sentinel audit of this project; no prior report to diff against, so all report trend columns read `baseline`.

- **Artifact count basis (99)**: 1 `CLAUDE.md` + 1 `plugin.json` + 14 commands + 1 skill + 2 hooks + 69 ADRs (68 finalized, 1 draft) + 1 `DECISIONS_INDEX.md` + 1 spec + 2 idea ledgers + `DESIGN.md` + `docs/architecture.md` + `TEST_TOPOLOGY.md` + 2 tech-debt ledger files + `calibration_log.md` + `docs/PRE_PLAN.md`.
- **Scope**: knotica is a Praxion *consumer* project. Checks presupposing an in-repo `agents/` + `rules/` tree (BC01/BC03/BC04, X01/X05/X06/X08, EC01/EC03/EC04/EC06, GL01/GL03, V01–V04, S02/S03, C03/C05, N02, F04) are N/A-by-scope, not failures.
- **Cross-cutting signal**: all five Important findings share one signature — *present container, absent substance*. Nothing project-local validates `.ai-state/` metadata, so every DL/SH/CA finding was invisible until this audit.
- **Test topology (first-day check)**: TT01–TT05 all PASS. 18/18 `subsystems` entries resolve verbatim to `DESIGN.md` §3 Built components, 1:1, zero uncovered. TT04/TT06 skip by policy.
- **Dark dimensions**: `.ai-state/metrics_reports/` holds only `index.html`, deactivating TD01–TD04, RD01, and TT04. Running `/project-metrics` would reactivate six checks — the lowest-effort, highest-information-gain follow-up.
- **Ledger**: 4 rows filed (`td-032`–`td-035`), all `class = drift`, all LLM-judgment (no metrics thresholds available). Active rows 7 → 11.

**2026-08-05 00:06:36 — second run; coherence-weighted; diffed against the 2026-08-04 baseline.**

- **Baseline diff: 7 of 10 findings closed, 1 dispositioned by decision, 3 newly introduced, 0 regressed.** All five baseline Importants (I1 ADR YAML, I2 reciprocity, I3 §3 package gap, I4 spec traceability, I5 dead calibration gate) verified closed **from the artifacts**, not from the ledger's resolution notes. Baseline S4 (`re_affirms` broad usage) is now accepted by `dec-draft-df837e3b` with a mechanical reciprocity gate — dispositioned, not open.
- **Artifact count basis (102)**: same basis as the baseline's 99, +3 draft ADRs (1 → 4).
- **New signature.** The baseline's *present container, absent substance* pattern is **gone** — every container audited holds real substance. The new pattern is **write-forward without re-read**: correct when written, invalidated by a later commit, never re-read. All three new Importants are instances (I1 three `core/` modules absent from both architecture docs; I2 two internal contradictions in `TEST_TOPOLOGY.md`; I3 a topic-predicate fork surviving its own consolidation).
- **Highest-leverage recommendation**: `make verify` gates the topology and the ADR corpus but not the architecture documents, and the ungated pair is the one that lagged 3 commits behind the code. A ~20-line module-coverage check modelled on `check_adr_health.py` would have caught I1 at commit time.
- **Gate the project built**: `scripts/check_adr_health.py` + `tests/test_adr_health.py` (9 tests, each a golden bad-case, wired into `make verify`) is why DL02/DL04/DL06 all went FAIL/WARN → PASS and stay there.
- **Verification for the truncation question**: full `pytest` **2520 passed / exit 0** at the exact count `TEST_TOPOLOGY.md` claims, plus clean `ruff`, `mypy` (192 files), `check_adr_health.py`, `test_group.py --check`, and a clean working tree. **No partially-applied work found.**
- **Method correction from prior-run calibration**: every count this run was produced by a script with the method stated inline (prior run over-counted DL06 by ~3×). Duplication detection now uses **three lenses** — cross-module private imports, name-level cross-module, and AST-normalized bodies; the name-level lens is what found I3, which the AST lens alone reports as three unrelated functions.
- **Dark dimensions**: `.ai-state/metrics_reports/` still holds only `index.html` — TD01–TD04, RD01, TT04 remain inactive. Unchanged from baseline; `/project-metrics` is still the cheapest reactivation.
- **Ledger**: 3 rows filed (`td-038`–`td-040`). `LEDGER.md` 13 → 16 rows (11 non-terminal, 5 `resolved` awaiting post-merge migration); `RESOLVED.md` 24, **0 missing `resolved-by`** (was 3).

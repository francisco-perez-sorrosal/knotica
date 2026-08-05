# Pending Praxion Feedback

Candidate ecosystem-defect reports awaiting `/report-praxion-issue`. This file is git-committed and mechanically sanitized at capture time.

## 2026-08-04 — `check_calibration_coverage.py` fails open on the header its own onboarding writes

**Severity:** the calibration-coverage gate never fires for any project onboarded to the canonical
skeleton. It reports success rather than erroring, so the failure is silent.

**The defect.** `scripts/check_calibration_coverage.py` locates the calibration table by a
case-sensitive substring match on the literal `"Timestamp"`:

```python
_TIMESTAMP_COL = "Timestamp"
...
if _TIMESTAMP_COL in line and line.strip().startswith("|"):
```

But `commands/onboard-project.md` §Phase 2 seeds `calibration_log.md` with a **lowercase** header:

```
| timestamp | task | signals | recommended-tier | actual-tier | source | retrospective |
```

`"Timestamp" in "| timestamp | task | ..."` is `False`. `_newest_calibration_timestamp` returns
`None`, and the coverage result falls through to `covered: True`.

**Why it is worse than a cosmetic mismatch.** The two halves of the contract disagree, so the gate
does not fail loudly on a malformed log — it reports the log as *covered* on a log it never read.
A project accumulating uncalibrated commits looks compliant indefinitely.

**Reproduction** (isolated, against the real module):

| Header fed to `_newest_calibration_timestamp` | Result |
|---|---|
| `\| timestamp \| task \| signals \| ...` — canonical, written by `/onboard-project` | header not found → `covered: True` |
| `\| Timestamp \| Task \| Signals \|` — what the checker searches for | parsed correctly |

**Observed here.** This project's log has 21 real data rows and the checker reports
`calibration_log.md present but contains no data rows — skip-with-INFO`. Note this project carries
an *additional*, independent divergence — its header is `| Date | Task | Tier | Right call? |
Actual shape | Retrospective |`, a different schema from the skeleton — but that is secondary: the
canonical header fails too, so fixing this project's header alone would not fix the gate.

**Candidate fixes** (upstream's call):
1. Case-insensitive match, plus accept `date` as a synonym for `timestamp`.
2. Match on table *position* (first column of the first pipe-table) rather than a column name.
3. Make an unparseable-but-present log an explicit error rather than a `covered: True` skip — this
   is the part that turns a schema drift into a silent one, and is worth doing regardless of 1/2.

**Discovered by:** `sentinel` first run on this project (finding I5); independently reproduced
against the live module before capture.

**Status:** filed upstream as
[praxion#58](https://github.com/francisco-perez-sorrosal/praxion/issues/58) on 2026-08-04
(labels `bug`, `auto-filed`, `from-managed-project`). The arming label `ecosystem-feedback` was
deliberately **not** applied — it is maintainer-applied and arms the P5 issue-autofix agent.

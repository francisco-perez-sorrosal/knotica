#!/usr/bin/env python3
"""Summarize a dispatch-telemetry window, and compare two of them.

The lane rename rewrites the whole tool surface at once. The risk it carries is
that a model gets *worse* at driving knotica and nothing notices, so a window is
captured before the change and another after, and this reads both.

**It prints the instrument's limits with every report, on purpose.** A rate
without its blind spots invites confidence the data cannot support, and this
number gates a one-way door.

Rates, never raw counts. Two windows are never the same length and never contain
the same work, so a count comparison measures how busy the week was. The primary
signal is the **rejected-action rate** — `rejected / (dispatch + rejected)` — the
share of calls that reached the right dispatcher and named an action it does not
have. That is precisely what consolidating twenty-one flat tools into six lanes
threatens, and it is a rate, so it survives the comparison.

Usage:
    summarize_telemetry.py <window-dir> [--compare <after-dir>] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- Comparability floor -----------------------------------------------------
# A window smaller than this cannot support the verdict below. 200 dispatch
# records puts the rule-of-three upper bound on a zero-rejection rate at ~1.5%,
# which is tight enough for the +5pt threshold to mean something; five sessions
# stop one atypical session being the whole sample; three days stop a single
# work-burst being it.
MIN_RECORDS = 200
MIN_SESSIONS = 5
MIN_DAYS = 3

# --- Degradation thresholds, fixed BEFORE capture ----------------------------
#: Overall rejected-action rate rising this far is a regression, not noise: on a
#: baseline near 0-2% it is a 3-5x relative increase.
RED_REJECTED_DELTA_PP = 5.0
#: One call in ten to a lane naming an action it does not have is broken UX on
#: its own terms, whatever the baseline was.
RED_DISPATCHER_RATE_PCT = 10.0
#: ...but only once the lane has been exercised enough for 10% to mean more than
#: a couple of events. Found by running this tool on a real capture: a lane with
#: one call and one refusal reads as 50% and turned a healthy window RED. Below
#: thirty routed calls a 10% rate is <=3 events, which is noise; the overall rate
#: still covers those calls, so nothing goes unwatched -- it is judged in
#: aggregate instead of being singled out on a sample too small to carry it.
MIN_DISPATCHER_ATTEMPTS = 30
#: Broader than rejected-action: includes arguments refused after routing.
AMBER_INVALID_DELTA_PP = 3.0
#: Zero-to-three on a ~200-record window is outside the rule-of-three band.
AMBER_NEW_REJECTIONS = 3

LIMITS = """LIMITS — read these with the numbers, not after them:
  CAN see  per-tool and per-dispatcher counts; rejected-action rate; the
           five-label outcome distribution; sessions (distinct `run`); window
           span; billed legs.
  CANNOT   (a) inter-tool mis-selection — MCP rejects an unknown tool at the
           protocol layer before knotica code runs, so a wrong-tool call leaves
           no record at all; (b) "the model called knotica less often" — only
           rates compare across windows, never raw counts.
  Both are the un-mitigated remainder of the rename risk. A green verdict bounds
  the LARGE regressions; it is not evidence of no harm."""


@dataclass
class Window:
    """One capture window, reduced to the quantities that compare."""

    records: int = 0
    sessions: set[str] = field(default_factory=set)
    days: set[str] = field(default_factory=set)
    first_ts: str = ""
    last_ts: str = ""
    per_tool: Counter[str] = field(default_factory=Counter)
    outcomes: Counter[str] = field(default_factory=Counter)
    dispatched: Counter[str] = field(default_factory=Counter)
    rejected: Counter[str] = field(default_factory=Counter)
    billed: int = 0
    malformed: int = 0

    @property
    def rejected_rate(self) -> float:
        """Share of routed calls that named an action the dispatcher lacks."""
        attempts = sum(self.dispatched.values()) + sum(self.rejected.values())
        return 0.0 if attempts == 0 else 100.0 * sum(self.rejected.values()) / attempts

    def rate_for(self, tool: str) -> float:
        attempts = self.dispatched[tool] + self.rejected[tool]
        return 0.0 if attempts == 0 else 100.0 * self.rejected[tool] / attempts

    @property
    def invalid_share(self) -> float:
        """Share of dispatch outcomes that were INVALID_ARGUMENT."""
        total = sum(self.outcomes.values())
        return 0.0 if total == 0 else 100.0 * self.outcomes["INVALID_ARGUMENT"] / total

    @property
    def shortfalls(self) -> list[str]:
        """Why this window cannot carry a verdict, if it cannot."""
        return [
            note
            for note, ok in (
                (
                    f"{self.records} dispatch records (need {MIN_RECORDS})",
                    self.records >= MIN_RECORDS,
                ),
                (
                    f"{len(self.sessions)} sessions (need {MIN_SESSIONS})",
                    len(self.sessions) >= MIN_SESSIONS,
                ),
                (f"{len(self.days)} distinct days (need {MIN_DAYS})", len(self.days) >= MIN_DAYS),
            )
            if not ok
        ]


def read_window(directory: Path) -> Window:
    """Fold every JSONL record in a sink directory into one :class:`Window`."""
    window = Window()
    timestamps: list[str] = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # Two clients can share one sink (Desktop and Claude Code both
                # write here), so a concurrent append can tear a line. Skipping
                # it silently would hide data loss, and raising would throw away
                # a whole window for one bad byte -- so it is counted and shown.
                window.malformed += 1
                continue
            event, tool = record.get("event"), record.get("tool", "")
            timestamps.append(str(record.get("ts", "")))
            if run := record.get("run"):
                window.sessions.add(str(run))
            if ts := record.get("ts"):
                window.days.add(str(ts)[:10])
            if event == "dispatch":
                window.records += 1
                window.per_tool[tool] += 1
                window.outcomes[str(record.get("outcome", ""))] += 1
                window.dispatched[tool] += 1
            elif event == "rejected":
                window.rejected[tool] += 1
            elif event == "two_phase" and record.get("billed"):
                window.billed += 1
    if timestamps:
        window.first_ts, window.last_ts = min(timestamps), max(timestamps)
    return window


def verdict(before: Window, after: Window) -> tuple[str, list[str]]:
    """RED / AMBER / GREEN against thresholds fixed before either capture."""
    reasons: list[str] = []
    delta = after.rejected_rate - before.rejected_rate
    if delta >= RED_REJECTED_DELTA_PP:
        reasons.append(
            f"RED: overall rejected-action rate {before.rejected_rate:.1f}% → "
            f"{after.rejected_rate:.1f}% (+{delta:.1f}pt, threshold +{RED_REJECTED_DELTA_PP}pt)"
        )
    for tool in sorted(set(after.dispatched) | set(after.rejected)):
        attempts = after.dispatched[tool] + after.rejected[tool]
        rate = after.rate_for(tool)
        if attempts >= MIN_DISPATCHER_ATTEMPTS and rate > RED_DISPATCHER_RATE_PCT:
            reasons.append(
                f"RED: `{tool}` rejects {rate:.1f}% of its {attempts} calls "
                f"(threshold {RED_DISPATCHER_RATE_PCT}%)"
            )
    if reasons:
        return "RED", reasons

    invalid_delta = after.invalid_share - before.invalid_share
    if invalid_delta >= AMBER_INVALID_DELTA_PP:
        reasons.append(
            f"AMBER: INVALID_ARGUMENT share {before.invalid_share:.1f}% → "
            f"{after.invalid_share:.1f}% (+{invalid_delta:.1f}pt)"
        )
    for tool in sorted(after.rejected):
        if before.rejected[tool] == 0 and after.rejected[tool] >= AMBER_NEW_REJECTIONS:
            reasons.append(
                f"AMBER: `{tool}` had no rejections before and {after.rejected[tool]} now"
            )
    return ("AMBER", reasons) if reasons else ("GREEN", ["no threshold crossed"])


def _report(label: str, window: Window) -> list[str]:
    lines = [
        f"== {label} ==",
        f"  span         {window.first_ts or '—'} → {window.last_ts or '—'}",
        f"  dispatches   {window.records}",
        f"  sessions     {len(window.sessions)}   days {len(window.days)}   billed legs {window.billed}",
        *(
            [f"  ⚠ {window.malformed} unparseable line(s) skipped (torn concurrent append?)"]
            if window.malformed
            else []
        ),
        f"  rejected-action rate  {window.rejected_rate:.2f}%",
        f"  INVALID_ARGUMENT share {window.invalid_share:.2f}%",
        "  outcome distribution:",
    ]
    total = sum(window.outcomes.values()) or 1
    for outcome, count in window.outcomes.most_common():
        lines.append(f"    {outcome:18} {count:6}  {100.0 * count / total:5.1f}%")
    lines.append("  per-tool invocations (share of window):")
    for tool, count in window.per_tool.most_common():
        share = 100.0 * count / (window.records or 1)
        rate = window.rate_for(tool)
        suffix = f"   rejected {rate:.1f}%" if window.rejected[tool] else ""
        lines.append(f"    {tool:24} {count:6}  {share:5.1f}%{suffix}")
    if window.shortfalls:
        lines.append("  ⚠ BELOW THE COMPARABILITY FLOOR — cannot carry a verdict:")
        lines.extend(f"      {note}" for note in window.shortfalls)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("window", type=Path, help="sink directory (KNOTICA_TELEMETRY_DIR)")
    parser.add_argument("--compare", type=Path, help="a second, later window to compare against")
    parser.add_argument("--json", action="store_true", help="machine-readable summary")
    args = parser.parse_args()

    if not args.window.is_dir():
        print(f"no such window directory: {args.window}", file=sys.stderr)
        return 2
    before = read_window(args.window)
    if before.records == 0:
        print(
            f"{args.window} holds no dispatch records — was KNOTICA_TELEMETRY_DIR exported "
            "for the server process? An unset sink is a silent no-op that looks like success.",
            file=sys.stderr,
        )
        return 1

    after = read_window(args.compare) if args.compare else None
    payload: dict[str, Any] = {
        "baseline": {
            "records": before.records,
            "sessions": len(before.sessions),
            "days": len(before.days),
            "rejected_rate_pct": round(before.rejected_rate, 3),
            "invalid_share_pct": round(before.invalid_share, 3),
            "per_tool": dict(before.per_tool),
            "outcomes": dict(before.outcomes),
            "comparable": not before.shortfalls,
            "shortfalls": before.shortfalls,
        }
    }
    lines = _report("BASELINE" if after else "WINDOW", before)
    if after is not None:
        lines += [""] + _report("AFTER", after)
        label, reasons = verdict(before, after)
        payload["after"] = {
            "records": after.records,
            "rejected_rate_pct": round(after.rejected_rate, 3),
            "invalid_share_pct": round(after.invalid_share, 3),
            "comparable": not after.shortfalls,
            "shortfalls": after.shortfalls,
        }
        payload["verdict"] = {"label": label, "reasons": reasons}
        lines += ["", f"== VERDICT: {label} =="] + [f"  {reason}" for reason in reasons]
        if before.shortfalls or after.shortfalls:
            lines.append("  ⚠ a window is below the floor — treat this verdict as indicative only")

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("\n".join(lines))
        print()
        print(LIMITS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

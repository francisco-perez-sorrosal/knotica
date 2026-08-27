"""The numbers that decide a one-way door must themselves be checked.

`summarize_telemetry.py` reduces a capture window to a handful of rates, and the
rename ships or does not ship on what they say. A wrong denominator here would
be worse than having no measurement at all: it would produce a confident number
nobody re-derives.

So every rate is pinned against a hand-counted fixture, both directions of each
threshold are exercised, and the comparability floor is asserted to *withhold* a
verdict rather than quietly issue one on four records.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "summarize_telemetry.py"


@pytest.fixture(scope="module")
def summary() -> ModuleType:
    """Load the summarizer as a module -- `scripts/` is not an importable package.

    It must be registered in ``sys.modules`` before execution: the script uses
    ``@dataclass`` under ``from __future__ import annotations``, and dataclasses
    resolves those string annotations by looking its own module up by name.
    """
    spec = importlib.util.spec_from_file_location("summarize_telemetry", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(directory: Path, records: list[dict[str, object]], day: str = "2026-08-10") -> Path:
    """Write records as the sink would: one JSONL file per UTC day."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"dispatch-{day}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps({"ts": f"{day}T12:00:00.000000Z", "run": "r1", **record}) + "\n"
            )
    return directory


def _dispatch(tool: str, outcome: str = "ok") -> dict[str, object]:
    return {"event": "dispatch", "tool": tool, "action": tool, "outcome": outcome}


def _rejected(tool: str) -> dict[str, object]:
    return {"event": "rejected", "tool": tool, "action": "nope", "valid_actions": ["a"]}


# ---------------------------------------------------------------------------
# The rates
# ---------------------------------------------------------------------------


def test_the_rejected_rate_counts_refusals_against_every_routed_call(
    summary: ModuleType, tmp_path: Path
) -> None:
    """The denominator is dispatch + rejected, not dispatch alone.

    Getting this wrong understates the rate exactly when refusals are common,
    which is the situation the metric exists to detect.
    """
    _write(tmp_path, [_dispatch("loop")] * 8 + [_rejected("loop")] * 2)

    window = summary.read_window(tmp_path)

    assert window.rejected_rate == pytest.approx(20.0)


def test_a_rejection_is_not_counted_as_a_dispatch(summary: ModuleType, tmp_path: Path) -> None:
    """`records` is the dispatch census; rejections are a separate event."""
    _write(tmp_path, [_dispatch("loop")] * 3 + [_rejected("loop")] * 5)

    window = summary.read_window(tmp_path)

    assert window.records == 3


def test_each_dispatcher_carries_its_own_rate(summary: ModuleType, tmp_path: Path) -> None:
    """A single bad lane must be visible behind a healthy overall average."""
    _write(
        tmp_path,
        [_dispatch("vault")] * 90 + [_dispatch("loop")] * 5 + [_rejected("loop")] * 5,
    )

    window = summary.read_window(tmp_path)

    assert window.rate_for("loop") == pytest.approx(50.0)
    assert window.rate_for("vault") == pytest.approx(0.0)
    assert window.rejected_rate == pytest.approx(5.0)


def test_the_invalid_argument_share_is_taken_over_dispatch_outcomes(
    summary: ModuleType, tmp_path: Path
) -> None:
    _write(tmp_path, [_dispatch("loop", "INVALID_ARGUMENT")] * 3 + [_dispatch("loop")] * 7)

    window = summary.read_window(tmp_path)

    assert window.invalid_share == pytest.approx(30.0)


def test_sessions_days_and_billed_legs_are_counted(summary: ModuleType, tmp_path: Path) -> None:
    _write(tmp_path, [_dispatch("loop"), {"event": "two_phase", "tool": "loop", "billed": True}])
    _write(tmp_path, [{**_dispatch("loop"), "run": "r2"}], day="2026-08-11")

    window = summary.read_window(tmp_path)

    assert window.sessions == {"r1", "r2"}
    assert window.days == {"2026-08-10", "2026-08-11"}
    assert window.billed == 1


def test_an_empty_window_reports_zero_rather_than_dividing_by_zero(
    summary: ModuleType, tmp_path: Path
) -> None:
    tmp_path.mkdir(exist_ok=True)

    window = summary.read_window(tmp_path)

    assert (window.rejected_rate, window.invalid_share, window.records) == (0.0, 0.0, 0)


# ---------------------------------------------------------------------------
# The comparability floor
# ---------------------------------------------------------------------------


def test_a_window_below_the_floor_says_so_on_every_axis(
    summary: ModuleType, tmp_path: Path
) -> None:
    """Silence here is the failure mode: a verdict on four records looks identical."""
    _write(tmp_path, [_dispatch("loop")] * 4)

    shortfalls = summary.read_window(tmp_path).shortfalls

    assert len(shortfalls) == 3
    assert any("dispatch records" in note for note in shortfalls)
    assert any("sessions" in note for note in shortfalls)
    assert any("distinct days" in note for note in shortfalls)


def test_a_window_meeting_the_floor_reports_no_shortfall(
    summary: ModuleType, tmp_path: Path
) -> None:
    for index in range(summary.MIN_DAYS):
        for session in range(summary.MIN_SESSIONS):
            _write(
                tmp_path,
                [{**_dispatch("loop"), "run": f"r{session}"}] * 20,
                day=f"2026-08-{10 + index}",
            )

    window = summary.read_window(tmp_path)

    assert window.records >= summary.MIN_RECORDS
    assert window.shortfalls == []


# ---------------------------------------------------------------------------
# The verdict — both sides of every threshold
# ---------------------------------------------------------------------------


def _window(summary: ModuleType, tmp_path: Path, name: str, records: list) -> object:
    return summary.read_window(_write(tmp_path / name, records))


def test_a_large_rise_in_the_rejected_rate_is_red(summary: ModuleType, tmp_path: Path) -> None:
    before = _window(summary, tmp_path, "b", [_dispatch("loop")] * 100)
    after = _window(summary, tmp_path, "a", [_dispatch("loop")] * 90 + [_rejected("loop")] * 10)

    label, reasons = summary.verdict(before, after)

    assert label == "RED"
    assert "rejected-action rate" in reasons[0]


def test_one_bad_dispatcher_is_red_even_when_the_average_is_fine(
    summary: ModuleType, tmp_path: Path
) -> None:
    """The averaging failure this guards: 900 healthy calls hiding one broken lane.

    `loop` is given enough traffic to clear `MIN_DISPATCHER_ATTEMPTS` — a lane
    that is genuinely broken gets exercised, and the volume floor exists to
    suppress the one-call artifact, not a real regression.
    """
    before = _window(summary, tmp_path, "b", [_dispatch("vault")] * 100)
    after = _window(
        summary,
        tmp_path,
        "a",
        [_dispatch("vault")] * 900 + [_dispatch("loop")] * 20 + [_rejected("loop")] * 20,
    )

    label, reasons = summary.verdict(before, after)

    assert label == "RED"
    assert any("`loop` rejects" in reason for reason in reasons)


def test_a_smaller_rise_in_invalid_arguments_is_amber(summary: ModuleType, tmp_path: Path) -> None:
    before = _window(summary, tmp_path, "b", [_dispatch("loop")] * 100)
    after = _window(
        summary,
        tmp_path,
        "a",
        [_dispatch("loop", "INVALID_ARGUMENT")] * 5 + [_dispatch("loop")] * 95,
    )

    label, reasons = summary.verdict(before, after)

    assert label == "AMBER"
    assert "INVALID_ARGUMENT share" in reasons[0]


def test_a_dispatcher_that_never_refused_before_and_now_does_is_amber(
    summary: ModuleType, tmp_path: Path
) -> None:
    before = _window(summary, tmp_path, "b", [_dispatch("notes")] * 200)
    after = _window(summary, tmp_path, "a", [_dispatch("notes")] * 200 + [_rejected("notes")] * 3)

    label, reasons = summary.verdict(before, after)

    assert label == "AMBER"
    assert any("had no rejections before" in reason for reason in reasons)


def test_a_rarely_used_lane_cannot_trip_red_on_a_single_refusal(
    summary: ModuleType, tmp_path: Path
) -> None:
    """Found by running the tool on a real capture, where it produced a false RED.

    One call to `vault` plus one refusal reads as a 50% rejection rate. Without a
    volume floor that turns a healthy window RED, and a verdict that cries wolf
    is one nobody reads. Those calls are not unwatched -- the overall rate still
    counts them; they are simply judged in aggregate.
    """
    before = _window(summary, tmp_path, "b", [_dispatch("loop")] * 100)
    after = _window(
        summary,
        tmp_path,
        "a",
        [_dispatch("loop")] * 100 + [_dispatch("vault")] + [_rejected("vault")],
    )

    label, _reasons = summary.verdict(before, after)

    assert label != "RED"


def test_a_well_exercised_lane_over_the_rate_still_trips_red(
    summary: ModuleType, tmp_path: Path
) -> None:
    """The mirror: the floor must suppress noise without suppressing the signal."""
    before = _window(summary, tmp_path, "b", [_dispatch("loop")] * 100)
    after = _window(
        summary,
        tmp_path,
        "a",
        [_dispatch("loop")] * 100 + [_dispatch("vault")] * 30 + [_rejected("vault")] * 10,
    )

    label, reasons = summary.verdict(before, after)

    assert label == "RED"
    assert any("`vault` rejects" in reason for reason in reasons)


def test_an_unchanged_surface_is_green(summary: ModuleType, tmp_path: Path) -> None:
    before = _window(summary, tmp_path, "b", [_dispatch("loop")] * 100)
    after = _window(summary, tmp_path, "a", [_dispatch("loop")] * 120)

    label, reasons = summary.verdict(before, after)

    assert label == "GREEN"
    assert reasons == ["no threshold crossed"]


def test_red_outranks_amber_so_the_worst_finding_leads(summary: ModuleType, tmp_path: Path) -> None:
    before = _window(summary, tmp_path, "b", [_dispatch("loop")] * 100)
    after = _window(
        summary,
        tmp_path,
        "a",
        [_dispatch("loop", "INVALID_ARGUMENT")] * 20 + [_rejected("loop")] * 20,
    )

    label, _reasons = summary.verdict(before, after)

    assert label == "RED"


# ---------------------------------------------------------------------------
# The silent-no-op failure the whole step is exposed to
# ---------------------------------------------------------------------------


def test_an_unexported_sink_is_reported_as_a_failure_not_an_empty_success(
    summary: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    """A window that recorded nothing must not read as a clean run of zero problems."""
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr("sys.argv", ["summarize_telemetry.py", str(tmp_path / "empty")])

    code = summary.main()

    assert code == 1
    assert "KNOTICA_TELEMETRY_DIR" in capsys.readouterr().err


def test_the_report_always_prints_the_instruments_limits(
    summary: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    """The limits are printed with the numbers so the two cannot be separated."""
    _write(tmp_path, [_dispatch("loop")] * 5)
    monkeypatch.setattr("sys.argv", ["summarize_telemetry.py", str(tmp_path)])

    summary.main()

    out = capsys.readouterr().out
    assert "CANNOT" in out
    assert "inter-tool mis-selection" in out


# ---------------------------------------------------------------------------
# Torn lines — two clients share one sink, so concurrent appends can tear
# ---------------------------------------------------------------------------


def test_a_torn_line_costs_one_record_not_the_whole_window(
    summary: ModuleType, tmp_path: Path
) -> None:
    """Found in review: one unparseable line used to abort the entire read.

    Desktop and Claude Code are both wired to the same sink directory, so two
    processes append to one day-file and a torn write is reachable. Losing a
    whole baseline to one bad byte is the worse failure of the two.
    """
    _write(tmp_path, [_dispatch("loop")])
    (tmp_path / "dispatch-2026-08-10.jsonl").open("a", encoding="utf-8").write(
        '{"ts":"2026-08-10T12:00:00Z","run":"r1","event":"dis\n'
    )
    _write(tmp_path, [_dispatch("loop")])

    window = summary.read_window(tmp_path)

    assert window.records == 2
    assert window.malformed == 1


def test_skipped_lines_are_reported_rather_than_swallowed(
    summary: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    """Silent tolerance would hide data loss in the window that gates the rename."""
    _write(tmp_path, [_dispatch("loop")])
    (tmp_path / "dispatch-2026-08-10.jsonl").open("a", encoding="utf-8").write("{oops\n")
    monkeypatch.setattr("sys.argv", ["summarize_telemetry.py", str(tmp_path)])

    summary.main()

    assert "unparseable line(s) skipped" in capsys.readouterr().out

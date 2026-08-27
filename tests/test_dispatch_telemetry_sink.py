"""The dispatch-telemetry JSONL sink, tested as an instrument rather than a logger.

This sink is what a pre-rename baseline window is *made of*: the numbers a
one-way-door gate is argued from are computed from these files and nothing
else. That changes what the tests have to prove. A logger is adequate when the
lines look right; an instrument is adequate only when the quantities derived
from it survive a round trip through the file, when it is silent unless
deliberately switched on, when it cannot contaminate the record with anything
it was not asked to carry, and when it cannot take down the surface it observes.

Five properties carry that weight, and each is asserted directly:

- **One append-only file per UTC day, with self-consistent timestamps.** A
  window is then a contiguous set of whole files rather than a slice of one,
  and no record can land in a file whose date disagrees with its own ``ts`` --
  the failure that would silently mis-date a window.
- **The routing-outcome label is closed by construction.** Callers pass raw
  knotica error codes; every one of them buckets into the five-value
  vocabulary, so an outcome distribution is a partition rather than a
  long tail.
- **The derived quantities round-trip.** Per-tool counts, the rejected-action
  rate, and the per-process ``run`` id are recomputed here from the file text,
  because that is how they will be recomputed later.
- **Off by default, provably.** The suite itself is the adversary: with
  ``KNOTICA_TELEMETRY_DIR`` unset, thousands of test invocations must write
  nothing anywhere, or a real capture window drowns in suite traffic.
- **Nothing but tool, action and topic reaches disk.** A secret written into a
  telemetry file is the one failure here that cannot be undone after the fact,
  so it is asserted at the sink *and* at the wire, with canaries planted in the
  environment, in a free-text tool argument, and in vault page content.

Redirecting the sink is ``monkeypatch.setenv(KNOTICA_TELEMETRY_DIR, tmp_path)``
and nothing else -- no root is threaded anywhere, which is what keeps
``dispatch_telemetry`` a stdlib-only leaf.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from knotica.mcp_server import dispatch_telemetry
from knotica.mcp_server.dispatch_telemetry import (
    OUTCOME_CONFIRMED,
    OUTCOME_PREVIEW,
    OUTCOME_STALE_CONFIRM,
    ROUTING_ERROR,
    ROUTING_INVALID_ARGUMENT,
    ROUTING_NOT_FOUND,
    ROUTING_OK,
    ROUTING_OUTCOMES,
    ROUTING_TOPIC_NOT_FOUND,
    SINK_DIR_ENV_VAR,
    record_dispatch,
    record_rejected_action,
    record_two_phase,
    sink_path,
)
from support.dispatch import TOPIC, build_verb_server, call_tool, payload_of

REPO_ROOT = Path(__file__).resolve().parent.parent
TELEMETRY_LOGGER = "knotica.mcp_server.dispatch_telemetry"

#: Fields every record carries, whatever the event.
COMMON_FIELDS = frozenset(
    {"schema_version", "ts", "run", "event", "tool", "action", "topic", "outcome"}
)

#: The exact key set each event is allowed to write. Exhaustive on purpose: a
#: record that grows a field nobody asked for is how vault content, tool
#: arguments or credentials would first appear on disk.
EVENT_FIELDS = {
    "dispatch": COMMON_FIELDS,
    "rejected": COMMON_FIELDS | {"valid_actions"},
    "two_phase": COMMON_FIELDS | {"phase", "billed"},
}

#: One emitter per event, so a schema assertion can be parametrized over all
#: three without branching inside a test body.
EMITTERS = {
    "dispatch": lambda: record_dispatch("vault", "status", TOPIC),
    "rejected": lambda: record_rejected_action("vault", "stauts", ("status", "health")),
    "two_phase": lambda: record_two_phase("loop", "run_once", TOPIC, outcome=OUTCOME_CONFIRMED),
}


# ---------------------------------------------------------------------------
# Reading the sink back
# ---------------------------------------------------------------------------


def sink_files(directory: Path) -> list[Path]:
    """Every day file in the sink, in date order (the filename sorts as the date)."""
    return sorted(directory.glob("dispatch-*.jsonl"))


def sink_lines(directory: Path) -> list[str]:
    """Raw lines across every day file, unfiltered.

    Deliberately not filtered for blanks: a stray newline would then be
    invisible, and "exactly one line per record" is part of the contract a
    ``jq`` pass over the window depends on.
    """
    return [
        line
        for path in sink_files(directory)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def sink_records(directory: Path) -> list[dict[str, Any]]:
    """Every record in the window, decoded, in write order."""
    return [json.loads(line) for line in sink_lines(directory)]


def sole_record(directory: Path) -> dict[str, Any]:
    """The one record the sink holds -- asserting there is exactly one first."""
    records = sink_records(directory)
    assert len(records) == 1, f"expected exactly one record, got {records!r}"
    return records[0]


def day_file(directory: Path, moment: datetime) -> Path:
    return directory / f"dispatch-{moment.strftime('%Y-%m-%d')}.jsonl"


def tree_snapshot(root: Path) -> set[Path]:
    """Every path under ``root``, for a before/after "nothing was written" diff."""
    return set(root.rglob("*"))


class FrozenClock:
    """A stand-in for ``datetime`` handing out a scripted sequence of instants.

    The sink reads the clock exactly once per record and uses that single
    instant for both the filename and the ``ts`` field, so a scripted sequence
    is enough to place a write on either side of midnight.
    """

    def __init__(self, *instants: datetime) -> None:
        self._remaining = list(instants)

    def now(self, _tz: Any = None) -> datetime:
        assert self._remaining, "the clock was read more times than the test scripted"
        return self._remaining.pop(0)


class PathSpy:
    """A recording, delegating stand-in for ``pathlib.Path``.

    Every filesystem destination the module could reach is constructed through
    the ``Path`` name in its own namespace, so an empty ``constructed`` list is
    a positive statement that no destination was even considered -- independent
    of where a write would have landed, and independent of whether the write
    would have raised.
    """

    def __init__(self) -> None:
        self.constructed: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Path:
        self.constructed.append(args)
        return Path(*args, **kwargs)


@pytest.fixture
def sink_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An enabled sink rooted in ``tmp_path`` -- the only redirection needed."""
    directory = tmp_path / "telemetry"
    monkeypatch.setenv(SINK_DIR_ENV_VAR, str(directory))
    return directory


# ---------------------------------------------------------------------------
# One append-only file per UTC day, with timestamps that agree with it
# ---------------------------------------------------------------------------


def test_a_recorded_dispatch_lands_as_exactly_one_json_line_in_todays_file(sink_dir: Path) -> None:
    record_dispatch("vault", "status", TOPIC)

    assert sink_files(sink_dir) == [day_file(sink_dir, datetime.now(UTC))]
    assert sole_record(sink_dir) | {"ts": None} == {
        "schema_version": 1,
        "ts": None,
        "run": dispatch_telemetry._RUN_ID,
        "event": "dispatch",
        "tool": "vault",
        "action": "status",
        "topic": TOPIC,
        "outcome": ROUTING_OK,
    }


def test_successive_records_accumulate_in_write_order_one_line_each(sink_dir: Path) -> None:
    record_dispatch("vault", "status", TOPIC)
    record_dispatch("loop", "status", TOPIC)
    record_rejected_action("loop", "stauts", ("status", "run_once"))

    assert len(sink_lines(sink_dir)) == 3
    assert [(r["tool"], r["action"]) for r in sink_records(sink_dir)] == [
        ("vault", "status"),
        ("loop", "status"),
        ("loop", "stauts"),
    ]


def test_an_existing_days_file_is_appended_to_and_never_truncated(sink_dir: Path) -> None:
    sink_dir.mkdir(parents=True)
    existing = day_file(sink_dir, datetime.now(UTC))
    existing.write_text('{"event": "from-an-earlier-process"}\n', encoding="utf-8")

    record_dispatch("vault", "status", TOPIC)

    assert [record["event"] for record in sink_records(sink_dir)] == [
        "from-an-earlier-process",
        "dispatch",
    ]


def test_every_timestamp_is_utc_aware_and_parses_as_iso_8601(sink_dir: Path) -> None:
    record_dispatch("vault", "status", TOPIC)

    parsed = datetime.fromisoformat(sole_record(sink_dir)["ts"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_a_record_written_after_midnight_lands_in_the_next_days_file(
    sink_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = datetime(2026, 3, 14, 23, 59, 59, tzinfo=UTC)
    after = datetime(2026, 3, 15, 0, 0, 1, tzinfo=UTC)
    monkeypatch.setattr(dispatch_telemetry, "datetime", FrozenClock(before, after))

    record_dispatch("vault", "status", TOPIC)
    record_dispatch("loop", "status", TOPIC)

    assert sink_files(sink_dir) == [day_file(sink_dir, before), day_file(sink_dir, after)]
    assert len(day_file(sink_dir, before).read_text(encoding="utf-8").splitlines()) == 1
    assert len(day_file(sink_dir, after).read_text(encoding="utf-8").splitlines()) == 1


def test_a_records_timestamp_always_agrees_with_the_day_file_it_was_written_to(
    sink_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = datetime(2026, 3, 14, 23, 59, 59, tzinfo=UTC)
    after = datetime(2026, 3, 15, 0, 0, 1, tzinfo=UTC)
    monkeypatch.setattr(dispatch_telemetry, "datetime", FrozenClock(before, after))

    record_dispatch("vault", "status", TOPIC)
    record_dispatch("loop", "status", TOPIC)

    dated = [
        (
            path.stem.removeprefix("dispatch-"),
            json.loads(path.read_text(encoding="utf-8"))["ts"][:10],
        )
        for path in sink_files(sink_dir)
    ]
    assert dated == [("2026-03-14", "2026-03-14"), ("2026-03-15", "2026-03-15")]


def test_the_sink_path_expands_a_home_relative_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv(SINK_DIR_ENV_VAR, "~/knotica-telemetry")

    resolved = sink_path(datetime(2026, 3, 14, tzinfo=UTC))

    assert resolved == tmp_path / "home" / "knotica-telemetry" / "dispatch-2026-03-14.jsonl"


# ---------------------------------------------------------------------------
# The routing-outcome label: five values, closed by construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("passed", "recorded"),
    [
        pytest.param(ROUTING_OK, ROUTING_OK, id="ok"),
        pytest.param(ROUTING_INVALID_ARGUMENT, ROUTING_INVALID_ARGUMENT, id="invalid-argument"),
        pytest.param(ROUTING_NOT_FOUND, ROUTING_NOT_FOUND, id="not-found"),
        pytest.param(ROUTING_TOPIC_NOT_FOUND, ROUTING_TOPIC_NOT_FOUND, id="topic-not-found"),
        pytest.param(ROUTING_ERROR, ROUTING_ERROR, id="error"),
    ],
)
def test_each_of_the_five_routing_outcomes_is_recorded_as_itself(
    sink_dir: Path, passed: str, recorded: str
) -> None:
    record_dispatch("vault", "status", TOPIC, outcome=passed)

    assert sole_record(sink_dir)["outcome"] == recorded


@pytest.mark.parametrize(
    ("code", "bucket"),
    [
        pytest.param("", ROUTING_OK, id="an-unset-outcome-means-routing-resolved"),
        pytest.param("PAGE_NOT_FOUND", ROUTING_NOT_FOUND, id="page-not-found"),
        pytest.param("SUGGESTION_NOT_FOUND", ROUTING_NOT_FOUND, id="suggestion-not-found"),
        pytest.param("NOTE_NOT_FOUND", ROUTING_NOT_FOUND, id="note-not-found"),
        pytest.param(
            "TOPIC_NOT_FOUND", ROUTING_TOPIC_NOT_FOUND, id="topic-not-found-stays-its-own"
        ),
        pytest.param("VAULT_UNCONFIGURED", ROUTING_ERROR, id="an-unmapped-code-buckets-to-error"),
        pytest.param("  INVALID_ARGUMENT  ", ROUTING_INVALID_ARGUMENT, id="surrounding-whitespace"),
        pytest.param("page_not_found", ROUTING_NOT_FOUND, id="a-lowercased-code"),
    ],
)
def test_a_raw_knotica_error_code_is_bucketed_into_the_closed_vocabulary(
    sink_dir: Path, code: str, bucket: str
) -> None:
    record_dispatch("vault", "read", TOPIC, outcome=code)

    assert sole_record(sink_dir)["outcome"] == bucket


@pytest.mark.parametrize(
    "hostile",
    ["", "   ", "confirmed", "ok ok", "42", "NOT_AN_ERROR_CODE", "ERROR", "🙂"],
)
def test_whatever_a_caller_passes_the_recorded_outcome_stays_inside_the_vocabulary(
    sink_dir: Path, hostile: str
) -> None:
    record_dispatch("vault", "read", TOPIC, outcome=hostile)

    assert sole_record(sink_dir)["outcome"] in ROUTING_OUTCOMES


def test_a_rejected_action_is_recorded_as_invalid_argument_with_the_action_echoed(
    sink_dir: Path,
) -> None:
    record_rejected_action("loop", "stauts", ("status", "run_once", "run_eval"))

    assert sole_record(sink_dir) | {"ts": None} == {
        "schema_version": 1,
        "ts": None,
        "run": dispatch_telemetry._RUN_ID,
        "event": "rejected",
        "tool": "loop",
        "action": "stauts",
        "topic": "",
        "outcome": ROUTING_INVALID_ARGUMENT,
        "valid_actions": ["status", "run_once", "run_eval"],
    }


@pytest.mark.parametrize(
    ("leg", "billed"),
    [
        pytest.param(OUTCOME_PREVIEW, False, id="preview-bills-nothing"),
        pytest.param(OUTCOME_CONFIRMED, True, id="confirmed-is-the-billing-leg"),
        pytest.param(OUTCOME_STALE_CONFIRM, False, id="a-stale-confirm-bills-nothing"),
    ],
)
def test_the_billing_leg_is_recorded_beside_the_routing_outcome_never_inside_it(
    sink_dir: Path, leg: str, billed: bool
) -> None:
    record_two_phase("loop", "run_eval", TOPIC, outcome=leg)

    record = sole_record(sink_dir)
    assert (record["phase"], record["billed"]) == (leg, billed)
    assert record["outcome"] == ROUTING_OK


# ---------------------------------------------------------------------------
# The derived quantities, recomputed from the file text
# ---------------------------------------------------------------------------


def test_per_tool_invocation_counts_survive_a_round_trip_through_the_sink(sink_dir: Path) -> None:
    record_dispatch("vault", "status", TOPIC)
    record_dispatch("vault", "read", TOPIC)
    record_dispatch("vault", "read", TOPIC)
    record_dispatch("loop", "status", TOPIC)
    record_two_phase("loop", "run_eval", TOPIC, outcome=OUTCOME_CONFIRMED)

    records = sink_records(sink_dir)
    per_tool = {tool: sum(r["tool"] == tool for r in records) for tool in {"vault", "loop"}}
    per_action = {(r["tool"], r["action"]) for r in records}

    assert per_tool == {"vault": 3, "loop": 2}
    assert per_action == {
        ("vault", "status"),
        ("vault", "read"),
        ("loop", "status"),
        ("loop", "run_eval"),
    }


def test_the_rejected_action_rate_of_one_dispatcher_is_computable_from_the_sink_alone(
    sink_dir: Path,
) -> None:
    record_dispatch("loop", "status", TOPIC)
    record_dispatch("loop", "status", TOPIC)
    record_dispatch("loop", "run_once", TOPIC)
    record_rejected_action("loop", "stauts", ("status", "run_once"))
    record_dispatch("vault", "status", TOPIC)

    loop_records = [r for r in sink_records(sink_dir) if r["tool"] == "loop"]
    rejected = sum(r["event"] == "rejected" for r in loop_records)

    assert rejected / len(loop_records) == pytest.approx(0.25)


def test_every_record_carries_the_same_process_run_id_across_events_and_day_files(
    sink_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = datetime(2026, 3, 14, 23, 59, 59, tzinfo=UTC)
    after = datetime(2026, 3, 15, 0, 0, 1, tzinfo=UTC)
    monkeypatch.setattr(dispatch_telemetry, "datetime", FrozenClock(before, after, after))

    record_dispatch("vault", "status", TOPIC)
    record_rejected_action("loop", "stauts", ("status",))
    record_two_phase("loop", "run_eval", TOPIC, outcome=OUTCOME_PREVIEW)

    runs = {record["run"] for record in sink_records(sink_dir)}
    assert runs == {dispatch_telemetry._RUN_ID}
    assert len(dispatch_telemetry._RUN_ID) == 12


@pytest.mark.parametrize("event", sorted(EVENT_FIELDS))
def test_a_record_carries_exactly_the_fields_its_event_declares_and_no_others(
    sink_dir: Path, event: str
) -> None:
    EMITTERS[event]()

    assert set(sole_record(sink_dir)) == EVENT_FIELDS[event]


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------


def test_nothing_is_written_anywhere_when_the_sink_directory_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    """The suite is the adversary here: ~2 800 tests must leave no trace.

    Two independent proofs, because either alone can be satisfied by accident:
    no filesystem destination is even constructed, and the isolated home plus
    the working directory are byte-for-byte unchanged afterwards.
    """
    monkeypatch.delenv(SINK_DIR_ENV_VAR, raising=False)
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    spy = PathSpy()
    monkeypatch.setattr(dispatch_telemetry, "Path", spy)
    before = tree_snapshot(tmp_path)

    record_dispatch("vault", "status", TOPIC, outcome=ROUTING_INVALID_ARGUMENT)
    record_rejected_action("loop", "stauts", ("status",))
    record_two_phase("loop", "run_eval", TOPIC, outcome=OUTCOME_CONFIRMED)

    assert spy.constructed == []
    assert tree_snapshot(tmp_path) == before
    assert sink_path() is None


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_an_empty_or_whitespace_only_sink_directory_leaves_the_sink_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    monkeypatch.setenv(SINK_DIR_ENV_VAR, blank)
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    spy = PathSpy()
    monkeypatch.setattr(dispatch_telemetry, "Path", spy)

    record_dispatch("vault", "status", TOPIC)

    assert spy.constructed == []
    assert tree_snapshot(workdir) == set()


def test_the_stderr_log_line_still_emits_while_the_file_sink_is_off(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv(SINK_DIR_ENV_VAR, raising=False)

    with caplog.at_level("INFO", logger=TELEMETRY_LOGGER):
        record_dispatch("vault", "status", TOPIC)

    assert any("dispatch tool=vault action=status" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Nothing but tool, action and topic reaches disk
# ---------------------------------------------------------------------------

#: Distinctive strings planted where a leak would have to come from. Each is
#: unique so a failure names the channel that leaked.
ENV_CANARY = "sk-ant-canary-env-9f13c7a4"
ARG_CANARY = "canary-free-text-argument-6b0d21e5"
PAGE_CANARY = "canary-vault-page-body-3ac85f70"


def test_no_environment_secret_reaches_a_record(
    sink_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_CANARY)
    monkeypatch.setenv("KNOTICA_SOME_SECRET", ENV_CANARY)

    record_dispatch("vault", "status", TOPIC)
    record_rejected_action("loop", "stauts", ("status",))
    record_two_phase("loop", "run_eval", TOPIC, outcome=OUTCOME_CONFIRMED)

    written = "".join(sink_lines(sink_dir))
    assert ENV_CANARY not in written
    assert os.environ["HOME"] not in written


def test_a_free_text_tool_argument_and_vault_page_content_never_reach_the_sink(
    sink_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    vault_config: Path,
    template_vault: Path,
) -> None:
    """The one failure that cannot be undone once written, asserted at the wire.

    A unit-level assertion only proves the sink does not invent fields; this
    drives a real dispatcher on the real server, with a secret in the
    environment, a secret in a free-text argument the tool actually accepts,
    and a secret in the page body the topic resolves to.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_CANARY)
    page = template_vault / TOPIC / "agent-memory.md"
    page.write_text(page.read_text(encoding="utf-8") + f"\n{PAGE_CANARY}\n", encoding="utf-8")

    result = call_tool(
        build_verb_server(),
        "notes",
        {"action": "list", "topic": TOPIC, "question": ARG_CANARY},
    )

    assert getattr(result, "isError", False) is False, payload_of(result)
    written = "".join(sink_lines(sink_dir))
    assert ARG_CANARY not in written
    assert ENV_CANARY not in written
    assert PAGE_CANARY not in written
    assert str(template_vault) not in written
    assert ("notes", "list", TOPIC) in {
        (r["tool"], r["action"], r["topic"]) for r in sink_records(sink_dir)
    }


# ---------------------------------------------------------------------------
# A sink failure never takes down the thing it observes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "make_bad_dir",
    [
        pytest.param(lambda root: _file_at(root / "occupied"), id="a-file-where-the-dir-should-be"),
        pytest.param(lambda root: _file_at(root / "occupied") / "under", id="a-file-as-the-parent"),
        pytest.param(lambda root: _symlink_loop(root) / "sink", id="a-symlink-loop"),
    ],
)
def test_an_unwritable_sink_directory_is_logged_and_swallowed_never_raised(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    make_bad_dir: Any,
) -> None:
    monkeypatch.setenv(SINK_DIR_ENV_VAR, str(make_bad_dir(tmp_path)))

    with caplog.at_level("WARNING", logger=TELEMETRY_LOGGER):
        record_dispatch("vault", "status", TOPIC)
        record_rejected_action("loop", "stauts", ("status",))
        record_two_phase("loop", "run_eval", TOPIC, outcome=OUTCOME_CONFIRMED)

    assert sum("telemetry sink write failed" in r.getMessage() for r in caplog.records) == 3


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_read_only_sink_directory_is_logged_and_swallowed_never_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    read_only = tmp_path / "read-only"
    read_only.mkdir()
    read_only.chmod(0o500)
    monkeypatch.setenv(SINK_DIR_ENV_VAR, str(read_only / "sink"))

    with caplog.at_level("WARNING", logger=TELEMETRY_LOGGER):
        record_dispatch("vault", "status", TOPIC)

    assert any("telemetry sink write failed" in r.getMessage() for r in caplog.records)


def test_a_tool_call_still_succeeds_when_the_sink_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vault_config: Path
) -> None:
    """Telemetry must never take down the surface it observes."""
    monkeypatch.setenv(SINK_DIR_ENV_VAR, str(_file_at(tmp_path / "occupied")))

    result = call_tool(build_verb_server(), "notes", {"action": "list", "topic": TOPIC})

    body = payload_of(result)
    assert getattr(result, "isError", False) is False, body
    assert not (isinstance(body, dict) and "error" in body), body


def _file_at(path: Path) -> Path:
    """A regular file standing where a directory is expected."""
    path.write_text("not a directory\n", encoding="utf-8")
    return path


def _symlink_loop(root: Path) -> Path:
    """A pair of symlinks pointing at each other -- resolving either one is ELOOP."""
    first, second = root / "loop-a", root / "loop-b"
    first.symlink_to(second)
    second.symlink_to(first)
    return first


# ---------------------------------------------------------------------------
# A sink accidentally pointed inside this repo cannot reach the index
# ---------------------------------------------------------------------------


def check_ignored(relative: str) -> bool:
    """Whether git would ignore ``relative`` -- asked of a path that need not exist."""
    completed = subprocess.run(
        ["git", "check-ignore", "-q", relative],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


def test_a_sink_pointed_at_this_repos_working_directory_cannot_reach_the_index() -> None:
    assert check_ignored(".knotica/telemetry/dispatch-2026-03-14.jsonl")


def test_the_repo_ignore_rule_does_not_shadow_the_committed_vault_template() -> None:
    """The ignore rule is anchored, and an unanchored one would match at every depth.

    ``vault-template/.knotica/`` is committed and carries the four seed
    prompts. Already-tracked files survive an over-broad rule, so the damage
    would only appear on the *next* file added there -- which is why this asks
    about a path that does not exist.
    """
    assert not check_ignored("vault-template/.knotica/prompts/not-yet-written.md")

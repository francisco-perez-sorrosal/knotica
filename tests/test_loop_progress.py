"""Behavioral spec for :mod:`knotica.core.loop_progress` -- the in-flight eval
progress primitive, td-013 regression coverage, and the ``examples`` accumulation.

td-013 (open at the time this suite was written): all 4 of ``dspy.Evaluate``'s
scoring threads shared one ``path.with_suffix(".tmp")`` write target, so
concurrent ``write_progress`` calls raced on ``os.replace`` (``[Errno 2] .tmp ->
.json``) and the escaping ``OSError`` made dspy cancel the whole eval run. The
fix has two parts this suite pins:

1. **Primitive safety** -- a unique per-write temp file (not a shared path) and
   a ``write_progress`` that never lets an ``OSError`` escape (a progress
   hiccup must never cancel a run).
2. **Accumulation** -- ``write_progress`` gains an ``examples: list[dict] |
   None`` parameter that ``read_progress`` round-trips (defaulting to ``[]``),
   bounded by a length cap and a per-entry ``detail`` truncation.

No live evals, no model calls -- ``tmp_path`` stands in for the vault root
(``loop_progress`` never touches git).
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from knotica.core.loop_progress import read_progress, write_progress

TOPIC = "agentic-systems"

#: More writers than the 4 dspy scoring threads td-013 was observed under --
#: stresses the shared-``.tmp`` race harder than the original failure mode.
CONCURRENT_WRITERS = 16

#: PINNED negotiable: SYSTEMS_PLAN/ADR leave the exact bound to the implementer
#: ("cap length, e.g. 200"). This suite pins 200, mirroring the sibling
#: ``detail[:200]`` convention already used for the scalar progress field. If
#: the implementer picks a different number, reconcile this constant at the
#: integration checkpoint rather than treating the mismatch as a defect.
EXAMPLES_CAP = 200
DETAIL_CAP = 200


def _progress_json_path(vault_root: Path, topic: str) -> Path:
    return vault_root / ".knotica" / "locks" / f"loop-progress-{topic}.json"


def _outcome(
    example_id: str, *, status: str = "ok", error_class: str = "", detail: str = ""
) -> dict[str, str]:
    return {"id": example_id, "status": status, "error_class": error_class, "detail": detail}


def test_concurrent_writes_from_many_threads_never_raise_and_leave_valid_json(
    tmp_path: Path,
) -> None:
    """The direct td-013 regression proof.

    Probabilistic by nature (it proves the absence of a race) -- run this test
    repeatedly (e.g. 3x) before trusting a single green as proof the race is
    fixed. The unique-per-write-tempfile design is race-free *by construction*,
    so a barrier-synced burst of ``CONCURRENT_WRITERS`` threads writing at the
    same instant is the worst case, not a best-effort approximation of it.
    """
    barrier = threading.Barrier(CONCURRENT_WRITERS)

    def _write(i: int) -> None:
        barrier.wait(timeout=10)
        write_progress(
            tmp_path,
            TOPIC,
            phase="evaluating",
            current=i,
            total=CONCURRENT_WRITERS,
            examples=[_outcome(f"q{i}")],
        )

    with ThreadPoolExecutor(max_workers=CONCURRENT_WRITERS) as pool:
        futures = [pool.submit(_write, i) for i in range(CONCURRENT_WRITERS)]
        for future in futures:
            future.result(timeout=15)  # re-raises here if any writer thread raised

    payload = read_progress(tmp_path, TOPIC)
    assert payload is not None, "the final write must leave a well-formed, parseable payload"
    assert isinstance(payload["examples"], list)

    locks_dir = tmp_path / ".knotica" / "locks"
    leftover = [p.name for p in locks_dir.iterdir() if p.name != f"loop-progress-{TOPIC}.json"]
    assert leftover == [], "no unique per-write temp file should survive a successful replace"


def test_write_progress_does_not_raise_when_os_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated os.replace failure")

    monkeypatch.setattr("knotica.core.loop_progress.os.replace", _boom)

    result = write_progress(tmp_path, TOPIC, phase="evaluating", current=1, total=5)
    assert result is None


def test_write_progress_does_not_raise_on_a_non_serializable_examples_entry(
    tmp_path: Path,
) -> None:
    """A non-JSON-serializable outcome value must be swallowed, not propagate.

    ``json.dumps`` raises ``TypeError`` (not ``OSError``) on such a payload, and
    it runs inside the write -- the contract is "never raise", so a poisoned
    ``examples`` entry must not cancel the eval run it reports on.
    """
    poisoned = [{"id": "gold-1", "status": "ok", "error_class": "", "detail": object()}]

    result = write_progress(tmp_path, TOPIC, phase="evaluating", examples=poisoned)

    assert result is None


def test_write_progress_does_not_raise_when_cleanup_unlink_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cleanup-time failure must not mask (or replace) the swallowed original.

    When the primary write fails, the ``finally`` temp-file cleanup runs; if the
    unlink itself raises, that too must be swallowed rather than propagate.
    """

    def _replace_boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated os.replace failure")

    def _unlink_boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated cleanup unlink failure")

    monkeypatch.setattr("knotica.core.loop_progress.os.replace", _replace_boom)
    monkeypatch.setattr("knotica.core.loop_progress.Path.unlink", _unlink_boom)

    result = write_progress(tmp_path, TOPIC, phase="evaluating", current=1, total=5)

    assert result is None


def test_write_progress_does_not_raise_when_locks_dir_is_unwritable(tmp_path: Path) -> None:
    """A real OS-boundary failure, independent of which internal call the
    implementer routes through -- an adversarial pair with the monkeypatched
    ``os.replace`` failure above.
    """
    locks_dir = tmp_path / ".knotica" / "locks"
    locks_dir.mkdir(parents=True)
    locks_dir.chmod(0o500)  # read + execute only -- no file creation permitted
    try:
        result = write_progress(tmp_path, TOPIC, phase="evaluating", current=1, total=5)
        assert result is None
    finally:
        locks_dir.chmod(0o700)


def test_examples_round_trip_through_write_and_read(tmp_path: Path) -> None:
    outcomes = [
        _outcome("q1"),
        _outcome("q2", status="error", error_class="rate_limit_429", detail="HTTP 429"),
    ]

    write_progress(tmp_path, TOPIC, phase="evaluating", current=2, total=5, examples=outcomes)

    payload = read_progress(tmp_path, TOPIC)
    assert payload is not None
    assert payload["examples"] == outcomes


def test_examples_defaults_to_empty_list_when_omitted(tmp_path: Path) -> None:
    write_progress(tmp_path, TOPIC, phase="evaluating", current=1, total=5)

    payload = read_progress(tmp_path, TOPIC)
    assert payload is not None
    assert payload["examples"] == []


def test_examples_list_is_capped_at_the_documented_bound(tmp_path: Path) -> None:
    outcomes = [_outcome(f"q{i}") for i in range(EXAMPLES_CAP + 50)]

    write_progress(
        tmp_path,
        TOPIC,
        phase="evaluating",
        current=len(outcomes),
        total=len(outcomes),
        examples=outcomes,
    )

    payload = read_progress(tmp_path, TOPIC)
    assert payload is not None
    assert len(payload["examples"]) == EXAMPLES_CAP


def test_examples_detail_is_truncated_at_the_documented_bound(tmp_path: Path) -> None:
    long_detail = "x" * (DETAIL_CAP + 50)

    write_progress(
        tmp_path,
        TOPIC,
        phase="evaluating",
        current=1,
        total=1,
        examples=[_outcome("q1", status="error", error_class="other", detail=long_detail)],
    )

    payload = read_progress(tmp_path, TOPIC)
    assert payload is not None
    recorded_detail = payload["examples"][0]["detail"]
    assert recorded_detail == long_detail[:DETAIL_CAP]
    assert len(recorded_detail) == DETAIL_CAP


def test_stale_multi_entry_payload_returns_none_regardless_of_examples(tmp_path: Path) -> None:
    write_progress(
        tmp_path,
        TOPIC,
        phase="evaluating",
        current=1,
        total=5,
        examples=[_outcome("q1")],
    )

    # Backdate updated_at past the 15-minute staleness window by rewriting the
    # JSON file directly -- write_progress always stamps "now", so this is the
    # only way to simulate a leftover entry from a dead run.
    path = _progress_json_path(tmp_path, TOPIC)
    payload = json.loads(path.read_text(encoding="utf-8"))
    stale_at = datetime.now(UTC) - timedelta(minutes=20)
    payload["updated_at"] = stale_at.isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert read_progress(tmp_path, TOPIC) is None


def test_each_write_narrates_one_line_to_the_operator_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A minutes-long eval was silent in the server log between its clone line and
    # its verdict, so a stalled run and a working one looked identical. Each write
    # now emits one line. Consecutive lines must carry new information: the
    # recorded/failed counts are what separate an outcome write from the substage
    # write immediately before it, which is otherwise identical.
    with caplog.at_level(logging.INFO, logger="knotica.core.loop_progress"):
        write_progress(
            tmp_path,
            TOPIC,
            phase="evaluating",
            current=2,
            total=10,
            substage="judging",
            sub_current=1,
            sub_total=3,
            examples=[{"id": "g1", "status": "ok"}, {"id": "g2", "status": "error"}],
        )

    assert f"progress {TOPIC} evaluating 2/10 judging 1/3 (2 recorded, 1 failed)" in caplog.text, (
        "the log line must carry phase, position, substage and outcome counts"
    )


def test_a_long_question_is_truncated_in_the_log_but_not_in_the_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The file feeds a UI that can wrap; the log feeds a terminal that cannot.
    question = "q" * 150
    with caplog.at_level(logging.INFO, logger="knotica.core.loop_progress"):
        write_progress(tmp_path, TOPIC, phase="evaluating", current=1, total=2, detail=question)

    entry = read_progress(tmp_path, TOPIC)
    assert entry is not None
    assert len(entry["detail"]) == 150, "the file keeps the detail up to DETAIL_CAP"
    assert "q" * 150 not in caplog.text, "the log must not carry the untruncated question"
    assert "q" * 70 in caplog.text, "the log carries the truncated head of it"

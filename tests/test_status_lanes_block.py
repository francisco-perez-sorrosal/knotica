"""Behavioral tests for the ``lanes`` block on ``wiki_status view="summary"``.

Each per-topic row gains a ``"lanes"`` dict, one entry per non-Home lane
(``learn``, ``answer``, ``improve``, ``fill``, ``tend``), each entry the
lane's rail rendered through ``process_model.derive_stages`` -- server-derived
state, never a client-side recomputation.

RED-first: the ``lanes`` key does not exist on ``gather_wiki_status``'s
payload yet when this file is written (the paired implementer step lands
concurrently). Every test below reads ``row["lanes"]``, so the uniform first-run
failure is a ``KeyError: 'lanes'``, not a collection error or an import
failure -- every production symbol this file imports (``derive_stages``,
``write_loop_state``, ``append_ingest_event``, ``SuggestionRecord``) already
exists today.

This step tests the *adapters* -- the translation from already-computed
per-topic fields into ``derive_stages``'s position payload -- not
``derive_stages`` itself (covered exhaustively by
``tests/test_process_model_predicates.py``). Assertions therefore compare
state **per stage id**, not raw dict equality including prose: a blocked
stage's ``reason`` text is adapter-owned prose, so tests assert presence/
absence of a reason, never its exact wording.

**Load-bearing assumptions about the not-yet-landed adapter mapping**
(the paired implementation wins on conflict; each is independently falsified
in isolation, not tangled with its neighbours):

* **Fill** -- watermark derived from suggestion ``status``/``gate_outcome``,
  singular records only (no multi-suggestion aggregation is exercised here):
  no suggestions at all -> idle; one ``pending`` suggestion (discovery already
  ran) -> active at ``approve``; one ``approved`` suggestion whose most recent
  ``gate_outcome`` verdict is ``refused`` (the existing
  ``refused_awaiting_rework`` signal) -> blocked at ``gate``; one ``ingested``
  suggestion with a ``merged`` verdict -> terminal (every stage complete).
* **Improve** -- watermark derived from ``loop.stage``/``candidate_branch``
  (the existing ``_gate_and_loop`` output): no persisted loop state -> idle;
  ``LoopStage.evaluating`` -> active at ``observe``. **Deliberately not
  covered**: a ``blocked``/``terminal`` case for Improve. Unlike Fill's
  ``refused_awaiting_rework``/merged-verdict signals (already named fields on
  the existing payload), no single ``LoopStage`` value unambiguously names a
  *blocked* position on the six-stage rail, and Improve's rail
  (instrument/observe/gate/heal/promote/prove) reads as an ongoing
  measurement cycle rather than a one-shot pipeline that terminates the way
  Fill/Learn do -- asserting one would pin a guess neither the plan nor any
  existing field grounds. Recorded in ``LEARNINGS.md`` as a gap for
  reconciliation against the landed adapter, not silently dropped.
* **Tend** (a checklist lane, independently-evaluated checks, no watermark)
  -- covered at the invariant level rather than by exact equality against a
  guessed ``{checks, reasons}`` dict: every declared check id is present in
  rail order; ``"active"`` never appears (``derive_stages``'s own contract:
  a checklist's ``"active"`` is client-side focus, never server-derived --
  but note ``_derive_checklist_stages`` merely echoes whatever the caller
  passes, so this is a real assertion on the adapter, not a tautology of
  ``derive_stages`` itself); and the one check with an existing, unambiguous,
  already-computed real signal -- ``lint`` via ``lint_violations`` -- reflects
  it: complete on a mechanically clean topic, blocked with a reason once a
  real lint violation is introduced. ``doctor``/``okf``'s exact check-key
  semantics are not asserted -- no existing per-topic field maps to them
  unambiguously.
* **Learn** -- the watermark is read off the *real* ingest-activity journal
  (via ``ingest_activity.read_ingest_activity``'s monotonic ``stage_index``),
  never a second, independently-computed position. The expected rail index is
  derived from ``Stage.journal_stages`` (a public field on the live
  declaration), not hand-picked or read from ``process_model``'s private
  fold table.
* **Answer** -- no persisted state at all; always idle, unconditionally.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from knotica.core import process_model
from knotica.core.gapfill import suggestions_path
from knotica.core.ingest_activity import append_ingest_event
from knotica.core.loop_state import LoopStage, LoopState, write_loop_state
from knotica.core.process_model import derive_stages
from knotica.core.records import SuggestionRecord
from knotica.core.status import gather_wiki_status
from knotica.core.transaction import VaultTransaction
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"
MEMORY_PAGE_RELPATH = f"{TOPIC}/agent-memory.md"

NON_HOME_LANES = ("learn", "answer", "improve", "fill", "tend")

FILL_IDS = [stage.id for stage in process_model.LANE_STAGES["fill"]]
APPROVE_INDEX = FILL_IDS.index("approve")
GATE_INDEX = FILL_IDS.index("gate")
FILL_TERMINAL_WATERMARK = len(FILL_IDS)

IMPROVE_IDS = [stage.id for stage in process_model.LANE_STAGES["improve"]]
OBSERVE_INDEX = IMPROVE_IDS.index("observe")

TEND_IDS = [stage.id for stage in process_model.LANE_STAGES["tend"]]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _topic_row(store: LocalFSStore, vault: Path) -> dict[str, Any]:
    body = gather_wiki_status(store, vault, topic=TOPIC, view="summary")
    assert len(body["topics"]) == 1, f"expected exactly one topic row, got {body['topics']!r}"
    row: dict[str, Any] = body["topics"][0]
    return row


def _suggestion_record(
    *, suggestion_id: str, status: str = "pending", **overrides: Any
) -> SuggestionRecord:
    payload: dict[str, Any] = {
        "suggestion_id": suggestion_id,
        "topic": TOPIC,
        "gap_id": f"gap-{suggestion_id}",
        "qa_id": f"golden-{suggestion_id}",
        "fault_class": "genuine_gap",
        "question": "How does speculative decoding interact with draft-model verification?",
        "reference_pages": ("speculative-decoding",),
        "rank": 1,
        "query_text": "speculative decoding draft model verification",
        "candidate": {
            "url": f"https://arxiv.org/abs/{suggestion_id}",
            "title": "Accelerating LLM Inference with Speculative Decoding",
            "snippet": "We propose...",
            "source_provider": "fake",
            "doi": None,
            "citation_count": 412,
            "schema_version": 1,
        },
        "status": status,
        "proposed_at": "2026-07-19T07:30:00Z",
        "decided_at": None,
        "decided_reason": None,
        "ingested_at": None,
        "detected_generation": 42,
    }
    payload.update(overrides)
    return SuggestionRecord(**payload)


def _seed_suggestions(store: LocalFSStore, vault: Path, records: list[SuggestionRecord]) -> None:
    path = suggestions_path(TOPIC)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed suggestions for test") as txn:
        txn.write(path, body)


def _refused_gate_outcome() -> dict[str, object]:
    return {
        "verdict": "refused",
        "scalar": 0.9201,
        "baseline_scalar": 0.9655,
        "ref": "loop/x/agentic-systems/source-a1b2c3d4",
        "reason": "regressed 3 previously-passing golden questions",
        "regressed_questions": ["q-0001", "q-0007", "q-0012"],
    }


def _merged_gate_outcome() -> dict[str, object]:
    return {
        "verdict": "merged",
        "scalar": 0.97,
        "baseline_scalar": 0.9655,
        "ref": "loop/r/abc123def456",
        "reason": None,
        "regressed_questions": None,
    }


def _rail_index_for_journal_stage(lane: str, journal_stage: str) -> int:
    """The rail position ``journal_stage`` folds into, read off the live,
    public declaration (``Stage.journal_stages``) -- never a private lookup
    or a hand-picked literal."""
    for index, stage in enumerate(process_model.LANE_STAGES[lane]):
        if journal_stage in stage.journal_stages:
            return index
    raise AssertionError(f"no {lane} rail stage declares journal stage {journal_stage!r}")


def _assert_matches_watermark(
    actual_stages: Sequence[dict[str, Any]],
    lane: str,
    watermark: int | None,
    *,
    blocked: bool = False,
) -> None:
    """Compare per-stage state against ``derive_stages``'s own output for the
    given watermark. The blocked stage's exact reason text is adapter-owned
    prose -- only its presence/absence is asserted, never its wording."""
    placeholder_reason = "placeholder reason for shape comparison only" if blocked else None
    expected = derive_stages(lane, {"watermark": watermark, "blocked_reason": placeholder_reason})
    assert [stage["id"] for stage in actual_stages] == [stage["id"] for stage in expected]
    assert [stage["state"] for stage in actual_stages] == [stage["state"] for stage in expected]
    for actual, exp in zip(actual_stages, expected, strict=True):
        if exp["reason"] is None:
            assert actual["reason"] is None, (
                f"{lane}/{actual['id']}: expected no reason, got {actual['reason']!r}"
            )
        else:
            assert actual["reason"], f"{lane}/{actual['id']}: expected a non-empty blocked reason"


# ---------------------------------------------------------------------------
# Totality -- every non-Home lane present, stage ids in declared order
# ---------------------------------------------------------------------------


def test_lanes_block_covers_every_non_home_lane_with_stage_ids_in_declared_order(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    row = _topic_row(store, template_vault)

    assert set(row["lanes"]) == set(NON_HOME_LANES)
    for lane in NON_HOME_LANES:
        declared_ids = [stage.id for stage in process_model.LANE_STAGES[lane]]
        assert [stage["id"] for stage in row["lanes"][lane]] == declared_ids


# ---------------------------------------------------------------------------
# Answer -- no persisted state, always idle
# ---------------------------------------------------------------------------


def test_answer_lane_is_always_idle_with_no_persisted_state(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    row = _topic_row(store, template_vault)

    assert row["lanes"]["answer"] == derive_stages(
        "answer", {"watermark": None, "blocked_reason": None}
    )


# ---------------------------------------------------------------------------
# Learn -- watermark read off the real ingest-activity journal
# ---------------------------------------------------------------------------


def test_learn_lane_reflects_the_real_ingest_journal_position(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    append_ingest_event(
        store, template_vault, topic=TOPIC, stage="fetch", title="Fetch source", status="ok"
    )

    row = _topic_row(store, template_vault)

    expected_index = _rail_index_for_journal_stage("learn", "fetch")
    _assert_matches_watermark(row["lanes"]["learn"], "learn", expected_index)

    # Non-vacuity: a topic with no journal activity at all must not report the
    # same position -- proving this exercises the real journal, not a
    # hardcoded index that would pass regardless of the seeded event.
    idle = derive_stages("learn", {"watermark": None, "blocked_reason": None})
    assert row["lanes"]["learn"] != idle


# ---------------------------------------------------------------------------
# Improve -- watermark read off the existing `_gate_and_loop` loop dict
# ---------------------------------------------------------------------------


def test_improve_lane_is_idle_when_no_loop_state_is_recorded(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    row = _topic_row(store, template_vault)

    _assert_matches_watermark(row["lanes"]["improve"], "improve", None)


def test_improve_lane_reflects_an_in_flight_evaluation_cycle(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    write_loop_state(store, template_vault, LoopState(topic=TOPIC, stage=LoopStage.evaluating))

    row = _topic_row(store, template_vault)

    _assert_matches_watermark(row["lanes"]["improve"], "improve", OBSERVE_INDEX)


# ---------------------------------------------------------------------------
# Fill -- watermark read off suggestion status/gate_outcome
# ---------------------------------------------------------------------------


def test_fill_lane_is_idle_when_no_suggestions_exist(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    row = _topic_row(store, template_vault)

    _assert_matches_watermark(row["lanes"]["fill"], "fill", None)


def test_fill_lane_is_active_at_approve_with_one_pending_suggestion(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    _seed_suggestions(
        store,
        template_vault,
        [_suggestion_record(suggestion_id="s-pending", status="pending")],
    )

    row = _topic_row(store, template_vault)

    _assert_matches_watermark(row["lanes"]["fill"], "fill", APPROVE_INDEX)


def test_fill_lane_is_blocked_at_gate_when_a_suggestion_is_refused_awaiting_rework(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    _seed_suggestions(
        store,
        template_vault,
        [
            _suggestion_record(
                suggestion_id="s-refused",
                status="approved",
                gate_outcome=_refused_gate_outcome(),
            )
        ],
    )

    row = _topic_row(store, template_vault)

    _assert_matches_watermark(row["lanes"]["fill"], "fill", GATE_INDEX, blocked=True)


def test_fill_lane_is_terminal_when_a_suggestion_has_merged(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    _seed_suggestions(
        store,
        template_vault,
        [
            _suggestion_record(
                suggestion_id="s-merged",
                status="ingested",
                gate_outcome=_merged_gate_outcome(),
            )
        ],
    )

    row = _topic_row(store, template_vault)

    _assert_matches_watermark(row["lanes"]["fill"], "fill", FILL_TERMINAL_WATERMARK)


# ---------------------------------------------------------------------------
# Tend -- a checklist lane, covered at the invariant level
# ---------------------------------------------------------------------------


def test_tend_lane_never_reports_the_active_state(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    row = _topic_row(store, template_vault)

    tend = row["lanes"]["tend"]
    assert [stage["id"] for stage in tend] == TEND_IDS
    assert not any(stage["state"] == "active" for stage in tend), (
        "tend is a checklist lane -- 'active' is client-side focus, never server-derived "
        f"(got {tend!r})"
    )


def test_tend_lint_check_is_complete_on_a_mechanically_clean_topic(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    row = _topic_row(store, template_vault)

    lint_check = next(stage for stage in row["lanes"]["tend"] if stage["id"] == "lint")
    assert lint_check["state"] == "complete"
    assert lint_check["reason"] is None


def test_tend_lint_check_is_blocked_when_the_topic_has_a_real_lint_violation(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    memory_page = template_vault / MEMORY_PAGE_RELPATH
    memory_page.write_text(memory_page.read_text() + "\n\nSee [[does-not-exist-xyz]] for more.\n")

    row = _topic_row(store, template_vault)

    lint_check = next(stage for stage in row["lanes"]["tend"] if stage["id"] == "lint")
    assert lint_check["state"] == "blocked", (
        f"a real lint violation must block the lint check, got {lint_check!r}"
    )
    assert lint_check["reason"]

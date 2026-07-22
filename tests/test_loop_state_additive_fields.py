"""Additive-field forward/back compatibility for :class:`knotica.core.loop_state.LoopState`."""

from knotica.core.loop_state import LoopState

TOPIC = "agentic-systems"

# A LoopState JSON document as it would have been serialized before
# `last_eval_started_at` / `pending_retry` existed on the model.
_PRE_CHANGE_STATE_JSON = """
{
    "schema_version": 1,
    "topic": "agentic-systems",
    "stage": "idle",
    "baseline_policy": "latest",
    "baseline_scalar": 0.57,
    "baseline_harness_version": "h1",
    "baseline_corpus_ref": null,
    "candidate_branch": null,
    "candidate_sha": null,
    "last_scalar": null,
    "last_generation": null,
    "last_harness_version": null,
    "last_decision": "none",
    "last_error": null,
    "cursors": {},
    "updated_at": "2026-01-01T00:00:00Z"
}
"""


def test_pre_change_serialized_state_deserializes_with_stated_defaults() -> None:
    state = LoopState.model_validate_json(_PRE_CHANGE_STATE_JSON)
    assert state.last_eval_started_at is None
    assert state.pending_retry is False


def test_state_with_new_fields_round_trips() -> None:
    state = LoopState(
        topic=TOPIC,
        last_eval_started_at="2026-02-01T12:00:00+00:00",
        pending_retry=True,
    )
    reloaded = LoopState.model_validate_json(state.model_dump_json())
    assert reloaded.last_eval_started_at == state.last_eval_started_at
    assert reloaded.pending_retry is True


def test_pending_retry_defaults_false_on_a_fresh_state() -> None:
    # Placeholder covering only the field default here — the clearing behavior on a
    # successful eval completion is asserted once the failure-handler fix lands.
    state = LoopState(topic=TOPIC)
    assert state.pending_retry is False

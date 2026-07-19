"""Behavioral contract tests for the ``SuggestionRecord`` schema (``knotica.core.records``).

Derived from ``INTERFACE_DESIGN.md`` §D2 (the frozen ``suggestions.jsonl`` schema
v1 sample) — never from the implementation. ``SuggestionRecord`` mirrors
``GapRecord``: a frozen, ``kw_only`` dataclass with a self-describing
``schema_version``, an enum-validated ``status``, and a
``to_json_line``/``from_json_line`` pair that tolerates unknown future fields.
The candidate is embedded verbatim as the opaque ``SourceCandidate.to_record()``
payload — this is P3's *regression lock*: once frozen, the exact field
inventory (including the denormalized display fields copied off the motivating
gap) and the lifecycle-state enum must not silently drift.

Production imports are deferred into a helper so collection succeeds before
``SuggestionRecord`` exists (RED-first: the paired implementer step lands the
module concurrently). This file was written without reading the
implementer's code.
"""

import json

import pytest


def _records_module():
    import knotica.core.records

    return knotica.core.records


# The exact denormalized field set from INTERFACE_DESIGN.md §D2 -- this is the
# regression lock. A future field must be added here deliberately, never
# discovered by accident.
SUGGESTION_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "suggestion_id",
        "topic",
        "gap_id",
        "qa_id",
        "fault_class",
        "question",
        "reference_pages",
        "rank",
        "query_text",
        "candidate",
        "status",
        "proposed_at",
        "decided_at",
        "decided_reason",
        "ingested_at",
        "detected_generation",
    }
)


def _candidate_record(**overrides) -> dict[str, object]:
    """A verbatim ``SourceCandidate.to_record()`` payload -- the frozen P2 contract."""
    from knotica.discovery.records import ReputabilityScore, ReputabilityTier, SourceCandidate

    candidate = SourceCandidate(
        url="https://arxiv.org/abs/2302.01318",
        title="Accelerating LLM Inference with Speculative Decoding",
        snippet="We propose a novel decoding scheme...",
        source_provider="exa",
        authors=("Chen", "Borgeaud"),
        venue="ICML 2023",
        published_date="2023-02-02",
        doi="10.48550/arXiv.2302.01318",
        citation_count=412,
        is_open_access=True,
        fwci=8.1,
        provider_score=0.94,
        reputability=ReputabilityScore(
            tier=ReputabilityTier.PEER_REVIEWED,
            score=0.91,
            signals=("venue=ICML", "citations=412", "year=2023"),
        ),
    )
    record = candidate.to_record()
    record.update(overrides)
    return record


def _suggestion_record(**overrides):
    mod = _records_module()
    payload: dict[str, object] = {
        "suggestion_id": "a1b2c3d4e5f60718",
        "topic": "agentic-systems",
        "gap_id": "9f3c1a2b7d4e5f60",
        "qa_id": "q-0007",
        "fault_class": "genuine_gap",
        "question": "How does speculative decoding interact with draft-model verification?",
        "reference_pages": ("Speculative Decoding",),
        "rank": 1,
        "query_text": "speculative decoding draft model verification",
        "candidate": _candidate_record(),
        "status": "pending",
        "proposed_at": "2026-07-19T07:30:00Z",
        "decided_at": None,
        "decided_reason": None,
        "ingested_at": None,
        "detected_generation": 42,
    }
    payload.update(overrides)
    return mod.SuggestionRecord(**payload)


# ---------------------------------------------------------------------------
# Round-trip + frozen field set
# ---------------------------------------------------------------------------


def test_suggestion_record_round_trips_with_identical_fields():
    record = _suggestion_record()

    rendered = record.to_json_line()
    parsed = _records_module().SuggestionRecord.from_json_line(rendered)

    assert parsed == record
    assert parsed.to_json_line() == rendered, (
        "serialization must be a fixed point: parse(render(x)) must render back identically"
    )


def test_suggestion_record_line_carries_exactly_the_frozen_field_set():
    rendered = json.loads(_suggestion_record().to_json_line())

    assert set(rendered) == SUGGESTION_RECORD_FIELDS


def test_suggestion_record_defaults_to_schema_version_one():
    record = _suggestion_record()

    assert record.schema_version == _records_module().SUGGESTION_SCHEMA_VERSION
    assert record.schema_version == 1


def test_suggestion_record_tuples_serialize_as_json_arrays_and_parse_back_as_tuples():
    record = _suggestion_record(reference_pages=("Speculative Decoding", "Draft Models"))

    rendered = json.loads(record.to_json_line())
    assert rendered["reference_pages"] == ["Speculative Decoding", "Draft Models"]

    parsed = _records_module().SuggestionRecord.from_json_line(record.to_json_line())
    assert parsed.reference_pages == ("Speculative Decoding", "Draft Models")


# ---------------------------------------------------------------------------
# The candidate is embedded verbatim -- a discovery-aware consumer rehydrates
# it losslessly (the frozen SourceCandidate must never be embedded lossily).
# ---------------------------------------------------------------------------


def test_embedded_candidate_reconstructs_the_original_source_candidate():
    from knotica.discovery.records import SourceCandidate

    original = SourceCandidate(
        url="https://arxiv.org/abs/2302.01318",
        title="Accelerating LLM Inference with Speculative Decoding",
        snippet="We propose a novel decoding scheme...",
        source_provider="exa",
        doi="10.48550/arXiv.2302.01318",
        citation_count=412,
        is_open_access=True,
    )
    record = _suggestion_record(candidate=original.to_record())

    rendered = json.loads(record.to_json_line())
    rehydrated = SourceCandidate.from_record(rendered["candidate"])

    assert rehydrated == original


# ---------------------------------------------------------------------------
# Enum validation (mirrors GapRecord's fault_class/status validation)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["staged", "in_review", ""])
def test_status_outside_the_lifecycle_domain_is_rejected(status):
    with pytest.raises(ValueError, match="status"):
        _suggestion_record(status=status)


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "deferred", "ingested"])
def test_each_lifecycle_status_round_trips(status):
    record = _suggestion_record(status=status)

    parsed = _records_module().SuggestionRecord.from_json_line(record.to_json_line())

    assert parsed.status == status


def test_schema_version_below_one_is_rejected():
    with pytest.raises(ValueError, match="schema_version"):
        _suggestion_record(schema_version=0)


# ---------------------------------------------------------------------------
# Parse errors (missing/malformed fields)
# ---------------------------------------------------------------------------


def _suggestion_line_missing(field: str) -> str:
    payload = json.loads(_suggestion_record().to_json_line())
    del payload[field]
    return json.dumps(payload)


@pytest.mark.parametrize(
    ("field",),
    [
        pytest.param("suggestion_id", id="missing-suggestion-id"),
        pytest.param("gap_id", id="missing-gap-id"),
        pytest.param("candidate", id="missing-candidate"),
        pytest.param("status", id="missing-status"),
    ],
)
def test_suggestion_line_missing_a_required_field_is_rejected(field):
    mod = _records_module()

    with pytest.raises((mod.RecordParseError, ValueError)):
        mod.SuggestionRecord.from_json_line(_suggestion_line_missing(field))


def test_malformed_suggestion_line_is_rejected():
    mod = _records_module()

    with pytest.raises((mod.RecordParseError, ValueError)):
        mod.SuggestionRecord.from_json_line("not a json object {")


def test_candidate_that_is_not_a_json_object_is_rejected():
    mod = _records_module()
    payload = json.loads(_suggestion_record().to_json_line())
    payload["candidate"] = "not-an-object"

    with pytest.raises((mod.RecordParseError, ValueError)):
        mod.SuggestionRecord.from_json_line(json.dumps(payload))


# ---------------------------------------------------------------------------
# Additive-evolution tolerance (dec-006 discipline)
# ---------------------------------------------------------------------------


def test_future_suggestion_schema_version_with_unknown_fields_still_parses():
    payload = json.loads(_suggestion_record().to_json_line())
    payload["schema_version"] = 2
    payload["novel_field"] = "added by a future proposer_version bump"
    future_line = json.dumps(payload)

    record = _records_module().SuggestionRecord.from_json_line(future_line)

    assert record is not None
    assert record.suggestion_id == "a1b2c3d4e5f60718"


# ---------------------------------------------------------------------------
# parse_suggestions_jsonl -- whole-file parse (mirrors parse_gaps_jsonl)
# ---------------------------------------------------------------------------


def test_parse_suggestions_jsonl_skips_blank_lines_and_preserves_order():
    mod = _records_module()
    first = _suggestion_record(suggestion_id="aaa").to_json_line()
    second = _suggestion_record(suggestion_id="bbb", status="approved").to_json_line()
    text = f"{first}\n\n{second}\n"

    records = mod.parse_suggestions_jsonl(text)

    assert [record.suggestion_id for record in records] == ["aaa", "bbb"]


def test_parse_suggestions_jsonl_reports_the_offending_line_number():
    mod = _records_module()
    good = _suggestion_record().to_json_line()
    text = f"{good}\nnot a json object {{\n"

    with pytest.raises(mod.RecordParseError, match="line 2"):
        mod.parse_suggestions_jsonl(text)

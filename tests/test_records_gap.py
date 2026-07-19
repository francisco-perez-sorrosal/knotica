"""Behavioral contract tests for the ``GapRecord`` schema (``knotica.core.records``).

Derived from ``SYSTEMS_PLAN.md`` §Interfaces (the frozen ``gaps.jsonl`` schema v1
sample) — never from the implementation. ``GapRecord`` mirrors ``QARecord`` /
``MetricsRecord``: a frozen, ``kw_only`` dataclass with a self-describing
``schema_version``, an enum-validated ``fault_class``/``status``, and a
``to_json_line``/``from_json_line`` pair that tolerates unknown future fields.

Production imports are deferred into a helper so collection succeeds before
``GapRecord`` exists (RED-first: the paired implementer step lands the module
concurrently).
"""

import json

import pytest


def _records_module():
    import knotica.core.records

    return knotica.core.records


GAP_EVIDENCE_FIELDS = frozenset(
    {
        "quality_delta",
        "qa_accuracy_delta",
        "citation_validity_delta",
        "retrieval_trace",
        "pages_added",
        "pages_removed",
        "prior_generation",
    }
)

GAP_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "gap_id",
        "topic",
        "qa_id",
        "fault_class",
        "status",
        "classifier_version",
        "detected_generation",
        "detected_at",
        "scalar_at_detection",
        "baseline_scalar",
        "question",
        "reference_pages",
        "reference_pages_exist",
        "evidence",
        "manifest_ref",
        "origin",
        "reported_reason",
    }
)


def _evidence(**overrides):
    mod = _records_module()
    payload = {
        "quality_delta": -0.5,
        "qa_accuracy_delta": -0.5,
        "citation_validity_delta": 0.0,
        "retrieval_trace": ("reflexion", "toolformer"),
        "pages_added": ("newpage",),
        "pages_removed": ("react",),
        "prior_generation": 4,
    }
    payload.update(overrides)
    return mod.GapEvidence(**payload)


def _gap_record(**overrides):
    mod = _records_module()
    payload: dict[str, object] = {
        "gap_id": "abc123def4567890",
        "topic": "agentic-systems",
        "qa_id": "golden-72c431a0cdbd7ead",
        "fault_class": "genuine_gap",
        "status": "open",
        "classifier_version": 1,
        "detected_generation": 5,
        "detected_at": "2026-07-18T23:01:00Z",
        "scalar_at_detection": 0.9493,
        "baseline_scalar": 0.96,
        "question": "How does Reflexion differ from ReAct?",
        "reference_pages": ("react",),
        "reference_pages_exist": False,
        "evidence": _evidence(),
        "manifest_ref": "agentic-systems/.knotica/eval-runs/gen-5/manifest.json",
    }
    payload.update(overrides)
    return mod.GapRecord(**payload)


# ---------------------------------------------------------------------------
# Round-trip + frozen field set
# ---------------------------------------------------------------------------


def test_gap_record_round_trips_with_identical_fields():
    record = _gap_record()

    rendered = record.to_json_line()
    parsed = _records_module().GapRecord.from_json_line(rendered)

    assert parsed == record
    assert parsed.to_json_line() == rendered, (
        "serialization must be a fixed point: parse(render(x)) must render back identically"
    )


def test_gap_record_line_carries_exactly_the_frozen_field_set():
    rendered = json.loads(_gap_record().to_json_line())

    assert set(rendered) == GAP_RECORD_FIELDS
    assert set(rendered["evidence"]) == GAP_EVIDENCE_FIELDS


def test_gap_record_defaults_to_schema_version_one():
    record = _gap_record()

    assert record.schema_version == _records_module().GAP_SCHEMA_VERSION
    assert record.schema_version == 1


def test_gap_record_tuples_serialize_as_json_arrays_and_parse_back_as_tuples():
    record = _gap_record(reference_pages=("react", "toolformer"))

    rendered = json.loads(record.to_json_line())
    assert rendered["reference_pages"] == ["react", "toolformer"]

    parsed = _records_module().GapRecord.from_json_line(record.to_json_line())
    assert parsed.reference_pages == ("react", "toolformer")
    assert parsed.evidence.retrieval_trace == record.evidence.retrieval_trace


# ---------------------------------------------------------------------------
# Enum validation (mirrors QARecord's verdict/source validation)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fault_class", ["generation_fault", "retrieval_fault", "unclassified", ""])
def test_fault_class_outside_the_persisted_domain_is_rejected(fault_class):
    # Only knowledge-cause classes are ever persisted (SYSTEMS_PLAN §P3 Consumer
    # Contract point 3) — generation_fault/retrieval_fault never become gap records.
    with pytest.raises(ValueError, match="fault_class"):
        _gap_record(fault_class=fault_class)


@pytest.mark.parametrize("status", ["pending", "closed", ""])
def test_status_outside_the_lifecycle_domain_is_rejected(status):
    with pytest.raises(ValueError, match="status"):
        _gap_record(status=status)


@pytest.mark.parametrize("fault_class", ["genuine_gap", "dilution"])
def test_each_persisted_fault_class_round_trips(fault_class):
    record = _gap_record(fault_class=fault_class)

    parsed = _records_module().GapRecord.from_json_line(record.to_json_line())

    assert parsed.fault_class == fault_class


@pytest.mark.parametrize("status", ["open", "resolved", "dismissed"])
def test_each_lifecycle_status_round_trips(status):
    record = _gap_record(status=status)

    parsed = _records_module().GapRecord.from_json_line(record.to_json_line())

    assert parsed.status == status


def test_schema_version_below_one_is_rejected():
    with pytest.raises(ValueError, match="schema_version"):
        _gap_record(schema_version=0)


# ---------------------------------------------------------------------------
# origin -- additive provenance field (measured|reported), dec-025/piece-B
# ---------------------------------------------------------------------------


def test_gap_record_defaults_origin_to_measured_when_not_given():
    # ``_gap_record()`` never sets ``origin`` in its payload -- the default
    # applies exactly as it would for any pre-feature construction site.
    record = _gap_record()

    assert record.origin == "measured", (
        "a gap record built without an explicit origin must default to 'measured' -- "
        "every existing (pre-feature) loop-written record is measured"
    )


def test_gap_record_accepts_reported_origin_and_round_trips_it():
    record = _gap_record(origin="reported")

    rendered = json.loads(record.to_json_line())
    parsed = _records_module().GapRecord.from_json_line(record.to_json_line())

    assert rendered["origin"] == "reported"
    assert parsed.origin == "reported"


@pytest.mark.parametrize("origin", ["synthetic", "loop", ""])
def test_origin_outside_the_provenance_domain_is_rejected(origin):
    with pytest.raises(ValueError, match="origin"):
        _gap_record(origin=origin)


def test_gap_record_accepts_retracted_origin_and_round_trips_it():
    # A guillotine-filed retraction gap carries origin="retracted" -- a third
    # provenance value distinct from measured/reported (dec-025 lineage, D).
    record = _gap_record(origin="retracted")

    rendered = json.loads(record.to_json_line())
    parsed = _records_module().GapRecord.from_json_line(record.to_json_line())

    assert rendered["origin"] == "retracted"
    assert parsed.origin == "retracted"


def test_gap_line_missing_origin_defaults_to_measured():
    """A ``gaps.jsonl`` line written before this feature has no ``origin`` key at
    all -- the additive field must default on *parse*, not just on construction,
    so every pre-existing record still parses unaffected."""
    payload = json.loads(_gap_record().to_json_line())
    del payload["origin"]

    record = _records_module().GapRecord.from_json_line(json.dumps(payload))

    assert record.origin == "measured"


# ---------------------------------------------------------------------------
# reported_reason -- additive, persisted advisory context
# ---------------------------------------------------------------------------


def test_gap_record_defaults_reported_reason_to_none_when_not_given():
    record = _gap_record()

    assert record.reported_reason is None, (
        "a gap built without an explicit reported_reason must default to None -- "
        "every existing (pre-feature) record has no reason attached"
    )


def test_gap_record_accepts_a_reported_reason_and_round_trips_it():
    record = _gap_record(reported_reason="the wiki could not answer this at all")

    rendered = json.loads(record.to_json_line())
    parsed = _records_module().GapRecord.from_json_line(record.to_json_line())

    assert rendered["reported_reason"] == "the wiki could not answer this at all"
    assert parsed.reported_reason == "the wiki could not answer this at all"


def test_gap_line_missing_reported_reason_defaults_to_none():
    """A ``gaps.jsonl`` line written before this field existed has no
    ``reported_reason`` key at all -- it must default on parse, so every
    pre-existing record (measured or reported) still parses unaffected."""
    payload = json.loads(_gap_record().to_json_line())
    del payload["reported_reason"]

    record = _records_module().GapRecord.from_json_line(json.dumps(payload))

    assert record.reported_reason is None


# ---------------------------------------------------------------------------
# Parse errors (missing/malformed fields)
# ---------------------------------------------------------------------------


def _gap_line_missing(field: str) -> str:
    payload = json.loads(_gap_record().to_json_line())
    del payload[field]
    return json.dumps(payload)


@pytest.mark.parametrize(
    ("field",),
    [
        pytest.param("qa_id", id="missing-qa-id"),
        pytest.param("fault_class", id="missing-fault-class"),
        pytest.param("gap_id", id="missing-gap-id"),
        pytest.param("evidence", id="missing-evidence"),
    ],
)
def test_gap_line_missing_a_required_field_is_rejected(field):
    mod = _records_module()

    with pytest.raises((mod.RecordParseError, ValueError)):
        mod.GapRecord.from_json_line(_gap_line_missing(field))


def test_malformed_gap_line_is_rejected():
    mod = _records_module()

    with pytest.raises((mod.RecordParseError, ValueError)):
        mod.GapRecord.from_json_line("not a json object {")


# ---------------------------------------------------------------------------
# Additive-evolution tolerance (dec-006 discipline)
# ---------------------------------------------------------------------------


def test_future_gap_schema_version_with_unknown_fields_still_parses():
    payload = json.loads(_gap_record().to_json_line())
    payload["schema_version"] = 2
    payload["novel_field"] = "added by a future classifier_version bump"
    future_line = json.dumps(payload)

    record = _records_module().GapRecord.from_json_line(future_line)

    assert record is not None
    assert record.qa_id == "golden-72c431a0cdbd7ead"


# ---------------------------------------------------------------------------
# parse_gaps_jsonl -- whole-file parse (mirrors parse_qa_jsonl)
# ---------------------------------------------------------------------------


def test_parse_gaps_jsonl_skips_blank_lines_and_preserves_order():
    mod = _records_module()
    first = _gap_record(qa_id="golden-aaa").to_json_line()
    second = _gap_record(qa_id="golden-bbb", fault_class="dilution").to_json_line()
    text = f"{first}\n\n{second}\n"

    records = mod.parse_gaps_jsonl(text)

    assert [record.qa_id for record in records] == ["golden-aaa", "golden-bbb"]


def test_parse_gaps_jsonl_reports_the_offending_line_number():
    mod = _records_module()
    good = _gap_record().to_json_line()
    text = f"{good}\nnot a json object {{\n"

    with pytest.raises(mod.RecordParseError, match="line 2"):
        mod.parse_gaps_jsonl(text)

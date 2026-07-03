"""Behavioral contract for the ``curate_example`` operation.

``curate_example`` appends one curated ``(query, pages_used, answer, verdict)``
example to a topic's ``.knotica/datasets/qa.jsonl`` (the DSPy flywheel) as one
git commit. The appended line is a field-complete record carrying its
``schema_version`` and every frozen ``qa.jsonl`` field, so downstream trainsets
consume it without a migration. It is idempotent by content-hash: re-submitting
an identical example is a no-op (no second line, no second commit). It reports
``example_count`` so a caller can say "N examples, M to compile-ready", and it
refuses a missing topic.

These tests are written from the tool contract, not from the implementation --
they run concurrently with the operation being built and pin observable
behavior (the JSONL file, its records, git history), never internal shape. As
in the companion create_topic suite, the helpers accept either error-surfacing
convention (raised ``KnoticaError`` or an ``{"error": {...}}`` envelope).
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.records import QA_SCHEMA_VERSION, parse_qa_jsonl
from knotica.store import LocalFSStore
from support.vault import (
    git_commit_count,
    git_commit_subjects,
    git_status_porcelain,
    parse_knotica_commit,
    parse_log_entries,
)

# agentic-systems ships in the template with an empty qa.jsonl -- the natural
# target for append behavior.
SEED_TOPIC = "agentic-systems"
QA_DATASET = f"{SEED_TOPIC}/.knotica/datasets/qa.jsonl"

GOOD_EXAMPLE = {
    "query": "What is Agent Workflow Memory?",
    "answer": "A method that induces reusable workflows from an agent's own experience.",
    "verdict": "good",
    "pages_used": ["agent-workflow-memory"],
}


# ---------------------------------------------------------------------------
# Dual-convention operation call helpers (deferred import for the RED handshake)
# ---------------------------------------------------------------------------


def _curate(vault: Path, topic: str, **fields: object) -> object:
    """Invoke the operation under test; imported lazily so collection succeeds.

    Operations are config-agnostic: they take an already-resolved ``store`` and
    ``vault_root``, so the test constructs the store directly on the throwaway
    vault. ``pages_used`` defaults to an empty tuple when a caller omits it.
    """
    from knotica.core.operations.curate_example import curate_example

    fields.setdefault("pages_used", ())
    return curate_example(LocalFSStore(vault), vault, topic, **fields)


def _success_data(result: object) -> Mapping[str, object]:
    """Assert ``result`` is a success envelope and return its data mapping."""
    assert isinstance(result, Mapping), f"expected a result mapping, got {result!r}"
    assert "error" not in result, f"expected success, got an error envelope: {result!r}"
    return result


def _failed(result_or_exc: object) -> bool:
    """Whether an invocation outcome represents a failure (enveloped form)."""
    return isinstance(result_or_exc, Mapping) and "error" in result_or_exc


def _curate_is_rejected(vault: Path, topic: str, **fields: object) -> bool:
    """Whether the operation refuses this example (raised OR enveloped failure)."""
    try:
        result = _curate(vault, topic, **fields)
    except (KnoticaError, ValueError):
        return True
    return _failed(result)


def _error_code_of(vault: Path, topic: str, **fields: object) -> str:
    """Return the error code the operation surfaces (raised OR enveloped)."""
    try:
        result = _curate(vault, topic, **fields)
    except KnoticaError as exc:
        return exc.code.value
    assert _failed(result), f"expected a failure, got a success envelope: {result!r}"
    error = result["error"]  # type: ignore[index]
    assert isinstance(error, Mapping)
    return str(error["code"])


def _read_records(vault: Path) -> list:
    """Parse the seed topic's qa.jsonl into records (validates field-completeness)."""
    return parse_qa_jsonl((vault / QA_DATASET).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Append semantics: a field-complete, schema-versioned record
# ---------------------------------------------------------------------------


def test_appends_a_field_complete_qa_record(template_vault: Path):
    _success_data(_curate(template_vault, SEED_TOPIC, **GOOD_EXAMPLE))

    records = _read_records(template_vault)
    assert len(records) == 1, "exactly one record must be appended"
    record = records[0]
    assert record.schema_version == QA_SCHEMA_VERSION
    assert record.topic == SEED_TOPIC
    assert record.query == GOOD_EXAMPLE["query"]
    assert record.answer == GOOD_EXAMPLE["answer"]
    assert record.verdict == "good"


@pytest.mark.parametrize("verdict", ["good", "bad"])
def test_good_and_bad_verdicts_are_accepted(template_vault: Path, verdict: str):
    example = {**GOOD_EXAMPLE, "verdict": verdict}
    _success_data(_curate(template_vault, SEED_TOPIC, **example))

    records = _read_records(template_vault)
    assert len(records) == 1
    assert records[0].verdict == verdict


def test_an_unknown_verdict_is_refused_and_appends_nothing(
    template_vault: Path,
):
    bad = {**GOOD_EXAMPLE, "verdict": "maybe"}

    assert _curate_is_rejected(template_vault, SEED_TOPIC, **bad), (
        "an unknown verdict must be refused (good|bad are the accepted values)"
    )
    assert _read_records(template_vault) == [], "a refused example must not be appended"


# ---------------------------------------------------------------------------
# example_count and content-hash idempotency
# ---------------------------------------------------------------------------


def test_example_count_reflects_the_dataset(template_vault: Path):
    first = _success_data(_curate(template_vault, SEED_TOPIC, **GOOD_EXAMPLE))
    assert first["example_count"] == 1

    second = {
        **GOOD_EXAMPLE,
        "query": "What does workflow induction extract?",
        "answer": "Reusable, abstracted sub-routines from agent trajectories.",
    }
    result = _success_data(_curate(template_vault, SEED_TOPIC, **second))
    assert result["example_count"] == 2, "example_count must track the number of stored examples"


def test_a_duplicate_example_is_not_appended(template_vault: Path):
    first = _success_data(_curate(template_vault, SEED_TOPIC, **GOOD_EXAMPLE))
    assert first["appended"] is True
    commits_after_first = git_commit_count(template_vault)

    duplicate = _success_data(_curate(template_vault, SEED_TOPIC, **GOOD_EXAMPLE))

    assert duplicate["appended"] is False, "an identical example must not be appended again"
    assert duplicate["example_count"] == 1, "a duplicate must not grow the example count"
    assert len(_read_records(template_vault)) == 1, "the dataset must still hold one record"
    assert git_commit_count(template_vault) == commits_after_first, (
        "a duplicate example must make no second commit"
    )
    assert git_status_porcelain(template_vault) == "", "a duplicate no-op leaves a clean tree"


def test_appends_preserve_prior_records_and_order(template_vault: Path):
    _success_data(_curate(template_vault, SEED_TOPIC, **GOOD_EXAMPLE))
    second = {
        **GOOD_EXAMPLE,
        "query": "What persists in agent memory?",
        "answer": "Persistent experience-derived memory for LM agents.",
    }
    _success_data(_curate(template_vault, SEED_TOPIC, **second))

    raw_lines = [
        line
        for line in (template_vault / QA_DATASET).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(raw_lines) == 2, "each example is one JSONL line (line-atomic append)"

    records = _read_records(template_vault)
    assert [r.query for r in records] == [GOOD_EXAMPLE["query"], second["query"]], (
        "append order must be preserved and the prior record left intact"
    )


# ---------------------------------------------------------------------------
# One commit, frozen grammar, and the missing-topic refusal
# ---------------------------------------------------------------------------


def test_curate_makes_exactly_one_commit_following_the_frozen_grammar(
    template_vault: Path,
):
    commits_before = git_commit_count(template_vault)

    _success_data(_curate(template_vault, SEED_TOPIC, **GOOD_EXAMPLE))

    assert git_commit_count(template_vault) == commits_before + 1, "one effective op is one commit"
    assert git_status_porcelain(template_vault) == "", "the tree must be clean after the commit"

    parsed = parse_knotica_commit(git_commit_subjects(template_vault)[0])
    assert parsed is not None, "the commit subject must follow the frozen grammar"
    assert parsed["op"] == "curate_example"
    assert parsed["topic"] == SEED_TOPIC

    log_entries = parse_log_entries((template_vault / "log.md").read_text(encoding="utf-8"))
    assert log_entries[-1].op == "curate_example"
    assert log_entries[-1].topic == SEED_TOPIC


def test_curating_into_a_missing_topic_is_refused(template_vault: Path):
    commits_before = git_commit_count(template_vault)

    code = _error_code_of(template_vault, "no-such-topic", **GOOD_EXAMPLE)

    assert code == ErrorCode.TOPIC_NOT_FOUND.value, (
        "curating into a topic that does not exist must fail with TOPIC_NOT_FOUND"
    )
    assert git_commit_count(template_vault) == commits_before, "a refused curate makes no commit"
    assert git_status_porcelain(template_vault) == "", "a refused curate leaves a clean tree"

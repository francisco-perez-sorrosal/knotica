"""Behavioral contract for the ``create_topic`` operation.

``create_topic`` scaffolds a brand-new topic in one git commit: its directory, an
empty overlay ``SCHEMA.md`` that inherits the root constitution (divergence is
earned, so the overlay starts empty), the hidden ``.knotica/`` state (an empty
``datasets/qa.jsonl`` plus empty ``prompts/`` and ``compiled/`` dirs), and a
catalog entry in the root ``index.md``. ``metrics.jsonl`` is deliberately NOT
created here -- it is a lazy artifact whose absence means "not yet evaluated".
Creating a topic whose name collides with a reserved top-level name is refused;
recreating an existing topic is a no-op (no second commit).

These tests are written from the tool contract, not from the implementation --
they run concurrently with the operation being built and pin observable
behavior (files on disk, git history, schema resolution), never internal shape.

Operation-call conventions this suite is deliberately robust to (both are
consistent with the vault's established error contract, where core *raises*
``KnoticaError`` and adapters render the envelope): a failing operation may
either raise ``KnoticaError`` or return an ``{"error": {...}}`` envelope, and a
succeeding operation returns a mapping of result fields (a plain dict or an
``ok(...)`` envelope). The helpers below accept either form.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.schema import resolve_schema
from knotica.store import LocalFSStore
from support.vault import (
    git_commit_count,
    git_commit_subjects,
    git_status_porcelain,
    parse_knotica_commit,
    parse_log_entries,
)

# The reserved top-level namespace create_topic must protect (REQ-TOOL-03 set).
RESERVED_TOP_LEVEL_NAMES = [
    "sources",
    "index.md",
    "log.md",
    "SCHEMA.md",
    "START_HERE.md",
    ".knotica",
    ".git",
]

NEW_TOPIC = "reinforcement-learning"


# ---------------------------------------------------------------------------
# Dual-convention operation call helpers (deferred import for the RED handshake)
# ---------------------------------------------------------------------------


def _create_topic(vault: Path, topic: str, **kwargs: object) -> object:
    """Invoke the operation under test; imported lazily so collection succeeds.

    Operations are config-agnostic: they take an already-resolved ``store`` and
    ``vault_root`` (config resolution belongs to the adapter), so the test
    constructs the store directly on the throwaway vault.
    """
    from knotica.core.operations.create_topic import create_topic

    return create_topic(LocalFSStore(vault), vault, topic, **kwargs)


def _success_data(result: object) -> Mapping[str, object]:
    """Assert ``result`` is a success envelope and return its data mapping."""
    assert isinstance(result, Mapping), f"expected a result mapping, got {result!r}"
    assert "error" not in result, f"expected success, got an error envelope: {result!r}"
    return result


def _error_code_of(vault: Path, topic: str, **kwargs: object) -> str:
    """Return the error code the operation surfaces (raised OR enveloped)."""
    try:
        result = _create_topic(vault, topic, **kwargs)
    except KnoticaError as exc:
        return exc.code.value
    assert isinstance(result, Mapping) and "error" in result, (
        f"expected a failure (raised KnoticaError or error envelope), got: {result!r}"
    )
    error = result["error"]
    assert isinstance(error, Mapping)
    return str(error["code"])


@dataclass
class TopicCreation:
    """The outcome of creating a fresh topic, with its pre-creation git baseline."""

    result: Mapping[str, object]
    vault: Path
    topic: str
    commits_before: int


@pytest.fixture
def created_topic(template_vault: Path) -> TopicCreation:
    """Create ``NEW_TOPIC`` in a fresh throwaway vault; capture the baseline."""
    commits_before = git_commit_count(template_vault)
    result = _success_data(_create_topic(template_vault, NEW_TOPIC))
    return TopicCreation(
        result=result,
        vault=template_vault,
        topic=NEW_TOPIC,
        commits_before=commits_before,
    )


# ---------------------------------------------------------------------------
# Scaffolding: exactly the contracted files, and no more
# ---------------------------------------------------------------------------


def test_new_topic_creates_its_directory_and_schema_overlay(created_topic: TopicCreation):
    topic_dir = created_topic.vault / created_topic.topic
    assert topic_dir.is_dir(), "the topic directory was not scaffolded"
    assert (topic_dir / "SCHEMA.md").is_file(), "the overlay SCHEMA.md was not scaffolded"


def test_new_topic_scaffolds_an_empty_qa_dataset(created_topic: TopicCreation):
    dataset = created_topic.vault / created_topic.topic / ".knotica" / "datasets" / "qa.jsonl"
    assert dataset.is_file(), "the .knotica/datasets/qa.jsonl dataset was not scaffolded"
    assert dataset.read_text(encoding="utf-8") == "", "a fresh qa.jsonl must start empty"


def test_new_topic_does_not_create_metrics_jsonl(created_topic: TopicCreation):
    metrics = created_topic.vault / created_topic.topic / ".knotica" / "metrics.jsonl"
    assert not metrics.exists(), (
        "metrics.jsonl must NOT be created at topic creation -- it is a lazy "
        "artifact whose absence means 'not yet evaluated'"
    )


def test_new_topic_scaffolds_empty_prompts_and_compiled_dirs(created_topic: TopicCreation):
    knotica_dir = created_topic.vault / created_topic.topic / ".knotica"
    assert (knotica_dir / "prompts").is_dir(), "the .knotica/prompts/ dir was not scaffolded"
    assert (knotica_dir / "compiled").is_dir(), "the .knotica/compiled/ dir was not scaffolded"


def test_new_topic_adds_a_catalog_entry_to_root_index(created_topic: TopicCreation):
    index = (created_topic.vault / "index.md").read_text(encoding="utf-8")
    assert created_topic.topic in index, "the new topic was not added to the root index.md catalog"


# ---------------------------------------------------------------------------
# One commit, clean tree, frozen grammar (VaultTransaction composition)
# ---------------------------------------------------------------------------


def test_new_topic_makes_exactly_one_commit_with_a_clean_tree(created_topic: TopicCreation):
    assert git_commit_count(created_topic.vault) == created_topic.commits_before + 1, (
        "scaffolding a topic must be exactly one commit"
    )
    assert git_status_porcelain(created_topic.vault) == "", (
        "the working tree must be clean after the transaction commits"
    )


def test_new_topic_commit_and_log_follow_the_frozen_grammar(created_topic: TopicCreation):
    newest_subject = git_commit_subjects(created_topic.vault)[0]
    parsed = parse_knotica_commit(newest_subject)
    assert parsed is not None, f"commit subject broke the frozen grammar: {newest_subject!r}"
    assert parsed["op"] == "create_topic"
    assert parsed["topic"] == created_topic.topic

    log_entries = parse_log_entries((created_topic.vault / "log.md").read_text(encoding="utf-8"))
    latest = log_entries[-1]
    assert latest.op == "create_topic"
    assert latest.topic == created_topic.topic


# ---------------------------------------------------------------------------
# The overlay is a valid, inheriting, non-contradicting schema layer
# ---------------------------------------------------------------------------


def test_created_overlay_inherits_root_schema_and_adds_no_contradiction(
    created_topic: TopicCreation,
):
    store = LocalFSStore(created_topic.vault)
    resolved = resolve_schema(store, created_topic.topic)

    assert resolved.overlay is not None, "create_topic must scaffold a topic overlay layer"
    assert resolved.schema_version == resolved.root.schema_version, (
        "the new topic's effective schema version must inherit the root constitution's"
    )
    assert resolved.root.body and resolved.root.body in resolved.merged, (
        "the resolved schema must carry the root constitution (the overlay inherits it)"
    )


# ---------------------------------------------------------------------------
# Reserved-name refusal and idempotency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reserved_name", RESERVED_TOP_LEVEL_NAMES)
def test_reserved_top_level_name_is_refused(template_vault: Path, reserved_name: str):
    commits_before = git_commit_count(template_vault)

    code = _error_code_of(template_vault, reserved_name)

    assert code == ErrorCode.RESERVED_NAME.value, (
        f"creating reserved name {reserved_name!r} must fail with RESERVED_NAME"
    )
    assert git_commit_count(template_vault) == commits_before, (
        "a refused create_topic must make no commit"
    )
    assert git_status_porcelain(template_vault) == "", "a refused create must leave a clean tree"


def test_recreating_an_existing_topic_is_a_no_op(template_vault: Path):
    # agentic-systems ships in the template; recreating it must change nothing.
    commits_before = git_commit_count(template_vault)

    result = _success_data(_create_topic(template_vault, "agentic-systems"))

    assert result.get("existed") is True, "recreating an existing topic must report existed=True"
    assert git_commit_count(template_vault) == commits_before, (
        "an existing-topic no-op must make no commit"
    )
    assert git_status_porcelain(template_vault) == "", "an existing-topic no-op leaves a clean tree"


def test_creating_a_fresh_topic_reports_it_did_not_already_exist(created_topic: TopicCreation):
    assert created_topic.result.get("existed") is False, (
        "a freshly created topic must report existed=False"
    )

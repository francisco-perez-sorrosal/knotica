"""The synthetic-gap topic guard: a gap is filed AGAINST a topic, never a way
to create one.

A one-word conversational report (`report_gap(topic="physics",
question="physics")`) once scaffolded a whole stray topic in a real vault,
which the loop runner then began tending (baseline probe, observations). Every
other topic-scoped mutation (store_source, write_page, promote_note,
baseline_probe) already guards existence through ``require_topic``; these
tests pin that the two synthetic-gap entry points now do too — refusing with
the typed topic-not-found error, opening no transaction, making no commit,
and leaving no directory behind.
"""

from pathlib import Path

import pytest

from knotica.core.page import TopicNotFoundError
from knotica.store import LocalFSStore
from support.vault import git_commit_count


def _gapfill_module():
    import knotica.core.gapfill

    return knotica.core.gapfill


def test_report_gap_refuses_a_nonexistent_topic_and_creates_nothing(
    template_vault: Path,
) -> None:
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    before_count = git_commit_count(template_vault)

    with pytest.raises(TopicNotFoundError):
        mod.report_gap(store, template_vault, "physics", question="physics")

    assert git_commit_count(template_vault) == before_count, (
        "a refused report must make zero commits"
    )
    assert not (template_vault / "physics").exists(), (
        "a refused report must leave no topic scaffold behind"
    )


def test_retracted_gap_refuses_a_nonexistent_topic_the_same_way(
    template_vault: Path,
) -> None:
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    before_count = git_commit_count(template_vault)

    with pytest.raises(TopicNotFoundError):
        mod.file_retracted_gap(
            store,
            template_vault,
            "no-such-topic",
            "a weakened claim",
            verdict="RETRACT",
            report_path="guillotine/report.md",
        )

    assert git_commit_count(template_vault) == before_count
    assert not (template_vault / "no-such-topic").exists()

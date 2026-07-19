"""Behavioral contract tests for ``knotica.core.source_ingest`` -- the
client-driven ingest session lifecycle (open / publish / abandon).

The contract under test: an approved gap-fill suggestion is ingested by a
sequence of client-driven writes that must never touch the vault's default
working tree or ref while the ingest is in progress. ``open_ingest`` opens (or
resumes) a private worktree checked out on its own ``loop/wip/<topic>/
source-<id8>`` branch, deterministically derived from the suggestion id --
never restarted on re-open, so a multi-turn ingest can be resumed exactly
where it left off. ``publish_ingest`` finalizes it by atomically renaming that
branch onto the public ``loop/c/<topic>/source-<id8>`` name and tearing down
the worktree; ``abandon_ingest`` discards a crashed/discarded session leaving
no trace.

Only an ``approved`` suggestion may be opened -- every other lifecycle state
(``pending``, ``rejected``, ``deferred``, ``ingested``) is refused with a
typed, actionable error, before any worktree is created.

RED-first: ``knotica.core.source_ingest`` does not exist yet when this file is
written (the paired implementer step lands the module concurrently) -- the
module is resolved lazily inside a helper so collection succeeds and the
first run fails with ``ModuleNotFoundError``, not a collection error. This
file was written without reading the implementer's code.

The exact return shape of ``open_ingest`` (dataclass vs. mapping) is a
paired-step negotiable the plan text does not pin beyond field names
(``candidate``, ``state``, ``resume``, ``provenance``); ``_field`` reads
either shape so the tests exercise behavior, not a guessed structural choice.
``publish_ingest``/``abandon_ingest`` are exercised by passing back exactly
what ``open_ingest`` returned -- the plan's own language ("opaque handle")
for the finalize/discard boundary.
"""

from pathlib import Path

import pytest

from knotica.core import gapfill
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"


def _source_ingest_module():
    import knotica.core.source_ingest

    return knotica.core.source_ingest


def _field(obj: object, name: str) -> object:
    """Read ``name`` off either a mapping or a dataclass/object return shape."""
    if isinstance(obj, dict):
        return obj[name]
    return getattr(obj, name)


# ---------------------------------------------------------------------------
# Fixture builders -- a real approved (or other-state) suggestion, built and
# transitioned through the real gapfill decision state machine, never
# hand-forged into a status the machine itself would refuse to reach.
# ---------------------------------------------------------------------------


def _gap_record(*, gap_id: str, qa_id: str, **overrides):
    from knotica.core.records import GapEvidence, GapRecord

    evidence = GapEvidence(
        quality_delta=-0.5,
        qa_accuracy_delta=-0.5,
        citation_validity_delta=0.0,
        retrieval_trace=(),
        pages_added=(),
        pages_removed=(),
        prior_generation=4,
    )
    payload: dict[str, object] = {
        "gap_id": gap_id,
        "topic": TOPIC,
        "qa_id": qa_id,
        "fault_class": "genuine_gap",
        "status": "open",
        "classifier_version": 1,
        "detected_generation": 5,
        "detected_at": "2026-07-18T23:01:00Z",
        "scalar_at_detection": 0.9493,
        "baseline_scalar": 0.96,
        "question": f"What is the retrieval augmentation story for {qa_id}?",
        "reference_pages": ("agent-workflow-memory",),
        "reference_pages_exist": False,
        "evidence": evidence,
        "manifest_ref": "agentic-systems/.knotica/eval-runs/gen-5/manifest.json",
    }
    payload.update(overrides)
    return GapRecord(**payload)


def _candidate(**overrides):
    from knotica.discovery.records import SourceCandidate

    payload: dict[str, object] = {
        "url": "https://arxiv.org/abs/2409.07429",
        "title": "Agent Workflow Memory",
        "snippet": "We propose inducing reusable workflows from past experience...",
        "source_provider": "fake",
        "doi": "10.48550/arXiv.2409.07429",
        "citation_count": 12,
    }
    payload.update(overrides)
    return SourceCandidate(**payload)


def _seed_suggestions(store, root, topic: str, records) -> None:
    from knotica.core.transaction import VaultTransaction

    path = gapfill.suggestions_path(topic)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(
        store, Path(root), "test_seed", topic, "seed suggestions for test"
    ) as txn:
        txn.write(path, body)


def _suggestion(
    template_vault: Path,
    store,
    *,
    qa_id: str,
    gap_id: str,
    status: str = "pending",
    reason: str | None = None,
) -> str:
    """Build one suggestion and drive it to ``status`` via the real decision
    state machine (approve / reject / defer / mark_ingested) -- never a
    hand-forged record, so a fixture can never reach a status the production
    lifecycle itself would refuse."""
    gap = _gap_record(gap_id=gap_id, qa_id=qa_id)
    records = gapfill.build_suggestion_records(
        gap, [_candidate()], proposer_version=1, clock=lambda: "2026-07-19T00:00:00Z"
    )
    _seed_suggestions(store, template_vault, TOPIC, records)
    suggestion_id = records[0].suggestion_id

    if status == "pending":
        return suggestion_id
    if status == "approved":
        gapfill.apply_decision(store, template_vault, TOPIC, suggestion_id, decision="approve")
        return suggestion_id
    if status == "rejected":
        gapfill.apply_decision(
            store,
            template_vault,
            TOPIC,
            suggestion_id,
            decision="reject",
            reason=reason or "not a fit for this topic",
        )
        return suggestion_id
    if status == "deferred":
        gapfill.apply_decision(store, template_vault, TOPIC, suggestion_id, decision="defer")
        return suggestion_id
    if status == "ingested":
        gapfill.apply_decision(store, template_vault, TOPIC, suggestion_id, decision="approve")
        gapfill.apply_decision(
            store, template_vault, TOPIC, suggestion_id, decision="mark_ingested"
        )
        return suggestion_id
    raise ValueError(f"unsupported fixture status: {status!r}")


# ---------------------------------------------------------------------------
# open_ingest -- happy path: private worktree + branch, deterministic naming
# ---------------------------------------------------------------------------


def test_open_ingest_on_an_approved_suggestion_creates_a_private_worktree_branch(
    template_vault: Path,
) -> None:
    mod = _source_ingest_module()
    store = LocalFSStore(template_vault)
    suggestion_id = _suggestion(
        template_vault, store, qa_id="golden-open", gap_id="gap-open", status="approved"
    )
    vcs = VaultVcs(template_vault)
    canonical_head = vcs.head_sha()
    canonical_branch = vcs.current_branch()

    handle = mod.open_ingest(store, template_vault, TOPIC, suggestion_id)

    expected_branch = f"loop/wip/{TOPIC}/source-{suggestion_id[:8]}"
    assert _field(handle, "candidate") == expected_branch
    assert _field(handle, "state") == "created"
    assert vcs.branch_exists(expected_branch)
    worktree_branches = {entry.get("branch") for entry in vcs.list_worktrees()}
    assert expected_branch in worktree_branches
    assert vcs.ref_sha(expected_branch) == canonical_head, (
        "a fresh ingest must branch off the canonical vault's HEAD at open time"
    )
    # Opening the ingest must never move the canonical vault's own checkout.
    assert vcs.head_sha() == canonical_head
    assert vcs.current_branch() == canonical_branch


def test_open_ingest_returns_the_suggestions_provenance(template_vault: Path) -> None:
    mod = _source_ingest_module()
    store = LocalFSStore(template_vault)
    suggestion_id = _suggestion(
        template_vault, store, qa_id="golden-prov", gap_id="gap-prov", status="approved"
    )

    handle = mod.open_ingest(store, template_vault, TOPIC, suggestion_id)

    provenance = _field(handle, "provenance")
    assert _field(provenance, "suggestion_id") == suggestion_id
    assert _field(provenance, "gap_id") == "gap-prov"
    assert _field(provenance, "qa_id") == "golden-prov"
    assert (
        _field(provenance, "query_text")
        == "What is the retrieval augmentation story for golden-prov?"
    )


# ---------------------------------------------------------------------------
# open_ingest -- refusal on any non-approved lifecycle state, before any
# worktree/branch is ever created (fail fast, no partial state left behind)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["pending", "rejected", "deferred", "ingested"])
def test_open_ingest_on_a_non_approved_suggestion_is_refused(
    template_vault: Path, status: str
) -> None:
    mod = _source_ingest_module()
    store = LocalFSStore(template_vault)
    suggestion_id = _suggestion(
        template_vault, store, qa_id=f"golden-{status}", gap_id=f"gap-{status}", status=status
    )

    with pytest.raises(KnoticaError) as excinfo:
        mod.open_ingest(store, template_vault, TOPIC, suggestion_id)

    assert excinfo.value.code == ErrorCode.SUGGESTION_NOT_APPROVED
    assert excinfo.value.fix, "the refusal must carry an actionable fix, not a bare code"
    expected_branch = f"loop/wip/{TOPIC}/source-{suggestion_id[:8]}"
    assert not VaultVcs(template_vault).branch_exists(expected_branch), (
        "a refused open must never leave a WIP branch behind"
    )


# ---------------------------------------------------------------------------
# open_ingest -- re-opening resumes; the branch is never restarted
# ---------------------------------------------------------------------------


def test_reopening_an_in_progress_ingest_resumes_without_restarting_the_branch(
    template_vault: Path,
) -> None:
    mod = _source_ingest_module()
    store = LocalFSStore(template_vault)
    suggestion_id = _suggestion(
        template_vault, store, qa_id="golden-resume", gap_id="gap-resume", status="approved"
    )
    first = mod.open_ingest(store, template_vault, TOPIC, suggestion_id)
    candidate = _field(first, "candidate")

    # Simulate partial client progress -- a source and one page already
    # committed on the WIP branch. Seeded via a direct VaultVcs commit
    # (mirroring this suite's established test-only-seeding pattern) because
    # the candidate-scoped store_source/write_page wiring this step precedes
    # is not yet in scope.
    vcs = VaultVcs(template_vault)
    worktree_entry = next(
        entry for entry in vcs.list_worktrees() if entry.get("branch") == candidate
    )
    worktree_path = Path(worktree_entry["path"])
    (worktree_path / "sources" / TOPIC).mkdir(parents=True, exist_ok=True)
    (worktree_path / "sources" / TOPIC / "wang2024awm.md").write_text(
        "---\nurl: https://arxiv.org/abs/2409.07429\n---\nsource body\n", encoding="utf-8"
    )
    (worktree_path / TOPIC / "agent-workflow-memory.md").write_text(
        "# Agent Workflow Memory\n\nbody\n", encoding="utf-8"
    )
    worktree_vcs = VaultVcs(worktree_path)
    worktree_vcs.commit_paths(
        [f"sources/{TOPIC}/wang2024awm.md", f"{TOPIC}/agent-workflow-memory.md"],
        "knotica(write_page): agentic-systems — seed partial ingest for test",
    )
    tip_before_resume = worktree_vcs.head_sha()

    second = mod.open_ingest(store, template_vault, TOPIC, suggestion_id)

    assert _field(second, "candidate") == candidate
    assert _field(second, "state") == "resumed"
    resume = _field(second, "resume")
    assert _field(resume, "source_present") is True
    pages_present = _field(resume, "pages_present")
    assert any("agent-workflow-memory" in str(page) for page in pages_present), (
        f"resume.pages_present must reflect the already-committed page, got {pages_present!r}"
    )
    # The re-open must never reset or recreate the branch -- prior work survives.
    assert VaultVcs(worktree_path).head_sha() == tip_before_resume
    assert vcs.ref_sha(candidate) == tip_before_resume


# ---------------------------------------------------------------------------
# publish_ingest -- atomic rename onto the public candidate name, worktree torn down
# ---------------------------------------------------------------------------


def test_publish_ingest_renames_the_wip_branch_onto_the_public_candidate_and_tears_down_the_worktree(
    template_vault: Path,
) -> None:
    mod = _source_ingest_module()
    store = LocalFSStore(template_vault)
    suggestion_id = _suggestion(
        template_vault, store, qa_id="golden-publish", gap_id="gap-publish", status="approved"
    )
    handle = mod.open_ingest(store, template_vault, TOPIC, suggestion_id)
    wip_branch = _field(handle, "candidate")
    vcs = VaultVcs(template_vault)
    wip_tip = vcs.ref_sha(wip_branch)

    mod.publish_ingest(handle)

    expected_public_branch = f"loop/c/{TOPIC}/source-{suggestion_id[:8]}"
    assert not vcs.branch_exists(wip_branch), "the WIP branch name must be gone after publish"
    assert vcs.branch_exists(expected_public_branch)
    assert vcs.ref_sha(expected_public_branch) == wip_tip, (
        "publish must be a pure rename -- the tip SHA is unchanged by publishing"
    )
    worktree_branches = {entry.get("branch") for entry in vcs.list_worktrees()}
    assert wip_branch not in worktree_branches
    assert expected_public_branch not in worktree_branches, (
        "the worktree must be removed once the branch is published -- no lingering checkout"
    )


# ---------------------------------------------------------------------------
# abandon_ingest -- crashed/discarded session leaves no orphan state
# ---------------------------------------------------------------------------


def test_abandon_ingest_removes_the_worktree_and_deletes_the_wip_branch(
    template_vault: Path,
) -> None:
    mod = _source_ingest_module()
    store = LocalFSStore(template_vault)
    suggestion_id = _suggestion(
        template_vault, store, qa_id="golden-abandon", gap_id="gap-abandon", status="approved"
    )
    handle = mod.open_ingest(store, template_vault, TOPIC, suggestion_id)
    wip_branch = _field(handle, "candidate")

    mod.abandon_ingest(handle)

    vcs = VaultVcs(template_vault)
    assert not vcs.branch_exists(wip_branch), (
        "abandon must delete the WIP branch outright, not just orphan it"
    )
    worktree_branches = {entry.get("branch") for entry in vcs.list_worktrees()}
    assert wip_branch not in worktree_branches

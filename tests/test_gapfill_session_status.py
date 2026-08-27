"""Behavioral contract tests for the Fill lane's read-only session projection
-- the nine-state watch contract at ``INTERFACE_DESIGN.md`` §3.3 (dec-091).

An approved suggestion's ingest session moves through branch and record state
that only git (and the suggestion record) actually knows about -- the client
never reports in. This module drives that state through the *real* production
lifecycle (``source_ingest.open_ingest``/``publish_ingest``, a real
``LoopRunner`` gate cycle) rather than hand-forging branches, so each fixture
is a git/record layout an actual user session can reach, then asserts the
projection classifies it into the one right-named state with the one
right-named ``next.actor`` -- the anti-dead-end guarantee dec-091 exists for.

Two things this file also pins directly, because they are stated risks, not
incidental behavior: the **cost discipline** (2-3 git subprocesses per call,
never a batch-shaped signature) and the **read-only guarantee** (observing a
session must never advance a branch or make a commit) -- the second closes
the concurrent M2 pre-mortem's item 3 ("rail state leaks into client-side
derivation"): a projection that mutated what it reads would be exactly that
leak, just via git instead of the wire.

RED-first: ``knotica.core.gapfill_session`` does not exist when this file is
written -- the paired implementer step lands the module concurrently. The
module is resolved lazily inside a helper so collection succeeds and the
first run fails with ``ModuleNotFoundError`` inside each test body, not a
collection error hiding the rest of the file. Written without reading the
implementation; every assertion is derived from ``INTERFACE_DESIGN.md``
§3.3's own predicate table.

Three load-bearing assumptions the paired implementation wins on conflict
(full reasoning in ``LEARNINGS_test-engineer_step51.md``):

1. **Signature**: ``session_status(store, root, topic, suggestion_id)``,
   mirroring ``source_ingest.open_ingest``'s own parameter order -- there is
   no existing call site to consult. The return shape (mapping vs.
   dataclass) is read through ``_field`` below so either choice satisfies
   these tests.
2. **``blocked`` outranks the other eight rows.** ``blocked``'s own predicate
   ("no frozen baseline") names nothing about branches or suggestion status,
   so for the nine rows to be mutually exclusive it must be evaluated ahead
   of (or independent of) the rest. Every other fixture therefore freezes a
   baseline explicitly; only ``blocked``'s own fixture leaves it unset.
3. **``swept`` is a live staleness check on a still-present branch, not a
   post-removal state.** If the WIP branch were already gone, its git shape
   would be identical to ``not_started``'s, and the paired implementation's
   own done-when condition ("every one of the nine states is reachable from a
   fixture branch/record layout") would be unmet. So the fixture backdates
   the WIP branch's tip commit past 24h rather than removing the branch.

Zero network, zero billing: every writer under test is real vault/git I/O; the
one path that could reach a credentialed client (a passing gate cycle's
post-merge trainset grower) is stubbed.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import tempfile
import typing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from knotica.core import gapfill, source_ingest
from knotica.core.branch_namespaces import quarantine_branch_name
from knotica.core.loop import LoopRunner, wrap_harness_result
from knotica.core.loop_state import empty_loop_state, write_loop_state
from knotica.core.records import MetricsComponents, MetricsRecord, parse_suggestions_jsonl
from knotica.core.vcs import VaultVcs
from knotica.evals.harness import EvalRunResult
from knotica.store import LocalFSStore
from support.vault import git_status_porcelain

TOPIC = "agentic-systems"
PAGE = "agent-workflow-memory"
SOURCE_KEY = "session-status-fixture"
HARNESS = "fake-session-status-gate"
BASELINE = 0.80

#: The read contract's own cost ceiling (INTERFACE_DESIGN.md §3.3, REQ-22b).
MAX_GIT_SUBPROCESSES_PER_CALL = 3


def _session_status_fn():
    """The projection under test, imported at call time (RED handshake)."""
    from knotica.core.gapfill_session import session_status

    return session_status


def _field(obj: object, name: str) -> object:
    """Read ``name`` off either a mapping or a dataclass/object return shape."""
    if isinstance(obj, dict):
        return obj[name]
    return getattr(obj, name)


def _state_of(result: object) -> object:
    return _field(result, "state")


def _actor_of(result: object) -> object:
    return _field(_field(result, "next"), "actor")


# ---------------------------------------------------------------------------
# Suggestion + gate fixture builders -- real production writers throughout,
# never a hand-forged branch or a guessed internal shape.
# ---------------------------------------------------------------------------


def _gap_record(*, gap_id: str, qa_id: str):
    from knotica.core.records import GapEvidence, GapRecord

    return GapRecord(
        gap_id=gap_id,
        topic=TOPIC,
        qa_id=qa_id,
        fault_class="genuine_gap",
        status="open",
        classifier_version=1,
        detected_generation=5,
        detected_at="2026-07-18T23:01:00Z",
        scalar_at_detection=0.9493,
        baseline_scalar=0.96,
        question=f"What is the retrieval augmentation story for {qa_id}?",
        reference_pages=(PAGE,),
        reference_pages_exist=False,
        evidence=GapEvidence(
            quality_delta=-0.5,
            qa_accuracy_delta=-0.5,
            citation_validity_delta=0.0,
            retrieval_trace=(),
            pages_added=(),
            pages_removed=(),
            prior_generation=4,
        ),
        manifest_ref=f"{TOPIC}/.knotica/eval-runs/gen-5/manifest.json",
    )


def _candidate_source():
    from knotica.discovery.records import SourceCandidate

    return SourceCandidate(
        url="https://arxiv.org/abs/2409.07429",
        title="Agent Workflow Memory",
        snippet="We propose inducing reusable workflows from past experience...",
        source_provider="fake",
        doi="10.48550/arXiv.2409.07429",
        citation_count=12,
    )


def _approved_suggestion(vault: Path, store: LocalFSStore, *, gap_id: str, qa_id: str) -> str:
    """An approved suggestion, driven through the real decision state machine."""
    from knotica.core.transaction import VaultTransaction

    gap = _gap_record(gap_id=gap_id, qa_id=qa_id)
    records = gapfill.build_suggestion_records(
        gap, [_candidate_source()], proposer_version=1, clock=lambda: "2026-07-19T00:00:00Z"
    )
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed suggestions") as txn:
        txn.write(
            gapfill.suggestions_path(TOPIC), "".join(r.to_json_line() + "\n" for r in records)
        )
    suggestion_id = records[0].suggestion_id
    gapfill.apply_decision(store, vault, TOPIC, suggestion_id, decision="approve")
    return suggestion_id


def _freeze_baseline(vault: Path, store: LocalFSStore, *, scalar: float = BASELINE) -> None:
    """Freeze a gate baseline directly (no ``LoopRunner`` needed) -- every
    state's implicit "a baseline exists" precondition except ``blocked``'s."""
    write_loop_state(
        store, vault, empty_loop_state(TOPIC).model_copy(update={"baseline_scalar": scalar})
    )


def _worktree_path(vault: Path, branch: str) -> Path:
    entry = next(e for e in VaultVcs(vault).list_worktrees() if e.get("branch") == branch)
    return Path(entry["path"])


def _write_worktree_content(vault: Path, handle, *, body: str) -> None:
    """Commit one source chunk and one page onto an open session's worktree."""
    worktree = _worktree_path(vault, handle.candidate)
    source = worktree / "sources" / TOPIC / f"{SOURCE_KEY}.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(f"# {SOURCE_KEY}\n\nverbatim stored source text\n", encoding="utf-8")
    page = worktree / TOPIC / f"{PAGE}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"# Agent Workflow Memory\n\n{body}\n", encoding="utf-8")
    VaultVcs(worktree).commit_paths(
        [f"sources/{TOPIC}/{SOURCE_KEY}.md", f"{TOPIC}/{PAGE}.md"],
        f"knotica(write_page): {TOPIC} — ingest {PAGE}",
    )


def _commit_stale_marker(vault: Path, handle, *, hours_ago: float) -> None:
    """Commit an out-of-band marker onto the session's WIP branch, backdated.

    The marker lives under the topic's own ``.knotica/`` scratch directory and
    is neither under ``sources/<topic>/`` nor a ``.md`` page, so it can never
    be mistaken for written content -- a swept session must read as *nothing
    written*, not as abandoned work in progress.
    """
    worktree = _worktree_path(vault, handle.candidate)
    marker = worktree / TOPIC / ".knotica" / "session-status-fixture-marker.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('{"note": "backdated fixture marker"}\n', encoding="utf-8")

    stale = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_DATE": stale,
        "GIT_COMMITTER_DATE": stale,
    }
    relpath = str(marker.relative_to(worktree))
    subprocess.run(
        ["git", "-C", str(worktree), "add", "--", relpath],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-m", "backdated fixture marker"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def _stub_headless_trainset_grower(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the real Anthropic-backed post-merge trainset grower, so a
    passing gate cycle completes deterministically and network-free."""
    monkeypatch.setattr("knotica.evals.llm.AnthropicClient", lambda: object())
    monkeypatch.setattr("knotica.evals.train_bootstrap.bootstrap_trainset", lambda *_a, **_k: {})


def _fake_evaluate(scalar: float):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-session-status-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-19T00:00:00Z",
            generation=1,
            harness_version=HARNESS,
            scalar=float(scalar),
            components=MetricsComponents(
                qa_accuracy=float(scalar),
                citation_validity=1.0,
                lint_violations=0.0,
                token_cost=0.0,
            ),
            n_examples=1,
            corpus_ref=f"git:{clone.head_sha()}",
            artifact_ref=None,
        )
        return wrap_harness_result(EvalRunResult(record=record, clone_root=clone.root))

    return _evaluate


def _suggestion_record(store: LocalFSStore, suggestion_id: str):
    records = parse_suggestions_jsonl(store.read_text(gapfill.suggestions_path(TOPIC)))
    return next(r for r in records if r.suggestion_id == suggestion_id)


def _refuse_the_published_candidate(vault: Path, store: LocalFSStore, suggestion_id: str) -> None:
    """Drive a real, regressing gate cycle to a refusal (freezes the baseline)."""
    runner = LoopRunner(vault, TOPIC, evaluate=_fake_evaluate(0.50), branch_prefix="loop/c/")
    runner.set_baseline(BASELINE, harness_version=HARNESS)
    result = runner.poll_once()
    assert result.acted is True, "the fixture must produce a real gate cycle"
    record = _suggestion_record(store, suggestion_id)
    assert record.gate_outcome is not None
    assert record.gate_outcome["verdict"] == "refused", "the fixture must produce a real refusal"


def _merge_the_published_candidate(
    vault: Path, store: LocalFSStore, suggestion_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive a real, passing gate cycle to a merge (freezes the baseline)."""
    _stub_headless_trainset_grower(monkeypatch)
    runner = LoopRunner(vault, TOPIC, evaluate=_fake_evaluate(0.95), branch_prefix="loop/c/")
    runner.set_baseline(BASELINE, harness_version=HARNESS)
    result = runner.poll_once()
    assert result.acted is True, "the fixture must produce a real gate cycle"
    record = _suggestion_record(store, suggestion_id)
    assert record.gate_outcome is not None
    assert record.gate_outcome["verdict"] == "merged", "the fixture must produce a real merge"


# ---------------------------------------------------------------------------
# Per-state fixture builders -- one per INTERFACE_DESIGN.md §3.3 row
# ---------------------------------------------------------------------------


def _build_not_started(vault: Path, store: LocalFSStore) -> str:
    suggestion_id = _approved_suggestion(vault, store, gap_id="gap-not-started", qa_id="qa-1")
    _freeze_baseline(vault, store)
    return suggestion_id


def _build_waiting_on_client(
    vault: Path, store: LocalFSStore, *, gap_id: str = "gap-waiting", qa_id: str = "qa-2"
):
    suggestion_id = _approved_suggestion(vault, store, gap_id=gap_id, qa_id=qa_id)
    _freeze_baseline(vault, store)
    handle = source_ingest.open_ingest(store, vault, TOPIC, suggestion_id)
    return suggestion_id, handle


def _build_client_wrote(
    vault: Path, store: LocalFSStore, *, gap_id: str = "gap-client-wrote", qa_id: str = "qa-3"
):
    suggestion_id, handle = _build_waiting_on_client(vault, store, gap_id=gap_id, qa_id=qa_id)
    _write_worktree_content(vault, handle, body="first draft")
    return suggestion_id, handle


def _build_submitted(vault: Path, store: LocalFSStore) -> str:
    suggestion_id, handle = _build_client_wrote(vault, store, gap_id="gap-submitted", qa_id="qa-4")
    source_ingest.publish_ingest(handle)
    return suggestion_id


def _build_refused(vault: Path, store: LocalFSStore) -> str:
    suggestion_id, handle = _build_client_wrote(vault, store, gap_id="gap-refused", qa_id="qa-5")
    source_ingest.publish_ingest(handle)
    _refuse_the_published_candidate(vault, store, suggestion_id)
    return suggestion_id


def _build_rework_in_flight(vault: Path, store: LocalFSStore):
    suggestion_id = _build_refused(vault, store)
    handle = source_ingest.open_ingest(store, vault, TOPIC, suggestion_id)
    assert handle.state == "resumed", "the fixture must be a real reopen of a refused session"
    assert handle.resume.restored_from == quarantine_branch_name(TOPIC, suggestion_id)
    return suggestion_id, handle


def _build_merged(vault: Path, store: LocalFSStore, monkeypatch: pytest.MonkeyPatch) -> str:
    suggestion_id, handle = _build_client_wrote(vault, store, gap_id="gap-merged", qa_id="qa-6")
    source_ingest.publish_ingest(handle)
    _merge_the_published_candidate(vault, store, suggestion_id, monkeypatch)
    return suggestion_id


def _build_blocked(vault: Path, store: LocalFSStore) -> str:
    return _approved_suggestion(vault, store, gap_id="gap-blocked", qa_id="qa-7")


def _build_swept(vault: Path, store: LocalFSStore):
    suggestion_id, handle = _build_waiting_on_client(vault, store, gap_id="gap-swept", qa_id="qa-8")
    _commit_stale_marker(vault, handle, hours_ago=48)
    assert VaultVcs(vault).branch_exists(handle.candidate), (
        "a swept session's branch must still exist at read time -- staleness is a live "
        "check, not a post-removal state (see the module docstring's assumption 3)"
    )
    return suggestion_id, handle


# ---------------------------------------------------------------------------
# The nine states, each reached from a fixture matching its own git/record
# shape, none guessed from the implementation.
# ---------------------------------------------------------------------------


def test_not_started_when_an_approved_suggestion_has_no_session_yet(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id = _build_not_started(template_vault, store)
    fn = _session_status_fn()

    result = fn(store, template_vault, TOPIC, suggestion_id)

    assert _state_of(result) == "not_started"
    assert _actor_of(result) == "you"


def test_waiting_on_client_when_a_session_is_open_but_nothing_is_written(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id, _handle = _build_waiting_on_client(template_vault, store)
    fn = _session_status_fn()

    result = fn(store, template_vault, TOPIC, suggestion_id)

    assert _state_of(result) == "waiting_on_client"
    assert _actor_of(result) == "claude"


def test_client_wrote_once_a_source_and_page_are_committed_to_the_open_session(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id, _handle = _build_client_wrote(template_vault, store)
    fn = _session_status_fn()

    result = fn(store, template_vault, TOPIC, suggestion_id)

    assert _state_of(result) == "client_wrote"
    assert _actor_of(result) == "you"


def test_rework_in_flight_when_a_refused_session_is_reopened(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id, _handle = _build_rework_in_flight(template_vault, store)
    fn = _session_status_fn()

    result = fn(store, template_vault, TOPIC, suggestion_id)

    assert _state_of(result) == "rework_in_flight"
    assert _actor_of(result) == "claude"


def test_submitted_once_a_session_is_published_but_not_yet_gated(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id = _build_submitted(template_vault, store)
    fn = _session_status_fn()

    result = fn(store, template_vault, TOPIC, suggestion_id)

    assert _state_of(result) == "submitted"
    assert _actor_of(result) == "system"


def test_merged_once_the_gate_accepts_the_published_candidate(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id = _build_merged(template_vault, store, monkeypatch)
    fn = _session_status_fn()

    result = fn(store, template_vault, TOPIC, suggestion_id)

    assert _state_of(result) == "merged"
    assert _actor_of(result) == "none"


def test_refused_while_a_quarantined_session_awaits_rework(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id = _build_refused(template_vault, store)
    fn = _session_status_fn()

    result = fn(store, template_vault, TOPIC, suggestion_id)

    assert _state_of(result) == "refused"
    assert _actor_of(result) == "you"


def test_blocked_when_no_baseline_has_ever_been_frozen(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id = _build_blocked(template_vault, store)
    fn = _session_status_fn()

    result = fn(store, template_vault, TOPIC, suggestion_id)

    assert _state_of(result) == "blocked"
    assert _actor_of(result) == "you"


def test_swept_when_an_open_sessions_branch_has_gone_stale(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id, _handle = _build_swept(template_vault, store)
    fn = _session_status_fn()

    result = fn(store, template_vault, TOPIC, suggestion_id)

    assert _state_of(result) == "swept"
    assert _actor_of(result) == "you"


# ---------------------------------------------------------------------------
# Cost discipline and the unrepresentable-batch guarantee (REQ-22b)
# ---------------------------------------------------------------------------


def test_the_projection_never_exceeds_its_git_subprocess_budget(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read contract budgets 2-3 git subprocesses per suggestion -- checked
    against the deepest row (a reopened, quarantine-restored session), which
    needs a WIP check, a quarantine check, and a resume diff."""
    store = LocalFSStore(template_vault)
    suggestion_id, _handle = _build_rework_in_flight(template_vault, store)
    fn = _session_status_fn()

    real_run = subprocess.run
    calls: list[object] = []

    def _counting_run(*args, **kwargs):
        calls.append(args)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _counting_run)

    fn(store, template_vault, TOPIC, suggestion_id)

    assert len(calls) <= MAX_GIT_SUBPROCESSES_PER_CALL, (
        f"INTERFACE_DESIGN.md §3.3 budgets {MAX_GIT_SUBPROCESSES_PER_CALL} git subprocesses "
        f"per suggestion; observed {len(calls)} for one call"
    )


def test_the_projection_takes_a_single_suggestion_id_not_a_batch(template_vault: Path) -> None:
    """REQ-22b: a batch call must be unrepresentable, not merely undocumented --
    the signature itself must carry no list/collection-shaped parameter."""
    fn = _session_status_fn()
    parameters = inspect.signature(fn).parameters

    assert "suggestion_id" in parameters, (
        "the read contract names a single suggestion_id, not a batch"
    )
    assert "suggestion_ids" not in parameters

    annotation = parameters["suggestion_id"].annotation
    origin = typing.get_origin(annotation)
    assert origin not in (list, tuple, set, frozenset), (
        f"suggestion_id must be a scalar id, not a collection-shaped annotation ({annotation!r})"
    )


# ---------------------------------------------------------------------------
# Read-only guarantee (M2 pre-mortem item 3)
# ---------------------------------------------------------------------------


def test_observing_a_session_makes_no_commit_and_leaves_every_branch_tip_untouched(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id, _handle = _build_client_wrote(
        template_vault, store, gap_id="gap-read-only", qa_id="qa-9"
    )
    vcs = VaultVcs(template_vault)
    before_status = git_status_porcelain(template_vault)
    before_branches = sorted(vcs.list_branch_tips("loop/"))
    fn = _session_status_fn()

    fn(store, template_vault, TOPIC, suggestion_id)

    assert git_status_porcelain(template_vault) == before_status, (
        "observing a session must never dirty the canonical vault's working tree"
    )
    assert sorted(vcs.list_branch_tips("loop/")) == before_branches, (
        "observing a session must never create, move, or delete a loop/* branch tip"
    )

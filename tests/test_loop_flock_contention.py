"""Flock-contention integration tests for the widened vault-mutation span.

A background watcher pass (``observe_default``) and a synchronous gate pass
(``poll_once`` → keep) are two separate processes contending on one live vault.
Before the span lock was widened, the two passes' checkout/merge/branch-delete
sequences interleaved at the git-porcelain level and corrupted the working tree
(``MERGE_HEAD exists`` / ``cannot lock ref`` / ``cannot do a partial commit
during a merge``) in ~60-65% of barrier-synced runs.

The contract under test:

1. **No corruption under contention.** Two barrier-synced full passes on the
   same vault both complete with no git error, no leftover ``MERGE_HEAD``, no
   stale ``index.lock`` — across many consecutive runs.
2. **Span-entry self-heal.** A crash mid-span auto-releases the flock (OS) but
   can leave a dangling ``MERGE_HEAD`` / stale ``index.lock``; the next span
   acquisition clears them and proceeds.
3. **Bounded acquire, never a hang.** A span that cannot get the flock returns
   a typed retryable ``LOCK_BUSY`` within the timeout bound, not a hang.

Real git on ``template_vault``; evaluate is injected (zero network).
"""

from __future__ import annotations

import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.loop import EvalOutcome, build_loop_runner, wrap_harness_result
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.transaction import VaultTransaction, vault_mutation_span
from knotica.core.vcs import VaultVcs
from knotica.evals.harness import EvalRunResult
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"
CANDIDATE = "loop/c/wound"
BASELINE = 0.50
#: Both passes score above baseline so each takes its keep/merge branch — the
#: contiguous checkout/merge/delete span that used to race.
PASS_SCALAR = 0.60

#: Consecutive barrier-synced runs the fix must survive clean (parametrized so
#: each run gets a fresh vault and is an independent, deterministic case).
CONSECUTIVE_CONTENTION_RUNS = 20

#: Generous wall-clock ceiling that proves "bounded, not a hang" without flaking
#: on a loaded machine.
HANG_CEILING_SECONDS = 5.0

#: Git error fragments that mark the working-tree corruption this fix eliminates.
_CORRUPTION_SIGNATURES = (
    "merge_head",
    "cannot lock ref",
    "partial commit during a merge",
    "would be overwritten by merge",
)


def _fake_evaluate(
    scalar: float,
    marker_name: str,
    barrier: threading.Barrier,
) -> Callable[[str, Path, str | None], EvalOutcome]:
    """An injected evaluate that clones, drops a distinct marker, and barrier-waits.

    The clone + marker commit is the real eval clone the merge span pulls home.
    The barrier is released at the END of eval, so both passes emerge from their
    (clone-based, unlocked) eval simultaneously and race into their git-mutation
    spans at the same instant — the worst case for interleaving. Distinct marker
    files per pass keep the two merges from producing an artificial *content*
    conflict, isolating the pure timing race the span lock must serialize.
    """

    def _evaluate(topic: str, source_root: Path, ref: str | None) -> EvalOutcome:
        dest = Path(tempfile.mkdtemp(prefix="knotica-contention-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        marker = clone.root / TOPIC / ".knotica" / marker_name
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"scalar={scalar}\n", encoding="utf-8")
        run_git(clone.root, "add", "-A")
        run_git(clone.root, "commit", "-m", f"eval marker {marker_name}")
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-21T00:00:00Z",
            generation=1,
            harness_version="fake-contention",
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
        barrier.wait(timeout=30)
        return wrap_harness_result(EvalRunResult(record=record, clone_root=clone.root))

    return _evaluate


def _open_candidate(vault: Path, body: str) -> None:
    """Publish a ``loop/c/wound`` prompt candidate for the gate pass to process."""
    vcs = VaultVcs(vault)
    default = vcs.default_branch()
    vcs.checkout_branch(default)
    if vcs.branch_exists(CANDIDATE):
        vcs.delete_branch(CANDIDATE, force=True)
    vcs.create_branch(CANDIDATE, default)
    vcs.checkout_branch(CANDIDATE)
    wound = vault / ".knotica" / "prompts" / "query.md"
    wound.parent.mkdir(parents=True, exist_ok=True)
    wound.write_text(body, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: wound query.md")
    vcs.checkout_branch(default)


def _run_capturing(fn: Callable[[], object]) -> Callable[[], None]:
    """Wrap a pass so its return value or exception is captured for post-join asserts."""
    captured: dict[str, object] = {}

    def _runner() -> None:
        try:
            captured["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 — re-surfaced by the caller
            captured["error"] = exc

    _runner.captured = captured  # type: ignore[attr-defined]
    return _runner


@pytest.mark.slow
@pytest.mark.parametrize("run_index", range(CONSECUTIVE_CONTENTION_RUNS))
def test_concurrent_watcher_and_gate_passes_never_corrupt_git(
    template_vault: Path, run_index: int
) -> None:
    # A fresh vault per parametrized case: 20 independent, barrier-synced runs.
    barrier = threading.Barrier(2)
    watcher = build_loop_runner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(PASS_SCALAR, "watcher-marker.txt", barrier),
    )
    watcher.set_baseline(BASELINE, harness_version="fake-contention")
    gate = build_loop_runner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(PASS_SCALAR, "gate-marker.txt", barrier),
        branch_prefix="loop/c/",
    )
    _open_candidate(template_vault, "# healed query\n")

    watcher_pass = _run_capturing(lambda: watcher.observe_default())
    gate_pass = _run_capturing(lambda: gate.poll_once())
    watcher_thread = threading.Thread(target=watcher_pass, daemon=True)
    gate_thread = threading.Thread(target=gate_pass, daemon=True)
    watcher_thread.start()
    gate_thread.start()
    watcher_thread.join(timeout=60)
    gate_thread.join(timeout=60)

    watcher_out = watcher_pass.captured  # type: ignore[attr-defined]
    gate_out = gate_pass.captured  # type: ignore[attr-defined]
    assert watcher_out.get("error") is None, f"watcher raised: {watcher_out.get('error')!r}"
    assert gate_out.get("error") is None, f"gate raised: {gate_out.get('error')!r}"
    # Non-vacuity: each pass must actually take its merge span, not early-return.
    watcher_result = watcher_out.get("result")
    gate_result = gate_out.get("result")
    assert watcher_result is not None and watcher_result.acted is True, "watcher did not act"
    assert gate_result is not None and gate_result.acted is True, "gate did not act"

    vcs = VaultVcs(template_vault)
    assert not vcs.is_merge_in_progress(), "a dangling merge survived the contention"
    assert not (template_vault / ".git" / "index.lock").exists(), "a stale index.lock survived"
    assert not vcs.branch_exists(CANDIDATE), "the gate pass never consumed its candidate"


def _induce_dangling_merge(vault: Path) -> None:
    """Leave a real in-progress (conflicted) merge on ``vault`` — a crash remnant."""
    vcs = VaultVcs(vault)
    default = vcs.default_branch()
    conflict_path = vault / TOPIC / "conflict.md"
    conflict_path.parent.mkdir(parents=True, exist_ok=True)

    conflict_path.write_text("base\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: conflict base")

    vcs.create_branch("crash/side", default)
    vcs.checkout_branch("crash/side")
    conflict_path.write_text("side change\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: side change")

    vcs.checkout_branch(default)
    conflict_path.write_text("default change\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: default change")

    # A conflicting merge left unresolved: git exits non-zero and MERGE_HEAD
    # stays. Run it directly (``run_git`` raises on non-zero) so the expected
    # failure leaves the crash remnant in place.
    merge = subprocess.run(
        ["git", "-C", str(vault), "merge", "crash/side"],
        capture_output=True,
        text=True,
    )
    assert merge.returncode != 0, "precondition: the induced merge must conflict"
    assert vcs.is_merge_in_progress(), "precondition: the induced merge must be in progress"


def test_span_entry_self_heals_a_crash_left_merge_and_index_lock(template_vault: Path) -> None:
    _induce_dangling_merge(template_vault)
    # Simulate the other crash remnant: a stale index.lock the dead process left.
    stale_lock = template_vault / ".git" / "index.lock"
    stale_lock.write_text("", encoding="utf-8")

    store = LocalFSStore(template_vault)
    # Entering a mutation span heals both remnants, then a commit inside it lands.
    with vault_mutation_span(template_vault):
        vcs = VaultVcs(template_vault)
        assert not vcs.is_merge_in_progress(), "span entry must abort the dangling merge"
        assert not stale_lock.exists(), "span entry must clear the stale index.lock"
        with VaultTransaction(store, template_vault, "loop", TOPIC, "post-heal write") as txn:
            txn.write(f"{TOPIC}/post-heal.md", "healed and proceeding\n")

    assert store.exists(f"{TOPIC}/post-heal.md"), "the span proceeded past the self-heal"
    assert not VaultVcs(template_vault).is_merge_in_progress()


@contextmanager
def _span_held_in_thread(vault: Path) -> Iterator[None]:
    """Hold a mutation span (real flock) in a separate thread until exit.

    A different thread carries its own, empty, thread-local span depth, so it
    takes the real flock and genuinely contends with the main thread.
    """
    held = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        with vault_mutation_span(vault):
            held.set()
            release.wait(timeout=30)

    holder = threading.Thread(target=_hold, daemon=True)
    holder.start()
    try:
        assert held.wait(timeout=10), "span-holder thread never acquired the flock"
        yield
    finally:
        release.set()
        holder.join(timeout=10)


def test_span_busy_returns_retryable_lock_busy_within_the_bound(template_vault: Path) -> None:
    with _span_held_in_thread(template_vault):
        started = time.monotonic()
        with pytest.raises(KnoticaError) as caught:
            with vault_mutation_span(template_vault, lock_timeout=0.2):
                pytest.fail("a second span must not enter while the flock is held")
        elapsed = time.monotonic() - started

    assert caught.value.code is ErrorCode.LOCK_BUSY
    assert caught.value.retryable is True
    assert elapsed < HANG_CEILING_SECONDS, "acquisition must be bounded by the timeout, not hang"

"""Wound → red → revert → green cycle for :class:`~knotica.core.loop.LoopRunner`.

Zero network: evaluate is injected. Real git branches on ``template_vault``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from knotica.core.loop import LoopDecision, LoopRunner, wrap_harness_result
from knotica.core.loop_state import read_loop_state
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.status import gather_wiki_status
from knotica.core.vcs import VaultVcs
from knotica.evals.harness import EvalRunResult
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"
CANDIDATE = "loop/c/wound"


def _fake_evaluate(scalar: float):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-m2-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        # Drop a marker file so a keep-merge is observable on the default branch.
        marker = clone.root / TOPIC / ".knotica" / "loop-eval-marker.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"scalar={scalar}\n", encoding="utf-8")
        run_git(clone.root, "add", "-A")
        run_git(clone.root, "commit", "-m", f"eval: record scalar {scalar}")
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-17T00:00:00Z",
            generation=1,
            harness_version="fake-m2",
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


def _open_candidate(vault: Path, body: str) -> str:
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
    sha = vcs.head_sha()
    vcs.checkout_branch(default)
    return sha


def test_wound_red_revert_then_green_keep(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.40),
        branch_prefix="loop/c/",
    )
    runner.set_baseline(0.5707, harness_version="fake-m2")

    # --- red path ---
    _open_candidate(template_vault, "# wounded query\n")
    red = runner.poll_once()
    assert red.acted is True
    assert red.decision is LoopDecision.fail
    assert not VaultVcs(template_vault).branch_exists(CANDIDATE)

    status = gather_wiki_status(store, template_vault, topic=TOPIC)
    assert status["gate"]["baseline"] == 0.5707
    assert status["gate"]["state"] == "fail"
    assert status["loop"]["last_decision"] == "fail"
    assert status["loop"]["stage"] == "failed"

    # Restart mid-cycle survival: cursors prevent reprocessing the same tip.
    noop = runner.poll_once()
    assert noop.acted is False

    # --- green path ---
    runner_ok = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.60),
        branch_prefix="loop/c/",
    )
    _open_candidate(template_vault, "# healed query\n")
    green = runner_ok.poll_once()
    assert green.acted is True
    assert green.decision is LoopDecision.pass_
    assert not VaultVcs(template_vault).branch_exists(CANDIDATE)

    marker = template_vault / TOPIC / ".knotica" / "loop-eval-marker.txt"
    assert marker.is_file(), "keep path must merge the eval clone tip onto default"
    assert "0.6" in marker.read_text(encoding="utf-8")

    status = gather_wiki_status(store, template_vault, topic=TOPIC)
    assert status["gate"]["state"] == "pass"
    assert status["loop"]["stage"] == "passed"
    assert status["loop"]["last_decision"] == "pass"

    state = read_loop_state(store, TOPIC)
    assert state is not None
    assert state.baseline_scalar == 0.5707

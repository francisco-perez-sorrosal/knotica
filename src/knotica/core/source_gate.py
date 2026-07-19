"""Source-candidate gate -- classify by branch name, merge or quarantine.

A ``source`` candidate is a discovered source that an approved gap-fill
suggestion was ingested into, published (by :mod:`knotica.core.source_ingest`)
at ``loop/c/<topic>/source-<id8>``. Any other ``loop/c/*`` tip is a ``prompt``
candidate -- today's arena / keep / discard flow. The loop's
``_process_candidate`` reads a candidate's kind from its branch name alone (a
pure function of the name, no persisted cross-call state -- dec-004) and, for a
source candidate, delegates here instead of falling through to the prompt path.

Two outcomes, and one hard rule between them:

* **pass** (scalar holds the baseline) -- the source closed its gap without
  regressing other answers: fast-forward it onto the default branch (the loop's
  own ``_keep`` flow) and auto-advance the linked suggestion
  ``approved -> ingested`` with a ``merged`` ``gate_outcome``.
* **refuse** (scalar regressed) -- the source diluted the wiki: the candidate
  branch is *renamed* (never deleted) to ``loop/x/<topic>/source-<id8>`` so its
  work plus a bounded per-question dilution diff survive as a durable audit
  trail; the suggestion stays ``approved`` (re-workable) and gains a ``refused``
  ``gate_outcome``. ``loop/x/*`` is pruned to the newest five per topic.
* **never the arena.** A content-dilution regression is not prompt-fixable, and
  racing prompt variants against it risks a variant that *masks* the dilution --
  the reward-hacking hazard the autoresearch defenses exclude. The arena heals
  prompt regressions only; a source candidate never enters it.

This module is loop-side: it reaches into the :class:`~knotica.core.loop.LoopRunner`
that drives a cycle (its git surface, store, and the ``_keep`` merge flow) rather
than re-implementing them, which is the whole point of the extraction -- keeping
the source-gate logic out of the already-large ``loop.py`` (td-008) without
duplicating its merge choreography. ``gapfill`` is imported lazily inside the
functions that need it, mirroring ``loop.py``'s own discipline.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Literal

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.loop import RESULT_BRANCH_PREFIX, LoopCycleResult
from knotica.core.loop_state import LoopDecision, LoopStage, write_loop_state

# Reuse the source-candidate naming convention from its minting module (single
# source of truth for the ``loop/c/`` prefix and the ``source-`` infix) rather
# than re-deriving the literals here.
from knotica.core.source_ingest import CANDIDATE_BRANCH_PREFIX, _SOURCE_INFIX
from knotica.core.transaction import VaultTransaction

if TYPE_CHECKING:
    from knotica.core.loop import EvalOutcome, LoopRunner
    from knotica.core.loop_state import LoopState

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "QUARANTINE_BRANCH_PREFIX",
    "classify_candidate",
    "gate_source_candidate",
    "handle_source_pass",
    "handle_source_refuse",
    "suggestion_id_from_branch",
]

#: A refused source candidate is renamed here (kept, never deleted) -- invisible
#: to the loop's ``loop/c/`` candidate scan, but preserved as an audit trail.
QUARANTINE_BRANCH_PREFIX = "loop/x/"

#: Where a refused candidate's bounded per-question dilution diff is committed on
#: its quarantine branch (mirrors the per-topic ``.knotica/`` placement of gaps
#: and suggestions).
_QUARANTINE_DIFF_DIR = ".knotica/quarantine"

#: Op slot for the best-effort quarantine-diff commit (the loop's own op name).
_QUARANTINE_OP = "loop"

#: Newest quarantine branches kept per topic (mirrors ``_prune_result_branches``).
_QUARANTINE_KEEP = 5

#: Bounded top-N per-question dilution rows carried on a refusal (agent-pagination
#: cap -- the full manifest is the pointer target).
_MAX_REGRESSED_QUESTIONS = 10

#: Mirrors :mod:`knotica.evals.golden`'s own (private, unexported) constant --
#: a fixed vault-layout literal, not shared logic, so a small local
#: redeclaration is preferable to reaching into another module's private
#: surface (the same precedent :mod:`knotica.core.source_ingest` sets for
#: ``_SOURCES_DIR``).
_SCHEMA_OVERLAY_FILENAME = "SCHEMA.md"


def classify_candidate(branch: str) -> Literal["source", "prompt"] | None:
    """Classify a candidate branch by name alone (no state, no git read).

    Returns ``"source"`` for a ``loop/c/<topic>/source-<id8>`` branch,
    ``"prompt"`` for any other ``loop/c/*`` tip (today's arena/keep/discard
    candidate), and ``None`` for a branch that is not a candidate at all.
    """
    if not branch.startswith(CANDIDATE_BRANCH_PREFIX):
        return None
    topic, sep, leaf = branch.removeprefix(CANDIDATE_BRANCH_PREFIX).partition("/")
    if sep and topic and leaf.startswith(_SOURCE_INFIX) and leaf.removeprefix(_SOURCE_INFIX):
        return "source"
    return "prompt"


def suggestion_id_from_branch(branch: str) -> str:
    """Recover the ``id8`` a source candidate branch encodes.

    The branch carries the linked suggestion's id truncated to its infix length
    (``suggestion_id[:8]``); the full id is resolved against ``suggestions.jsonl``
    at gate time (see :func:`_resolve_suggestion_id`). Raises ``ValueError`` for a
    branch that is not a source candidate.
    """
    _topic, id8 = _parse_candidate_branch(branch)
    return id8


def gate_source_candidate(
    runner: "LoopRunner",
    state: "LoopState",
    branch: str,
    sha: str,
    outcome: "EvalOutcome",
) -> LoopCycleResult:
    """Gate one already-evaluated source candidate: merge on pass, quarantine on fail.

    The single delegation entry ``loop.py``'s ``_process_candidate`` calls. A
    source candidate is **never** routed through the arena on either outcome --
    that is the load-bearing reward-hacking guard.
    """
    passed = float(outcome.scalar) >= float(state.baseline_scalar or 0.0)
    if passed:
        return handle_source_pass(runner, state, branch, sha, outcome)
    return handle_source_refuse(runner, state, branch, sha, outcome)


def handle_source_pass(
    runner: "LoopRunner",
    state: "LoopState",
    branch: str,
    sha: str,
    outcome: "EvalOutcome",
) -> LoopCycleResult:
    """Merge a passing source candidate; auto-advance its suggestion to ``ingested``.

    Reuses the loop's own ``_keep`` flow (fetch eval tip -> FF-merge onto the
    default branch -> drop the candidate -> prune), then stamps the linked
    suggestion ``approved -> ingested`` with a ``merged`` ``gate_outcome``. The
    suggestion id is resolved *before* the merge so a missing record fails fast,
    without leaving a merged-but-unstamped source behind. After the stamp, a
    best-effort trainset grower (:func:`_grow_trainset_from_merge`) seeds
    examples for exactly the pages this merge landed -- never load-bearing for
    the merge itself.
    """
    from knotica.core.gapfill import GATE_VERDICT_MERGED, apply_gate_outcome

    topic, id8 = _parse_candidate_branch(branch)
    suggestion_id = _resolve_suggestion_id(runner, topic, id8)
    # Read-only: the default branch's tip *before* the merge, independent of
    # whatever is currently checked out (``_keep`` checks out default itself).
    merge_base = runner._vcs.ref_sha(runner._vcs.default_branch())
    result = runner._keep(state, branch, sha, outcome)
    # ``_keep`` checks out the default branch (or raises); re-verify before the
    # load-bearing record commit so it can never land on the wrong branch.
    _require_default_checkout(runner)
    gate_outcome = {
        "verdict": GATE_VERDICT_MERGED,
        "scalar": float(outcome.scalar),
        "baseline_scalar": float(state.baseline_scalar or 0.0),
        "ref": f"{RESULT_BRANCH_PREFIX}{sha[:12]}",
    }
    apply_gate_outcome(
        runner._store,
        runner._root,
        topic,
        suggestion_id,
        verdict=GATE_VERDICT_MERGED,
        gate_outcome=gate_outcome,
    )
    _grow_trainset_from_merge(runner, topic, merge_base, runner._vcs.head_sha())
    return result


def handle_source_refuse(
    runner: "LoopRunner",
    state: "LoopState",
    branch: str,
    sha: str,
    outcome: "EvalOutcome",
) -> LoopCycleResult:
    """Quarantine a regressing source candidate; leave its suggestion re-workable.

    Renames ``loop/c/<topic>/source-<id8>`` -> ``loop/x/<topic>/source-<id8>``
    (kept, invisible to the ``loop/c/`` scan), commits a bounded per-question
    dilution diff onto the quarantine branch (best-effort), stamps a ``refused``
    ``gate_outcome`` on the still-``approved`` suggestion, prunes older quarantine
    branches, and records the failed cycle in loop-state. The candidate is
    **never** raced through the arena.
    """
    from knotica.core.gapfill import GATE_VERDICT_REFUSED, apply_gate_outcome

    topic, id8 = _parse_candidate_branch(branch)
    suggestion_id = _resolve_suggestion_id(runner, topic, id8)
    quarantine = f"{QUARANTINE_BRANCH_PREFIX}{topic}/{_SOURCE_INFIX}{id8}"
    regressed = _regressed_questions(outcome, topic)

    runner._vcs.publish_branch(branch, quarantine)
    _commit_quarantine_diff(runner, quarantine, topic, id8, regressed)
    # The diff commit above briefly checked out the quarantine branch; verify
    # the checkout is back on default before the load-bearing record commit so a
    # failed restore fails loud rather than misrouting the commit onto loop/x.
    _require_default_checkout(runner)
    apply_gate_outcome(
        runner._store,
        runner._root,
        topic,
        suggestion_id,
        verdict=GATE_VERDICT_REFUSED,
        gate_outcome={
            "verdict": GATE_VERDICT_REFUSED,
            "scalar": float(outcome.scalar),
            "baseline_scalar": float(state.baseline_scalar or 0.0),
            "ref": quarantine,
            "reason": _refusal_reason(outcome, state, regressed),
            "regressed_questions": list(regressed),
        },
    )
    _prune_quarantine_branches(runner, topic)
    _record_refusal_state(runner, state, branch, sha, outcome)
    return LoopCycleResult(
        acted=True,
        branch=branch,
        sha=sha,
        decision=LoopDecision.fail,
        scalar=float(outcome.scalar),
        message=f"source refused; quarantined at {quarantine}",
    )


# ---------------------------------------------------------------------------
# Post-merge trainset grower (best-effort, git-derived page subset)
# ---------------------------------------------------------------------------


def _grow_trainset_from_merge(
    runner: "LoopRunner", topic: str, merge_base: str, merged_head: str
) -> None:
    """Best-effort: seed trainset examples for the pages this merge just landed.

    Restricted to exactly the pages ``changed_paths(merge_base, merged_head)``
    reports and :func:`_is_entity_page_path` accepts -- never a client-reported
    list. Runs loop-side, on the default branch the merge already landed on
    (AC-8: the interactive ingest path never touches an LLM), reusing the same
    lazily-imported ``AnthropicClient``/``WORKER_SNAPSHOT`` pairing the
    ``bootstrap-train`` CLI already wires headlessly. Mirrors
    ``_prune_result_branches``'s best-effort discipline: a missing credential,
    a transient API error, or an empty page set is logged and swallowed here,
    never re-raised -- the merge this runs after has already committed and
    must never be failed or rolled back by a grower problem.
    """
    changed_pages = [
        path
        for path in runner._vcs.changed_paths(merge_base, merged_head)
        if _is_entity_page_path(topic, path)
    ]
    if not changed_pages:
        return
    try:
        from knotica.evals.config import WORKER_SNAPSHOT
        from knotica.evals.llm import AnthropicClient
        from knotica.evals.train_bootstrap import bootstrap_trainset

        bootstrap_trainset(
            runner._store,
            runner._root,
            topic,
            AnthropicClient(),
            WORKER_SNAPSHOT,
            pages=changed_pages,
        )
    except KnoticaError as error:
        _LOGGER.info("skipping post-merge trainset grower for %s: %s", topic, error)
    except Exception:  # noqa: BLE001 -- the grower is best-effort; never fails an already-merged candidate
        _LOGGER.warning(
            "post-merge trainset grower failed for %s (best-effort, merge unaffected)",
            topic,
            exc_info=True,
        )


def _is_entity_page_path(topic: str, path: str) -> bool:
    """True when a git-changed ``path`` is a page :func:`entity_pages` would read.

    Mirrors :func:`knotica.evals.golden.entity_pages`'s own filtering -- every
    ``.md`` page under the topic, bar the schema overlay and any dot-prefixed
    segment (e.g. ``<topic>/.knotica/...``) -- without importing its private
    constant (see :data:`_SCHEMA_OVERLAY_FILENAME`).
    """
    prefix = f"{topic}/"
    if not path.startswith(prefix) or not path.endswith(".md"):
        return False
    rest = path.removeprefix(prefix)
    if rest == _SCHEMA_OVERLAY_FILENAME:
        return False
    return not any(part.startswith(".") for part in rest.split("/"))


# ---------------------------------------------------------------------------
# Branch-name parsing + suggestion resolution
# ---------------------------------------------------------------------------


def _parse_candidate_branch(branch: str) -> tuple[str, str]:
    """Parse ``loop/c/<topic>/source-<id8>`` into ``(topic, id8)``.

    Raises ``ValueError`` for any branch that is not a source candidate.
    """
    if not branch.startswith(CANDIDATE_BRANCH_PREFIX):
        raise ValueError(f"{branch!r} is not a source candidate branch")
    topic, sep, leaf = branch.removeprefix(CANDIDATE_BRANCH_PREFIX).partition("/")
    id8 = leaf.removeprefix(_SOURCE_INFIX)
    if not (sep and topic and leaf.startswith(_SOURCE_INFIX) and id8):
        raise ValueError(f"{branch!r} is not a source candidate branch")
    return topic, id8


def _resolve_suggestion_id(runner: "LoopRunner", topic: str, id8: str) -> str:
    """Recover the full suggestion id an ``id8`` branch infix stands for.

    Prefix-scans ``suggestions.jsonl`` and fails closed (raises) on a zero- or
    multi-match: a source candidate must map to exactly one suggestion.
    """
    from knotica.core.gapfill import suggestions_path
    from knotica.core.records import parse_suggestions_jsonl

    path = suggestions_path(topic)
    text = runner._store.read_text(path) if runner._store.exists(path) else ""
    records = parse_suggestions_jsonl(text) if text.strip() else []
    matches = [record.suggestion_id for record in records if record.suggestion_id.startswith(id8)]
    if len(matches) != 1:
        raise KnoticaError(
            ErrorCode.SUGGESTION_NOT_FOUND,
            f"the source candidate infix {id8!r} matches {len(matches)} suggestions in "
            f"topic {topic!r}; a candidate must map to exactly one approved suggestion.",
            fix="Re-open the ingest for a single approved suggestion via source_ingest_open.",
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Per-question dilution diff (best-effort, read from the clone manifest)
# ---------------------------------------------------------------------------


def _regressed_questions(outcome: "EvalOutcome", topic: str) -> tuple[dict[str, object], ...]:
    """Bounded top-N per-question dilution rows from the eval clone's manifest.

    Best-effort: a fake/v1 eval (no v2 ``held_out_delta`` manifest) yields an
    empty tuple rather than raising, so the refusal's load-bearing outcomes never
    depend on a diagnostic substrate being present.
    """
    try:
        from knotica.core.gap_classifier import read_regression_manifest

        manifest = read_regression_manifest(outcome.clone_root, topic, int(outcome.generation))
    except Exception:  # noqa: BLE001 -- the per-question diff is advisory, never load-bearing
        return ()
    if not isinstance(manifest, dict):
        return ()
    return _top_regressed(manifest)


def _top_regressed(manifest: dict[str, object]) -> tuple[dict[str, object], ...]:
    """Build the worst-first, capped per-question diff from a v2 manifest."""
    delta = manifest.get("held_out_delta")
    per_id = delta.get("per_id") if isinstance(delta, dict) else None
    if not isinstance(per_id, dict):
        return ()
    example_by_id = _index_per_example(manifest)
    rows: list[dict[str, object]] = []
    for qa_id, scores in per_id.items():
        if not isinstance(scores, dict):
            continue
        quality_delta = _as_float(scores.get("quality_delta"))
        if quality_delta >= 0 and _as_float(scores.get("qa_accuracy_delta")) >= 0:
            continue
        example = example_by_id.get(qa_id, {})
        candidate_score = _as_float(example.get("quality"))
        question = example.get("question")
        rows.append(
            {
                "qa_id": qa_id,
                "question": question if isinstance(question, str) else "",
                "baseline_score": candidate_score - quality_delta,
                "candidate_score": candidate_score,
                "delta": quality_delta,
            }
        )
    rows.sort(key=lambda row: _as_float(row["delta"]))
    return tuple(rows[:_MAX_REGRESSED_QUESTIONS])


def _index_per_example(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    """Map each per-example entry's ``id`` to the entry (empty on a malformed shape)."""
    entries = manifest.get("per_example")
    if not isinstance(entries, list):
        return {}
    return {
        entry["id"]: entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }


def _as_float(value: object) -> float:
    """Coerce a manifest number to ``float`` (``0.0`` for anything non-numeric)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _refusal_reason(
    outcome: "EvalOutcome", state: "LoopState", regressed: tuple[dict[str, object], ...]
) -> str:
    """One-line human summary of why the source was refused."""
    baseline = float(state.baseline_scalar or 0.0)
    return (
        f"scalar {float(outcome.scalar):.4f} regressed below baseline {baseline:.4f} "
        f"({len(regressed)} golden question(s) worse)"
    )


# ---------------------------------------------------------------------------
# Quarantine-branch artifact + prune + loop-state
# ---------------------------------------------------------------------------


def _commit_quarantine_diff(
    runner: "LoopRunner",
    quarantine: str,
    topic: str,
    id8: str,
    regressed: tuple[dict[str, object], ...],
) -> None:
    """Best-effort: commit the per-question dilution diff onto the quarantine branch.

    The gate runs loop-side on the live vault (like ``_keep``), so committing an
    artifact onto a branch other than the checked-out default means briefly
    checking that branch out and restoring the default afterwards.

    The **artifact write** is best-effort -- a failure never fails the refusal,
    whose load-bearing outcomes (the rename, the ``gate_outcome`` stamp, the
    prune) have their own commits. The **restore is not**: it runs in a
    ``finally`` and is deliberately un-swallowed, because the caller's next
    action is a ``gate_outcome`` commit onto whichever branch is checked out. A
    restore failure raises here, failing the cycle visibly, rather than leaving
    the checkout stranded on the quarantine branch and silently misrouting that
    load-bearing commit. Relies on the loop being the single writer during its
    cycle -- the same assumption ``_keep``'s own checkout+merge already makes.
    """
    default = runner._vcs.default_branch()
    if runner._vcs.current_branch() != default:
        # Only operate from a known-default checkout; never risk stranding the
        # live tree on a non-default branch we were not the one to switch to.
        return
    try:
        runner._vcs.checkout_branch(quarantine)
        body = (
            json.dumps(
                {
                    "topic": topic,
                    "candidate": f"{_SOURCE_INFIX}{id8}",
                    "regressed_questions": list(regressed),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        with VaultTransaction(
            runner._store, runner._root, _QUARANTINE_OP, topic, f"quarantine diff {id8}"
        ) as txn:
            txn.write(_quarantine_diff_path(topic, id8), body)
    except Exception:  # noqa: BLE001 -- the artifact is best-effort; never fail the refusal
        pass
    finally:
        # Un-swallowed by design: a failure to return to the default branch must
        # surface, not be masked into a wrong-branch gate-outcome commit.
        runner._vcs.checkout_branch(default)


def _require_default_checkout(runner: "LoopRunner") -> None:
    """Assert the live checkout is on the default branch before a load-bearing commit.

    The gate-outcome record commit uses a ``VaultTransaction`` with no
    ``work_dir``, so it lands on whichever branch is currently checked out. This
    guard fails loud -- a typed error rather than a silent misroute -- if the
    checkout is not on the default branch, so a stranded quarantine-branch
    checkout can never redirect the record commit onto the wrong branch.
    """
    default = runner._vcs.default_branch()
    if runner._vcs.current_branch() != default:
        raise KnoticaError(
            ErrorCode.GIT_ERROR,
            f"gate aborted: the live checkout is not on the default branch {default!r}; "
            "refusing to commit the gate outcome onto the wrong branch.",
            fix=f"Restore the checkout with `git checkout {default}` and re-run the loop.",
        )


def _quarantine_diff_path(topic: str, id8: str) -> str:
    """Vault-relative path of a refused candidate's diff artifact on its branch."""
    return f"{topic}/{_QUARANTINE_DIFF_DIR}/{_SOURCE_INFIX}{id8}.json"


def _prune_quarantine_branches(
    runner: "LoopRunner", topic: str, *, keep: int = _QUARANTINE_KEEP
) -> None:
    """Drop quarantine branches beyond the newest ``keep`` for ``topic`` (best-effort)."""
    prefix = f"{QUARANTINE_BRANCH_PREFIX}{topic}/"
    try:
        tips = [
            (runner._vcs.commit_timestamp(sha), name)
            for name, sha in runner._vcs.list_branch_tips(prefix)
        ]
        tips.sort(reverse=True)
        for _timestamp, name in tips[keep:]:
            runner._safe_delete_branch(name)
    except Exception:  # noqa: BLE001 -- housekeeping must never break the gate
        pass


def _record_refusal_state(
    runner: "LoopRunner",
    state: "LoopState",
    branch: str,
    sha: str,
    outcome: "EvalOutcome",
) -> None:
    """Record the refused cycle in loop-state (mirrors ``_discard``'s bookkeeping)."""
    write_loop_state(
        runner._store,
        runner._root,
        state.model_copy(
            update={
                "stage": LoopStage.failed,
                "last_scalar": float(outcome.scalar),
                "last_generation": int(outcome.generation),
                "last_harness_version": outcome.harness_version,
                "last_decision": LoopDecision.fail,
                "candidate_branch": None,
                "candidate_sha": None,
                "last_error": None,
            }
        ).mark_processed(branch, sha),
        title=f"source refused {branch} scalar={outcome.scalar:.4f}",
    )

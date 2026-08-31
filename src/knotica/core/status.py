"""Deterministic vault status aggregation — shared by CLI and MCP tools.

Pure reads over a :class:`~knotica.store.VaultStore`: page/curated counts,
live lint violation counts, last eval scalar, and gate/loop stage from
persisted :mod:`knotica.core.loop_state`. No LLM, no mutation, no lock.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from knotica.core.arena import read_arena_state
from knotica.core.compiled import load_compiled
from knotica.core.compile_state import CompileState, empty_compile_state, read_compile_state
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.lint import LOG_PATH, lint_vault, topic_of_violation
from knotica.core.loop import DEFAULT_BRANCH_PREFIX
from knotica.core.loop_heartbeat import read_runner_liveness
from knotica.core.loop_progress import read_progress
from knotica.core.loop_state import LoopState, compute_gate, read_loop_state
from knotica.core.metrics import last_eval_summary, read_last_metrics
from knotica.core.notes.store import ResolvedNote, list_notes
from knotica.core.notes_config import resolve_notes_config
from knotica.core.page import TopicNotFoundError
from knotica.core import process_model
from knotica.core.records import (
    parse_log_entries,
)
from knotica.core.status_counts import gap_block, golden_count, page_count, suggestion_block
from knotica.core.status_lanes import lanes_block
from knotica.core.topics import is_topic, topic_directories
from knotica.core.trainset import count_query_train_examples
from knotica.core.vcs import GitError, VaultVcs
from knotica.evals.golden import EVAL_MIN_GOLDEN
from knotica.store import VaultStore

__all__ = [
    "COMPILE_READY_MIN_EXAMPLES",
    "LINT_STALE_AFTER_DAYS",
    "STATUS_SCHEMA_VERSION",
    "VALID_STATUS_VIEWS",
    "TopicStatus",
    "gather_wiki_status",
]

#: Stable version of the ``wiki_status`` / ``knotica status --json`` envelope.
STATUS_SCHEMA_VERSION = 1

#: Recognized ``wiki_status`` ``view`` values. ``summary`` is the default,
#: byte-identical to the pre-``view``-param payload. ``scope`` is the
#: cheapest view -- topic enumeration only, no per-topic stats, no lint, no
#: loop/runner reads -- for the client-side conversational routing scope-check.
#: ``process_model`` is the served process-model declaration -- lanes, stage
#: ids, titles, order and handoff flags, structure only -- vault- and
#: topic-independent, so the dashboard can prefer the connected server's live
#: declaration over its bundled fallback. ``attention`` is the cross-topic
#: inbox projection -- every topic's actionable counts plus runner liveness,
#: under a hard budget (no lint walk, no note-anchor resolution).
VALID_STATUS_VIEWS = frozenset({"summary", "scope", "process_model", "attention"})

#: Query-style curated examples required before a topic can run DSPy compile
#: (PRE_PLAN Phase 3a floor ~30–50; ingest-style qa lines do not count).
COMPILE_READY_MIN_EXAMPLES = 30

#: Days after which the last recorded mechanical lint counts as stale. The
#: attention view reports staleness against this instead of re-walking the
#: vault, so the threshold is a server-side policy the client only renders.
#: 7 is a policy default, not a measurement: one human working week -- a vault
#: whose last lint predates the week you are working in has plausibly drifted.
#: Tune it when real usage shows the attention row nagging too early or too
#: late; it gates a *hint*, never an action.
LINT_STALE_AFTER_DAYS = 7

#: Log ops that count as a lint run for the "last lint" readout.
_LINT_OPS = frozenset({"lint", "lint_check"})


@dataclass(frozen=True, slots=True)
class TopicStatus:
    """Per-topic progress numbers for status surfaces."""

    topic: str
    pages: int
    curated: int
    trainset_n: int
    golden_n: int
    compile_ready: bool
    compiled: dict[str, Any] | None
    lint_violations: int
    last_eval: dict[str, Any] | None
    suggestions: dict[str, Any]
    gaps: dict[str, Any]
    notes: dict[str, int]
    lanes: dict[str, tuple[dict[str, Any], ...]]

    @property
    def to_compile_ready(self) -> int:
        """Query-train examples still needed to reach the compile-ready floor."""
        return max(0, COMPILE_READY_MIN_EXAMPLES - self.trainset_n)

    def render(self) -> dict[str, Any]:
        """JSON object for one topic row."""
        return {
            "topic": self.topic,
            "pages": self.pages,
            "curated": self.curated,
            "trainset_n": self.trainset_n,
            "golden_n": self.golden_n,
            "compile_ready": self.compile_ready,
            "to_compile_ready": self.to_compile_ready,
            "compiled": self.compiled,
            "lint_violations": self.lint_violations,
            "last_eval": self.last_eval,
            "suggestions": self.suggestions,
            "gaps": self.gaps,
            "notes": self.notes,
            "lanes": self.lanes,
        }


def gather_wiki_status(
    store: VaultStore,
    vault_path: Path,
    *,
    topic: str = "",
    vault_name: str = "",
    default_vault: str = "",
    available_vaults: list[dict[str, Any]] | None = None,
    view: str = "summary",
) -> dict[str, Any]:
    """Build the ``wiki_status`` payload for the whole vault or one topic.

    Raises :class:`~knotica.core.page.TopicNotFoundError` when ``topic`` is
    non-empty and does not name an existing topic directory. Raises
    :class:`~knotica.core.errors.KnoticaError` (``INVALID_ARGUMENT``) when
    ``view`` is not one of :data:`VALID_STATUS_VIEWS`.

    ``vault`` remains the absolute path (compat). Prefer ``vault_name`` /
    ``vault_path`` for new surfaces. ``available_vaults`` feeds a future
    multi-vault switcher (entries from :func:`knotica.core.config.list_vaults`).

    ``view="summary"`` (default) is today's full payload, unchanged.
    ``view="scope"`` is the cheapest view -- topic enumeration only, no
    per-topic stats -- for the client-side conversational routing check.
    ``view="process_model"`` serves the live process-model declaration
    (lanes, stage ids, titles, order, handoff flags) -- vault- and
    topic-independent, so ``topic``/``vault_name`` are accepted but ignored.
    ``view="attention"`` is the cross-topic inbox projection -- every topic in
    the vault, always, so ``topic`` is accepted but ignored there too.
    """
    if view not in VALID_STATUS_VIEWS:
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message=(
                f"wiki_status failed because view must be one of "
                f"{sorted(VALID_STATUS_VIEWS)}, got {view!r}"
            ),
            fix=f"Pass view as one of: {', '.join(sorted(VALID_STATUS_VIEWS))}.",
        )
    scope = topic.strip()
    name = vault_name or vault_path.name
    if view == "process_model":
        return _process_model_status()
    if view == "scope":
        return _scope_status(store, name, scope=scope)
    if view == "attention":
        return _attention_status(store, vault_path, name)

    vcs = VaultVcs(vault_path)
    topics, vault_lint = _topic_statuses(store, vcs, vault_path, scope=scope or None)
    last_lint = _last_lint(store)
    unpushed = _unpushed(vault_path)
    gate, loop = _gate_and_loop(store, vault_path, topics)
    compile_info = _compile_info(store, topics)
    path = str(vault_path)
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "vault": path,
        "vault_name": name,
        "vault_path": path,
        "default_vault": default_vault or name,
        "available_vaults": list(available_vaults or []),
        "compile_ready_threshold": COMPILE_READY_MIN_EXAMPLES,
        "eval_min_golden": EVAL_MIN_GOLDEN,
        "topics": [row.render() for row in topics],
        "totals": {
            "topics": len(topics),
            "pages": sum(t.pages for t in topics),
            "curated": sum(t.curated for t in topics),
            "lint_violations": sum(t.lint_violations for t in topics),
            # Findings no topic owns (log.md, index.md, root schema, reserved
            # names) -- reported here so they cannot vanish between the
            # per-topic buckets, which is how status once read 0 while the
            # eval harness counted 12 on the same corpus.
            "lint_violations_vault_level": vault_lint,
            "notes": {
                "total": sum(t.notes["total"] for t in topics),
                "drifted": sum(t.notes["drifted"] for t in topics),
            },
        },
        "last_lint": last_lint,
        "unpushed": unpushed,
        "gate": gate,
        "loop": loop,
        "compile": compile_info,
        "llm": _llm_availability(),
    }


def _process_model_status() -> dict[str, Any]:
    """The ``view="process_model"`` payload: the live process-model declaration.

    Structure only -- lane order, stage ids, titles, handoff flags -- mirroring
    exactly what :mod:`scripts.generate_process_model_ts` projects into the
    dashboard's bundled fallback, so the two payloads are directly comparable.
    No predicates, no per-stage state: dynamic rail state is served on the
    per-topic ``summary`` payload, not here (``derive_stages`` is a separate
    concern from this static structure). Vault- and topic-independent -- the
    declaration is the same regardless of which vault or topic was asked
    about, so no store read and no vault path are needed.
    """
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "lanes": list(process_model.LANES),
        "lane_stages": {
            lane: [
                {"id": stage.id, "title": stage.title, "handoff": stage.handoff}
                for stage in process_model.LANE_STAGES[lane]
            ]
            for lane in process_model.LANES
        },
    }


def _scope_status(store: VaultStore, vault_name: str, *, scope: str) -> dict[str, Any]:
    """The ``view="scope"`` payload: topic enumeration only, no per-topic stats.

    Deliberately cheap -- no lint, no compile/trainset/golden counts, no
    loop-state or runner-liveness reads. Config resolution + a directory
    listing only, so it stays safe to call speculatively during ordinary
    conversation (the client-side routing scope-check).

    Raises :class:`~knotica.core.page.TopicNotFoundError` when ``scope`` is
    non-empty and does not name an existing topic directory.
    """
    if scope:
        if not is_topic(store, scope):
            raise TopicNotFoundError(scope)
        names = [scope]
    else:
        names = topic_directories(store)
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "vault_name": vault_name,
        "topics": names,
        "totals": {"topics": len(names)},
    }


def _attention_status(store: VaultStore, vault_path: Path, vault_name: str) -> dict[str, Any]:
    """The ``view="attention"`` payload: the cross-topic inbox, on a hard budget.

    Reports, for every topic in the vault, the honest fields its two consumers
    -- ``knotica status --nudge`` and the dashboard's Home rail -- derive the
    same seven attention signals from: pending suggestions,
    refused-awaiting-rework, open gaps with no discovery yet, an aborted arena
    race, compile-readiness, runner liveness, and an unreachable gate baseline.
    Which of them *means* a row is the client's call, so this docstring names
    the fields, never the rules -- ``cli/status.py`` and
    ``dashboard/src/lanes/home/attentionRows.ts`` are where a signal is added.

    Three costs ``view="summary"`` pays are deliberately *not* paid here
    (dec-092), because a projection that costs what summary costs is not a
    projection:

    * **No mechanical lint walk.** ``last_lint`` reports the newest recorded
      lint date and whether it has gone stale; no ``lint_vault`` pass runs.
    * **No note-anchor resolution.** The drift row is a marker only, with no
      computed count -- it pays for itself on expansion, not on every poll.
    * **No git subprocess at all**, and nothing that scales with topic count:
      every field is a small file read, so the whole-vault cost grows only in
      the number of topics, never in git process spawns.

    Runner liveness reads :func:`~knotica.core.loop_heartbeat.read_runner_liveness`
    per topic -- the same producer ``service.manager.status()`` uses -- rather
    than :func:`_gate_and_loop`, whose multi-topic branch reports every topic
    dead unconditionally. A wrong answer is worse than an absent one.

    The ``topic`` argument the other views scope by is ignored: "which topics
    need me?" has no single-topic reading.
    """
    rows = [_attention_row(store, vault_path, name) for name in topic_directories(store)]
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "vault_name": vault_name,
        "topics": rows,
        "totals": {
            "topics": len(rows),
            "pending": sum(row["suggestions"]["pending"] for row in rows),
            "refused_awaiting_rework": sum(
                row["suggestions"]["refused_awaiting_rework"] for row in rows
            ),
            "compile_ready": sum(1 for row in rows if row["compile_ready"]),
            "runners_alive": sum(1 for row in rows if row["runner"]["alive"]),
        },
        "last_lint": _last_lint_status(store),
        # Marker, never a count: resolving drift means resolving every note's
        # anchor, which is exactly the cost this view exists to avoid.
        "drift": {"default_collapsed": True, "count": None},
    }


def _attention_row(store: VaultStore, vault_path: Path, topic: str) -> dict[str, Any]:
    """One topic's attention row -- small file reads only, no git, no lint.

    ``gaps`` and ``arena`` add **one small file read each** (``gaps.jsonl`` and
    the arena state file), and ``gate`` two more (loop state + the metrics
    tail), taking the row from four reads to eight. All stay inside the
    dec-092 budget: no git subprocess, no lint walk, no note-anchor
    resolution, and cost still linear in topic count.

    They exist because "needs a human" conditions reached Home through no
    signal at all. A topic with open gaps and no suggestions -- because
    discovery never ran against them -- tripped none of the four original
    branches, so Home reported "nothing needs you" while the gap queue rotted.
    An arena race refused before scoring is a *stopped* pipeline that was
    visible only to someone already standing in Improve -> Heal on that topic.
    And a baseline the default branch cannot measure up to fails every
    candidate by construction while Home stayed silent -- a field report found
    exactly that topic reading "nothing needs you" as its whole pipeline
    jammed.

    Only the honest numbers are returned; whether they *mean* a row is the
    client's call, exactly as every other attention signal is derived
    client-side.
    """
    trainset_n = count_query_train_examples(store, topic)
    return {
        "topic": topic,
        "suggestions": suggestion_block(store, topic),
        "gaps": {"open_total": gap_block(store, topic)["open_total"]},
        "compile_ready": _is_compile_ready(trainset_n, golden_count(store, topic)),
        "runner": read_runner_liveness(vault_path, topic),
        "arena": _attention_arena_block(store, topic),
        "gate": {
            "baseline_unreachable": _baseline_unreachable(
                topic,
                last_eval_summary(read_last_metrics(store, topic)),
                read_loop_state(store, topic),
            )
        },
    }


def _attention_arena_block(store: VaultStore, topic: str) -> dict[str, Any]:
    """The topic's last arena stage, or null when no race was ever recorded.

    One small file read; no git, no scoring, no variant resolution. The *stage
    word* is all this returns -- whether an ``aborted`` race needs a decision is
    the client's call. A corrupt or absent state file reads as ``None`` via
    :func:`~knotica.core.arena.read_arena_state`, which is the honest answer:
    "no race we can speak for", never a guessed stage.
    """
    state = read_arena_state(store, topic)
    return {"stage": state.stage.value if state is not None else None}


def _last_lint_status(store: VaultStore, *, today: date | None = None) -> dict[str, Any]:
    """Last recorded lint date and its staleness -- read from the log, not re-walked.

    A vault that has never been linted, and one whose recorded date cannot be
    parsed, are both reported stale with a null age: "unknown" would invite a
    surface to render nothing, and never-linted is precisely the case that
    warrants attention.
    """
    recorded = _last_lint(store)
    reference = today if today is not None else datetime.now(UTC).date()
    try:
        age_days = (reference - date.fromisoformat(recorded or "")).days
    except ValueError:
        return {"date": recorded, "age_days": None, "stale": True}
    return {"date": recorded, "age_days": age_days, "stale": age_days >= LINT_STALE_AFTER_DAYS}


def _is_compile_ready(trainset_n: int, golden_n: int) -> bool:
    """Whether a topic clears both floors DSPy compile is gated on."""
    return trainset_n >= COMPILE_READY_MIN_EXAMPLES and golden_n >= EVAL_MIN_GOLDEN


def _topic_statuses(
    store: VaultStore, vcs: VaultVcs, vault_path: Path, *, scope: str | None
) -> tuple[list[TopicStatus], int]:
    """Per-topic status rows (optionally one topic), plus the vault-level lint count."""
    if scope:
        if not is_topic(store, scope):
            raise TopicNotFoundError(scope)
        names = [scope]
    else:
        names = topic_directories(store)

    lint_counts, vault_level = _lint_counts_by_topic(store, scope=scope)
    rows = [
        _topic_status(store, vcs, vault_path, name, lint_violations=lint_counts.get(name, 0))
        for name in names
    ]
    return rows, vault_level


def _topic_status(
    store: VaultStore, vcs: VaultVcs, vault_path: Path, name: str, *, lint_violations: int
) -> TopicStatus:
    trainset_n = count_query_train_examples(store, name)
    golden_n = golden_count(store, name)
    artifact = load_compiled(store, name)
    compiled: dict[str, Any] | None = None
    if artifact is not None:
        compiled = {
            "present": True,
            "version": artifact.version,
            "scalar": artifact.metrics.get("compiled"),
            "compiled_at": artifact.created_at,
            "optimizer": artifact.optimizer or None,
            "fallback_reason": artifact.fallback_reason or None,
        }
    compile_ready = _is_compile_ready(trainset_n, golden_n)
    notes = _notes_summary(store, vcs, name)
    last_eval = last_eval_summary(read_last_metrics(store, name))
    return TopicStatus(
        topic=name,
        pages=page_count(store, name),
        # ``curated`` is the legacy status column; it now means query-train count
        # (ingest-style qa lines are excluded — same as ``trainset_n``).
        curated=trainset_n,
        trainset_n=trainset_n,
        golden_n=golden_n,
        compile_ready=compile_ready,
        compiled=compiled,
        lint_violations=lint_violations,
        last_eval=last_eval,
        suggestions=suggestion_block(store, name),
        gaps=gap_block(store, name),
        notes=notes,
        lanes=lanes_block(
            store,
            vault_path,
            name,
            lint_violations=lint_violations,
            notes_drifted=notes["drifted"],
            # Improve's rail is a projection of these three numbers, which
            # this same read already produced -- never a second computation.
            datasets_present=bool(trainset_n or golden_n),
            eval_recorded=last_eval is not None,
            compile_ready=compile_ready,
        ),
    )


def _notes_summary(store: VaultStore, vcs: VaultVcs, topic: str) -> dict[str, int]:
    """Note counts for one topic: ``total`` notes, ``drifted`` (fuzzy-or-orphaned) notes.

    Drifted counts ``fuzzy`` and ``orphaned`` -- not ``shifted`` (the resolver
    healed it automatically at the same text), not ``unanchored`` (nothing was
    ever pointed at), and not ``anchor-invalid`` (a corrupt record, a
    data-integrity concern rather than "the wiki moved on").
    """
    notes_config = resolve_notes_config()
    listing = list_notes(
        store,
        vcs,
        topic,
        guess_threshold=notes_config.guess_threshold,
        complete_orphan_threshold=notes_config.complete_orphan_threshold,
    )
    drifted = sum(1 for note in listing.notes if _has_drifted_anchor(note))
    return {"total": len(listing.notes), "drifted": drifted}


#: The per-anchor statuses that count as drift for ``wiki_status`` -- the
#: resolver could not place the anchor verbatim and nothing healed it exactly.
#: ``shifted`` (verbatim survival at a new offset) and ``unanchored`` (never
#: pointed at a page) are deliberately excluded; see ``_notes_summary``.
_DRIFTED_ANCHOR_STATUSES = frozenset({"fuzzy", "orphaned"})


def _has_drifted_anchor(note: ResolvedNote) -> bool:
    return any(
        projection.status in _DRIFTED_ANCHOR_STATUSES for _, projection in note.resolved_anchors
    )


def _compile_info(store: VaultStore, topics: list[TopicStatus]) -> dict[str, Any] | None:
    if len(topics) != 1:
        return None
    state = read_compile_state(store, topics[0].topic) or empty_compile_state(topics[0].topic)
    return state.render()


def _lint_counts_by_topic(store: VaultStore, *, scope: str | None) -> tuple[Counter[str], int]:
    """Run mechanical lint once; per-topic counts plus the vault-level remainder.

    Attribution is ``core.lint.topic_of_violation`` -- the same rule the eval
    harness counts the scalar's ``lint_violations`` input with, so the two
    surfaces can no longer disagree structurally (the old first-segment
    bucketing dropped vault-level findings and filed ``sources/<topic>/…``
    under a non-topic; a scoped call even attributed root findings to the
    scope). Vault-level findings are returned as their own count rather than
    silently vanishing.
    """
    violations = lint_vault(store, scope or "")
    counts: Counter[str] = Counter()
    vault_level = 0
    for violation in violations:
        topic = topic_of_violation(violation.path)
        if topic is not None:
            counts[topic] += 1
        else:
            vault_level += 1
    return counts, vault_level


def _gate_and_loop(
    store: VaultStore, vault_path: Path, topics: list[TopicStatus]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Combine metrics + persisted loop-state into gate and loop readouts.

    Gate/loop are meaningful for a single topic scope (one row). With multiple
    topics, report an honest unknown gate and a null stage.
    """
    if len(topics) != 1:
        return (
            {
                "state": "unknown",
                "baseline": None,
                "last_scalar": None,
                "baseline_unreachable": None,
            },
            {
                "runner": {
                    "alive": False,
                    "pid": None,
                    "beat_at": None,
                    "interval_seconds": None,
                },
                "progress": None,
                "baseline_policy": "latest",
                "stage": None,
                "baseline_frozen": False,
                "baseline_scalar": None,
                "baseline_unreachable": None,
                "pending_candidates": [],
                "metrics_hint": None,
            },
        )

    row = topics[0]
    state = read_loop_state(store, row.topic)
    compile_state = read_compile_state(store, row.topic)
    last_scalar = _last_known_scalar(row, state, compile_state)
    last_harness = str(row.last_eval["harness_version"]) if row.last_eval else None
    gate = {
        **compute_gate(state, last_scalar=last_scalar, last_harness_version=last_harness),
        # Null in the healthy case; an object naming both scalars when the bar
        # outranks the corpus. A condition that refuses every future candidate
        # must not have to be inferred by comparing `baseline_scalar` against a
        # metrics record on another surface. It hangs off `gate` because it is a
        # gate finding -- the parent `view="attention"` has always used.
        "baseline_unreachable": _baseline_unreachable(row.topic, row.last_eval, state),
    }
    arena = read_arena_state(store, row.topic)
    pending = _pending_loop_candidates(vault_path, state)
    metrics_hint: dict[str, Any] | None = None
    if state is not None and (state.last_scalar is not None or state.last_generation is not None):
        metrics_hint = {
            "last_scalar": state.last_scalar,
            "last_generation": state.last_generation,
        }
    loop = {
        "runner": read_runner_liveness(vault_path, row.topic),
        "progress": read_progress(vault_path, row.topic),
        "baseline_policy": state.baseline_policy if state is not None else "latest",
        "stage": state.stage.value if state is not None else None,
        "candidate_branch": state.candidate_branch if state is not None else None,
        "last_decision": state.last_decision.value if state is not None else None,
        "arena_race_id": arena.race_id if arena is not None else None,
        "arena_stage": arena.stage.value if arena is not None else None,
        # The stage word alone is not diagnosable: "reverted" is the normal
        # terminal state when no variant cleared the baseline, and the reason --
        # which scalar lost to which bar -- lives only in this message. Without
        # it a revert forced by an unreachable baseline looks like a malfunction.
        "arena_message": arena.message if arena is not None else None,
        "baseline_frozen": state is not None and state.baseline_scalar is not None,
        "baseline_scalar": (
            float(state.baseline_scalar)
            if state is not None and state.baseline_scalar is not None
            else None
        ),
        # Deprecated mirror of `gate.baseline_unreachable`, kept for one release
        # so the dashboard's existing `loop.baseline_unreachable` read keeps
        # working; `gate` is the parent both views now agree on.
        "baseline_unreachable": gate["baseline_unreachable"],
        "pending_candidates": pending,
        "metrics_hint": metrics_hint,
    }
    return gate, loop


def _baseline_unreachable(
    topic: str, last_eval: dict[str, Any] | None, state: LoopState | None
) -> dict[str, Any] | None:
    """A bar the default branch's own corpus cannot clear -- always a misconfiguration.

    When the baseline sits above the default branch's *measured* scalar, nothing
    can pass the gate: not a candidate under test, not a perfect source, not an
    arena variant. Every refusal's diff then blames the content being evaluated
    for a shortfall the bar created, and ``gate.state: "fail"`` describes the
    topic rather than anything submitted to it -- which is exactly why this has
    to be said out loud rather than left to be inferred from two numbers.

    Read from the newest metrics record only, never from ``state.last_scalar``:
    a refusal writes the *candidate's* scalar there (see
    ``source_gate._record_refusal_state``), so the fallback chain
    :func:`_last_known_scalar` walks would report a perfectly healthy topic as
    unreachable for as long as its last candidate happened to score low.

    Two conditions withhold the finding rather than assert it:

    * **Cross-instrument.** Mirrors :func:`~knotica.core.loop_state.compute_gate`
      -- scalars from different harness versions are not orderable, so a
      mismatch is unknown, not unreachable.
    * **Probe anchors.** A ``baseline-probe`` record carries ``n_examples: 0``
      and measures nothing; ranking a real baseline against it is meaningless.
    """
    if state is None or state.baseline_scalar is None or last_eval is None:
        return None
    if not int(last_eval.get("n_examples") or 0):
        return None
    harness = last_eval.get("harness_version")
    if state.baseline_harness_version and harness and harness != state.baseline_harness_version:
        return None
    baseline = float(state.baseline_scalar)
    measured = float(last_eval["scalar"])
    if baseline <= measured:
        return None
    return {
        "baseline": baseline,
        "last_scalar": measured,
        "generation": last_eval.get("generation"),
        "message": (
            f"gate baseline {baseline:.4f} exceeds the default branch's own scalar "
            f"{measured:.4f}, so no candidate and no arena variant can pass the gate"
        ),
        "fix": (
            f"Lower the bar to what the corpus actually measures: "
            f"`improve action=loop loop_action=rebaseline mode=latest topic={topic}`."
        ),
    }


def _last_known_scalar(
    row: TopicStatus,
    state: LoopState | None,
    compile_state: CompileState | None,
) -> float | None:
    """Best-effort scalar for gate readout when no baseline is frozen yet."""
    if row.last_eval is not None:
        return float(row.last_eval["scalar"])
    if state is not None and state.last_scalar is not None:
        return float(state.last_scalar)
    if compile_state is not None and compile_state.scalar_after is not None:
        return float(compile_state.scalar_after)
    if row.compiled is not None and row.compiled.get("scalar") is not None:
        return float(row.compiled["scalar"])
    if compile_state is not None and compile_state.scalar_before is not None:
        return float(compile_state.scalar_before)
    return None


def _pending_loop_candidates(
    vault_path: Path,
    state: LoopState | None,
    *,
    prefix: str = DEFAULT_BRANCH_PREFIX,
) -> list[dict[str, Any]]:
    """Local ``loop/c/*`` tips with whether the runner still owes them a cycle."""
    try:
        vcs = VaultVcs(vault_path)
        default = vcs.default_branch()
        cursors = state.cursors if state is not None else {}
        out: list[dict[str, Any]] = []
        for branch, sha in vcs.list_branch_tips(prefix):
            if branch == default:
                continue
            out.append(
                {
                    "branch": branch,
                    "sha": sha[:12],
                    "pending": cursors.get(branch) != sha,
                }
            )
        return out
    except GitError:
        return []


def _last_lint(store: VaultStore) -> str | None:
    """Return the latest recorded lint date from ``log.md``, or ``None``."""
    if not store.exists(LOG_PATH):
        return None
    try:
        entries = parse_log_entries(store.read_text(LOG_PATH))
    except ValueError:
        return None
    lint_dates = [entry.date for entry in entries if entry.op in _LINT_OPS]
    return max(lint_dates) if lint_dates else None


def _llm_availability() -> dict[str, Any]:
    """Whether headless LLM work (query/eval/arena/compile) can actually run.

    Two independent preconditions, reported distinctly so surfaces can show the
    right remediation: credentials in the environment (OAuth-first, mirroring
    :mod:`knotica.evals.llm`) AND the ``anthropic`` package being importable
    (the ``evals`` dependency group — a server launched without it has working
    creds but no client). No network, no client construction.
    """
    from knotica.evals.llm import API_KEY_ENV_VAR, OAUTH_TOKEN_ENV_VAR

    if os.environ.get(OAUTH_TOKEN_ENV_VAR):
        mode = "oauth"
    elif os.environ.get(API_KEY_ENV_VAR):
        mode = "api_key"
    else:
        return {"available": False, "mode": None, "reason": "credentials"}
    if not _anthropic_installed():
        return {"available": False, "mode": mode, "reason": "deps"}
    return {"available": True, "mode": mode, "reason": None}


@lru_cache(maxsize=1)
def _anthropic_installed() -> bool:
    """Whether the ``anthropic`` package is resolvable (process-lifetime cached)."""
    from importlib.util import find_spec

    try:
        return find_spec("anthropic") is not None
    except (ImportError, ValueError):
        return False


def _unpushed(vault_path: Path) -> int | None:
    """Read-only count of commits ahead of the upstream (``None`` if no remote)."""
    try:
        return VaultVcs(vault_path).unpushed_count()
    except GitError:
        return None

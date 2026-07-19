"""Deterministic vault status aggregation — shared by CLI and MCP tools.

Pure reads over a :class:`~knotica.store.VaultStore`: page/curated counts,
live lint violation counts, last eval scalar, and gate/loop stage from
persisted :mod:`knotica.core.loop_state`. No LLM, no mutation, no lock.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from knotica.core.arena import read_arena_state
from knotica.core.compiled import load_compiled
from knotica.core.compile_state import CompileState, empty_compile_state, read_compile_state
from knotica.core.links import iter_page_paths
from knotica.core.lint import LOG_PATH, RESERVED_TOP_LEVEL_NAMES, lint_vault
from knotica.core.loop import DEFAULT_BRANCH_PREFIX
from knotica.core.loop_heartbeat import read_runner_liveness
from knotica.core.loop_progress import read_progress
from knotica.core.loop_state import LoopState, compute_gate, read_loop_state
from knotica.core.metrics import last_eval_summary, read_last_metrics
from knotica.core.page import TopicNotFoundError
from knotica.core.gapfill import suggestions_path
from knotica.core.records import RecordParseError, SuggestionRecord, parse_log_entries
from knotica.core.schema import overlay_path
from knotica.core.trainset import count_query_train_examples
from knotica.core.vcs import GitError, VaultVcs
from knotica.evals.golden import EVAL_MIN_GOLDEN, GoldenSetMissingError, load as load_golden
from knotica.store import VaultStore

__all__ = [
    "COMPILE_READY_MIN_EXAMPLES",
    "STATUS_SCHEMA_VERSION",
    "TopicStatus",
    "gather_wiki_status",
]

#: Stable version of the ``wiki_status`` / ``knotica status --json`` envelope.
STATUS_SCHEMA_VERSION = 1

#: Query-style curated examples required before a topic can run DSPy compile
#: (PRE_PLAN Phase 3a floor ~30–50; ingest-style qa lines do not count).
COMPILE_READY_MIN_EXAMPLES = 30

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
        }


def gather_wiki_status(
    store: VaultStore,
    vault_path: Path,
    *,
    topic: str = "",
    vault_name: str = "",
    default_vault: str = "",
    available_vaults: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the ``wiki_status`` payload for the whole vault or one topic.

    Raises :class:`~knotica.core.page.TopicNotFoundError` when ``topic`` is
    non-empty and does not name an existing topic directory.

    ``vault`` remains the absolute path (compat). Prefer ``vault_name`` /
    ``vault_path`` for new surfaces. ``available_vaults`` feeds a future
    multi-vault switcher (entries from :func:`knotica.core.config.list_vaults`).
    """
    scope = topic.strip()
    topics = _topic_statuses(store, scope=scope or None)
    last_lint = _last_lint(store)
    unpushed = _unpushed(vault_path)
    gate, loop = _gate_and_loop(store, vault_path, topics)
    compile_info = _compile_info(store, topics)
    path = str(vault_path)
    name = vault_name or vault_path.name
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
        },
        "last_lint": last_lint,
        "unpushed": unpushed,
        "gate": gate,
        "loop": loop,
        "compile": compile_info,
        "llm": _llm_availability(),
    }


def _topic_statuses(store: VaultStore, *, scope: str | None) -> list[TopicStatus]:
    """Gather per-topic status rows (optionally one topic)."""
    if scope:
        if not _is_topic(store, scope):
            raise TopicNotFoundError(scope)
        names = [scope]
    else:
        names = _topic_directories(store)

    lint_counts = _lint_counts_by_topic(store, scope=scope)
    return [_topic_status(store, name, lint_violations=lint_counts.get(name, 0)) for name in names]


def _topic_status(store: VaultStore, name: str, *, lint_violations: int) -> TopicStatus:
    trainset_n = count_query_train_examples(store, name)
    golden_n = _golden_count(store, name)
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
    compile_ready = trainset_n >= COMPILE_READY_MIN_EXAMPLES and golden_n >= EVAL_MIN_GOLDEN
    return TopicStatus(
        topic=name,
        pages=_page_count(store, name),
        # ``curated`` is the legacy status column; it now means query-train count
        # (ingest-style qa lines are excluded — same as ``trainset_n``).
        curated=trainset_n,
        trainset_n=trainset_n,
        golden_n=golden_n,
        compile_ready=compile_ready,
        compiled=compiled,
        lint_violations=lint_violations,
        last_eval=last_eval_summary(read_last_metrics(store, name)),
        suggestions=_suggestion_block(store, name),
    )


def _suggestion_block(store: VaultStore, topic: str) -> dict[str, Any]:
    """The per-topic gap-fill queue summary for the ingest handoff (all-zero when empty).

    Counts each lifecycle status and surfaces ``approved_awaiting_ingest`` (the
    approved-but-not-yet-ingested backlog that matters for the interactive
    ingest handoff) plus the newest ``proposed_at``. Reads ``suggestions.jsonl``
    line-by-line and skips a malformed line rather than raising, so a single
    corrupt record never breaks the status readout (mirrors ``_golden_count``).
    """
    counts = Counter[str]()
    newest: str | None = None
    path = suggestions_path(topic)
    if store.exists(path):
        for line in store.read_text(path).splitlines():
            if not line.strip():
                continue
            try:
                record = SuggestionRecord.from_json_line(line)
            except (ValueError, RecordParseError):
                continue
            counts[record.status] += 1
            if newest is None or record.proposed_at > newest:
                newest = record.proposed_at
    return {
        "pending": counts.get("pending", 0),
        "approved_awaiting_ingest": counts.get("approved", 0),
        "deferred": counts.get("deferred", 0),
        "rejected": counts.get("rejected", 0),
        "ingested": counts.get("ingested", 0),
        "newest_proposed_at": newest,
    }


def _golden_count(store: VaultStore, topic: str) -> int:
    try:
        return len(load_golden(store, topic))
    except GoldenSetMissingError:
        return 0
    except Exception:  # noqa: BLE001 — status stays readable on corrupt golden
        return 0


def _compile_info(store: VaultStore, topics: list[TopicStatus]) -> dict[str, Any] | None:
    if len(topics) != 1:
        return None
    state = read_compile_state(store, topics[0].topic) or empty_compile_state(topics[0].topic)
    return state.render()


def _lint_counts_by_topic(store: VaultStore, *, scope: str | None) -> Counter[str]:
    """Run mechanical lint once and bucket violations by topic directory."""
    violations = lint_vault(store, scope or "")
    counts: Counter[str] = Counter()
    for violation in violations:
        topic = violation.path.split("/", 1)[0] if violation.path else ""
        if topic and not topic.startswith("."):
            counts[topic] += 1
        elif scope:
            counts[scope] += 1
    return counts


def _gate_and_loop(
    store: VaultStore, vault_path: Path, topics: list[TopicStatus]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Combine metrics + persisted loop-state into gate and loop readouts.

    Gate/loop are meaningful for a single topic scope (one row). With multiple
    topics, report an honest unknown gate and a null stage.
    """
    if len(topics) != 1:
        return (
            {"state": "unknown", "baseline": None, "last_scalar": None},
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
                "pending_candidates": [],
                "metrics_hint": None,
            },
        )

    row = topics[0]
    state = read_loop_state(store, row.topic)
    compile_state = read_compile_state(store, row.topic)
    last_scalar = _last_known_scalar(row, state, compile_state)
    last_harness = str(row.last_eval["harness_version"]) if row.last_eval else None
    gate = compute_gate(state, last_scalar=last_scalar, last_harness_version=last_harness)
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
        "baseline_frozen": state is not None and state.baseline_scalar is not None,
        "baseline_scalar": (
            float(state.baseline_scalar)
            if state is not None and state.baseline_scalar is not None
            else None
        ),
        "pending_candidates": pending,
        "metrics_hint": metrics_hint,
    }
    return gate, loop


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


def _topic_directories(store: VaultStore) -> list[str]:
    """Visible top-level directories that are topics (reserved names excluded)."""
    return [name for name in sorted(store.list_dir("")) if _is_topic(store, name)]


def _is_topic(store: VaultStore, name: str) -> bool:
    """Whether a top-level entry is a topic: a visible, non-reserved directory."""
    if name.startswith(".") or name in RESERVED_TOP_LEVEL_NAMES:
        return False
    if not store.exists(name):
        return False
    try:
        store.list_dir(name)
    except (NotADirectoryError, FileNotFoundError):
        return False
    return True


def _page_count(store: VaultStore, topic: str) -> int:
    """Count content pages under ``topic`` (its schema overlay is not a page)."""
    overlay = overlay_path(topic)
    try:
        return sum(1 for path in iter_page_paths(store, topic) if path != overlay)
    except NotADirectoryError:
        return 0


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

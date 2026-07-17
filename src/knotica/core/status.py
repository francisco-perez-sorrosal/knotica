"""Deterministic vault status aggregation — shared by CLI and MCP tools.

Pure reads over a :class:`~knotica.store.VaultStore`: page/curated counts,
live lint violation counts, last eval scalar, and gate/loop stage from
persisted :mod:`knotica.core.loop_state`. No LLM, no mutation, no lock.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from knotica.core.links import iter_page_paths
from knotica.core.lint import LOG_PATH, RESERVED_TOP_LEVEL_NAMES, lint_vault
from knotica.core.loop_state import compute_gate, read_loop_state
from knotica.core.metrics import last_eval_summary, read_last_metrics
from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.page import TopicNotFoundError
from knotica.core.records import RecordParseError, parse_log_entries, parse_qa_jsonl
from knotica.core.schema import overlay_path
from knotica.core.vcs import GitError, VaultVcs
from knotica.store import VaultStore

__all__ = [
    "COMPILE_READY_MIN_EXAMPLES",
    "STATUS_SCHEMA_VERSION",
    "TopicStatus",
    "gather_wiki_status",
]

#: Stable version of the ``wiki_status`` / ``knotica status --json`` envelope.
STATUS_SCHEMA_VERSION = 1

#: Curated-example floor before a topic's ``qa.jsonl`` can seed a DSPy compile.
COMPILE_READY_MIN_EXAMPLES = 20

#: Log ops that count as a lint run for the "last lint" readout.
_LINT_OPS = frozenset({"lint", "lint_check"})


@dataclass(frozen=True, slots=True)
class TopicStatus:
    """Per-topic progress numbers for status surfaces."""

    topic: str
    pages: int
    curated: int
    lint_violations: int
    last_eval: dict[str, Any] | None

    @property
    def to_compile_ready(self) -> int:
        """Curated examples still needed to reach the compile-ready floor."""
        return max(0, COMPILE_READY_MIN_EXAMPLES - self.curated)

    def render(self) -> dict[str, Any]:
        """JSON object for one topic row."""
        return {
            "topic": self.topic,
            "pages": self.pages,
            "curated": self.curated,
            "to_compile_ready": self.to_compile_ready,
            "lint_violations": self.lint_violations,
            "last_eval": self.last_eval,
        }


def gather_wiki_status(
    store: VaultStore,
    vault_path: Path,
    *,
    topic: str = "",
) -> dict[str, Any]:
    """Build the ``wiki_status`` payload for the whole vault or one topic.

    Raises :class:`~knotica.core.page.TopicNotFoundError` when ``topic`` is
    non-empty and does not name an existing topic directory.
    """
    scope = topic.strip()
    topics = _topic_statuses(store, scope=scope or None)
    last_lint = _last_lint(store)
    unpushed = _unpushed(vault_path)
    gate, loop = _gate_and_loop(store, topics)
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "vault": str(vault_path),
        "compile_ready_threshold": COMPILE_READY_MIN_EXAMPLES,
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
    return [
        TopicStatus(
            topic=name,
            pages=_page_count(store, name),
            curated=_curated_count(store, name),
            lint_violations=lint_counts.get(name, 0),
            last_eval=last_eval_summary(read_last_metrics(store, name)),
        )
        for name in names
    ]


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
    store: VaultStore, topics: list[TopicStatus]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Combine metrics + persisted loop-state into gate and loop readouts.

    Gate/loop are meaningful for a single topic scope (one row). With multiple
    topics, report an honest unknown gate and a null stage.
    """
    if len(topics) != 1:
        return (
            {"state": "unknown", "baseline": None, "last_scalar": None},
            {"stage": None},
        )

    row = topics[0]
    state = read_loop_state(store, row.topic)
    last_scalar = float(row.last_eval["scalar"]) if row.last_eval else None
    last_harness = str(row.last_eval["harness_version"]) if row.last_eval else None
    gate = compute_gate(state, last_scalar=last_scalar, last_harness_version=last_harness)
    loop = {
        "stage": state.stage.value if state is not None else None,
        "candidate_branch": state.candidate_branch if state is not None else None,
        "last_decision": state.last_decision.value if state is not None else None,
    }
    return gate, loop


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


def _curated_count(store: VaultStore, topic: str) -> int:
    """Count curated examples in the topic's ``qa.jsonl`` (0 when absent)."""
    dataset = qa_dataset_path(topic)
    if not store.exists(dataset):
        return 0
    text = store.read_text(dataset)
    try:
        return len(parse_qa_jsonl(text))
    except RecordParseError:
        return sum(1 for line in text.splitlines() if line.strip())


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


def _unpushed(vault_path: Path) -> int | None:
    """Read-only count of commits ahead of the upstream (``None`` if no remote)."""
    try:
        return VaultVcs(vault_path).unpushed_count()
    except GitError:
        return None

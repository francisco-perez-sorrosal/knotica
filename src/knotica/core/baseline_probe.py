"""Naive cold-start probe — fixed zero anchor before any training/eval.

When a topic exists but has no gate-quality ``metrics.jsonl`` history yet,
``baseline_probe`` persists scalar ``0.0``. No LLM, no retrieval scoring, and
**never** loads ``golden.jsonl`` or ``qa.jsonl``.

Full ``knotica eval`` / compile-on-golden remain the gate-quality harnesses; this
probe is only a chart/UX floor until a real measurement exists.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from knotica.core.compiled import load_compiled
from knotica.core.compile_state import read_compile_state
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.loop_state import read_loop_state
from knotica.core.metrics import (
    BASELINE_PROBE_HARNESS_VERSION,
    LEGACY_BASELINE_PROBE_HARNESS_VERSIONS,
    append_metrics_record,
    build_baseline_probe_record,
    next_metrics_generation,
    read_last_metrics,
    render_metrics_record,
)
from knotica.core.vcs import GitError, VaultVcs
from knotica.store import VaultStore

__all__ = [
    "NAIVE_COLD_START_SCALAR",
    "BaselineProbeResult",
    "baseline_probe_eligible",
    "maybe_auto_baseline_probe",
    "run_baseline_probe",
    "topic_exists_for_probe",
]

#: Persisted cold-start floor — pre-training, pre-eval.
NAIVE_COLD_START_SCALAR = 0.0

#: Artifact / runner_mode tag persisted on metrics lines.
_RUNNER_MODE = "zero_anchor"

_PROBE_LOCK = threading.Lock()
_PROBING: set[str] = set()


@dataclass(frozen=True, slots=True)
class BaselineProbeResult:
    """Outcome of one baseline probe (measure + optional persist)."""

    topic: str
    scalar: float
    harness_version: str
    runner_mode: str
    n_examples: int
    corpus_ref: str
    generation: int
    persisted: bool
    record: dict[str, Any]

    def render(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "scalar": self.scalar,
            "harness_version": self.harness_version,
            "runner_mode": self.runner_mode,
            "n_examples": self.n_examples,
            "corpus_ref": self.corpus_ref,
            "generation": self.generation,
            "persisted": self.persisted,
            "record": self.record,
        }


def topic_exists_for_probe(store: VaultStore, topic: str) -> bool:
    """Whether ``topic`` is a vault directory (eligible for a zero cold-start)."""
    cleaned = topic.strip().strip("/")
    if not cleaned:
        return False
    if not store.exists(cleaned):
        return False
    try:
        store.list_dir(cleaned)
    except (NotADirectoryError, FileNotFoundError):
        return False
    return True


def baseline_probe_eligible(store: VaultStore, topic: str) -> bool:
    """Return whether an automatic cold-start probe should run for ``topic``."""
    cleaned = topic.strip().strip("/")
    if not topic_exists_for_probe(store, cleaned):
        return False

    last = read_last_metrics(store, cleaned)
    if last is not None:
        if last.harness_version == BASELINE_PROBE_HARNESS_VERSION:
            return False
        # Legacy measured probes (lexical / retrieval) are stale — allow re-anchor.
        if last.harness_version not in LEGACY_BASELINE_PROBE_HARNESS_VERSIONS:
            return False

    loop_state = read_loop_state(store, cleaned)
    if loop_state is not None:
        if loop_state.baseline_scalar is not None:
            return False
        if loop_state.last_scalar is not None:
            return False

    compile_state = read_compile_state(store, cleaned)
    if compile_state is not None and compile_state.scalar_after is not None:
        return False

    compiled = load_compiled(store, cleaned)
    if compiled is not None:
        compiled_scalar = compiled.metrics.get("compiled")
        if compiled_scalar is not None:
            return False

    return True


def maybe_auto_baseline_probe(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
) -> BaselineProbeResult | None:
    """Run ``run_baseline_probe`` once when the topic exists and no score yet."""
    cleaned = topic.strip().strip("/")
    if not baseline_probe_eligible(store, cleaned):
        return None

    key = f"{Path(vault_root).resolve()}:{cleaned}"
    with _PROBE_LOCK:
        if key in _PROBING:
            return None
        _PROBING.add(key)
    try:
        if not baseline_probe_eligible(store, cleaned):
            return None
        return run_baseline_probe(store, vault_root, cleaned)
    finally:
        with _PROBE_LOCK:
            _PROBING.discard(key)


def run_baseline_probe(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
    *,
    persist: bool = True,
) -> BaselineProbeResult:
    """Persist the naive zero cold-start scalar on the live vault."""
    cleaned = topic.strip().strip("/")
    if not topic_exists_for_probe(store, cleaned):
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            f"Topic {cleaned!r} does not exist; cannot write a cold-start anchor.",
            fix="Create the topic (e.g. create_topic) then retry.",
        )

    scalar = NAIVE_COLD_START_SCALAR
    corpus_ref = _corpus_ref(vault_root)
    generation = next_metrics_generation(store, cleaned)
    record = build_baseline_probe_record(
        cleaned,
        scalar,
        generation=generation,
        n_examples=0,
        corpus_ref=corpus_ref,
        runner_mode=_RUNNER_MODE,
    )
    if persist:
        append_metrics_record(
            store,
            vault_root,
            cleaned,
            record,
            operation="baseline_probe",
            title=f"naive cold-start {scalar:.4f}",
        )
    return BaselineProbeResult(
        topic=cleaned,
        scalar=scalar,
        harness_version=BASELINE_PROBE_HARNESS_VERSION,
        runner_mode=_RUNNER_MODE,
        n_examples=0,
        corpus_ref=corpus_ref,
        generation=generation,
        persisted=persist,
        record=render_metrics_record(record),
    )


def _corpus_ref(vault_root: str | Path) -> str:
    try:
        sha = VaultVcs(Path(vault_root)).head_sha()
    except GitError as error:
        raise KnoticaError(
            ErrorCode.GIT_ERROR,
            f"Could not read vault HEAD for baseline_probe corpus_ref: {error}",
            fix="Ensure the vault is a git repository with at least one commit.",
        ) from error
    return f"git:{sha}"

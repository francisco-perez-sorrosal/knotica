"""The opt-in loop-side gap-fill discovery batch trigger (``[gapfill]
discover_on_regression``, default off).

Derived from ``SYSTEMS_PLAN.md`` §Decision C / Risk R3 and
the planned opt-in batch contract -- never from the implementation. After
an all-knowledge-cause regression persists its ``genuine_gap`` records (the
existing, unchanged redirect path -- see
``tests/test_loop_runner.py::test_all_knowledge_cause_regression_...``), an
**opt-in**, config-gated hook may drain them into staged suggestions in the
same tick: capped by ``max_gaps``, isolated in its own try/except (a discovery
failure must never block the heal path, mirroring the classifier's own
isolation), and written in a transaction **separate** from the gap-record
commit (dec-008).

RED-first: the constructor kwarg / config-flag wiring for
``discover_on_regression`` does not exist on ``LoopRunner`` yet when this file
is written (paired implementer step lands concurrently) -- every production
symbol is resolved lazily inside a helper or the test body. This file was
written without reading the implementer's code.

**Signature assumption (flagged, not verified against the implementation):**
``LoopRunner(..., discover_on_regression=bool, max_gaps=int)`` -- taken
verbatim from the planned ``self._discover_on_regression``
cue, mirroring the existing explicit-kwarg pattern (``arena_enabled``,
``arena_score``, ...). If the implementer's landed wiring differs (e.g. reads
``config.toml`` directly rather than accepting constructor kwargs), that is a
reconciliation point for the integration checkpoint, not a test
design error.

Zero network throughout: the drain's own ``DiscoveryService`` is replaced via
``knotica.core.gapfill.build_default_discovery_service`` (the lazy-import
seam the drain calls at hook time), never a real provider.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from knotica.core.gap_classifier import gaps_path
from knotica.core.loop import LoopRunner, wrap_harness_result
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.vcs import VaultVcs
from knotica.evals.golden import GoldenSetFloorWarning, freeze, load
from knotica.evals.harness import EvalRunResult
from knotica.store import LocalFSStore
from support.vault import git_commit_count, run_git

TOPIC = "agentic-systems"


# ---------------------------------------------------------------------------
# Harness (duplicated from tests/test_loop_runner.py -- the specific subset
# this file needs to manufacture an all-knowledge-cause regression)
# ---------------------------------------------------------------------------


def _freeze_golden(vault: Path, *, query: str, answer: str, pages_used: tuple[str, ...]) -> str:
    store = LocalFSStore(vault)
    with pytest.warns(GoldenSetFloorWarning):
        freeze(
            store,
            vault,
            TOPIC,
            [{"question": query, "reference_answer": answer, "pages_used": list(pages_used)}],
        )
    matches = [record for record in load(store, TOPIC) if record.query == query]
    return matches[-1].id


def _per_id_delta(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "quality_delta": -0.3,
        "qa_accuracy_delta": -0.3,
        "citation_validity_delta": 0.0,
        "pages_added": [],
        "pages_removed": [],
    }
    payload.update(overrides)
    return payload


def _manifest_with_deltas(*, generation: int, per_id: dict[str, dict]) -> dict:
    return {
        "manifest_schema_version": 2,
        "generation": generation,
        "per_example": [{"id": qa_id, "pages": []} for qa_id in per_id],
        "held_out_delta": {
            "ids_added": [],
            "ids_removed": [],
            "prior_generation": generation - 1,
            "scalar_delta": -0.1,
            "per_id": per_id,
        },
    }


def _regression_fake_evaluate(scalar: float, *, generation: int, manifest: dict):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-gapfill-hook-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        manifest_dir = clone.root / topic / ".knotica" / "eval-runs" / f"gen-{generation}"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        run_git(clone.root, "add", "-A")
        run_git(clone.root, "commit", "-m", f"eval: write gen-{generation} manifest")
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-19T00:00:00Z",
            generation=generation,
            harness_version="fake-gapfill-hook",
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


def _commit_content_change(vault: Path, note: str) -> None:
    vcs = VaultVcs(vault)
    vcs.checkout_branch(vcs.default_branch())
    page = vault / TOPIC / "observed-note.md"
    page.write_text(f"# note\n\n{note}\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: {note}")


def _freeze_n_gaps(vault: Path, n: int) -> list[str]:
    """Freeze N golden questions that each reference a nonexistent page (so
    every one classifies as a distinct ``genuine_gap``); returns their ids."""
    store = LocalFSStore(vault)
    entries = [
        {
            "question": f"Does the vault cover made-up-topic-{i}?",
            "reference_answer": "No, that concept is absent from this vault.",
            "pages_used": [f"nonexistent-page-{i}"],
        }
        for i in range(n)
    ]
    with pytest.warns(GoldenSetFloorWarning):
        freeze(store, vault, TOPIC, entries)
    by_query = {record.query: record.id for record in load(store, TOPIC)}
    return [by_query[entry["question"]] for entry in entries]


class _FakeDiscoveryService:
    def __init__(self) -> None:
        self.calls: list = []

    def discover(self, query):
        self.calls.append(query)
        from knotica.discovery.records import SourceCandidate

        return [
            SourceCandidate(
                url=f"https://example.invalid/{len(self.calls)}",
                title="A discovered source",
                snippet="...",
                source_provider="fake",
                doi=None,
            )
        ]


class _RaisingDiscoveryService:
    def discover(self, query):
        raise RuntimeError("discovery unreachable")


def _gapfill_runner(vault: Path, *, evaluate, **hook_kwargs) -> LoopRunner:
    return LoopRunner(
        vault,
        TOPIC,
        evaluate=evaluate,
        arena_enabled=True,
        arena_score=lambda *_args, **_kwargs: 0.0,
        **hook_kwargs,
    )


# ---------------------------------------------------------------------------
# Flag off (default) -- byte-identical to pre-P3: no discover call, no write
# ---------------------------------------------------------------------------


def test_flag_off_by_default_never_calls_discover_or_writes_suggestions(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from knotica.core import gapfill as gapfill_mod

    store = LocalFSStore(template_vault)
    calls: list = []
    monkeypatch.setattr(
        gapfill_mod,
        "refresh_suggestions_for_gaps",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    qa_id = _freeze_n_gaps(template_vault, 1)[0]
    manifest = _manifest_with_deltas(generation=2, per_id={qa_id: _per_id_delta()})
    runner = _gapfill_runner(
        template_vault, evaluate=_regression_fake_evaluate(0.40, generation=2, manifest=manifest)
    )
    runner.set_baseline(0.90, harness_version="fake-gapfill-hook")
    _commit_content_change(template_vault, "the regressing ingest")

    result = runner.observe_default()

    assert result.acted is True
    assert store.exists(gaps_path(TOPIC)), "the gap record itself is unchanged (P1 path)"
    assert calls == [], "flag off (default) must never call the drain -- no discovery, ever"
    assert not gapfill_mod.suggestions_path(TOPIC) or not store.exists(
        gapfill_mod.suggestions_path(TOPIC)
    ), "flag off must never produce a suggestions.jsonl"


# ---------------------------------------------------------------------------
# Flag on -- drains at most max_gaps, in a transaction separate from the gap commit
# ---------------------------------------------------------------------------


def test_flag_on_drains_at_most_max_gaps_in_a_separate_commit(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from knotica.core import gapfill as gapfill_mod

    store = LocalFSStore(template_vault)
    fake_service = _FakeDiscoveryService()
    monkeypatch.setattr(
        gapfill_mod, "build_default_discovery_service", lambda **_kwargs: fake_service
    )
    qa_ids = _freeze_n_gaps(template_vault, 3)
    manifest = _manifest_with_deltas(
        generation=2, per_id={qa_id: _per_id_delta() for qa_id in qa_ids}
    )
    runner = _gapfill_runner(
        template_vault,
        evaluate=_regression_fake_evaluate(0.40, generation=2, manifest=manifest),
        discover_on_regression=True,
        max_gaps=1,
    )
    runner.set_baseline(0.90, harness_version="fake-gapfill-hook")
    before_commits = git_commit_count(template_vault)
    _commit_content_change(template_vault, "the regressing ingest")

    result = runner.observe_default()

    assert result.acted is True
    assert len(fake_service.calls) == 1, (
        "max_gaps=1 must cap the drain to exactly one discover call"
    )
    assert store.exists(gapfill_mod.suggestions_path(TOPIC)), "flag on must stage suggestions"
    after_commits = git_commit_count(template_vault)
    assert after_commits >= before_commits + 2, (
        "the gap-record commit and the suggestion-propose commit must be two distinct "
        "commits (dec-008) -- never piggybacked together"
    )


def test_flag_on_but_drain_raises_still_completes_the_heal_cycle(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure isolation: a discovery exception must never block the heal path --
    the gap records already persisted survive even when the opt-in drain blows up."""
    from knotica.core import gapfill as gapfill_mod

    store = LocalFSStore(template_vault)
    monkeypatch.setattr(
        gapfill_mod,
        "build_default_discovery_service",
        lambda **_kwargs: _RaisingDiscoveryService(),
    )
    qa_id = _freeze_n_gaps(template_vault, 1)[0]
    manifest = _manifest_with_deltas(generation=2, per_id={qa_id: _per_id_delta()})
    runner = _gapfill_runner(
        template_vault,
        evaluate=_regression_fake_evaluate(0.40, generation=2, manifest=manifest),
        discover_on_regression=True,
        max_gaps=5,
    )
    runner.set_baseline(0.90, harness_version="fake-gapfill-hook")
    _commit_content_change(template_vault, "the regressing ingest")

    result = runner.observe_default()

    assert result.acted is True, "a raising drain must never crash or block the observe cycle"
    assert store.exists(gaps_path(TOPIC)), (
        "the gap records committed before the drain ran must survive its failure"
    )
    assert qa_id in store.read_text(gaps_path(TOPIC))

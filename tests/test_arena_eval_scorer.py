"""The opt-in scorer that races on the gate's own instrument.

``[loop] arena_scorer = "eval"`` swaps the keyword heuristic for a scorer that
runs the real golden-set harness per variant. That makes a race's scalars
rankable against the gate baseline for the first time -- and makes a race cost
one full eval per variant, which is why it is opt-in rather than the default.

No test here reaches a model: ``run_eval`` is stubbed at its module seam
throughout, and the scorer's own construction is deliberately observable
without running it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from knotica.core.arena import EVAL_SCORER_ID, HEURISTIC_SCORER_ID
from knotica.core.arena_eval import build_eval_scorer, estimated_race_calls
from knotica.core.errors import KnoticaError
from knotica.core.loop_cadence_config import resolve_loop_cadence_config
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"
MANIFEST_SHA = "222e2eb707b9da30ad8d001fd02e24159e196cefa321bbee4ece97e583b0ab95"


def _freeze_golden(vault: Path, *, n: int = 3) -> None:
    """Write a golden set plus a manifest whose digest matches its bytes."""
    from knotica.core.records import body_sha256

    from knotica.core.records import QARecord

    rows = [
        QARecord(
            id=f"golden-{i}",
            topic=TOPIC,
            created="2026-08-08T00:00:00Z",
            query=f"question {i}?",
            pages_used=(),
            answer=f"answer {i}",
            citations=(),
            verdict="good",
            corrected_answer=None,
            source="distillation",
            model="test-model",
        )
        for i in range(n)
    ]
    body = "".join(row.to_json_line() + "\n" for row in rows)
    datasets = vault / TOPIC / ".knotica" / "datasets"
    datasets.mkdir(parents=True, exist_ok=True)
    (datasets / "golden.jsonl").write_text(body, encoding="utf-8")
    (datasets / "MANIFEST.json").write_text(
        json.dumps(
            {
                "sha256": body_sha256(body),
                "version": "1",
                "source": "human",
                "split": "held_out",
                "size": n,
            }
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_the_eval_scorer_declares_itself_comparable_with_its_provenance(
    template_vault: Path,
) -> None:
    _freeze_golden(template_vault, n=3)

    _score, info = build_eval_scorer(LocalFSStore(template_vault), TOPIC)

    assert info.id == EVAL_SCORER_ID
    assert info.comparable_to_eval is True, "this is the claim that unlocks ranking"
    assert info.n_examples == 3
    assert info.golden_manifest_sha


def test_building_the_eval_scorer_without_a_golden_set_is_refused(
    template_vault: Path,
) -> None:
    """A scorer with nothing to score would return numbers behind no measurement."""
    with pytest.raises(KnoticaError) as excinfo:
        build_eval_scorer(LocalFSStore(template_vault), TOPIC)

    assert "golden set" in str(excinfo.value)


def test_scoring_a_variant_swaps_only_the_prompt_body(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Comparability rests on this: same golden set, same judge, only the prompt differs."""
    _freeze_golden(template_vault)
    captured: dict[str, object] = {}

    def _fake_run_eval(topic: str, **kwargs: object) -> object:
        captured.update({"topic": topic, **kwargs})
        return SimpleNamespace(record=SimpleNamespace(scalar=0.71))

    monkeypatch.setattr("knotica.evals.harness.run_eval", _fake_run_eval)
    score, _info = build_eval_scorer(LocalFSStore(template_vault), TOPIC, num_threads=7)

    scalar = score(TOPIC, template_vault, "VARIANT PROMPT BODY")

    assert scalar == pytest.approx(0.71)
    assert captured["instructions_override"] == "VARIANT PROMPT BODY"
    assert captured["num_threads"] == 7
    assert captured["source_root"] == template_vault


def test_the_race_cost_is_quotable_before_it_is_paid(template_vault: Path) -> None:
    _freeze_golden(template_vault, n=21)

    assert estimated_race_calls(LocalFSStore(template_vault), TOPIC, n_variants=4) == 84


def test_the_cost_quote_is_none_when_the_golden_set_cannot_be_sized(
    template_vault: Path,
) -> None:
    assert estimated_race_calls(LocalFSStore(template_vault), TOPIC, n_variants=4) is None


# ---------------------------------------------------------------------------
# The config flag, and the factory that reads it
# ---------------------------------------------------------------------------


def test_the_arena_scorer_defaults_to_the_free_heuristic(vault_config: Path) -> None:
    """A default install must not start billing for prompt races."""
    del vault_config

    assert resolve_loop_cadence_config().arena_scorer == "heuristic"


def test_an_unknown_arena_scorer_is_refused_with_both_valid_values(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[loop]\narena_scorer = "vibes"\n', encoding="utf-8")

    with pytest.raises(KnoticaError) as excinfo:
        resolve_loop_cadence_config(config)

    assert "heuristic" in str(excinfo.value) and "eval" in str(excinfo.value)


def test_the_factory_builds_the_heuristic_scorer_by_default(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    from knotica.core.loop import build_loop_runner

    runner = build_loop_runner(template_vault, TOPIC, evaluate=lambda *_a: None)

    assert runner._arena_scorer_info is not None
    assert runner._arena_scorer_info.id == HEURISTIC_SCORER_ID
    assert runner._arena_scorer_info.comparable_to_eval is False


def test_the_factory_builds_the_eval_scorer_when_configured(
    vault_config: Path, template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del vault_config
    from knotica.core.config import config_file_path
    from knotica.core.loop import build_loop_runner

    _freeze_golden(template_vault, n=5)
    path = config_file_path()
    path.write_text(
        path.read_text(encoding="utf-8") + '\n[loop]\narena_scorer = "eval"\n', encoding="utf-8"
    )

    runner = build_loop_runner(template_vault, TOPIC, evaluate=lambda *_a: None)

    assert runner._arena_scorer_info is not None
    assert runner._arena_scorer_info.id == EVAL_SCORER_ID
    assert runner._arena_scorer_info.n_examples == 5


def test_an_unbuildable_eval_scorer_falls_back_without_claiming_comparability(
    vault_config: Path, template_vault: Path
) -> None:
    """The fallback must not be a downgrade in disguise.

    With ``arena_scorer = "eval"`` but no frozen golden set, construction cannot
    succeed. Falling back to the heuristic is fine; falling back while still
    claiming eval-comparability would put the original defect back, silently.
    """
    del vault_config
    from knotica.core.config import config_file_path
    from knotica.core.loop import build_loop_runner

    path = config_file_path()
    path.write_text(
        path.read_text(encoding="utf-8") + '\n[loop]\narena_scorer = "eval"\n', encoding="utf-8"
    )

    runner = build_loop_runner(template_vault, TOPIC, evaluate=lambda *_a: None)

    assert runner._arena_scorer_info is not None
    assert runner._arena_scorer_info.id == HEURISTIC_SCORER_ID
    assert runner._arena_scorer_info.comparable_to_eval is False

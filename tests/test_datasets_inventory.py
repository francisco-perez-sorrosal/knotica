"""Dataset inventory, records, and freeze-from-reviewed."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from knotica.core.datasets_inventory import (
    freeze_reviewed_dataset,
    gather_datasets_inventory,
    load_dataset_records,
)
from knotica.core.errors import KnoticaError
from knotica.core.golden_review import reviewed_relative_path, save_golden_review
from support.trainset import populate_query_trainset
from knotica.evals.golden import GoldenSetContaminationError
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"


def _candidate(i: int, *, question: str | None = None) -> dict:
    return {
        "question": question or f"What is concept {i} for datasets tests?",
        "reference_answer": f"Concept {i} is defined in the wiki.",
        "citations": [],
        "pages_used": [],
    }


def test_inventory_roles_and_labels(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    with patch("knotica.core.baseline_probe.maybe_auto_baseline_probe", return_value=None):
        populate_query_trainset(store, template_vault, TOPIC)

    inv = gather_datasets_inventory(store, TOPIC)
    roles = {row["role"]: row for row in inv["files"]}
    assert roles["trainset"]["label"] == "Trainset"
    assert roles["trainset"]["filename"] == "qa.jsonl"
    assert roles["held_out"]["label"] == "Held-out eval"
    assert roles["seal"]["filename"] == "MANIFEST.json"
    assert roles["candidates"]["filename"] == "golden.staging.jsonl"
    assert roles["reviewed"]["filename"] == "golden.staging.reviewed.jsonl"
    assert roles["trainset"]["exists"] is True
    assert inv["overlaps"]["train_held_out"] == 0


def test_load_trainset_records(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    with patch("knotica.core.baseline_probe.maybe_auto_baseline_probe", return_value=None):
        populate_query_trainset(store, template_vault, TOPIC)
    payload = load_dataset_records(store, TOPIC, "trainset", limit=5)
    assert payload["exists"] is True
    assert payload["total"] >= 5
    assert len(payload["records"]) == 5
    assert "query" in payload["records"][0]


def test_freeze_from_reviewed(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    with patch("knotica.core.baseline_probe.maybe_auto_baseline_probe", return_value=None):
        # Trainset only — do not seed golden so freeze can write a fresh set.
        populate_query_trainset(store, template_vault, TOPIC, golden_if_missing=False)

    accepted = [_candidate(i) for i in range(20)]
    save_golden_review(store, template_vault, TOPIC, accepted)
    assert store.exists(reviewed_relative_path(TOPIC))

    with patch("knotica.core.baseline_probe.maybe_auto_baseline_probe", return_value=None):
        result = freeze_reviewed_dataset(store, template_vault, TOPIC)

    assert result["n_frozen"] == 20
    assert result["below_floor"] is False
    assert store.exists(f"{TOPIC}/.knotica/datasets/golden.jsonl")
    assert store.exists(f"{TOPIC}/.knotica/datasets/MANIFEST.json")

    inv = gather_datasets_inventory(store, TOPIC)
    assert inv["pipeline"]["held_out_n"] == 20
    assert inv["pipeline"]["seal_ok"] is True


def test_freeze_refuses_train_overlap(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    with patch("knotica.core.baseline_probe.maybe_auto_baseline_probe", return_value=None):
        populate_query_trainset(store, template_vault, TOPIC, golden_if_missing=False)

    from knotica.core.trainset import load_query_train_examples

    train = load_query_train_examples(store, TOPIC)
    assert train
    contaminated = [
        _candidate(0, question=train[0].query),
        *[_candidate(i + 1) for i in range(19)],
    ]
    save_golden_review(store, template_vault, TOPIC, contaminated)

    with pytest.raises(GoldenSetContaminationError):
        freeze_reviewed_dataset(store, template_vault, TOPIC)


def test_freeze_missing_reviewed(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    with pytest.raises(KnoticaError):
        freeze_reviewed_dataset(store, template_vault, TOPIC)


def test_inventory_overlap_counts(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    with patch("knotica.core.baseline_probe.maybe_auto_baseline_probe", return_value=None):
        populate_query_trainset(store, template_vault, TOPIC, golden_if_missing=False)
    from knotica.core.trainset import load_query_train_examples

    train_q = load_query_train_examples(store, TOPIC)[0].query
    accepted = [_candidate(i) for i in range(20)]
    accepted[0]["question"] = train_q
    # Direct freeze path for held-out with contamination should fail; use inventory
    # on reviewed only.
    save_golden_review(store, template_vault, TOPIC, accepted)
    inv = gather_datasets_inventory(store, TOPIC)
    assert inv["overlaps"]["train_reviewed"] >= 1
    assert inv["pipeline"]["freeze_ready"] is False

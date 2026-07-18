"""Topic dataset inventory + record loads for the Datasets dashboard pane.

Disk filenames stay fixed (``qa.jsonl``, ``golden.jsonl``, …). This module
exposes **role labels**, readiness, contamination overlaps, and capped row
loads for MCP/UI. Bootstrap/freeze wrap :mod:`knotica.evals.golden`.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePath
from typing import Any, Literal

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.golden_review import (
    FLOOR,
    REVIEWED_NAME,
    STAGING_NAME,
    TARGET_HIGH,
    reviewed_relative_path,
    staging_relative_path,
)
from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.page import TopicNotFoundError
from knotica.core.records import RecordParseError, parse_qa_jsonl
from knotica.core.status import COMPILE_READY_MIN_EXAMPLES
from knotica.core.trainset import count_query_train_examples, is_query_train_example
from knotica.evals.golden import (
    EVAL_MIN_GOLDEN,
    GoldenSetMissingError,
    bootstrap,
    freeze,
    golden_dataset_path,
    golden_manifest_path,
    load as load_golden,
)
from knotica.evals.llm import LLMClient
from knotica.store import VaultStore

__all__ = [
    "DATASET_ROLES",
    "DatasetRole",
    "bootstrap_dataset_candidates",
    "freeze_reviewed_dataset",
    "gather_datasets_inventory",
    "load_dataset_records",
]

DatasetRole = Literal["trainset", "held_out", "seal", "candidates", "reviewed"]

DATASET_ROLES: tuple[DatasetRole, ...] = (
    "trainset",
    "held_out",
    "seal",
    "candidates",
    "reviewed",
)

_ROLE_META: dict[DatasetRole, dict[str, str]] = {
    "trainset": {
        "label": "Trainset",
        "group": "loop_corpora",
        "filename": "qa.jsonl",
        "purpose": "Compile/DSPy flywheel — curated query Q&A (good/corrected).",
    },
    "held_out": {
        "label": "Held-out eval",
        "group": "loop_corpora",
        "filename": "golden.jsonl",
        "purpose": "Frozen exam set for eval scalar and compile post-eval gate.",
    },
    "seal": {
        "label": "Held-out seal",
        "group": "loop_corpora",
        "filename": "MANIFEST.json",
        "purpose": "Tamper-evident seal (sha256, split=held_out) for golden.jsonl.",
    },
    "candidates": {
        "label": "Candidates",
        "group": "golden_pipeline",
        "filename": STAGING_NAME,
        "purpose": "Bootstrap scratchpad — uncommitted LLM-synthesized candidates.",
    },
    "reviewed": {
        "label": "Reviewed",
        "group": "golden_pipeline",
        "filename": REVIEWED_NAME,
        "purpose": "Human-kept candidates ready to freeze into held-out eval.",
    },
}

_DEFAULT_RECORDS_LIMIT = 200
_MAX_RECORDS_LIMIT = 1000


def gather_datasets_inventory(store: VaultStore, topic: str) -> dict[str, Any]:
    """Summarize all dataset roles for ``topic`` (counts, readiness, overlaps)."""
    cleaned = _require_topic(store, topic)
    train_questions = _train_questions(store, cleaned)
    held_questions = _held_out_questions(store, cleaned)
    reviewed_questions = _candidate_questions(store, reviewed_relative_path(cleaned))
    candidate_questions = _candidate_questions(store, staging_relative_path(cleaned))

    train_n = count_query_train_examples(store, cleaned)
    qa_total = _qa_total(store, cleaned)
    held_n = len(held_questions)
    reviewed_n = _jsonl_count(store, reviewed_relative_path(cleaned))
    candidates_n = _jsonl_count(store, staging_relative_path(cleaned))
    seal = _seal_info(store, cleaned)

    overlap_train_held = sorted(train_questions & held_questions)
    overlap_train_reviewed = sorted(train_questions & reviewed_questions)
    overlap_train_candidates = sorted(train_questions & candidate_questions)

    files = [
        _file_row(
            store,
            "trainset",
            qa_dataset_path(cleaned),
            count=qa_total,
            ready=train_n >= COMPILE_READY_MIN_EXAMPLES,
            extra={"query_train_n": train_n, "ready_min": COMPILE_READY_MIN_EXAMPLES},
        ),
        _file_row(
            store,
            "held_out",
            golden_dataset_path(cleaned),
            count=held_n,
            ready=held_n >= EVAL_MIN_GOLDEN and seal["ok"],
            extra={"ready_min": EVAL_MIN_GOLDEN},
        ),
        _file_row(
            store,
            "seal",
            golden_manifest_path(cleaned),
            count=1 if seal["exists"] else 0,
            ready=seal["ok"],
            extra={"seal": seal},
        ),
        _file_row(
            store,
            "candidates",
            staging_relative_path(cleaned),
            count=candidates_n,
            ready=candidates_n > 0,
            extra={},
        ),
        _file_row(
            store,
            "reviewed",
            reviewed_relative_path(cleaned),
            count=reviewed_n,
            ready=reviewed_n >= FLOOR,
            extra={"ready_min": FLOOR, "target_high": TARGET_HIGH},
        ),
    ]

    return {
        "topic": cleaned,
        "floor": FLOOR,
        "target_high": TARGET_HIGH,
        "compile_ready_min": COMPILE_READY_MIN_EXAMPLES,
        "eval_min_golden": EVAL_MIN_GOLDEN,
        "files": files,
        "overlaps": {
            "train_held_out": len(overlap_train_held),
            "train_reviewed": len(overlap_train_reviewed),
            "train_candidates": len(overlap_train_candidates),
            "train_held_out_samples": overlap_train_held[:5],
            "train_reviewed_samples": overlap_train_reviewed[:5],
        },
        "pipeline": {
            "candidates_n": candidates_n,
            "reviewed_n": reviewed_n,
            "held_out_n": held_n,
            "seal_ok": seal["ok"],
            "freeze_ready": reviewed_n >= FLOOR and len(overlap_train_reviewed) == 0,
        },
    }


def load_dataset_records(
    store: VaultStore,
    topic: str,
    role: str,
    *,
    limit: int = _DEFAULT_RECORDS_LIMIT,
) -> dict[str, Any]:
    """Load capped rows for one dataset role (read-only)."""
    cleaned = _require_topic(store, topic)
    resolved = _normalize_role(role)
    meta = _ROLE_META[resolved]
    path = _path_for_role(cleaned, resolved)
    cap = max(1, min(int(limit), _MAX_RECORDS_LIMIT))

    if resolved == "seal":
        seal = _seal_info(store, cleaned)
        return {
            "topic": cleaned,
            "role": resolved,
            "label": meta["label"],
            "filename": meta["filename"],
            "path": path,
            "exists": seal["exists"],
            "records": [seal] if seal["exists"] else [],
            "truncated": False,
            "total": 1 if seal["exists"] else 0,
        }

    if not store.exists(path):
        return {
            "topic": cleaned,
            "role": resolved,
            "label": meta["label"],
            "filename": meta["filename"],
            "path": path,
            "exists": False,
            "records": [],
            "truncated": False,
            "total": 0,
        }

    if resolved == "trainset":
        records, total = _load_trainset_rows(store, path, cap)
    elif resolved == "held_out":
        records, total = _load_held_out_rows(store, cleaned, cap)
    else:
        records, total = _load_candidate_rows(store, path, cap)

    return {
        "topic": cleaned,
        "role": resolved,
        "label": meta["label"],
        "filename": meta["filename"],
        "path": path,
        "exists": True,
        "records": records,
        "truncated": total > len(records),
        "total": total,
    }


def bootstrap_dataset_candidates(
    store: VaultStore,
    topic: str,
    *,
    llm_client: LLMClient,
    snapshot: str,
) -> dict[str, Any]:
    """Run golden bootstrap and return staging summary."""
    cleaned = _require_topic(store, topic)
    candidates = bootstrap(store, cleaned, llm_client, snapshot)
    path = staging_relative_path(cleaned)
    return {
        "topic": cleaned,
        "role": "candidates",
        "path": path,
        "n_candidates": len(candidates),
        "filename": STAGING_NAME,
    }


def freeze_reviewed_dataset(
    store: VaultStore,
    vault_root: str | Path | PurePath,
    topic: str,
) -> dict[str, Any]:
    """Freeze ``golden.staging.reviewed.jsonl`` into held-out golden + MANIFEST."""
    cleaned = _require_topic(store, topic)
    reviewed = reviewed_relative_path(cleaned)
    if not store.exists(reviewed):
        raise KnoticaError(
            ErrorCode.PAGE_NOT_FOUND,
            f"no reviewed candidates at {reviewed}",
            fix="Save a reviewed set from the Datasets pane (Review), then Freeze.",
        )
    accepted = _read_jsonl_dicts(store.read_text(reviewed))
    if not accepted:
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            "reviewed set is empty — nothing to freeze",
            fix="Keep at least some candidates, save reviewed, then retry Freeze.",
        )
    result = freeze(store, vault_root, cleaned, accepted)
    return {
        "topic": cleaned,
        "dataset_path": result.dataset_path,
        "manifest_path": result.manifest_path,
        "commit_sha": result.commit_sha,
        "changed": result.changed,
        "n_frozen": result.manifest.size,
        "below_floor": result.below_floor,
        "manifest": {
            "sha256": result.manifest.sha256,
            "version": result.manifest.version,
            "source": result.manifest.source,
            "split": result.manifest.split,
            "size": result.manifest.size,
        },
    }


def _file_row(
    store: VaultStore,
    role: DatasetRole,
    path: str,
    *,
    count: int,
    ready: bool,
    extra: dict[str, Any],
) -> dict[str, Any]:
    meta = _ROLE_META[role]
    exists = store.exists(path)
    return {
        "role": role,
        "label": meta["label"],
        "group": meta["group"],
        "filename": meta["filename"],
        "path": path,
        "purpose": meta["purpose"],
        "exists": exists,
        "count": count if exists else 0,
        "ready": bool(ready and exists),
        **extra,
    }


def _path_for_role(topic: str, role: DatasetRole) -> str:
    if role == "trainset":
        return qa_dataset_path(topic)
    if role == "held_out":
        return golden_dataset_path(topic)
    if role == "seal":
        return golden_manifest_path(topic)
    if role == "candidates":
        return staging_relative_path(topic)
    return reviewed_relative_path(topic)


def _normalize_role(role: str) -> DatasetRole:
    cleaned = role.strip().lower().replace("-", "_")
    aliases = {
        "qa": "trainset",
        "train": "trainset",
        "golden": "held_out",
        "heldout": "held_out",
        "manifest": "seal",
        "staging": "candidates",
        "golden_staging": "candidates",
        "golden_reviewed": "reviewed",
    }
    mapped = aliases.get(cleaned, cleaned)
    if mapped not in _ROLE_META:
        raise KnoticaError(
            ErrorCode.INVALID_FRONTMATTER,
            f"unknown dataset role {role!r}",
            fix=f"Use one of: {', '.join(DATASET_ROLES)}.",
        )
    return mapped  # type: ignore[return-value]


def _require_topic(store: VaultStore, topic: str) -> str:
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    if not store.exists(cleaned):
        raise TopicNotFoundError(cleaned)
    try:
        store.list_dir(cleaned)
    except (NotADirectoryError, FileNotFoundError) as exc:
        raise TopicNotFoundError(cleaned) from exc
    return cleaned


def _qa_total(store: VaultStore, topic: str) -> int:
    path = qa_dataset_path(topic)
    if not store.exists(path):
        return 0
    try:
        return len(parse_qa_jsonl(store.read_text(path)))
    except RecordParseError:
        return _jsonl_count(store, path)


def _train_questions(store: VaultStore, topic: str) -> set[str]:
    path = qa_dataset_path(topic)
    if not store.exists(path):
        return set()
    try:
        records = parse_qa_jsonl(store.read_text(path))
    except RecordParseError:
        return set()
    return {
        record.query.strip().lower()
        for record in records
        if is_query_train_example(record) and record.query.strip()
    }


def _held_out_questions(store: VaultStore, topic: str) -> set[str]:
    try:
        golden = load_golden(store, topic)
    except GoldenSetMissingError:
        # Unsealed / missing — still count raw golden.jsonl questions if present.
        path = golden_dataset_path(topic)
        if not store.exists(path):
            return set()
        try:
            records = parse_qa_jsonl(store.read_text(path))
        except RecordParseError:
            return set()
        return {record.query.strip().lower() for record in records if record.query.strip()}
    except Exception:  # noqa: BLE001 — inventory must not crash on integrity errors
        path = golden_dataset_path(topic)
        if not store.exists(path):
            return set()
        try:
            records = parse_qa_jsonl(store.read_text(path))
        except RecordParseError:
            return set()
        return {record.query.strip().lower() for record in records if record.query.strip()}
    return {record.query.strip().lower() for record in golden if record.query.strip()}


def _candidate_questions(store: VaultStore, path: str) -> set[str]:
    if not store.exists(path):
        return set()
    questions: set[str] = set()
    for row in _read_jsonl_dicts(store.read_text(path)):
        q = str(row.get("question", "")).strip().lower()
        if q:
            questions.add(q)
    return questions


def _seal_info(store: VaultStore, topic: str) -> dict[str, Any]:
    path = golden_manifest_path(topic)
    if not store.exists(path):
        return {"exists": False, "ok": False, "path": path}
    try:
        payload = json.loads(store.read_text(path))
    except json.JSONDecodeError:
        return {"exists": True, "ok": False, "path": path, "error": "invalid JSON"}
    if not isinstance(payload, dict):
        return {"exists": True, "ok": False, "path": path, "error": "not an object"}
    split = payload.get("split")
    sha = payload.get("sha256")
    ok = split == "held_out" and isinstance(sha, str) and bool(sha)
    return {
        "exists": True,
        "ok": ok,
        "path": path,
        "sha256": sha,
        "version": payload.get("version"),
        "source": payload.get("source"),
        "split": split,
        "size": payload.get("size"),
    }


def _jsonl_count(store: VaultStore, path: str) -> int:
    if not store.exists(path):
        return 0
    return sum(1 for line in store.read_text(path).splitlines() if line.strip())


def _read_jsonl_dicts(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _load_trainset_rows(
    store: VaultStore, path: str, limit: int
) -> tuple[list[dict[str, Any]], int]:
    try:
        records = parse_qa_jsonl(store.read_text(path))
    except RecordParseError:
        raw = _read_jsonl_dicts(store.read_text(path))
        return raw[:limit], len(raw)
    out: list[dict[str, Any]] = []
    for record in records[:limit]:
        out.append(
            {
                "id": record.id,
                "query": record.query,
                "answer": record.answer,
                "corrected_answer": record.corrected_answer,
                "verdict": record.verdict,
                "source": record.source,
                "citations": list(record.citations),
                "pages_used": list(record.pages_used),
                "query_train": is_query_train_example(record),
            }
        )
    return out, len(records)


def _load_held_out_rows(
    store: VaultStore, topic: str, limit: int
) -> tuple[list[dict[str, Any]], int]:
    path = golden_dataset_path(topic)
    try:
        records = list(load_golden(store, topic))
    except Exception:  # noqa: BLE001
        try:
            records = parse_qa_jsonl(store.read_text(path))
        except RecordParseError:
            raw = _read_jsonl_dicts(store.read_text(path))
            return raw[:limit], len(raw)
    out = [
        {
            "id": record.id,
            "query": record.query,
            "answer": record.answer,
            "verdict": record.verdict,
            "source": record.source,
            "citations": list(record.citations),
            "pages_used": list(record.pages_used),
        }
        for record in records[:limit]
    ]
    return out, len(records)


def _load_candidate_rows(
    store: VaultStore, path: str, limit: int
) -> tuple[list[dict[str, Any]], int]:
    rows = _read_jsonl_dicts(store.read_text(path))
    slim = [
        {
            "question": row.get("question", ""),
            "reference_answer": row.get("reference_answer", ""),
            "citations": row.get("citations", []),
            "pages_used": row.get("pages_used", []),
            "support": row.get("support"),
        }
        for row in rows[:limit]
    ]
    return slim, len(rows)

"""Deterministic four-way fault classifier for the loop regression hook.

Pure set logic over already-persisted eval data: given a regressed generation's
schema-v2 manifest (``held_out_delta.per_id`` + ``per_example`` trace) and the
frozen golden set, it assigns each regressed golden id exactly one fault class
via an ordered first-match cascade, then recommends a route (``HEAL`` keeps the
existing arena prompt heal; ``REDIRECT`` skips it and persists gap records).

No LLM, no harness change -- so the eval fingerprint is untouched. Reads run
against the eval *clone* store only (never the live vault). The golden loader is
imported lazily inside :func:`classify_regression` so this module stays
cold-start-clean regardless of how ``evals/`` evolves; every top-level import is
a deterministic ``core``/``store`` sibling.

Exception discipline: nothing here is caught. A malformed manifest, an
unreadable golden set, or a bad page name propagates as ``ValueError``/
``KeyError``; the loop hook owns the single ``try/except`` boundary and falls
through to the existing heal on any failure (failure isolation).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from knotica.core.page import page_path
from knotica.core.records import GapEvidence, GapRecord, parse_gaps_jsonl
from knotica.core.transaction import VaultTransaction
from knotica.store import VaultStore

__all__ = [
    "ADDED_ID_FAILING_FLOOR",
    "CLASSIFIER_VERSION",
    "FaultClass",
    "GapVerdict",
    "RegressionClassification",
    "build_gap_records",
    "classify_regression",
    "gaps_path",
    "prior_generation_of",
    "read_regression_manifest",
    "regressed_ids_from_manifest",
    "write_gap_records",
]

#: A manifest is only a usable classifier substrate at schema v2+ (it carries the
#: ``per_example[].id``/``.pages`` trace and the ``held_out_delta`` object).
_MIN_MANIFEST_SCHEMA_VERSION = 2

#: Which classifier logic produced a record -- an independent capability probe
#: from the record schema_version (bump this when the cascade changes shape-compatibly).
CLASSIFIER_VERSION = 1

#: Directory name under each topic that owns loop/eval artifacts.
_KNOTICA_DIR = ".knotica"
#: Basename + subdir of the per-topic knowledge-gap queue.
_GAPS_DIRNAME = "gaps"
_GAPS_FILENAME = "gaps.jsonl"
#: Op slot of the gap-record commit (own transaction, one commit per detection).
_GAP_RECORD_OP = "gap_record"

#: Below this floor on either current ``qa_accuracy`` or ``quality``, a newly
#: frozen golden id (``held_out_delta.ids_added`` -- no prior generation, hence
#: no delta) is treated as failing and enters the cascade with an empty-delta
#: evidence context. A healthy new question (both scores at or above the floor)
#: never classifies.
ADDED_ID_FAILING_FLOOR = 0.5


class FaultClass:
    """The five fault classes as plain ``str`` values.

    Persisted verbatim and read out-of-process, so they are bare tagged strings
    (not a ``StrEnum``) that round-trip without enum-coercion failure. Only the
    two knowledge-cause classes are ever written as a gap record; the neutral
    ``UNCLASSIFIED`` and the prompt/retrieval classes route to the arena heal.
    """

    GENUINE_GAP = "genuine_gap"
    DILUTION = "dilution"
    GENERATION_FAULT = "generation_fault"
    RETRIEVAL_FAULT = "retrieval_fault"
    UNCLASSIFIED = "unclassified"


#: Verdicts that are persisted and let the loop skip the arena.
_KNOWLEDGE_CAUSE: frozenset[str] = frozenset({FaultClass.GENUINE_GAP, FaultClass.DILUTION})


@dataclass(frozen=True, kw_only=True)
class GapVerdict:
    """One regressed id's fault classification plus its detection-time evidence.

    The evidence fields are lifted verbatim from the manifest's per-id delta and
    per-example trace so a downstream gap record carries them without any
    re-derivation (the P3 page-name join depends on byte-identity).
    """

    qa_id: str
    fault_class: str
    question: str
    reference_pages: tuple[str, ...]
    refs_exist: bool
    quality_delta: float
    qa_accuracy_delta: float
    citation_validity_delta: float
    retrieval_trace: tuple[str, ...]
    pages_added: tuple[str, ...]
    pages_removed: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class RegressionClassification:
    """Per-id verdicts plus the whole-cycle route recommendation."""

    verdicts: tuple[GapVerdict, ...]
    route: Literal["HEAL", "REDIRECT"]


def classify_regression(
    store: VaultStore,
    topic: str,
    clone_root: str | Path,
    generation: int,
    manifest: Mapping[str, object],
    regressed_ids: Sequence[str],
) -> RegressionClassification:
    """Classify each regressed golden id from an already-parsed v2 manifest.

    ``store`` is a :class:`VaultStore` rooted at the eval clone (``clone_root``)
    -- every read (golden set, page existence) goes through it, never the live
    vault. The manifest dict is the one the loop already parsed for
    ``held_out_delta``; it is not re-read here. ``clone_root`` and ``generation``
    are part of the loop-hook call contract (clone identity + gap-record
    provenance); the cascade itself needs only the manifest and the clone reads.
    ``route`` is ``REDIRECT`` only when every regressed id is knowledge-cause and
    the verdict list is non-empty, else ``HEAL``.
    """
    from knotica.evals.golden import load as load_golden

    golden = {record.id: record for record in load_golden(store, topic)}
    per_id = _as_mapping(_nested(manifest, "held_out_delta"), "per_id")
    trace_by_id, question_by_id = _index_per_example(manifest)

    verdicts: list[GapVerdict] = []
    for qa_id in regressed_ids:
        reference_pages = golden[qa_id].pages_used if qa_id in golden else ()
        trace = trace_by_id.get(qa_id, ())
        delta = _as_mapping(per_id, qa_id) if qa_id in per_id else {}
        refs_exist = any(store.exists(page_path(topic, page)) for page in reference_pages)
        pages_added = _str_tuple(delta.get("pages_added", ()))
        pages_removed = _str_tuple(delta.get("pages_removed", ()))
        fault_class = _classify_one(reference_pages, refs_exist, trace, pages_added, pages_removed)
        verdicts.append(
            GapVerdict(
                qa_id=qa_id,
                fault_class=fault_class,
                question=question_by_id.get(qa_id, ""),
                reference_pages=reference_pages,
                refs_exist=refs_exist,
                quality_delta=_number(delta.get("quality_delta", 0.0)),
                qa_accuracy_delta=_number(delta.get("qa_accuracy_delta", 0.0)),
                citation_validity_delta=_number(delta.get("citation_validity_delta", 0.0)),
                retrieval_trace=trace,
                pages_added=pages_added,
                pages_removed=pages_removed,
            )
        )

    route: Literal["HEAL", "REDIRECT"] = (
        "REDIRECT"
        if verdicts and all(verdict.fault_class in _KNOWLEDGE_CAUSE for verdict in verdicts)
        else "HEAL"
    )
    return RegressionClassification(verdicts=tuple(verdicts), route=route)


def read_regression_manifest(
    clone_root: str | Path,
    topic: str,
    generation: int,
) -> dict[str, object] | None:
    """Load the regressed generation's manifest iff it carries a v2 diagnostic delta.

    Returns the parsed manifest only when ``manifest_schema_version >= 2`` and a
    non-null ``held_out_delta`` is present -- the substrate the cascade needs.
    Returns ``None`` for a v1 (delta-free) manifest or a null delta so the caller
    routes to a plain arena heal and writes nothing. A missing file or malformed
    JSON propagates: the loop hook owns the single exception boundary.
    """
    path = Path(clone_root) / _manifest_ref(topic, generation)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"manifest must be a JSON object, got {manifest!r}")
    version = manifest.get("manifest_schema_version", 1)
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(f"manifest 'manifest_schema_version' must be an int, got {version!r}")
    if version < _MIN_MANIFEST_SCHEMA_VERSION or manifest.get("held_out_delta") is None:
        return None
    return manifest


def regressed_ids_from_manifest(manifest: Mapping[str, object]) -> list[str]:
    """Golden ids whose held-out quality or QA-accuracy fell vs the prior generation.

    The id-level regression predicate is ``quality_delta < 0`` OR
    ``qa_accuracy_delta < 0``; a citation-only movement is not a regression at
    this granularity (it routes to the arena heal, never a gap record).

    Also includes ids newly frozen this generation (``held_out_delta.ids_added``
    -- absent from ``per_id`` because they carry no prior-generation score)
    whose CURRENT per-example ``qa_accuracy`` or ``quality`` falls below
    :data:`ADDED_ID_FAILING_FLOOR`. This closes the "freeze a question about
    missing content" gap-manufacture path: such a golden id would otherwise
    never classify since it has no delta to regress on. A healthy new question
    (both scores at or above the floor) is not a gap and never enters the
    cascade.
    """
    held_out_delta = _nested(manifest, "held_out_delta")
    per_id = _as_mapping(held_out_delta, "per_id")
    regressed: list[str] = []
    for qa_id in per_id:
        delta = _as_mapping(per_id, qa_id)
        quality_fell = _number(delta.get("quality_delta", 0.0)) < 0
        accuracy_fell = _number(delta.get("qa_accuracy_delta", 0.0)) < 0
        if quality_fell or accuracy_fell:
            regressed.append(qa_id)

    ids_added = _str_tuple(held_out_delta.get("ids_added", ()))
    if ids_added:
        scores_by_id = _index_current_scores(manifest)
        for qa_id in ids_added:
            qa_accuracy, quality = scores_by_id[qa_id]
            if qa_accuracy < ADDED_ID_FAILING_FLOOR or quality < ADDED_ID_FAILING_FLOOR:
                regressed.append(qa_id)
    return regressed


def prior_generation_of(manifest: Mapping[str, object]) -> int:
    """The generation the held-out delta was computed against (global to the delta)."""
    value = _nested(manifest, "held_out_delta").get("prior_generation")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"manifest held_out_delta.prior_generation must be an int, got {value!r}")
    return value


def _classify_one(
    reference_pages: tuple[str, ...],
    refs_exist: bool,
    trace: tuple[str, ...],
    pages_added: tuple[str, ...],
    pages_removed: tuple[str, ...],
) -> str:
    """The ordered first-match cascade -- precedence resolves co-occurrence.

    Order matters: ``generation_fault`` (reference present in the trace) is
    tested before ``dilution``, and ``dilution`` requires *both* a fresh
    displacement (a reference in ``pages_removed``) *and* a new competitor
    (``pages_added`` non-empty). Everything else with an existing-but-missing
    reference is ``retrieval_fault``; a reference-less id is ``unclassified``.
    """
    if not reference_pages:
        return FaultClass.UNCLASSIFIED
    if not refs_exist:
        return FaultClass.GENUINE_GAP
    trace_set = set(trace)
    if any(page in trace_set for page in reference_pages):
        return FaultClass.GENERATION_FAULT
    removed_set = set(pages_removed)
    if any(page in removed_set for page in reference_pages) and pages_added:
        return FaultClass.DILUTION
    return FaultClass.RETRIEVAL_FAULT


def build_gap_records(
    verdicts: Sequence[GapVerdict],
    *,
    topic: str,
    generation: int,
    scalar_at_detection: float,
    baseline_scalar: float,
    prior_generation: int,
    classifier_version: int = CLASSIFIER_VERSION,
    clock: Callable[[], str] | None = None,
) -> list[GapRecord]:
    """Turn knowledge-cause verdicts into gap records (pure; prompt-cause dropped).

    ``clock`` yields the ISO-8601 UTC ``detected_at`` stamp and is injectable for
    testability, defaulting to wall-clock UTC.
    """
    stamp = clock or _utc_now_iso
    manifest_ref = _manifest_ref(topic, generation)
    detected_at = stamp()
    records: list[GapRecord] = []
    for verdict in verdicts:
        if verdict.fault_class not in _KNOWLEDGE_CAUSE:
            continue
        records.append(
            GapRecord(
                gap_id=_gap_id(topic, verdict.qa_id, verdict.fault_class),
                topic=topic,
                qa_id=verdict.qa_id,
                fault_class=verdict.fault_class,
                status="open",
                classifier_version=classifier_version,
                detected_generation=generation,
                detected_at=detected_at,
                scalar_at_detection=scalar_at_detection,
                baseline_scalar=baseline_scalar,
                question=verdict.question,
                reference_pages=verdict.reference_pages,
                reference_pages_exist=verdict.refs_exist,
                evidence=GapEvidence(
                    quality_delta=verdict.quality_delta,
                    qa_accuracy_delta=verdict.qa_accuracy_delta,
                    citation_validity_delta=verdict.citation_validity_delta,
                    retrieval_trace=verdict.retrieval_trace,
                    pages_added=verdict.pages_added,
                    pages_removed=verdict.pages_removed,
                    prior_generation=prior_generation,
                ),
                manifest_ref=manifest_ref,
            )
        )
    return records


def write_gap_records(
    store: VaultStore,
    root: str | Path,
    topic: str,
    records: Sequence[GapRecord],
) -> None:
    """Append gap records to ``<topic>/.knotica/gaps/gaps.jsonl`` in one own commit.

    Reads the existing queue, drops any record whose ``(qa_id, fault_class)`` pair
    already has an ``open`` entry (dedup so a persistent regression does not spam
    the queue every cycle), and writes the whole file once inside a single
    :class:`VaultTransaction`. Writing zero survivors opens no transaction -- no
    empty commit.
    """
    if not records:
        return
    path = gaps_path(topic)
    existing_text = store.read_text(path) if store.exists(path) else ""
    existing = parse_gaps_jsonl(existing_text) if existing_text.strip() else []
    open_keys = {
        (record.qa_id, record.fault_class) for record in existing if record.status == "open"
    }
    survivors = [
        record for record in records if (record.qa_id, record.fault_class) not in open_keys
    ]
    if not survivors:
        return
    body = _append_jsonl_lines(existing_text, [record.to_json_line() for record in survivors])
    title = f"{len(survivors)} knowledge gaps at gen-{survivors[0].detected_generation}"
    with VaultTransaction(store, Path(root), _GAP_RECORD_OP, topic, title) as txn:
        txn.write(path, body)


def gaps_path(topic: str) -> str:
    """Vault-relative path of a topic's ``gaps.jsonl``."""
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned or cleaned in {".", ".."}:
        raise ValueError(f"topic must be a single path segment, got {topic!r}")
    return f"{cleaned}/{_KNOTICA_DIR}/{_GAPS_DIRNAME}/{_GAPS_FILENAME}"


def _manifest_ref(topic: str, generation: int) -> str:
    """The clone-relative manifest path recorded as gap-record provenance."""
    return f"{topic}/{_KNOTICA_DIR}/eval-runs/gen-{generation}/manifest.json"


def _gap_id(topic: str, qa_id: str, fault_class: str) -> str:
    """Stable 16-hex dedup + P3 join key over the identifying triple."""
    return hashlib.sha1(f"{topic}|{qa_id}|{fault_class}".encode()).hexdigest()[:16]


def _utc_now_iso() -> str:
    """Wall-clock ``detected_at`` stamp in ISO-8601 UTC (``…Z`` suffix)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_jsonl_lines(existing_text: str, lines: Sequence[str]) -> str:
    """Append JSONL lines, preserving prior records and a single trailing newline."""
    block = "\n".join(lines) + "\n"
    if not existing_text.strip():
        return block
    return existing_text.rstrip("\n") + "\n" + block


def _index_per_example(
    manifest: Mapping[str, object],
) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    """Index the manifest ``per_example`` trace and question text by golden id."""
    entries = manifest.get("per_example", [])
    if not isinstance(entries, list):
        raise ValueError(f"manifest 'per_example' must be an array, got {entries!r}")
    trace_by_id: dict[str, tuple[str, ...]] = {}
    question_by_id: dict[str, str] = {}
    for entry in entries:
        entry_id = entry["id"]
        trace_by_id[entry_id] = _str_tuple(entry.get("pages", ()))
        question_by_id[entry_id] = str(entry.get("question", ""))
    return trace_by_id, question_by_id


def _index_current_scores(manifest: Mapping[str, object]) -> dict[str, tuple[float, float]]:
    """Index each ``per_example`` entry's current ``(qa_accuracy, quality)`` by id."""
    entries = manifest.get("per_example", [])
    if not isinstance(entries, list):
        raise ValueError(f"manifest 'per_example' must be an array, got {entries!r}")
    return {
        entry["id"]: (_number(entry.get("qa_accuracy", 0.0)), _number(entry.get("quality", 0.0)))
        for entry in entries
    }


def _nested(manifest: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = manifest.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"manifest {key!r} must be an object, got {value!r}")
    return value


def _as_mapping(container: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = container.get(key, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"manifest field {key!r} must be an object, got {value!r}")
    return value


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"expected an array of strings, got {value!r}")
    return tuple(value)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"expected a number, got {value!r}")
    return float(value)

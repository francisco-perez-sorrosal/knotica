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
    "CLASSIFIER_VERSION",
    "FaultClass",
    "GapVerdict",
    "RegressionClassification",
    "build_gap_records",
    "classify_regression",
    "gaps_path",
    "write_gap_records",
]

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

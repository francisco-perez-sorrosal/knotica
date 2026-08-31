"""The freeze stage: human-accepted candidates become the topic's frozen golden set.

The one human-gated write. :func:`freeze` replaces the entire ``golden.jsonl`` with
the accepted candidates (it does not append), verifies the set is disjoint from the
flywheel trainset **before** writing anything, and commits the set plus its
content-addressing ``MANIFEST.json`` through exactly one
:class:`~knotica.core.transaction.VaultTransaction` -- one commit, the single-writer
invariant, which is why this stage takes ``vault_root`` and the generate stage does
not.

Two deliberate asymmetries. The contamination check runs at freeze time as well as
read time, so a contaminated set is refused before any byte is written. And a set
below :data:`~knotica.evals.golden.EVAL_MIN_GOLDEN` still freezes, emitting a
warning rather than raising -- the human is the gate; a small set just makes the
scalar noisier.
"""

import hashlib
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath

from knotica.core.errors import KnoticaWarning
from knotica.core.records import QARecord, body_sha256
from knotica.core.scrub import scrub
from knotica.core.transaction import VaultTransaction
from knotica.evals.golden.candidates import (
    _ANSWER_KEY,
    _CITATIONS_KEY,
    _PAGES_KEY,
    _QUESTION_KEY,
    _optional_candidate_str_tuple,
    _required_candidate_str,
)
from knotica.evals.golden.contract import (
    EVAL_MIN_GOLDEN,
    GoldenSetFloorWarning,
    golden_dataset_path,
    golden_manifest_path,
)
from knotica.evals.golden.manifest import GOLDEN_SPLIT, GoldenManifest, _render_manifest
from knotica.evals.golden.read import verify_disjoint_from_trainset
from knotica.store import VaultStore

#: ``source`` field the frozen ``MANIFEST.json`` records for a bootstrapped set --
#: the dataset-level *provenance* marker. Distinct from the per-record
#: ``QARecord.source`` (:data:`_RECORD_SOURCE`): the records read as ordinary
#: human-curated examples (no schema change), while the manifest carries the
#: "these began as synthetic candidates" provenance the records do not.
_MANIFEST_SOURCE = "synthetic"

#: The frozen ``QARecord.source`` enum value: human review-and-freeze *is* a
#: curation act, so the frozen enum is reused rather than migrated for a new
#: ``synthetic`` value. Equal to ``curate_example.py``'s source by design.
_RECORD_SOURCE = "curate_example"

#: The ``QARecord.verdict`` a frozen golden pair carries: an accepted reference
#: answer is a good one by definition (the human vouched for it).
_RECORD_VERDICT = "good"

#: The ``QARecord.model`` a frozen golden record carries. The record reads as a
#: human-curated example (mirroring ``curate_example.py``'s ``"unknown"``); the
#: synthetic-generation provenance lives in the manifest, not the record.
_RECORD_MODEL = "unknown"

#: The :class:`~knotica.core.transaction.VaultTransaction` op name + title for a
#: freeze commit. Reuses ``curate_example``'s op grammar (``QARecord.source``
#: already carries the provenance distinction, so no new op grammar is needed).
_FREEZE_OP = "curate_example"
_FREEZE_TITLE = "freeze golden set"


@dataclass(frozen=True, kw_only=True)
class FreezeResult:
    """The outcome of a completed :func:`freeze`.

    ``manifest`` is the sibling ``MANIFEST.json`` as written (its ``sha256``
    content-addresses the frozen ``golden.jsonl`` bytes); ``below_floor`` is
    ``True`` when the set fell under :data:`~knotica.evals.golden.EVAL_MIN_GOLDEN`
    (the warning that was also emitted); ``warnings`` carries any secret-scrub
    findings from the write.
    """

    manifest: GoldenManifest
    dataset_path: str
    manifest_path: str
    commit_sha: str
    changed: bool
    below_floor: bool
    warnings: tuple[KnoticaWarning, ...]


def freeze(
    store: VaultStore,
    vault_root: str | PurePath,
    topic: str,
    accepted: Sequence[Mapping[str, object]],
) -> FreezeResult:
    """Freeze human-accepted candidates into the topic's held-out golden set.

    Replaces the entire frozen ``golden.jsonl`` with the accepted candidates
    (does not append to an existing set). Builds a :class:`~knotica.core.records.QARecord`
    from each accepted candidate (``source: curate_example``), verifies the set is
    disjoint from the flywheel trainset **before** writing anything, then writes
    ``golden.jsonl`` and its sibling ``MANIFEST.json`` (``sha256`` content-addressing
    the frozen bytes, ``split: held_out``) through exactly one
    :class:`~knotica.core.transaction.VaultTransaction` -- one commit. Freezing
    fewer than :data:`~knotica.evals.golden.EVAL_MIN_GOLDEN` records still succeeds
    but emits a :class:`~knotica.evals.golden.GoldenSetFloorWarning` (the human is
    the gate).

    Args:
        store: The vault storage backend (the same vault ``vault_root`` names).
        vault_root: The already-resolved vault root the transaction commits on.
        topic: The topic the golden set belongs to.
        accepted: The human-reviewed candidate dicts to freeze (each carrying at
            least a ``question`` and ``reference_answer``; ``citations`` and
            ``pages_used`` default to empty when absent).

    Returns:
        A :class:`FreezeResult` with the written manifest, the commit sha, and any
        secret-scrub / below-floor findings.

    Raises:
        GoldenCandidateError: If an accepted candidate lacks a question/answer.
        GoldenSetContaminationError: If a frozen question also appears in the
            topic's flywheel ``qa.jsonl`` -- nothing is written.
    """
    records = [_build_golden_record(topic, candidate) for candidate in accepted]
    # Held-out-split guard at freeze time (not only at read time): a contaminated
    # set is refused before any byte is written, so nothing is committed.
    verify_disjoint_from_trainset(store, topic, records)

    below_floor = len(records) < EVAL_MIN_GOLDEN
    if below_floor:
        warnings.warn(_floor_message(topic, len(records)), GoldenSetFloorWarning, stacklevel=2)

    golden_body, manifest = _frozen_bytes_and_manifest(records)
    dataset_path = golden_dataset_path(topic)
    manifest_path = golden_manifest_path(topic)
    with VaultTransaction(store, vault_root, _FREEZE_OP, topic, _FREEZE_TITLE) as txn:
        txn.write(dataset_path, golden_body)
        txn.write(manifest_path, _render_manifest(manifest))
    result = txn.result
    freeze_result = FreezeResult(
        manifest=manifest,
        dataset_path=dataset_path,
        manifest_path=manifest_path,
        commit_sha=result.commit_sha,
        changed=result.changed,
        below_floor=below_floor,
        warnings=result.warnings(),
    )
    if len(records) >= EVAL_MIN_GOLDEN:
        from knotica.core.baseline_probe import maybe_auto_baseline_probe

        # ``vault_root`` is annotated as wide as the vault-transaction convention
        # allows (``str | PurePath``); the probe does real filesystem work and takes
        # the narrower ``str | Path``, so adapt at the boundary rather than widening
        # it. ``Path`` is idempotent over ``str``/``Path``, which is all this ever
        # receives -- it is the same conversion the probe performs on its first line.
        maybe_auto_baseline_probe(store, Path(vault_root), topic)
    return freeze_result


def _build_golden_record(topic: str, candidate: Mapping[str, object]) -> QARecord:
    """Build one frozen golden ``QARecord`` from an accepted candidate dict."""
    query = _required_candidate_str(candidate, _QUESTION_KEY)
    answer = _required_candidate_str(candidate, _ANSWER_KEY)
    return QARecord(
        id=_golden_id(query, answer),
        topic=topic,
        created=datetime.now(UTC).isoformat(),
        query=query,
        pages_used=_optional_candidate_str_tuple(candidate, _PAGES_KEY),
        answer=answer,
        citations=_optional_candidate_str_tuple(candidate, _CITATIONS_KEY),
        verdict=_RECORD_VERDICT,
        corrected_answer=None,
        source=_RECORD_SOURCE,
        model=_RECORD_MODEL,
    )


def _golden_id(query: str, answer: str) -> str:
    """A deterministic record id from ``(query, answer)`` -- stable across re-freezes."""
    digest = hashlib.sha256("\x00".join((query, answer)).encode("utf-8")).hexdigest()
    return f"golden-{digest[:16]}"


def _frozen_bytes_and_manifest(records: Sequence[QARecord]) -> tuple[str, GoldenManifest]:
    """The golden.jsonl body and its content-addressing manifest for ``records``.

    The manifest's ``sha256`` is taken over the *scrubbed* form of the body -- the
    exact bytes the transaction stores after its secret scrub (identical to the raw
    body when there is nothing to redact) -- so the round-trip through
    :func:`~knotica.evals.golden.load` stays exact even if a secret slipped into a
    reviewed answer.
    """
    golden_body = _jsonl_body(records)
    scrubbed_body, _spans = scrub(golden_body)
    manifest = GoldenManifest(
        sha256=body_sha256(scrubbed_body),
        version=datetime.now(UTC).strftime("%Y-%m-%d"),
        source=_MANIFEST_SOURCE,
        split=GOLDEN_SPLIT,
        size=len(records),
    )
    return golden_body, manifest


def _jsonl_body(records: Sequence[QARecord]) -> str:
    """Render the whole frozen golden set: one JSON line per record, newline-terminated."""
    return "".join(record.to_json_line() + "\n" for record in records)


def _floor_message(topic: str, size: int) -> str:
    """The below-floor warning text (names the shortfall and that it is not a block)."""
    return (
        f"The golden set frozen for topic '{topic}' has {size} record(s), below the "
        f"recommended floor of {EVAL_MIN_GOLDEN}. The eval scalar will be noisier until "
        "more reviewed pairs are frozen; this is a warning, not a hard block."
    )

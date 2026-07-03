"""``curate_example`` -- the flywheel's write path: append one curated example.

Appends one ``(query, pages_used, answer, verdict)`` example to a topic's
``.knotica/datasets/qa.jsonl`` (the DSPy training substrate) inside a single
:class:`~knotica.core.transaction.VaultTransaction`. Idempotent by content: an
example whose ``(query, answer, verdict)`` already appears in the file is a
no-op (``appended=False``, no commit). A missing topic fails fast with
``TOPIC_NOT_FOUND``. The appended line is a frozen ``qa.jsonl`` record
(:class:`~knotica.core.records.QARecord`); an optional free-text ``notes`` is
carried as a tolerated extra field so no caller input is lost.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import PurePath

from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.records import QARecord, parse_qa_jsonl
from knotica.core.schema import validated_topic
from knotica.core.transaction import VaultTransaction
from knotica.store import VaultStore

#: ``source`` field stamped on examples that came through this manual tool
#: (distillation-produced examples use a different source).
_CURATE_SOURCE = "curate_example"

#: ``model`` placeholder: the deterministic server cannot know the client's
#: model at MVP (the tool schema does not expose it); recorded as unknown.
_UNKNOWN_MODEL = "unknown"

#: Max length of the query-derived commit/log title before truncation.
_TITLE_MAX_LEN = 72


def curate_example(
    store: VaultStore,
    vault_root: str | PurePath,
    topic: str,
    query: str,
    pages_used: tuple[str, ...],
    answer: str,
    verdict: str,
    notes: str | None = None,
) -> dict[str, object]:
    """Append one curated example to the topic's dataset, or report a duplicate.

    Args:
        store: The vault storage backend.
        vault_root: The already-resolved vault root (operations are config-agnostic).
        topic: Owning topic; must already exist.
        query: The user question the example captures.
        pages_used: Topic-relative page paths that grounded the answer.
        answer: The answer given.
        verdict: ``good`` or ``bad`` -- the user's judgment of answer quality.
        notes: Optional free-text note preserved on the record.

    Returns:
        A success envelope with pointer ``{path, example_count, appended}``
        (plus any secret-scrub warnings), or a typed failure envelope.
    """
    try:
        cleaned = validated_topic(topic)
    except ValueError as error:
        return err(ErrorCode.TOPIC_NOT_FOUND, f"curate_example failed because {error}")
    if not store.exists(cleaned):
        return err(
            ErrorCode.TOPIC_NOT_FOUND,
            f"curate_example failed because no topic named '{cleaned}' exists.",
        )

    dataset_path = qa_dataset_path(cleaned)
    existing_text = store.read_text(dataset_path) if store.exists(dataset_path) else ""
    existing = parse_qa_jsonl(existing_text)
    fingerprint = _fingerprint(query, answer, verdict)
    if any(_fingerprint(rec.query, rec.answer, rec.verdict) == fingerprint for rec in existing):
        return ok({"path": dataset_path, "example_count": len(existing), "appended": False})

    try:
        record = _build_record(cleaned, query, pages_used, answer, verdict)
        new_text = _appended_line(existing_text, _serialize(record, notes))
        with VaultTransaction(store, vault_root, "curate_example", cleaned, _title(query)) as txn:
            txn.write(dataset_path, new_text)
    except KnoticaError as error:
        return error.envelope()
    result = txn.result
    pointer = {"path": dataset_path, "example_count": len(existing) + 1, "appended": True}
    return ok(pointer, warnings=result.warnings())


def _fingerprint(query: str, answer: str, verdict: str) -> str:
    """Content hash keying idempotency on ``(query, answer, verdict)``."""
    payload = "\x00".join((query, answer, verdict)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _build_record(
    topic: str,
    query: str,
    pages_used: tuple[str, ...],
    answer: str,
    verdict: str,
) -> QARecord:
    """Construct a frozen ``qa.jsonl`` record from the tool inputs and stamps.

    ``id`` is derived from the idempotency fingerprint so an identical example
    always carries an identical id; ``created`` is stamped at append time.
    """
    return QARecord(
        id=f"qa-{_fingerprint(query, answer, verdict)[:16]}",
        topic=topic,
        created=datetime.now(UTC).isoformat(),
        query=query,
        pages_used=tuple(pages_used),
        answer=answer,
        citations=(),
        verdict=verdict,
        corrected_answer=None,
        source=_CURATE_SOURCE,
        model=_UNKNOWN_MODEL,
    )


def _serialize(record: QARecord, notes: str | None) -> str:
    """Serialize the record to one JSON line, carrying ``notes`` as a tolerated extra field."""
    line = record.to_json_line()
    if not notes or not notes.strip():
        return line
    payload = json.loads(line)
    payload["notes"] = notes
    return json.dumps(payload, ensure_ascii=False)


def _appended_line(existing_text: str, line: str) -> str:
    """Append one JSONL line, preserving prior records and a single trailing newline."""
    if not existing_text.strip():
        return line + "\n"
    return existing_text.rstrip("\n") + "\n" + line + "\n"


def _title(query: str) -> str:
    """One-line commit/log title derived from the query (whitespace-collapsed, truncated)."""
    collapsed = " ".join(query.split())
    if not collapsed:
        return "curated example"
    if len(collapsed) <= _TITLE_MAX_LEN:
        return collapsed
    return collapsed[: _TITLE_MAX_LEN - 1].rstrip() + "…"

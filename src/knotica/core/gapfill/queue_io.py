"""The two committed gap-fill queues as *files* -- read, index, replace, serialize.

The leaf every other module in this package sits on, and the only one that knows
where the queues live: ``<topic>/.knotica/gaps/gaps.jsonl`` (owned for writing by
``core.gap_classifier``, read here) and
``<topic>/.knotica/suggestions/suggestions.jsonl``. It holds no lifecycle rule and
opens no transaction -- what a record may become is the business of
:mod:`~knotica.core.gapfill.review` and :mod:`~knotica.core.gapfill.gap_review`;
this module only knows how to get a record out of a file and a file back out of a
list of records.

Three things beyond plain IO live here because more than one writer needs them and
a second declaration is how two writers drift apart: the candidate **identity**
keys (:func:`_source_key`, :func:`_candidate_url_key`, :func:`_suggestion_id`),
the **published-branch protection** both queue writers consult
(:func:`_published_source_id8s` / :func:`_is_protected`), and the three commit **op
slots**. The identity helpers reach ``discovery.normalize`` -- the single
declaration of when two candidates are the same source -- lazily, inside the
function that needs it, so this module stays off the MCP cold-start import path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from knotica.core.branch_namespaces import (
    CANDIDATE_BRANCH_PREFIX,
    WIP_BRANCH_PREFIX,
    _ID_INFIX_LENGTH,
    _SOURCE_INFIX,
)
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.gap_classifier import FaultClass, gaps_path
from knotica.core.records import (
    GapRecord,
    SuggestionRecord,
    parse_gaps_jsonl,
    parse_suggestions_jsonl,
)
from knotica.store import VaultStore

#: Directory name under each topic that owns loop/eval artifacts.
_KNOTICA_DIR = ".knotica"
#: Subdir + basename of the per-topic human-approval suggestion queue.
_SUGGESTIONS_DIRNAME = "suggestions"
_SUGGESTIONS_FILENAME = "suggestions.jsonl"
#: Op slot of the drain's suggestion-propose commit (own transaction, one commit).
_PROPOSE_OP = "suggestion_propose"
#: Op slot of the approve/reject/defer/mark-ingested commit. Also the slot of the
#: machine gate stamp, whose merged verdict closes the originating gap in the
#: same commit -- one operation, one commit, however many files it touches.
_REVIEW_OP = "suggestion_review"
#: Op slot of the human dismiss/reopen gap commit (own transaction, one commit).
_GAP_REVIEW_OP = "gap_review"


def suggestions_path(topic: str) -> str:
    """Vault-relative path of a topic's ``suggestions.jsonl`` (mirrors ``gaps_path``)."""
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned or cleaned in {".", ".."}:
        raise ValueError(f"topic must be a single path segment, got {topic!r}")
    return f"{cleaned}/{_KNOTICA_DIR}/{_SUGGESTIONS_DIRNAME}/{_SUGGESTIONS_FILENAME}"


# ---------------------------------------------------------------------------
# Gap queue -- reading, selection by identity, whole-file bodies
# ---------------------------------------------------------------------------


def _read_gaps(store: VaultStore, topic: str) -> list[GapRecord]:
    """Parse a topic's whole gap queue (empty when the file is absent/blank)."""
    path = gaps_path(topic)
    if not store.exists(path):
        return []
    text = store.read_text(path)
    return parse_gaps_jsonl(text) if text.strip() else []


def _open_genuine_gaps(store: VaultStore, topic: str) -> list[GapRecord]:
    """The open ``genuine_gap`` records eligible for a drain (dilution excluded)."""
    return [
        gap
        for gap in _read_gaps(store, topic)
        if gap.fault_class == FaultClass.GENUINE_GAP and gap.status == "open"
    ]


def _gap_index_of(
    gaps: Sequence[GapRecord],
    gap_id: str,
    *,
    status: str | None = None,
) -> int | None:
    """Position of the record under ``gap_id``, optionally required to be ``status``."""
    return next(
        (
            index
            for index, gap in enumerate(gaps)
            if gap.gap_id == gap_id and (status is None or gap.status == status)
        ),
        None,
    )


def _gaps_body_with(gaps: Sequence[GapRecord], index: int, updated: GapRecord) -> str:
    """The whole ``gaps.jsonl`` body with the record at ``index`` replaced."""
    replaced = list(gaps)
    replaced[index] = updated
    return "\n".join(gap.to_json_line() for gap in replaced) + "\n"


# ---------------------------------------------------------------------------
# Suggestion queue -- reading, record lookup, serialization
# ---------------------------------------------------------------------------


def _read_suggestions(store: VaultStore, topic: str) -> list[SuggestionRecord]:
    """Parse a topic's staged suggestions (empty when the file is absent/blank)."""
    path = suggestions_path(topic)
    if not store.exists(path):
        return []
    text = store.read_text(path)
    return parse_suggestions_jsonl(text) if text.strip() else []


def _index_of(records: Sequence[SuggestionRecord], suggestion_id: str) -> int | None:
    return next(
        (index for index, record in enumerate(records) if record.suggestion_id == suggestion_id),
        None,
    )


def _replace_at(
    records: Sequence[SuggestionRecord],
    index: int,
    updated: SuggestionRecord,
) -> list[SuggestionRecord]:
    new_records = list(records)
    new_records[index] = updated
    return new_records


def _serialize(records: Sequence[SuggestionRecord]) -> str:
    return "\n".join(record.to_json_line() for record in records) + "\n"


def _candidate_title(candidate: Mapping[str, object]) -> str:
    title = candidate.get("title")
    return title if isinstance(title, str) else ""


# ---------------------------------------------------------------------------
# Candidate identity (lazy ``discovery.normalize`` edge)
# ---------------------------------------------------------------------------


def _source_key(candidate: Mapping[str, object]) -> str:
    """The dedup + identity key of one candidate: normalized DOI, else URL.

    Delegates to ``discovery.normalize``, the single declaration of the rule, so
    the queue's dedup cannot drift from the service's. The candidate is an opaque
    dict here -- what keeps ``core/records.py`` free of an edge into
    ``discovery/`` -- so its two fields are coerced at this boundary.
    """
    from knotica.discovery.normalize import source_key

    doi, url = candidate.get("doi"), candidate.get("url")
    return source_key(doi if isinstance(doi, str) else None, url if isinstance(url, str) else "")


def _candidate_url_key(candidate: Mapping[str, object]) -> str:
    """The candidate's URL identity alone -- the vault-dedup handshake key.

    Deliberately *not* ``_source_key``: stored provenance records no DOI, so
    the vault comparison is URL-to-URL, and DOI-first keying would let a
    DOI-carrying candidate slip past a URL-recorded ingest of the same source.
    """
    from knotica.discovery.normalize import normalize_url

    url = candidate.get("url")
    return normalize_url(url if isinstance(url, str) else "")


def _suggestion_id(topic: str, gap_id: str, source_key: str) -> str:
    """Stable 16-hex identity + dedup key over ``(topic, gap_id, source_key)``."""
    import hashlib

    return hashlib.sha1(f"{topic}|{gap_id}|{source_key}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Published-branch protection -- shared by both queue writers
# ---------------------------------------------------------------------------


def _published_source_id8s(root: str | Path, topic: str) -> frozenset[str]:
    """The ``id8`` infixes of ``topic``'s live source-candidate branches.

    A suggestion whose ingest has published ``loop/c/<topic>/source-<id8>`` (or
    still holds its private ``loop/wip/`` session branch) has work in flight
    that the gate will merge and only then stamp. Both queue writers -- the
    dismiss cascade and the heal -- use this to leave such a record alone; the
    gate is what dispositions it. Skipping is deliberate: the alternative,
    refusing the whole dismissal, would punish an operator for an ingest they
    may not know exists.
    """
    from knotica.core.vcs import VaultVcs

    vcs = VaultVcs(root)
    leaf_prefix = f"{topic}/{_SOURCE_INFIX}"
    return frozenset(
        name.removeprefix(prefix).removeprefix(leaf_prefix)
        for prefix in (CANDIDATE_BRANCH_PREFIX, WIP_BRANCH_PREFIX)
        for name, _sha in vcs.list_branch_tips(f"{prefix}{leaf_prefix}")
    )


def _is_protected(record: SuggestionRecord, protected: frozenset[str]) -> bool:
    """Whether ``record`` is an approved suggestion with a live candidate branch."""
    return record.status == "approved" and record.suggestion_id[:_ID_INFIX_LENGTH] in protected


# ---------------------------------------------------------------------------
# Shared refusal wording + clock
# ---------------------------------------------------------------------------


def _invalid(message: str, fix: str) -> KnoticaError:
    """A typed argument-validation error (the house ``INVALID_ARGUMENT`` code)."""
    return KnoticaError(ErrorCode.INVALID_ARGUMENT, message, fix=fix)


def _legal_exits_hint(
    status: str,
    table: Mapping[str, frozenset[str]],
    *,
    noun: str = "suggestion",
) -> str:
    """The decisions legal from ``status``, for a refused transition's fix text.

    A refusal that names only the attempted decision's legal sources leaves the
    caller stuck when the record's *actual* status has a different exit --
    ``approved`` has ``withdraw``, but a refused ``reject`` never said so. Kept
    generic over the ``decision -> legal source statuses`` shape so both
    lifecycle tables (``review._ALLOWED_FROM`` for suggestions,
    ``gap_review._GAP_ALLOWED_FROM`` for gaps) derive their hint from the
    machine rather than a hand-written string that can drift from it. It lives
    on the shared leaf, and takes its table explicitly, precisely because
    neither lifecycle module owns it more than the other.
    """
    exits = sorted(decision for decision, sources in table.items() if status in sources)
    if not exits:
        return f"A {status!r} {noun} accepts no further decision."
    return f"From {status!r} the legal decisions are: {', '.join(exits)}."


def _utc_now_iso() -> str:
    """Wall-clock stamp in ISO-8601 UTC (``…Z`` suffix), matching the gap classifier."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

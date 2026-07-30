"""``capture_note`` -- the one-shot note write path.

Writes one note file under ``notes/<topic>/`` inside a single
:class:`~knotica.core.transaction.VaultTransaction`, following
:mod:`~knotica.core.operations.curate_example`'s template: validate everything
first, build the whole file in memory, then take the lock exactly once.

**Anchoring never fails the call.** A quote that cannot be found, a page that
does not exist, several pages that all match, or no quote at all -- each of
these lowers the recorded fidelity and rides back as an ``ANCHOR_DEGRADED``
warning on a *success* envelope. The user's reflection is durable before any
anchor quality is discussed. The only hard failures are an unknown topic,
empty note text, and an unrecognized ``intent`` -- none of which touch
anchoring.

``pages`` is plural and client-supplied best-first: the caller is a model that
has just synthesized a passage and often cannot say which single page it came
from, so its honest claim is a ranked list. When the quote matches more than
one of those pages the anchor holds at topic level rather than pinning the
first -- a guessed pin is the silent-wrong-anchor failure the whole design
exists to avoid. Matching is a plain substring search against the working-tree
page text, not the read-time
:func:`~knotica.core.notes.resolve.resolve_anchor` ladder: at capture the
pinned commit *is* HEAD, so an exact match is the only reachable outcome.

Every capture writes exactly one anchor bullet, even when it degrades all the
way to topic fidelity -- a capture is always an anchoring attempt tied to a
specific vault state. (A note with genuinely zero anchors stays valid, but only
a hand-authored one ever has that shape.)
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath

from knotica.core.errors import DEFAULT_FIX, ErrorCode, KnoticaError, KnoticaWarning, err, ok
from knotica.core.links import iter_page_paths
from knotica.core.notes.anchor import (
    DEFAULT_INTENT,
    DEFAULT_STATUS,
    NOTE_INTENTS,
    AnchorRecord,
    NoteDocument,
    derive_note_id,
    parse_note,
    serialize_note,
)
from knotica.core.schema import validated_topic
from knotica.core.transaction import VaultTransaction
from knotica.core.vcs import VaultVcs
from knotica.store import VaultStore

#: Operation name stamped on the commit subject and the operation log.
_CAPTURE_OP = "note_capture"

_NOTES_DIRECTORY_TEMPLATE = "notes/{topic}"
_MARKDOWN_SUFFIX = ".md"

#: Fidelity of the anchor of record, weakest last.
_SPAN_FIDELITY = "span"
_PAGE_FIDELITY = "page"
_TOPIC_FIDELITY = "topic"

#: Frontmatter timestamp shape (the grammar's ``2026-07-29T14:22:11Z``).
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: Max length of the note-derived commit/log title before truncation.
_TITLE_MAX_LEN = 72


@dataclass(frozen=True)
class _AnchorPlan:
    """What the capture could honestly say about where the note points.

    ``degradation`` is the human-readable reason the anchor is weaker than a
    pinned span, or ``None`` when nothing was lost.
    """

    page: str
    fidelity: str
    start: int | None
    degradation: str | None


@dataclass(frozen=True)
class _Request:
    """A capture request that passed every non-anchor validation."""

    topic: str
    body: str
    intent: str


def capture_note(
    store: VaultStore,
    vault_root: str | PurePath,
    vcs: VaultVcs,
    topic: str,
    note: str,
    *,
    quote: str = "",
    pages: Sequence[str] = (),
    intent: str = DEFAULT_INTENT,
) -> dict[str, object]:
    """Write one note under ``notes/<topic>/``, degrading the anchor as needed.

    Args:
        store: The vault storage backend.
        vault_root: The already-resolved vault root (operations are config-agnostic).
        vcs: Vault git access, read here only for the pre-capture HEAD sha.
        topic: Owning topic; must already exist.
        note: The user's free text; must not be blank.
        quote: The passage the note reacts to, verbatim. May be empty.
        pages: Vault-relative page paths the caller believes the quote came
            from, best-first. May be empty.
        intent: One of :data:`~knotica.core.notes.anchor.NOTE_INTENTS`.

    Returns:
        A success envelope ``{note_id, path, fidelity, duplicate}`` -- carrying
        an ``ANCHOR_DEGRADED`` warning when the anchor could not be pinned as
        claimed -- or a typed failure envelope for the three non-anchor errors.
    """
    request = _validate(store, topic, note, intent)
    if not isinstance(request, _Request):
        return request

    plan = _plan_anchor(store, quote, tuple(pages))
    directory = _NOTES_DIRECTORY_TEMPLATE.format(topic=request.topic)
    note_paths = _note_paths(store, directory)
    fingerprint = _fingerprint(request.topic, request.body, plan.page, quote)
    duplicate = _find_duplicate(store, note_paths, request.topic, fingerprint)
    if duplicate is not None:
        return ok({**duplicate, "fidelity": plan.fidelity, "duplicate": True})

    created = datetime.now(UTC).strftime(_TIMESTAMP_FORMAT)
    taken = {PurePath(path).stem for path in note_paths}
    note_id = derive_note_id(request.body, created, existing=taken.__contains__)
    path = f"{directory}/{note_id}{_MARKDOWN_SUFFIX}"
    # The anchor pins the vault as the user saw it -- this capture's own commit
    # does not exist yet and must never be what the anchor describes.
    content = _render(
        request, plan, note_id=note_id, created=created, pinned_at=vcs.head_sha(), quote=quote
    )

    try:
        with VaultTransaction(
            store, vault_root, _CAPTURE_OP, request.topic, _title(request.body)
        ) as txn:
            txn.write(path, content)
    except KnoticaError as error:
        return error.envelope()
    warnings = list(txn.result.warnings())
    if plan.degradation is not None:
        warnings.append(_degraded_warning(plan.degradation))
    pointer = {
        "note_id": note_id,
        "path": path,
        "fidelity": plan.fidelity,
        "duplicate": False,
    }
    return ok(pointer, warnings=warnings)


def _validate(
    store: VaultStore, topic: str, note: str, intent: str
) -> _Request | dict[str, object]:
    """The three hard failures, all of them checked before any anchoring."""
    try:
        cleaned = validated_topic(topic)
    except ValueError as error:
        return err(ErrorCode.TOPIC_NOT_FOUND, f"capture_note failed because {error}")
    if not store.exists(cleaned):
        return err(
            ErrorCode.TOPIC_NOT_FOUND,
            f"capture_note failed because no topic named '{cleaned}' exists.",
        )
    body = note.strip()
    if not body:
        return err(
            ErrorCode.INVALID_ARGUMENT,
            "capture_note failed because the note text is empty.",
        )
    if intent not in NOTE_INTENTS:
        return err(
            ErrorCode.INVALID_ARGUMENT,
            f"capture_note failed because intent {intent!r} is not one of "
            f"{', '.join(sorted(NOTE_INTENTS))}.",
        )
    return _Request(topic=cleaned, body=body, intent=intent)


def _render(
    request: _Request,
    plan: _AnchorPlan,
    *,
    note_id: str,
    created: str,
    pinned_at: str,
    quote: str,
) -> str:
    """Render the whole note file in memory, before any lock is taken."""
    anchor = AnchorRecord(
        page=plan.page,
        heading="",
        fidelity=plan.fidelity,
        pinned_at=pinned_at,
        quote=quote,
        start=plan.start,
    )
    return serialize_note(
        NoteDocument(
            id=note_id,
            topic=request.topic,
            intent=request.intent,
            created=created,
            updated=created,
            status=DEFAULT_STATUS,
            tags=(),
            body=request.body,
            anchors=(anchor,),
        )
    )


# ---------------------------------------------------------------------------
# Anchoring
# ---------------------------------------------------------------------------


def _plan_anchor(store: VaultStore, quote: str, pages: tuple[str, ...]) -> _AnchorPlan:
    """Decide what the capture can honestly pin, given what the caller claimed."""
    if not pages:
        return _AnchorPlan(page="", fidelity=_TOPIC_FIDELITY, start=None, degradation=None)
    existing = [page for page in pages if store.exists(page)]
    if not existing:
        return _AnchorPlan(
            page="",
            fidelity=_TOPIC_FIDELITY,
            start=None,
            degradation=(
                "None of the claimed pages exist in this vault "
                f"({', '.join(pages)}), so the note is anchored to the topic only."
            ),
        )
    if not quote:
        # Naming a page the server verified is real is a true statement even
        # with no passage to quote; degrading it would discard caller input.
        return _AnchorPlan(page=existing[0], fidelity=_PAGE_FIDELITY, start=None, degradation=None)

    matched = [page for page in existing if quote in store.read_text(page)]
    if len(matched) == 1:
        return _AnchorPlan(
            page=matched[0],
            fidelity=_SPAN_FIDELITY,
            start=_disambiguator(store.read_text(matched[0]), quote),
            degradation=None,
        )
    if matched:
        return _AnchorPlan(
            page="",
            fidelity=_TOPIC_FIDELITY,
            start=None,
            degradation=(
                f"The quote appears on {len(matched)} of the claimed pages "
                f"({', '.join(matched)}); pinning one of them would be a guess, so the note "
                "is anchored to the topic only."
            ),
        )
    return _AnchorPlan(
        page=existing[0],
        fidelity=_PAGE_FIDELITY,
        start=None,
        degradation=(
            f"The quote was not found on any claimed page, so the note is anchored to "
            f"'{existing[0]}' at page level rather than to a span within it."
        ),
    )


def _disambiguator(text: str, quote: str) -> int | None:
    """Offset of the first occurrence, recorded only when the quote repeats.

    At capture time "first occurrence" and "nearest occurrence" are the same
    rule -- there is no historical offset to be ambiguous relative to -- so a
    within-page repeat is not a degradation, just something read-time
    resolution will want a disambiguator for.
    """
    return text.find(quote) if text.count(quote) > 1 else None


def _degraded_warning(message: str) -> KnoticaWarning:
    return KnoticaWarning(
        code=ErrorCode.ANCHOR_DEGRADED,
        message=message,
        fix=DEFAULT_FIX[ErrorCode.ANCHOR_DEGRADED],
    )


# ---------------------------------------------------------------------------
# Existing notes: idempotency and same-second id collisions
# ---------------------------------------------------------------------------


def _note_paths(store: VaultStore, directory: str) -> list[str]:
    if not store.exists(directory):
        return []
    return list(iter_page_paths(store, directory))


def _fingerprint(topic: str, body: str, page: str, quote: str) -> str:
    """Content hash keying idempotency on what the capture actually recorded.

    ``pinned_at`` is deliberately excluded: the first capture moves HEAD, so an
    immediate re-capture of the same reflection would otherwise never match
    itself.
    """
    payload = "\x00".join((topic, body, page, quote)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _find_duplicate(
    store: VaultStore,
    note_paths: Sequence[str],
    topic: str,
    fingerprint: str,
) -> dict[str, object] | None:
    """Pointer to an already-captured identical note, or ``None``.

    A note file that no longer parses cannot be compared, so it is skipped --
    it is the read side's job to report it, not this one's.
    """
    for path in note_paths:
        document, error = parse_note(store.read_text(path))
        if error is not None or document is None:
            continue
        anchor = document.anchors[0] if document.anchors else None
        recorded = _fingerprint(
            topic,
            document.body,
            anchor.page if anchor else "",
            anchor.quote if anchor else "",
        )
        if recorded == fingerprint:
            return {"note_id": document.id, "path": path}
    return None


def _title(body: str) -> str:
    """One-line commit/log title derived from the note (collapsed, truncated)."""
    collapsed = " ".join(body.split())
    if len(collapsed) <= _TITLE_MAX_LEN:
        return collapsed
    return collapsed[: _TITLE_MAX_LEN - 1].rstrip() + "…"

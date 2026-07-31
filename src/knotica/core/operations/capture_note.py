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
a hand-authored one ever has that shape.) That "exactly one" holds for any note
text at all, because this is the layer that knows the body is untrusted prose:
an ``## Anchors`` heading inside it is escaped
(:func:`~knotica.core.notes.anchor.escape_anchors_heading`) before serialization,
so prose about the note format can never open a second section or be promoted
into an anchor the capture did not resolve.
"""

import hashlib
import re
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
    anchor_of_record,
    derive_note_id,
    escape_anchors_heading,
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

#: A heading line at any level, for locating the section a quote sits in.
_HEADING_LINE_RE = re.compile(r"^#{1,6}[ \t]+.*$", re.MULTILINE)


@dataclass(frozen=True)
class _AnchorPlan:
    """What the capture could honestly say about where the note points.

    ``degradation`` is the human-readable reason the anchor is weaker than a
    pinned span, or ``None`` when nothing was lost. ``alternatives`` is only
    ever non-empty for the multi-page-match ambiguity: one ``{page, heading}``
    mapping per claimed page the quote matched, in claimed order.
    """

    page: str
    fidelity: str
    start: int | None
    degradation: str | None
    alternatives: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class _Request:
    """A capture request that passed every non-anchor validation."""

    topic: str
    body: str
    intent: str
    tags: tuple[str, ...]


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
    tags: Sequence[str] = (),
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
        tags: Free-form labels recorded in the note's frontmatter.

    Returns:
        A success envelope ``{note_id, path, fidelity, duplicate, commit,
        alternatives}`` -- carrying an ``ANCHOR_DEGRADED`` warning when the
        anchor could not be pinned as claimed -- or a typed failure envelope
        for the three non-anchor errors. ``commit`` is the vault ``HEAD`` the
        note is durable at: the capture's own commit, or the head the
        duplicate was matched against. ``alternatives`` is a ``{page,
        heading}`` mapping per claimed page the quote matched, in claimed
        order -- populated only for the genuine multi-page-match ambiguity,
        empty otherwise, and never carried on the duplicate envelope.
    """
    request = _validate(store, topic, note, intent, tags)
    if not isinstance(request, _Request):
        return request

    plan = _plan_anchor(store, quote, tuple(pages))
    directory = _NOTES_DIRECTORY_TEMPLATE.format(topic=request.topic)
    note_paths = _note_paths(store, directory)
    fingerprint = _fingerprint(request.topic, request.body, plan.page, quote)
    duplicate = _find_duplicate(store, note_paths, request.topic, fingerprint)
    if duplicate is not None:
        return ok(
            {
                **duplicate,
                "fidelity": plan.fidelity,
                "duplicate": True,
                "commit": vcs.head_sha(),
            }
        )

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
        "commit": txn.result.commit_sha,
        "alternatives": list(plan.alternatives),
    }
    return ok(pointer, warnings=warnings)


def _validate(
    store: VaultStore, topic: str, note: str, intent: str, tags: Sequence[str]
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
    cleaned_tags = tuple(tag.strip() for tag in tags if tag.strip())
    # `serialize_note` escapes too -- that is the guarantee no writer can
    # bypass. Escaping *here as well* is not redundant: the fingerprint is
    # computed from this body, so it must be the same bytes the file will hold
    # or an immediate re-capture stops matching its own stored copy. The escape
    # is idempotent, so applying it twice is applying it once.
    return _Request(
        topic=cleaned, body=escape_anchors_heading(body), intent=intent, tags=cleaned_tags
    )


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
            tags=request.tags,
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
        return _AnchorPlan(
            page="", fidelity=_TOPIC_FIDELITY, start=None, degradation=None, alternatives=()
        )
    readable = _readable_pages(store, pages)
    if not readable:
        return _AnchorPlan(
            page="",
            fidelity=_TOPIC_FIDELITY,
            start=None,
            degradation=(
                "None of the claimed pages could be read as a page in this vault "
                f"({', '.join(repr(page) for page in pages)}) -- each one is missing, is not "
                "a file, or lies outside the vault -- so the note is anchored to the topic only."
            ),
            alternatives=(),
        )
    if not quote:
        # Naming a page the server read is a true statement even with no
        # passage to quote; degrading it would discard caller input.
        return _AnchorPlan(
            page=readable[0][0],
            fidelity=_PAGE_FIDELITY,
            start=None,
            degradation=None,
            alternatives=(),
        )

    matched = [(page, text) for page, text in readable if quote in text]
    if len(matched) == 1:
        page, text = matched[0]
        return _AnchorPlan(
            page=page,
            fidelity=_SPAN_FIDELITY,
            start=_disambiguator(text, quote),
            degradation=None,
            alternatives=(),
        )
    if matched:
        return _AnchorPlan(
            page="",
            fidelity=_TOPIC_FIDELITY,
            start=None,
            degradation=(
                f"The quote appears on {len(matched)} of the claimed pages "
                f"({', '.join(page for page, _ in matched)}); pinning one of them would be a "
                "guess, so the note is anchored to the topic only."
            ),
            alternatives=tuple(
                {"page": page, "heading": _enclosing_heading(text, quote)} for page, text in matched
            ),
        )
    return _AnchorPlan(
        page=readable[0][0],
        fidelity=_PAGE_FIDELITY,
        start=None,
        degradation=(
            f"The quote was not found on any claimed page, so the note is anchored to "
            f"'{readable[0][0]}' at page level rather than to a span within it."
        ),
        alternatives=(),
    )


def _enclosing_heading(text: str, quote: str) -> str:
    """The nearest heading line at or above the quote's first occurrence in ``text``.

    Empty string when the quote sits above every heading, ``text`` has none, or
    the quote is somehow absent -- matching every other heading field in this
    module's wire shapes, never ``None``. Derived from the same ``text`` the
    match was already found in, so this costs no extra page read.
    """
    offset = text.find(quote)
    if offset == -1:
        return ""
    enclosing = ""
    for match in _HEADING_LINE_RE.finditer(text):
        if match.start() > offset:
            break
        enclosing = match.group(0).lstrip("#").strip()
    return enclosing


def _readable_pages(store: VaultStore, pages: tuple[str, ...]) -> list[tuple[str, str]]:
    """The claimed pages that resolve to a readable file, paired with their text.

    ``pages`` is a model's provenance guess arriving straight off the wire, so a
    directory name, an empty string, or a path escaping the vault is ordinary
    input rather than an attack. A path that cannot be read is simply not a
    candidate: it is dropped here, and the storage layer's exception type never
    reaches the caller, because a bad path must cost the *pin*, never the note.
    """
    readable: list[tuple[str, str]] = []
    for page in pages:
        try:
            if store.exists(page):
                readable.append((page, store.read_text(page)))
        except (OSError, ValueError):
            # OSError covers a directory or an unreadable file; ValueError
            # covers PathOutsideVaultError and its siblings.
            continue
    return readable


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

    Identity is the note's body plus its *anchor of record*, read through
    :func:`~knotica.core.notes.anchor.anchor_of_record` -- which is where the
    constraint that binds this function to every future note writer is stated:
    anchors may only be appended, because a note whose first anchor changed no
    longer matches its own fingerprint.
    """
    for path in note_paths:
        document, error = parse_note(store.read_text(path))
        if error is not None or document is None:
            continue
        anchor = anchor_of_record(document)
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
    """One-line commit/log title derived from the note (collapsed, truncated).

    Wikilink syntax is *not* handled here. ``VaultTransaction`` flattens it for
    every operation on the way to the commit subject and the scored
    ``log.md`` -- see :func:`~knotica.core.transaction.neutralize_wikilinks`.
    Repeating the flattening here would run the same text through the same
    filter twice for no added guarantee.
    """
    collapsed = " ".join(body.split())
    if len(collapsed) <= _TITLE_MAX_LEN:
        return collapsed
    return collapsed[: _TITLE_MAX_LEN - 1].rstrip() + "…"

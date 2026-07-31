"""The notes read side -- enumerate, parse, and resolve. Read-only, always.

``list_notes`` and ``read_note`` are the two entry points a caller has for
seeing what a topic's ``notes/<topic>/`` directory holds, each anchor already
resolved against the vault's git history. Nothing here writes: no
``VaultTransaction``, no ``core.lock`` import, not even transitively -- listing
a topic full of notes must be exactly as cheap and side-effect-free as reading
a single page. Resolution is recomputed on every call rather than cached or
persisted on the note, which is what makes a resolver improvement in
:mod:`~knotica.core.notes.resolve` apply retroactively to every existing note.

A malformed note *file* (unparseable frontmatter, a missing required field) is
excluded from the listing and counted in ``NotesListing.skipped_malformed``. A
malformed *anchor bullet* inside an otherwise-valid note is a much milder case
handled entirely by :func:`~knotica.core.notes.anchor.parse_note` -- the note
stays listed with its readable anchors intact, reported on
``NoteDocument.skipped_anchor_count``.
"""

from dataclasses import dataclass

from knotica.core.links import iter_page_paths
from knotica.core.notes.anchor import AnchorRecord, NoteDocument, parse_note
from knotica.core.notes.resolve import Projection, resolve_anchor
from knotica.core.vcs import VaultVcs
from knotica.store import VaultStore

__all__ = ["NotesListing", "ResolvedNote", "list_notes", "read_note"]

_NOTES_DIRECTORY_TEMPLATE = "notes/{topic}"


@dataclass(frozen=True)
class ResolvedNote:
    """A parsed note together with its resolved anchor projections.

    ``resolved_anchors`` pairs each of ``document``'s anchors with its
    :class:`~knotica.core.notes.resolve.Projection`, in the same order as
    ``document.anchors`` -- deliberately not named ``anchors``, which already
    means a bare tuple of :class:`~knotica.core.notes.anchor.AnchorRecord` on
    ``NoteDocument`` and would collide in shape under the same name.
    """

    document: NoteDocument
    path: str
    resolved_anchors: tuple[tuple[AnchorRecord, Projection], ...]


@dataclass(frozen=True)
class NotesListing:
    """The result of enumerating a topic's ``notes/<topic>/`` directory."""

    notes: tuple[ResolvedNote, ...]
    skipped_malformed: int


def list_notes(
    store: VaultStore,
    vcs: VaultVcs,
    topic: str,
    *,
    guess_threshold: float,
    complete_orphan_threshold: float,
    anchored_page: str | None = None,
) -> NotesListing:
    """Enumerate, parse, and resolve every note under ``notes/<topic>/``.

    ``guess_threshold``/``complete_orphan_threshold`` are the resolution ladder's fuzzy/orphan
    gates -- required, no defaults, so a caller that forgets to resolve ``[notes]`` config
    fails loudly (mypy enumerates every call site) rather than silently resolving against a
    stale default. ``anchored_page``, when given, restricts the listing to notes with at
    least one anchor on that vault-relative page path. Read-only throughout: no commit, no
    lock -- resolution reads the historical blob via ``vcs.read_file_at`` and the current page
    via ``store.read_text``.
    """
    notes: list[ResolvedNote] = []
    skipped_malformed = 0
    for path in _iter_note_paths(store, topic):
        document, error = parse_note(store.read_text(path))
        if error is not None or document is None:
            skipped_malformed += 1
            continue
        resolved = ResolvedNote(
            document=document,
            path=path,
            resolved_anchors=tuple(
                _resolve_anchors(
                    store,
                    vcs,
                    document.anchors,
                    guess_threshold=guess_threshold,
                    complete_orphan_threshold=complete_orphan_threshold,
                )
            ),
        )
        if anchored_page is not None and not _anchors_page(resolved, anchored_page):
            continue
        notes.append(resolved)
    return NotesListing(notes=tuple(notes), skipped_malformed=skipped_malformed)


def read_note(
    store: VaultStore,
    vcs: VaultVcs,
    topic: str,
    note_id: str,
    *,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> ResolvedNote | None:
    """Return the single note ``note_id`` under ``notes/<topic>/``, or ``None``.

    ``None`` covers both a missing id and a malformed note file -- callers map
    either to a not-found outcome.
    """
    listing = list_notes(
        store,
        vcs,
        topic,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    for resolved in listing.notes:
        if resolved.document.id == note_id:
            return resolved
    return None


def _iter_note_paths(store: VaultStore, topic: str) -> list[str]:
    directory = _NOTES_DIRECTORY_TEMPLATE.format(topic=topic)
    if not store.exists(directory):
        return []
    return list(iter_page_paths(store, directory))


def _resolve_anchors(
    store: VaultStore,
    vcs: VaultVcs,
    anchors: tuple[AnchorRecord, ...],
    *,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> list[tuple[AnchorRecord, Projection]]:
    resolved: list[tuple[AnchorRecord, Projection]] = []
    for anchor in anchors:
        historical_text = vcs.read_file_at(anchor.pinned_at, anchor.page) or ""
        projection = resolve_anchor(
            historical_text,
            _head_text(store, anchor),
            anchor,
            guess_threshold=guess_threshold,
            complete_orphan_threshold=complete_orphan_threshold,
        )
        resolved.append((anchor, projection))
    return resolved


def _head_text(store: VaultStore, anchor: AnchorRecord) -> str | None:
    """The anchored page as it stands now, or ``None`` when there is no page.

    A topic-fidelity anchor records the empty path -- ``store.exists("")`` is
    true (the vault root is a real directory) and reading it raises, so the
    emptiness must be checked before the existence.
    """
    if not anchor.page or not store.exists(anchor.page):
        return None
    return store.read_text(anchor.page)


def _anchors_page(resolved: ResolvedNote, page: str) -> bool:
    return any(anchor.page == page for anchor, _ in resolved.resolved_anchors)

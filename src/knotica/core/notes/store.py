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

Both entry points build on the private ``_load_note`` primitive -- one file
read, parsed, and resolved -- rather than either being implemented in terms of
the other's aggregate. That is what keeps ``read_note``'s cost bounded by the
one note it returns, regardless of how many other notes share the topic.
"""

from dataclasses import dataclass

from knotica.core.links import iter_page_paths
from knotica.core.notes.anchor import AnchorRecord, NoteDocument, parse_note
from knotica.core.notes.resolve import Projection, resolve_anchor
from knotica.core.vcs import VaultVcs
from knotica.store import VaultStore

__all__ = ["NotesListing", "ResolvedNote", "list_notes", "read_note"]

_NOTES_DIRECTORY_TEMPLATE = "notes/{topic}"
_MARKDOWN_SUFFIX = ".md"


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


class _PassCache:
    """Memoizes page reads for the duration of **one** resolution pass.

    Anchors cluster on pages: several notes in a topic routinely quote the same
    page, and a single note often carries several anchors into one. Each of
    those anchors was re-reading byte-identical content -- the historical blob
    through a ``git show`` subprocess, the live page through the filesystem.

    Scoped to a single ``list_notes``/``read_note`` call and discarded with it,
    deliberately: a longer-lived cache would have to be invalidated on every
    vault mutation, which is the staleness obligation the derived-projection
    design exists to avoid. Within one pass the vault cannot change under it --
    nothing here writes, and the historical blobs are addressed by immutable
    commit sha.
    """

    def __init__(self) -> None:
        self._historical: dict[tuple[str, str], str] = {}
        self._head: dict[str, str | None] = {}

    def historical(self, vcs: VaultVcs, anchor: AnchorRecord) -> str:
        """The anchored page as it stood at the anchor's ``pinned_at`` commit."""
        key = (anchor.pinned_at, anchor.page)
        if key not in self._historical:
            self._historical[key] = vcs.read_file_at(anchor.pinned_at, anchor.page) or ""
        return self._historical[key]

    def head(self, store: VaultStore, anchor: AnchorRecord) -> str | None:
        """The anchored page as it stands now, or ``None`` when there is no page.

        A topic-fidelity anchor records the empty path -- ``store.exists("")``
        is true (the vault root is a real directory) and reading it raises, so
        the emptiness must be checked before the existence.
        """
        if anchor.page not in self._head:
            if not anchor.page or not store.exists(anchor.page):
                self._head[anchor.page] = None
            else:
                self._head[anchor.page] = store.read_text(anchor.page)
        return self._head[anchor.page]


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
    cache = _PassCache()
    for path in _iter_note_paths(store, topic):
        resolved = _load_note(
            store,
            vcs,
            path,
            cache,
            guess_threshold=guess_threshold,
            complete_orphan_threshold=complete_orphan_threshold,
        )
        if resolved is None:
            skipped_malformed += 1
            continue
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

    Cost is bounded by the one note returned, never by how many other notes
    the topic holds: the path is derived directly from ``note_id`` rather than
    enumerating and resolving every note in the topic (what ``list_notes``
    does) just to pick one out of it. This is safe only because of a frozen
    contract on the write side -- a note's filename stem *is* its frontmatter
    ``id`` (see :func:`~knotica.core.notes.anchor.derive_note_id`), and a note
    file is never renamed after capture -- so the stem alone is a sufficient
    address. The file found at that address is trusted as-is: its
    ``document.id`` is never cross-checked against ``note_id``. A strict
    verify would make a hand-renamed note unreachable by *both* its old id
    (the file is gone) and its new stem (the frontmatter still disagrees),
    which is strictly worse than trusting the path.

    ``note_id`` arrives unvalidated from the MCP boundary, so it is checked
    against the shape a real stem can take -- no path separators, no leading
    dot, not empty, no embedded null bytes -- before it is used to build a
    path. A hostile or malformed id returns ``None`` with no file ever read;
    there is no fallback scan on a miss. ``None`` also covers a missing id and
    a malformed note file at the derived path -- callers map every case to a
    not-found outcome alike.
    """
    if not _is_safe_note_id(note_id):
        return None
    path = f"{_NOTES_DIRECTORY_TEMPLATE.format(topic=topic)}/{note_id}{_MARKDOWN_SUFFIX}"
    if not store.exists(path):
        return None
    return _load_note(
        store,
        vcs,
        path,
        _PassCache(),
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )


def _load_note(
    store: VaultStore,
    vcs: VaultVcs,
    path: str,
    cache: _PassCache,
    *,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> ResolvedNote | None:
    """Read, parse, and resolve the single note file at ``path``.

    The shared primitive ``list_notes`` and ``read_note`` are both built on --
    load one note, parse it, resolve its anchors -- so that reading a topic's
    worth of notes and reading exactly one of them cost proportionally to
    what each actually returns. Returns ``None`` for a malformed note file;
    the caller decides what that means (counted and skipped for
    ``list_notes``, a not-found outcome for ``read_note``).
    """
    document, error = parse_note(store.read_text(path))
    if error is not None or document is None:
        return None
    return ResolvedNote(
        document=document,
        path=path,
        resolved_anchors=tuple(
            _resolve_anchors(
                store,
                vcs,
                document.anchors,
                cache,
                guess_threshold=guess_threshold,
                complete_orphan_threshold=complete_orphan_threshold,
            )
        ),
    )


def _is_safe_note_id(note_id: str) -> bool:
    """Whether ``note_id`` is safe to use as a single path component.

    ``note_id`` arrives unvalidated from the MCP boundary and ``read_note``
    uses it to build a path directly (see its docstring for why that is
    safe once the shape is confirmed). Reject anything that is not a bare
    filename component -- empty, a leading dot (catches ``.``, ``..``, and
    hidden-file-shaped ids alike), an embedded path separator (also rules out
    an absolute path, which starts with one), or a null byte -- before any
    file is touched.
    """
    if not note_id or note_id.startswith(".") or "\x00" in note_id:
        return False
    return "/" not in note_id and "\\" not in note_id


def _iter_note_paths(store: VaultStore, topic: str) -> list[str]:
    directory = _NOTES_DIRECTORY_TEMPLATE.format(topic=topic)
    if not store.exists(directory):
        return []
    return list(iter_page_paths(store, directory))


def _resolve_anchors(
    store: VaultStore,
    vcs: VaultVcs,
    anchors: tuple[AnchorRecord, ...],
    cache: _PassCache,
    *,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> list[tuple[AnchorRecord, Projection]]:
    resolved: list[tuple[AnchorRecord, Projection]] = []
    for anchor in anchors:
        projection = resolve_anchor(
            cache.historical(vcs, anchor),
            cache.head(store, anchor),
            anchor,
            guess_threshold=guess_threshold,
            complete_orphan_threshold=complete_orphan_threshold,
        )
        resolved.append((anchor, projection))
    return resolved


def _anchors_page(resolved: ResolvedNote, page: str) -> bool:
    return any(anchor.page == page for anchor, _ in resolved.resolved_anchors)

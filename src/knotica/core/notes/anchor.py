"""Note document model -- frontmatter, the ``## Anchors`` grammar, id derivation.

Pure functions over strings: no store, no git, no locking. A note is an ordinary
markdown file whose frontmatter names it a ``note`` and whose body may carry an
``## Anchors`` section -- one markdown bullet per anchor of record, each followed
by the verbatim quote it pins::

    - [[<vault-path>[#<Heading>]]] — `<fidelity>` · pinned@`<sha>`[ · at=<int>][ · <kind>]
      > <quote>

The wikilink and the quote line are each independently optional; the backticked
fidelity plus the ``pinned@`` token are the bullet's signature, and a bullet
carrying neither is not an anchor at all. A link-less bullet is how a page-less
(``topic``-fidelity) anchor keeps the passage that provoked the note::

    - `topic` · pinned@`a3f9c21`
      > the passage the user was reacting to, preserved verbatim

Four properties are load-bearing and deliberate:

*Reading never raises on content.* A file a human typed by hand in Obsidian is
the fourth capture surface, so the parser is tolerant by construction: irregular
whitespace around the separators, an absent ``#Heading``, an absent ``at=``
disambiguator, and a bare ``## Anchors`` heading with no bullets all parse. A
bullet the grammar cannot read is counted in ``skipped_anchor_count`` and
skipped -- never raised, and never a reason to drop the whole note. Only a
missing or wrong required frontmatter field makes a file un-readable *as a
note*, and that travels back as an error string, mirroring
:func:`~knotica.core.page.parse_page`'s ``(value, error)`` contract.

*Nothing the user typed goes missing.* An ``## Anchors`` heading opens an anchor
region and any other level-1/2 heading closes it; a note may open the region
more than once, and only anchor bullets and their quote lines are consumed from
inside it. Everything else flows back into the body in document order. See
:class:`_BodyScanner` for the full rule.

*Unknown values are carried, not rejected.* ``fidelity`` is typed as a plain
string rather than an enum: a note written by a later knotica generation may
name a fidelity this reader has never heard of, and losing it on a rewrite would
silently downgrade the anchor. ``schema_version`` is carried on the document for
the same reason -- a read/append cycle must not re-stamp a newer note as v1.
Phase-1 writers emit only the values in :data:`PHASE_ONE_FIDELITIES`.

*Corrections are appended, never in place.* An anchor is never rewritten once
written: a human-accepted re-anchor or a detach appends a new record and
leaves every earlier one's bytes untouched. ``kind`` -- ``"pinned"`` by
omission, so every note already on disk keeps parsing unchanged -- names what
an appended record represents, and is an opaque string like ``fidelity``,
never a closed enum. The *effective* anchor a reader should currently trust
is therefore a position in the history, not a stored flag on any one record
-- see :func:`effective_anchor`, the deliberately separate counterpart to
:func:`anchor_of_record`, which never moves.
"""

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from knotica.core.page import parse_page, serialize_frontmatter

__all__ = [
    "DEFAULT_INTENT",
    "DEFAULT_SCHEMA_VERSION",
    "DEFAULT_STATUS",
    "NOTE_INTENTS",
    "NOTE_TYPE",
    "PHASE_ONE_FIDELITIES",
    "REQUIRED_NOTE_FIELDS",
    "AnchorRecord",
    "NoteDocument",
    "anchor_of_record",
    "derive_note_id",
    "effective_anchor",
    "escape_anchors_heading",
    "parse_note",
    "serialize_note",
]

#: The invariant ``type`` value every note file carries.
NOTE_TYPE = "note"

#: Frontmatter fields a note cannot be read without.
REQUIRED_NOTE_FIELDS: tuple[str, ...] = ("type", "id", "topic", "created")

#: Fidelity values Phase-1 code emits; readers accept any string (see module docs).
PHASE_ONE_FIDELITIES: frozenset[str] = frozenset({"span", "page", "topic"})

DEFAULT_SCHEMA_VERSION = 1
DEFAULT_INTENT = "reflection"
DEFAULT_STATUS = "active"

#: The intents a *writer* may stamp. Reading deliberately does not enforce this
#: -- a hand-typed note with an unknown intent must stay readable -- so the
#: write paths (capture, and the tool boundary above it) are its only guard.
NOTE_INTENTS: frozenset[str] = frozenset({"reflection", "dispute", "gap", "question"})

_ANCHORS_HEADING = "## Anchors"
_ANCHORS_HEADING_RE = re.compile(r"^##\s+Anchors\s*$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^#{1,2}\s")
#: The leading hashes of a heading line, as escaped by :func:`escape_anchors_heading`.
_HEADING_HASHES_RE = re.compile(r"#+")
#: A fenced-code-block delimiter: three or more backticks or tildes, indented by
#: at most three spaces (CommonMark). The opener may carry an info string; a
#: closer may not, and must repeat the opener's character at least as many times.
_FENCE_RE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
#: Index of the anchor of record inside :attr:`NoteDocument.anchors`.
_ANCHOR_OF_RECORD_INDEX = 0
_BULLET_PREFIX = "- "
#: An anchor's ``kind`` by omission -- a bullet with no trailing kind token.
#: Every Phase-1 note on disk therefore keeps parsing unchanged.
_PINNED_KIND = "pinned"
#: The terminal ``kind``: once a note's newest anchor is detached, there is no
#: effective anchor left to resolve (see :func:`effective_anchor`).
_DETACHED_KIND = "detached"
#: The bullet's signature is the backticked fidelity plus the ``pinned@`` token;
#: the wikilink is optional (its absence is how a page-less anchor is written).
#: ``at=`` and the trailing ``kind`` token are each independently optional and
#: neither requires the other -- a kind-only bullet (no ``at=``) is valid.
_ANCHOR_LINE_RE = re.compile(
    r"^-\s+(?:\[\[(?P<target>[^\[\]]+)\]\]\s*—\s*)?"
    r"`(?P<fidelity>[^`]+)`"
    r"\s*·\s*pinned@`(?P<sha>[^`]+)`"
    r"(?:\s*·\s*at=(?P<start>\d+))?"
    r"(?:\s*·\s*(?P<kind>[\w-]+))?"
    r"\s*$"
)
_TIMESTAMP_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"[T ](?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
)
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MARKDOWN_SUFFIX = ".md"
_SLUG_WORD_LIMIT = 6
_SLUG_MAX_LENGTH = 40
#: Collision suffixes tried in order when a stem is already taken, same-second.
_COLLISION_SUFFIXES = tuple(chr(code) for code in range(ord("b"), ord("z") + 1))


@dataclass(frozen=True)
class AnchorRecord:
    """One anchor of record: what the reflection pointed at, as it was then.

    ``page`` is the resolved vault-relative path *with* its ``.md`` suffix (the
    wikilink in the file omits it, Obsidian-style; the parser applies it and the
    serializer strips it back off). ``heading`` is the empty string when the
    bullet named no ``#Heading``. ``start`` is the optional offset
    disambiguator, present only when the quote occurs more than once in the
    pinned blob. ``kind`` is ``"pinned"`` by omission -- an anchor that has
    never been superseded serializes with no trailing kind token at all -- and
    is otherwise an opaque string (``"reanchored"``, ``"kept"``, ``"detached"``,
    ...), never a closed enum: a later knotica generation may write a kind this
    reader has never heard of, and it must round-trip regardless.
    """

    page: str
    heading: str
    fidelity: str
    pinned_at: str
    quote: str
    start: int | None = None
    kind: str = _PINNED_KIND


@dataclass(frozen=True)
class NoteDocument:
    """A parsed note file.

    ``body`` is the free text before the ``## Anchors`` section, stripped.
    ``skipped_anchor_count`` reports bullets the grammar could not read -- data,
    not an error: the note itself stays valid and its readable anchors survive.
    """

    id: str
    topic: str
    intent: str
    created: str
    updated: str
    status: str
    tags: tuple[str, ...]
    body: str
    anchors: tuple[AnchorRecord, ...] = ()
    skipped_anchor_count: int = 0
    schema_version: int = DEFAULT_SCHEMA_VERSION


def parse_note(text: str) -> tuple[NoteDocument | None, str | None]:
    """Parse ``text`` into ``(document, error)``; never raises on content.

    ``error`` is non-``None`` only when the file cannot be read *as a note* at
    all: unparseable frontmatter, a missing required field, or a ``type`` other
    than ``note``. Everything the body can throw at the parser -- irregular
    anchor bullets included -- is absorbed and reported on the document.
    """
    fields, frontmatter_error, body_text = parse_page(text)
    if frontmatter_error is not None:
        return None, frontmatter_error

    present: Mapping[str, object] = fields if fields is not None else {}
    missing = [name for name in REQUIRED_NOTE_FIELDS if not _as_text(present.get(name))]
    if missing:
        return None, f"Note frontmatter is missing required field(s): {', '.join(missing)}."

    note_type = _as_text(present["type"])
    if note_type != NOTE_TYPE:
        return None, f"Note frontmatter 'type' must be {NOTE_TYPE!r}, got {note_type!r}."

    body, anchors, skipped = _parse_body(body_text)
    created = _as_text(present["created"])
    return (
        NoteDocument(
            id=_as_text(present["id"]),
            topic=_as_text(present["topic"]),
            intent=_as_text(present.get("intent")) or DEFAULT_INTENT,
            created=created,
            updated=_as_text(present.get("updated")) or created,
            status=_as_text(present.get("status")) or DEFAULT_STATUS,
            tags=_as_tags(present.get("tags")),
            body=body,
            anchors=anchors,
            skipped_anchor_count=skipped,
            schema_version=_as_schema_version(present.get("schema_version")),
        ),
        None,
    )


def serialize_note(document: NoteDocument) -> str:
    """Render ``document`` back to file text -- the inverse of :func:`parse_note`.

    Round-trips documents this module produced. A hand-authored file is only
    required to *parse*; rewriting one normalizes its formatting (separator
    whitespace, wikilink suffix) without changing its meaning. The document's
    own ``schema_version`` is emitted, so appending to a newer note never
    re-stamps it as an older one.

    The body is passed through :func:`escape_anchors_heading` here, at the one
    seam every writer already goes through, rather than in each writer: a
    ``## Anchors`` line in untrusted prose forges anchors the writer never
    resolved, and a guarantee that lives in a caller is one a later caller
    inherits only if its author knows to ask. Round-tripping is unaffected --
    :func:`parse_note` never leaves an *unfenced* sentinel in ``body`` (it
    consumes those as section openers) and the escape skips fenced ones, so
    re-serializing a parsed document is a no-op.
    """
    fields: dict[str, object] = {
        "type": NOTE_TYPE,
        "schema_version": document.schema_version,
        "id": document.id,
        "topic": document.topic,
        "intent": document.intent,
        "created": document.created,
        "updated": document.updated,
        "status": document.status,
        "tags": list(document.tags),
    }
    body = escape_anchors_heading(document.body.strip())
    parts = [serialize_frontmatter(fields), "\n", body, "\n"]
    if document.anchors:
        parts.append(f"\n{_ANCHORS_HEADING}\n\n")
        parts.append("\n".join(_serialize_anchor(anchor) for anchor in document.anchors))
        parts.append("\n")
    return "".join(parts)


def escape_anchors_heading(body: str) -> str:
    """Neutralize ``## Anchors`` headings in prose about to become a note body.

    :func:`serialize_note` calls this on every body it writes, so no writer has
    to remember to -- it emits the body verbatim above the section it renders
    itself, and a line the scanner would read back as the sentinel opens a
    second anchor region: the file ends up with two ``## Anchors`` sections and
    any bullet-shaped prose below the first is promoted into an
    :class:`AnchorRecord` the writer never created, carrying whatever
    ``pinned@`` sha the text happened to contain. Escaping the hashes the way
    markdown does keeps the line rendering as the words the user wrote while
    making it ordinary prose to :func:`parse_note`.

    Two properties are as load-bearing as the escape itself:

    *Everything else is byte-identical.* Splitting is on ``\\n`` alone -- never
    ``str.splitlines()``, which also breaks on ``\\x0c``, ``\\x85``, ``\\u2028``
    and friends and drops a trailing newline, silently rewriting the line
    structure of prose pasted from a PDF or a JS-serialized source. A note body
    is the user's words; a filter that only needs to touch one matching line
    must not rewrite every line. ``\\n``-only splitting is also exactly what
    :func:`parse_note`'s scanner reads back, so the two cannot disagree.

    *A fenced code block is left alone.* Inside a fence a backslash is not an
    escape character, so escaping there would show the user the literal
    ``\\#\\# Anchors`` -- and the reachable case is precisely a note written
    *about* the anchor format, properly fenced. :func:`parse_note` skips the
    same regions (:func:`_fenced_line_indices` is shared), so a fenced sentinel
    is inert on both sides rather than escaped on one and honored on the other.

    Idempotent: an already-escaped line no longer matches the sentinel, so
    re-capturing text copied back out of a note file does not double-escape.

    Reading is deliberately not the place for this: in a hand-authored file an
    unfenced sentinel is real, and the parser must stay tolerant.
    """
    lines = body.split("\n")
    fenced = _fenced_line_indices(lines)
    return "\n".join(
        _escape_heading_hashes(line)
        if index not in fenced and _ANCHORS_HEADING_RE.match(line.strip())
        else line
        for index, line in enumerate(lines)
    )


def _escape_heading_hashes(line: str) -> str:
    return _HEADING_HASHES_RE.sub(lambda match: r"\#" * len(match.group()), line, count=1)


def _fenced_line_indices(lines: Sequence[str]) -> frozenset[int]:
    """Indices of the lines inside a *closed* fenced code block, delimiters included.

    A dangling opener is deliberately *not* a region. Were it one, a note whose
    body ended mid-fence would swallow the real ``## Anchors`` section that
    :func:`serialize_note` appends below it, losing every anchor on the next
    read. Requiring closure means the escape and the parser make the same call
    about a dangling fence -- both treat it as ordinary prose -- which is the
    only way the two stay in agreement.
    """
    fenced: set[int] = set()
    opener: tuple[int, str] | None = None
    for index, line in enumerate(lines):
        match = _FENCE_RE.match(line)
        if match is None:
            continue
        marker = match.group("marker")
        if opener is None:
            opener = (index, marker)
            continue
        start, open_marker = opener
        if _closes_fence(open_marker, marker, match.group("info")):
            fenced.update(range(start, index + 1))
            opener = None
    return frozenset(fenced)


def _closes_fence(open_marker: str, marker: str, info: str) -> bool:
    """Whether a delimiter line closes the fence ``open_marker`` opened."""
    return marker[0] == open_marker[0] and len(marker) >= len(open_marker) and not info.strip()


def anchor_of_record(document: NoteDocument) -> AnchorRecord | None:
    """The note's *anchor of record*, or ``None`` when it carries no anchors.

    The anchor of record is the **first** anchor, and that is a contract rather
    than a convenience: capture idempotency fingerprints a note on its body plus
    this anchor's page and quote (see
    :func:`~knotica.core.operations.capture_note._find_duplicate`). Any writer
    that adds anchors to an existing note must therefore **append** -- prepending,
    reordering, or rewriting index 0 silently stops every previously captured
    note from matching its own fingerprint, and the next re-capture of unchanged
    text writes a duplicate file instead of returning the existing one. Routing
    the read through this function is what makes that dependency findable from
    the write side; there is no other supported way to ask for it.
    """
    if not document.anchors:
        return None
    return document.anchors[_ANCHOR_OF_RECORD_INDEX]


def effective_anchor(document: NoteDocument) -> AnchorRecord | None:
    """The anchor a reader should currently trust, or ``None`` when detached.

    Unlike :func:`anchor_of_record` (always index 0, immutable, never asked to
    move), the *effective* anchor is a different question -- which correction
    is newest -- answered by position rather than a stored flag: the last
    entry in :attr:`NoteDocument.anchors`. ``kind="detached"`` is terminal --
    once the newest entry says the note no longer points anywhere, there is no
    effective anchor left to resolve, even though the anchor of record and the
    rest of the history stay fully intact.
    """
    if not document.anchors:
        return None
    newest = document.anchors[-1]
    if newest.kind == _DETACHED_KIND:
        return None
    return newest


def derive_note_id(
    text: str,
    created_at: str,
    *,
    existing: Callable[[str], bool],
) -> str:
    """Derive the filename stem (and frontmatter ``id``) for a new note.

    The stem is ``<YYYYMMDD-HHMMSS>-<slug>``, the slug taken from the note's own
    first heading when it has one and otherwise from its opening words. Purity
    is the point: ``existing`` is the caller's "is this stem taken?" predicate,
    so collision handling stays testable without touching a filesystem.
    """
    base = _stem_prefix(created_at)
    slug = _slugify(_headline(text))
    if slug:
        base = f"{base}-{slug}"
    if not existing(base):
        return base
    for suffix in _COLLISION_SUFFIXES:
        candidate = f"{base}-{suffix}"
        if not existing(candidate):
            return candidate
    raise ValueError(f"Exhausted same-second note id suffixes for stem {base!r}.")


# ---------------------------------------------------------------------------
# Frontmatter coercion
# ---------------------------------------------------------------------------


def _as_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_tags(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _as_schema_version(value: object) -> int:
    if isinstance(value, bool):
        return DEFAULT_SCHEMA_VERSION
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return DEFAULT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Body / anchor-section parsing
# ---------------------------------------------------------------------------


def _parse_body(body_text: str) -> tuple[str, tuple[AnchorRecord, ...], int]:
    lines = body_text.split("\n")
    fenced = _fenced_line_indices(lines)
    scanner = _BodyScanner()
    for index, line in enumerate(lines):
        scanner.feed(line, fenced=index in fenced)
    return scanner.finish()


class _BodyScanner:
    """Separates a note body into prose and anchors, one line at a time.

    The section rule, chosen so that nothing a user typed can go missing: an
    ``## Anchors`` heading *opens* an anchor region and any other level-1/2
    heading closes it, but a note may open the region as many times as it likes
    and only two line shapes are ever consumed from inside it -- an anchor
    bullet and the quote line that completes it. Everything else the region
    contains is prose and flows back into the body in document order, so a note
    that merely *talks about* the anchor format cannot swallow its own real
    section, and a thought written between two bullets survives. A bullet the
    grammar cannot read is the one thing neither kept nor silently dropped: it
    is counted in ``skipped``, the module's report-as-data channel.

    Only the ``## Anchors`` heading lines themselves are discarded -- they are
    structure, not prose, and ``serialize_note`` re-emits exactly one of them.

    A line inside a closed fenced code block is prose unconditionally: a note
    that quotes the anchor grammar in a fence is showing an example, not
    declaring an anchor, and ``escape_anchors_heading`` leaves those lines alone
    for the same reason. The caller supplies the verdict
    (:func:`_fenced_line_indices`) so that the escape and the scan share one
    definition of "fenced".
    """

    def __init__(self) -> None:
        self.body_lines: list[str] = []
        self.anchors: list[AnchorRecord] = []
        self.skipped = 0
        self._in_section = False
        self._block: list[str] | None = None

    def feed(self, line: str, *, fenced: bool = False) -> None:
        if fenced:
            self._close_block()
            self.body_lines.append(line)
            return
        stripped = line.strip()
        if _ANCHORS_HEADING_RE.match(stripped):
            self._close_block()
            self._in_section = True
            return
        if not self._in_section:
            self.body_lines.append(line)
            return
        if _HEADING_RE.match(line):
            self._close_block()
            self._in_section = False
            self.body_lines.append(line)
            return
        self._feed_section_line(line, stripped)

    def finish(self) -> tuple[str, tuple[AnchorRecord, ...], int]:
        self._close_block()
        return "\n".join(self.body_lines).strip(), tuple(self.anchors), self.skipped

    def _feed_section_line(self, line: str, stripped: str) -> None:
        if line.lstrip().startswith(_BULLET_PREFIX):
            self._close_block()
            self._block = [line]
            return
        if self._block is not None and (not stripped or stripped.startswith(">")):
            self._block.append(line)
            if stripped.startswith(">"):
                self._close_block()
            return
        self._close_block()
        self.body_lines.append(line)

    def _close_block(self) -> None:
        """Resolve the open bullet, if any, into an anchor or a skipped count."""
        block, self._block = self._block, None
        if block is None:
            return
        anchor = _parse_anchor_block(block)
        if anchor is None:
            self.skipped += 1
            return
        self.anchors.append(anchor)


def _parse_anchor_block(block: list[str]) -> AnchorRecord | None:
    """Read one bullet block into an anchor, or ``None`` when it is malformed."""
    match = _ANCHOR_LINE_RE.match(block[0].strip())
    if match is None:
        return None
    page, heading = _split_wikilink_target(match.group("target") or "")
    start = match.group("start")
    kind = match.group("kind")
    return AnchorRecord(
        page=page,
        heading=heading,
        fidelity=match.group("fidelity").strip(),
        pinned_at=match.group("sha").strip(),
        quote=_first_quote(block[1:]),
        start=int(start) if start is not None else None,
        kind=kind.strip() if kind is not None else _PINNED_KIND,
    )


def _first_quote(lines: list[str]) -> str:
    """The bullet's verbatim quote line, or ``""`` when it supplied none.

    A bullet's block only ever collects blank lines and a single terminating
    quote line (:meth:`_BodyScanner._feed_section_line` sends anything else
    back to the body), so the absence of a ``>`` line means the quote was
    genuinely omitted -- a valid anchor, not a malformed one.
    """
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            return stripped[1:].strip()
    return ""


def _split_wikilink_target(target: str) -> tuple[str, str]:
    path, _, heading = target.strip().partition("#")
    page = path.strip()
    if page and not page.endswith(_MARKDOWN_SUFFIX):
        page += _MARKDOWN_SUFFIX
    return page, heading.strip()


def _serialize_anchor(anchor: AnchorRecord) -> str:
    """Render one anchor bullet, omitting the parts the record does not carry."""
    bullet = "-"
    if anchor.page or anchor.heading:
        target = anchor.page.removesuffix(_MARKDOWN_SUFFIX)
        if anchor.heading:
            target = f"{target}#{anchor.heading}"
        bullet += f" [[{target}]] —"
    bullet += f" `{anchor.fidelity}` · pinned@`{anchor.pinned_at}`"
    if anchor.start is not None:
        bullet += f" · at={anchor.start}"
    if anchor.kind != _PINNED_KIND:
        bullet += f" · {anchor.kind}"
    if not anchor.quote:
        return bullet
    return f"{bullet}\n  > {anchor.quote}"


# ---------------------------------------------------------------------------
# id / slug derivation
# ---------------------------------------------------------------------------


def _stem_prefix(created_at: str) -> str:
    match = _TIMESTAMP_RE.match(created_at.strip())
    if match is None:
        return re.sub(r"\D", "", created_at)[:14] or "00000000-000000"
    date = f"{match['year']}{match['month']}{match['day']}"
    return f"{date}-{match['hour']}{match['minute']}{match['second']}"


def _headline(text: str) -> str:
    """The note's own first heading, else its first non-empty line."""
    first_line = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        if not first_line:
            first_line = stripped
    return first_line


def _slugify(headline: str) -> str:
    words = " ".join(headline.split()[:_SLUG_WORD_LIMIT])
    ascii_only = unicodedata.normalize("NFKD", words).encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG_RE.sub("-", ascii_only.lower()).strip("-")
    return slug[:_SLUG_MAX_LENGTH].rstrip("-")

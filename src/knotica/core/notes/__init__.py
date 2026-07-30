"""Personal notes -- the reserved, unscored ``notes/<topic>/`` folder family.

A note is a reflection captured in-flight and anchored to the knowledge-base
span that provoked it. Notes live beside the wiki but never inside it: nothing
here participates in a score, and no read path in this package mutates the
vault.

:mod:`knotica.core.notes.anchor` owns the on-disk contract -- the note
frontmatter dialect, the ``## Anchors`` bullet grammar, and filename/id
derivation -- as pure functions over strings, so the same grammar serves a
tool-captured note and one a human typed by hand in Obsidian.
"""

from knotica.core.notes.anchor import (
    AnchorRecord,
    NoteDocument,
    derive_note_id,
    parse_note,
    serialize_note,
)

__all__ = [
    "AnchorRecord",
    "NoteDocument",
    "derive_note_id",
    "parse_note",
    "serialize_note",
]

"""Per-parameter schema grounding -- one declaration per parameter *meaning*.

A tool description is the executable interface for a model, and until now it
carried the whole burden: every one of the surface's parameters published as
``{"title": "Suggestion Id", "type": "string"}``, a title auto-derived from the
name and carrying nothing the name did not already carry. The legal values of
``decision``, ``mode``, ``status``, ``target`` and ``verdict`` lived only in
prose, kilobytes away from the field they constrain.

This module is the fix's single declaration. Each alias is an
:data:`~typing.Annotated` type carrying a :class:`~pydantic.Field` with a
one-sentence ``description`` and, for a closed vocabulary, an **advisory**
``enum``. Handlers annotate their own parameters with these aliases, so the
grounding is attached at the seam
:func:`~knotica.mcp_server.tools_dispatch_lane_common._lane_signature` already
reads -- a lane's union call shape inherits it with no second declaration to
drift.

Three rules hold this together:

* **Advisory, never enforced.** The enum rides in ``json_schema_extra``, never
  as a :data:`~typing.Literal`. A ``Literal`` would have pydantic reject an
  unknown value with a raw validation string, replacing this surface's typed
  ``{code, message, fix, retryable}`` envelope and losing the
  ``record_rejected_action`` signal. Advertised in the schema, enforced at the
  handler, is what keeps both.
* **The vocabulary is referenced, never copied.** Every :func:`grounded` call
  that publishes an enum passes the constant its own validation already reads.
  A value added to ``_ACTIONS`` or ``VALID_STATUS_VIEWS`` reaches the published
  schema for free.
* **One meaning, one alias.** A parameter name that means the same thing in
  every verb that declares it gets exactly one alias here, so the lane union
  sees identical metadata and preserves it. A name whose meaning genuinely
  differs per verb (``mode``, ``status``, ``target``, ``intent``) is annotated
  *locally* in each owning module instead: the lane union then sees disagreeing
  metadata and degrades to the plain type rather than publishing one verb's
  semantics over another's. See ``tools_dispatch_lane_common._optional``.

The ``str`` exception that module documents is preserved here by construction:
an alias over ``str`` stays a ``str``-annotated pydantic field (``Annotated``
metadata does not change ``FieldInfo.annotation``), so FastMCP's
``pre_parse_json`` still leaves string arguments alone.

:func:`grounded` returns the ``Field`` -- the *metadata* -- rather than the
finished alias, so every definition below reads ``Annotated[X, grounded(...)]``.
That is a subscript expression a type checker accepts as a type alias; a helper
returning the whole alias would be a plain call, and mypy would reject each
alias as "not valid as a type".
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any

from pydantic import Field

__all__ = [
    "VERDICT_VALUES",
    "Answer",
    "Branch",
    "Candidate",
    "CitationKey",
    "Confirm",
    "Content",
    "Cursor",
    "Intent",
    "Limit",
    "NoteId",
    "Page",
    "Query",
    "Question",
    "Quote",
    "Reason",
    "RunId",
    "SuggestionId",
    "Title",
    "Topic",
    "Vault",
    "Verdict",
    "grounded",
]

#: The closed vocabulary shared by ``verdict`` wherever it appears -- the two
#: values ``curate_example`` writes and ``notes action=promote`` re-uses.
VERDICT_VALUES: tuple[str, ...] = ("good", "bad")


def grounded(description: str, values: Iterable[str] | None = None) -> Any:
    """The :class:`~pydantic.Field` a parameter's ``Annotated`` metadata carries.

    Args:
        description: One sentence stating the parameter's meaning, its default,
            and its unit where it has one.
        values: The closed vocabulary to publish as an advisory ``enum``. Pass
            the constant the handler's own validation reads, never a restated
            list -- that is what keeps the published set and the enforced set
            from diverging. A set is sorted so the wire order is deterministic;
            a sequence is published in the order it declares.
    """
    if values is None:
        return Field(description=description)
    published = list(values) if isinstance(values, list | tuple) else sorted(values)
    return Field(description=description, json_schema_extra={"enum": published})


# ---------------------------------------------------------------------------
# The cross-verb aliases. One per parameter *meaning*; a name below is declared
# by two or more verbs and means the same thing in every one of them.
# ---------------------------------------------------------------------------

Topic = Annotated[
    str,
    grounded(
        "Topic to act in -- a directory name directly under the vault root, never "
        "a path. Required by verbs that write; where optional it defaults to "
        "empty, which the verb reads as vault-wide."
    ),
]

Vault = Annotated[
    str,
    grounded(
        "Configured vault name to act on; empty (the default) uses the configured default_vault."
    ),
]

Limit = Annotated[
    int,
    grounded(
        "Maximum records to return in this call. Each verb applies its own default "
        "and ceiling (20/50 for the gap, suggestion and note queues; 10 for search)."
    ),
]

Cursor = Annotated[
    str,
    grounded(
        "Opaque pagination token from a previous call's next_cursor; empty (the "
        "default) starts at the first page. Filters must stay identical across a "
        "paginated walk or the cursor is invalid."
    ),
]

Page = Annotated[
    str,
    grounded(
        "Page within the topic -- a topic-relative name (agent-memory), a "
        "vault-relative path as returned by search, or a bare citation key for a "
        "stored source."
    ),
]

Question = Annotated[
    str,
    grounded(
        "The natural-language question, in the user's own words -- what the wiki "
        "is being asked, or what the gap leaves unanswered."
    ),
]

Answer = Annotated[
    str,
    grounded(
        "The answer text this record carries, verbatim -- what was actually said, "
        "not a summary of it."
    ),
]

Query = Annotated[
    str,
    grounded(
        "The query text: the search string for search, or the question the curated example answers."
    ),
]

Quote = Annotated[
    str,
    grounded(
        "Exact substring of the page this record anchors to; it must appear in the "
        "page verbatim or the anchor is refused."
    ),
]

Reason = Annotated[
    str,
    grounded(
        "Free-text justification recorded with the decision. Required where the "
        "decision is terminal (reject, dismiss); advisory otherwise."
    ),
]

Content = Annotated[
    str,
    grounded(
        "Full markdown body to write, including frontmatter where the target "
        "requires it. Sent verbatim -- never a diff or a patch."
    ),
]

Title = Annotated[str, grounded("Human-readable title for the record being written.")]

CitationKey = Annotated[
    str,
    grounded(
        "Stable citation key identifying one stored source within its topic "
        "(lowercase, hyphenated, no path separators)."
    ),
]

SuggestionId = Annotated[
    str,
    grounded(
        "Id of one proposed-source record in the topic's suggestion queue, as "
        "returned by suggestions_read."
    ),
]

NoteId = Annotated[
    str,
    grounded("Id of one note in the personal-notes overlay, as returned by notes action=list."),
]

RunId = Annotated[
    str,
    grounded(
        "Id grouping one ingest run's journal entries; empty (the default) reads "
        "or appends to the topic's most recent run."
    ),
]

Branch = Annotated[
    str,
    grounded(
        "Git branch name in the vault repository, as returned by branches "
        "action=scoreboard; empty (the default) lets the verb pick its own."
    ),
]

Candidate = Annotated[
    str,
    grounded(
        "Candidate worktree to write into instead of the live vault; empty (the "
        "default) writes to the live vault."
    ),
]

Confirm = Annotated[
    str,
    grounded(
        "Single-use nonce returned by this same action's free preview call. Omit "
        "it to get the preview; pass it back to execute the billed run."
    ),
]

Intent = Annotated[
    str,
    grounded(
        "What the note is for -- a short free-text label (question, correction, "
        "todo, ...) used to filter and to gate promotion."
    ),
]

Verdict = Annotated[
    str,
    grounded(
        "Whether this example is a positive or a negative one; 'good' is the default.",
        VERDICT_VALUES,
    ),
]

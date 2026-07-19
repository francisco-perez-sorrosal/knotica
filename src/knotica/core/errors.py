"""The shared error contract -- code enum, error/warning types, envelope constructors.

Single source of the tool-result contract for every adapter (MCP tools, CLI,
future loops). The envelope has exactly one discriminator: the **presence of an
``error`` key** means failure; its absence means success. There is no top-level
status field. A success envelope is the result data itself, optionally carrying
a ``warnings`` list; a failure envelope is ``{"error": {code, message, fix,
retryable}}``.

Grammar for every error: "X failed because Y. To fix: Z." -- ``message`` states
what and why, ``fix`` is the exact next action, ``code`` is a stable enum the
model can branch on, and ``retryable`` tells it whether retrying can help
(only ``LOCK_BUSY`` is retryable: lock contention clears; everything else
needs a different call).

``SECRET_SCRUBBED`` is the one code that is a **warning, never an error**: a
write that scrubbed secrets still succeeds, and the warning rides on the
success envelope. Constructing an error with it fails fast.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ErrorCode(StrEnum):
    """Stable result codes -- the fixed enum adapters and models branch on."""

    NOT_CONFIGURED = "NOT_CONFIGURED"
    TOPIC_NOT_FOUND = "TOPIC_NOT_FOUND"
    PAGE_NOT_FOUND = "PAGE_NOT_FOUND"
    RESERVED_NAME = "RESERVED_NAME"
    SOURCE_EXISTS = "SOURCE_EXISTS"
    INVALID_FRONTMATTER = "INVALID_FRONTMATTER"
    SECRET_SCRUBBED = "SECRET_SCRUBBED"
    LOCK_BUSY = "LOCK_BUSY"
    GIT_ERROR = "GIT_ERROR"
    INVALID_CURSOR = "INVALID_CURSOR"
    LLM_API_ERROR = "LLM_API_ERROR"
    SEARCH_API_ERROR = "SEARCH_API_ERROR"
    SUGGESTION_NOT_FOUND = "SUGGESTION_NOT_FOUND"
    SUGGESTION_NOT_APPROVED = "SUGGESTION_NOT_APPROVED"


#: Canonical fix text per code (the static part of the contract). Callers may
#: extend or replace it with call-specific detail (e.g. the reserved-name list,
#: the nearest page matches) but must keep the action concrete.
DEFAULT_FIX: Mapping[ErrorCode, str] = MappingProxyType(
    {
        ErrorCode.NOT_CONFIGURED: ("Run `/knotica:setup` (Claude Code) or `knotica init` (CLI)."),
        ErrorCode.TOPIC_NOT_FOUND: (
            "Call `list_topics` to see valid topics, or `create_topic` to make a new one."
        ),
        ErrorCode.PAGE_NOT_FOUND: "Call `search` in this topic.",
        ErrorCode.RESERVED_NAME: (
            "Choose a non-reserved name; to update the catalog pass `index_entry` to"
            " `write_page` instead of writing `index.md`."
        ),
        ErrorCode.SOURCE_EXISTS: "Use a different citation_key; sources are immutable.",
        ErrorCode.INVALID_FRONTMATTER: ("Add or fix the frontmatter fields named in the message."),
        ErrorCode.SECRET_SCRUBBED: (
            "Review the redacted spans in the response before relying on the page."
        ),
        ErrorCode.LOCK_BUSY: "Another operation is in progress; retry in a moment.",
        ErrorCode.GIT_ERROR: (
            "Run `knotica doctor` / `/knotica:doctor` to inspect and offer rollback."
        ),
        ErrorCode.INVALID_CURSOR: "Restart the search without a cursor.",
        ErrorCode.LLM_API_ERROR: (
            "Check the eval credential mode and your plan limits; transient rate"
            " limits and server errors clear on their own -- wait and re-run."
        ),
        ErrorCode.SEARCH_API_ERROR: (
            "Check the search provider's status and your rate limits; transient"
            " rate limits and server errors clear on their own -- wait and re-run."
        ),
        ErrorCode.SUGGESTION_NOT_FOUND: ("Call `suggestions_read` to list current suggestion_ids."),
        ErrorCode.SUGGESTION_NOT_APPROVED: (
            "Approve it first: `suggestions_review(action=approve, mode=apply)`."
        ),
    }
)

#: The retryable codes: lock contention plus LLM and search-provider transport
#: throttling all clear on their own; every other failure needs a *different*
#: call, not the same one again. (An LLM_API_ERROR or SEARCH_API_ERROR raiser
#: passes retryable=False explicitly for non-transient statuses such as auth
#: rejections.)
RETRYABLE_CODES: frozenset[ErrorCode] = frozenset(
    {ErrorCode.LOCK_BUSY, ErrorCode.LLM_API_ERROR, ErrorCode.SEARCH_API_ERROR}
)

#: Codes that ride on *success* envelopes as warnings and can never be errors.
WARNING_CODES: frozenset[ErrorCode] = frozenset({ErrorCode.SECRET_SCRUBBED})

_ENVELOPE_RESERVED_KEYS: frozenset[str] = frozenset({"error", "warnings"})


class KnoticaError(Exception):
    """A failed operation, carrying the full envelope payload.

    Core raises this; adapters catch it and render it into the tool result
    (or the CLI stderr message) via :meth:`envelope` -- the model must *see*
    the actionable text in the result content, never only a transport-level
    exception.

    Args:
        code: Stable code from :class:`ErrorCode` (never ``SECRET_SCRUBBED`` --
            that one is a warning by contract).
        message: What failed and why, in the consumer's vocabulary.
        fix: The exact next action. Defaults to the code's canonical fix text.
        retryable: Whether retrying the same call can succeed. Defaults to the
            contract value for the code (true only for ``LOCK_BUSY``).
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        fix: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        _reject_warning_only_codes(code)
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = DEFAULT_FIX[code] if fix is None else fix
        self.retryable = (code in RETRYABLE_CODES) if retryable is None else retryable

    def envelope(self) -> dict[str, Any]:
        """Render this error as the failure envelope ``{"error": {...}}``."""
        return err(self.code, self.message, self.fix, self.retryable)


@dataclass(frozen=True, slots=True)
class KnoticaWarning:
    """A non-fatal finding that rides on a *success* envelope.

    Same field grammar as an error minus ``retryable`` (there is nothing to
    retry -- the operation succeeded). Rendered into the envelope's
    ``warnings`` list by :func:`ok`.
    """

    code: ErrorCode
    message: str
    fix: str

    def render(self) -> dict[str, str]:
        """Render as the plain-dict shape carried in ``warnings: [...]``."""
        return {"code": self.code.value, "message": self.message, "fix": self.fix}


def secret_scrubbed_warning(message: str) -> KnoticaWarning:
    """Build the canonical secret-scrub warning (uniform fix text everywhere).

    ``message`` should name the redacted spans so the consumer can review
    exactly what was replaced.
    """
    return KnoticaWarning(
        code=ErrorCode.SECRET_SCRUBBED,
        message=message,
        fix=DEFAULT_FIX[ErrorCode.SECRET_SCRUBBED],
    )


def ok(
    data: Mapping[str, Any],
    warnings: Iterable[KnoticaWarning] = (),
) -> dict[str, Any]:
    """Build a success envelope: the result data plus optional ``warnings``.

    ``data`` becomes the envelope's top level, so it must not carry the
    reserved keys ``error`` (the failure discriminator) or ``warnings``
    (owned by this constructor) -- either collision fails fast. The
    ``warnings`` key is present only when at least one warning is given.
    """
    collisions = _ENVELOPE_RESERVED_KEYS.intersection(data)
    if collisions:
        raise ValueError(
            f"Success data must not carry reserved envelope keys: {sorted(collisions)}"
        )
    envelope: dict[str, Any] = dict(data)
    rendered = [warning.render() for warning in warnings]
    if rendered:
        envelope["warnings"] = rendered
    return envelope


def err(
    code: ErrorCode,
    message: str,
    fix: str | None = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    """Build a failure envelope ``{"error": {code, message, fix, retryable}}``.

    ``fix`` and ``retryable`` default to the code's contract values
    (:data:`DEFAULT_FIX`, :data:`RETRYABLE_CODES`) so correct usage is the
    path of least resistance. ``SECRET_SCRUBBED`` is refused -- it is a
    warning by contract, never a failure.
    """
    _reject_warning_only_codes(code)
    return {
        "error": {
            "code": code.value,
            "message": message,
            "fix": DEFAULT_FIX[code] if fix is None else fix,
            "retryable": (code in RETRYABLE_CODES) if retryable is None else retryable,
        }
    }


def _reject_warning_only_codes(code: ErrorCode) -> None:
    """Fail fast when a warning-only code is used where an error is built."""
    if code in WARNING_CODES:
        raise ValueError(
            f"{code.value} is a warning by contract (the operation succeeds); "
            "attach it to a success envelope via ok(..., warnings=[...])."
        )

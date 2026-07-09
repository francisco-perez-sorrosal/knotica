"""Page model -- frontmatter parse/serialize, page paths, core-field validation.

Pure functions over the :class:`~knotica.store.VaultStore` protocol: no git, no
locking, no config resolution. The frontmatter dialect is the strict subset the
vault constitution (root ``SCHEMA.md``) actually uses -- scalar values, flow
sequences (``[a, b]``), and block sequences (``- item``, as Obsidian's
properties editor writes them). Anything outside that subset raises
:class:`FrontmatterParseError`; readers surface it as data on the
:class:`Page`, never as a crashed read.

Validation is against the *core* frontmatter field set frozen in the root
constitution. Topic overlays may add fields freely -- unknown fields are never
a violation; only missing or malformed core fields are. Validation results are
plain data (:class:`FieldProblem` rows) so callers can render them into the
user-facing error envelope.
"""

import difflib
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from knotica.store import VaultStore

__all__ = [
    "CONFIDENCE_VALUES",
    "OPTIONAL_FIELDS",
    "REQUIRED_FIELDS",
    "STATUS_VALUES",
    "FieldProblem",
    "FrontmatterParseError",
    "Page",
    "PageNotFoundError",
    "TopicNotFoundError",
    "normalize_page_name",
    "page_path",
    "parse_frontmatter_block",
    "parse_page",
    "read_page",
    "serialize_frontmatter",
    "validate_frontmatter",
]

#: Core frontmatter fields every content page must carry (root constitution).
REQUIRED_FIELDS: tuple[str, ...] = (
    "type",
    "topic",
    "created",
    "updated",
    "confidence",
    "sources",
    "status",
    "tags",
)

#: Core fields that may be absent.
OPTIONAL_FIELDS: tuple[str, ...] = ("supersedes", "superseded_by")

CONFIDENCE_VALUES: frozenset[str] = frozenset({"low", "medium", "high"})
STATUS_VALUES: frozenset[str] = frozenset({"active", "stale"})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_FRONTMATTER_FENCE = "---"
_KEY_VALUE_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*):(?:\s+(?P<value>.*))?$")
_PLAIN_SCALAR_SAFE_RE = re.compile(r"^[A-Za-z0-9._/@+-][A-Za-z0-9 ._/@+-]*$")
_MAX_PAGE_SUGGESTIONS = 3


class FrontmatterParseError(ValueError):
    """Frontmatter uses YAML constructs outside the vault's strict subset."""


class TopicNotFoundError(LookupError):
    """The requested topic has no directory at the vault root."""

    def __init__(self, topic: str) -> None:
        super().__init__(f"No topic directory named '{topic}' at the vault root.")
        self.topic = topic


class PageNotFoundError(LookupError):
    """The requested page does not exist; carries nearest-match suggestions."""

    def __init__(self, topic: str, page: str, suggestions: tuple[str, ...]) -> None:
        detail = f" Nearest matches: {', '.join(suggestions)}." if suggestions else ""
        super().__init__(f"No page '{page}' in topic '{topic}'.{detail}")
        self.topic = topic
        self.page = page
        self.suggestions = suggestions


@dataclass(frozen=True)
class FieldProblem:
    """One frontmatter validation finding, as data.

    ``field`` names the offending core field; ``problem`` is a human/model-
    readable description of what is missing or invalid.
    """

    field: str
    problem: str


@dataclass(frozen=True)
class Page:
    """A parsed wiki page.

    ``frontmatter`` is ``None`` both when the page has no frontmatter block and
    when the block failed to parse -- ``frontmatter_error`` distinguishes the
    two (``None`` means genuinely absent). ``body`` is the markdown after the
    frontmatter block; ``raw`` is the full file content as stored.
    """

    topic: str
    path: str
    frontmatter: dict[str, object] | None
    frontmatter_error: str | None
    body: str
    raw: str


def normalize_page_name(page: str) -> str:
    """Normalize a topic-relative page reference to its ``.md`` file path.

    The ``.md`` extension is optional on input and always present on output.
    Nested paths (``methods/react``) are allowed; absolute paths, ``.``/``..``
    segments, empty segments, and dot-prefixed segments (hidden files are not
    pages) are rejected with :class:`ValueError`.
    """
    candidate = page.strip()
    if not candidate:
        raise ValueError("Page name must not be empty.")
    if candidate.startswith("/") or "\\" in candidate:
        raise ValueError(f"Page name must be a relative POSIX path, got: {page!r}")
    for segment in candidate.split("/"):
        if not segment:
            raise ValueError(f"Page name has an empty path segment: {page!r}")
        if segment.startswith("."):
            raise ValueError(f"Page name must not contain dot-prefixed segments: {page!r}")
    if not candidate.endswith(".md"):
        candidate += ".md"
    return candidate


def page_path(topic: str, page: str) -> str:
    """Return the vault-relative path of ``page`` inside ``topic``."""
    cleaned_topic = topic.strip()
    if not cleaned_topic:
        raise ValueError("Topic must not be empty.")
    if "/" in cleaned_topic or cleaned_topic.startswith("."):
        raise ValueError(f"Topic must be a bare top-level directory name, got: {topic!r}")
    return f"{cleaned_topic}/{normalize_page_name(page)}"


def parse_page(text: str) -> tuple[dict[str, object] | None, str | None, str]:
    """Split ``text`` into ``(frontmatter, frontmatter_error, body)``.

    A page without a leading ``---`` fence has no frontmatter: ``(None, None,
    text)``. A malformed block yields ``(None, <error message>, body)`` -- the
    read never fails; the problem travels as data.
    """
    block, body = _split_frontmatter(text)
    if block is None:
        return None, None, body
    try:
        return parse_frontmatter_block(block), None, body
    except FrontmatterParseError as error:
        return None, str(error), body


def parse_frontmatter_block(block: str) -> dict[str, object]:
    """Parse the inside of a frontmatter block (strict subset; see module docs).

    Supports ``key: scalar``, ``key: [flow, list]``, and block sequences::

        key:
          - item

    Scalars are quoted or plain strings, integers, booleans, or null. Duplicate
    keys and any other construct raise :class:`FrontmatterParseError`.
    """
    fields: dict[str, object] = {}
    pending_list_key: str | None = None
    for line_number, line in enumerate(block.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            _append_block_item(fields, pending_list_key, stripped, line_number)
            continue
        pending_list_key = _parse_entry(fields, stripped, line_number)
    return fields


def serialize_frontmatter(fields: Mapping[str, object]) -> str:
    """Render ``fields`` as a frontmatter block in the constitution's style.

    Insertion order is preserved; lists render flow-style (``[a, b]``) as the
    template authors them. Returns the block including both ``---`` fences and
    a trailing newline.
    """
    lines = [_FRONTMATTER_FENCE]
    for key, value in fields.items():
        lines.append(f"{key}: {_serialize_value(value)}" if value is not None else f"{key}:")
    lines.append(_FRONTMATTER_FENCE)
    return "\n".join(lines) + "\n"


def read_page(store: VaultStore, topic: str, page: str) -> Page:
    """Read and parse one wiki page.

    Raises :class:`TopicNotFoundError` when the topic directory is absent and
    :class:`PageNotFoundError` (with nearest-match suggestions from the topic's
    existing pages) when the page file is absent.
    """
    path = page_path(topic, page)
    if not store.exists(topic.strip()):
        raise TopicNotFoundError(topic.strip())
    if not store.exists(path):
        raise PageNotFoundError(topic.strip(), page, _suggest_pages(store, topic.strip(), page))
    raw = store.read_text(path)
    frontmatter, error, body = parse_page(raw)
    return Page(
        topic=topic.strip(),
        path=path,
        frontmatter=frontmatter,
        frontmatter_error=error,
        body=body,
        raw=raw,
    )


def validate_frontmatter(
    frontmatter: Mapping[str, object],
    *,
    allowed_types: Collection[str] | None = None,
) -> list[FieldProblem]:
    """Validate ``frontmatter`` against the core field set; findings as data.

    Unknown extra fields are permitted (overlays extend the constitution).
    ``allowed_types``, when given, constrains the ``type`` field to the topic
    overlay's entity types. An empty result means the frontmatter conforms.
    """
    problems = [
        FieldProblem(field, "missing required field")
        for field in REQUIRED_FIELDS
        if field not in frontmatter
    ]
    checks = {
        "type": lambda v: _check_type_field(v, allowed_types),
        "topic": _check_nonempty_string,
        "created": _check_date,
        "updated": _check_date,
        "confidence": lambda v: _check_enum(v, CONFIDENCE_VALUES),
        "status": lambda v: _check_enum(v, STATUS_VALUES),
        "sources": _check_string_list,
        "tags": _check_string_list,
        "supersedes": _check_nonempty_string,
        "superseded_by": _check_nonempty_string,
    }
    for field, check in checks.items():
        if field not in frontmatter:
            continue
        value = frontmatter[field]
        if field in OPTIONAL_FIELDS and value is None:
            continue
        problem = check(value)
        if problem is not None:
            problems.append(FieldProblem(field, problem))
    return problems


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return ``(frontmatter block without fences, body)``; block may be absent."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != _FRONTMATTER_FENCE:
        return None, text
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == _FRONTMATTER_FENCE:
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    return None, text  # opening fence never closed -- treat as body, not frontmatter


def _parse_entry(fields: dict[str, object], stripped: str, line_number: int) -> str | None:
    """Parse one ``key: value`` line into ``fields``; return the key when it opens a block list."""
    match = _KEY_VALUE_RE.match(stripped)
    if match is None:
        raise FrontmatterParseError(
            f"Frontmatter line {line_number} is not a 'key: value' entry "
            f"in the vault's strict subset: {stripped!r}"
        )
    key = match.group("key")
    if key in fields:
        raise FrontmatterParseError(f"Duplicate frontmatter key {key!r} (line {line_number}).")
    value = match.group("value")
    if value is None or not value.strip():
        fields[key] = None  # empty value: null scalar, or a block list about to start
        return key
    fields[key] = _parse_scalar_or_flow(value.strip(), line_number)
    return None


def _append_block_item(
    fields: dict[str, object],
    pending_list_key: str | None,
    stripped: str,
    line_number: int,
) -> None:
    """Append a ``- item`` line to the block list opened by the preceding key."""
    if pending_list_key is None:
        raise FrontmatterParseError(
            f"Frontmatter line {line_number}: list item without a preceding 'key:' line."
        )
    current = fields[pending_list_key]
    if current is None:
        current = []
        fields[pending_list_key] = current
    if not isinstance(current, list):
        raise FrontmatterParseError(
            f"Frontmatter line {line_number}: list item under scalar key {pending_list_key!r}."
        )
    current.append(_parse_scalar(stripped[2:].strip(), line_number))


def _parse_scalar_or_flow(token: str, line_number: int) -> object:
    if token.startswith("["):
        if not token.endswith("]"):
            raise FrontmatterParseError(
                f"Frontmatter line {line_number}: unterminated flow sequence: {token!r}"
            )
        inner = token[1:-1].strip()
        if not inner:
            return []
        if "[" in inner or "]" in inner or "{" in inner:
            raise FrontmatterParseError(
                f"Frontmatter line {line_number}: nested collections are outside "
                f"the vault's strict subset: {token!r}"
            )
        return [_parse_scalar(item.strip(), line_number) for item in _split_flow_items(inner)]
    if token.startswith("{"):
        raise FrontmatterParseError(
            f"Frontmatter line {line_number}: flow mappings are outside "
            f"the vault's strict subset: {token!r}"
        )
    return _parse_scalar(token, line_number)


def _split_flow_items(inner: str) -> list[str]:
    """Split flow-sequence items on commas, honoring quoted items."""
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in inner:
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
            current.append(char)
        elif char == ",":
            items.append("".join(current))
            current = []
        else:
            current.append(char)
    items.append("".join(current))
    return items


def _parse_scalar(token: str, line_number: int) -> object:
    if len(token) >= 2 and token[0] in {'"', "'"} and token[-1] == token[0]:
        return token[1:-1]
    if token and (token[0] in {'"', "'"} or token[-1:] in {'"', "'"}):
        raise FrontmatterParseError(
            f"Frontmatter line {line_number}: unbalanced quotes in scalar: {token!r}"
        )
    if token in {"null", "~", ""}:
        return None
    if token == "true":
        return True
    if token == "false":
        return False
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    return token


def _serialize_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _serialize_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_serialize_value(item) for item in value) + "]"
    raise TypeError(f"Frontmatter values must be str/int/bool/list/None, got {type(value)!r}")


def _serialize_string(value: str) -> str:
    needs_quoting = (
        not value
        or not _PLAIN_SCALAR_SAFE_RE.fullmatch(value)
        or value in {"null", "~", "true", "false"}
        or re.fullmatch(r"-?\d+", value) is not None
    )
    if needs_quoting:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _suggest_pages(store: VaultStore, topic: str, page: str) -> tuple[str, ...]:
    """Nearest-match page names in ``topic`` for a missing ``page`` reference."""
    try:
        entries = store.list_dir(topic)
    except (FileNotFoundError, NotADirectoryError):
        return ()
    stems = [name[: -len(".md")] for name in entries if name.endswith(".md")]
    wanted = page.strip().removesuffix(".md")
    return tuple(difflib.get_close_matches(wanted, stems, n=_MAX_PAGE_SUGGESTIONS, cutoff=0.4))


def _check_nonempty_string(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return f"must be a non-empty string, got {value!r}"
    return None


def _check_date(value: object) -> str | None:
    if not isinstance(value, str):
        return f"must be a YYYY-MM-DD date or RFC 3339 datetime, got {value!r}"
    if _DATE_RE.fullmatch(value) or _DATETIME_RE.fullmatch(value):
        return None
    return f"must be a YYYY-MM-DD date or RFC 3339 datetime, got {value!r}"


def _check_enum(value: object, allowed: frozenset[str]) -> str | None:
    if value not in allowed:
        return f"must be one of {'|'.join(sorted(allowed))}, got {value!r}"
    return None


def _check_string_list(value: object) -> str | None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return f"must be a list of strings, got {value!r}"
    return None


def _check_type_field(value: object, allowed_types: Collection[str] | None) -> str | None:
    problem = _check_nonempty_string(value)
    if problem is not None:
        return problem
    if allowed_types is None:
        return None
    allowed_lower = {item.lower() for item in allowed_types}
    if str(value).lower() in allowed_lower:
        return None
    return f"must be one of {'|'.join(sorted(allowed_types))}, got {value!r}"

"""OKF log.md parsing, normalization, and native write path."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from re import Match

from knotica.core.records import LOG_ENTRY_RE

_OKF_DATE_HEADING_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$")
_OKF_ENTRY_RE = re.compile(r"^\* \*\*(?P<kind>[^*]+)\*\*: (?P<body>.+)$")
_KNOTICA_LOG_ENTRY_RE = LOG_ENTRY_RE
_BULLET_RE = re.compile(r"^- (?P<path>.+)$")
_PATH_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_OP_BODY_RE = re.compile(r"^(?P<op>[a-z_]+)\s*·\s*(?P<topic>.+?)\s*—\s*(?P<title>.+)\s*$")
_REPAIR_OPS = frozenset({"migrate", "guillotine", "repair", "okf"})


@dataclass(frozen=True)
class OkfLogEntry:
    """One OKF-style log bullet under a date heading."""

    date: str
    kind: str
    body: str
    touched_paths: tuple[str, ...] = ()
    source_format: str = "okf"  # okf | knotica


@dataclass(frozen=True)
class LogNormalization:
    """Result of normalizing a log.md file to OKF shape."""

    content: str
    warnings: tuple[str, ...]
    entries_parsed: int


def parse_log_entries(text: str) -> list[OkfLogEntry]:
    """Parse OKF date-grouped and legacy Knotica log entries (document order, newest first)."""
    entries = _parse_log_entries(text, recover_unclosed_fence=False)
    if not entries and "```" in text:
        entries = _parse_log_entries(text, recover_unclosed_fence=True)
    return entries


def _parse_log_entries(text: str, *, recover_unclosed_fence: bool) -> list[OkfLogEntry]:
    """Internal parser with optional recovery for a dangling preamble fence."""
    entries: list[OkfLogEntry] = []
    current_date: str | None = None
    pending_knotica: Match[str] | None = None
    pending_paths: list[str] = []

    def flush_knotica() -> None:
        nonlocal pending_knotica, pending_paths
        if pending_knotica is None:
            return
        entries.append(
            OkfLogEntry(
                date=pending_knotica.group("date"),
                kind=pending_knotica.group("op"),
                body=f"{pending_knotica.group('topic')} — {pending_knotica.group('title')}",
                touched_paths=tuple(pending_paths),
                source_format="knotica",
            )
        )
        pending_knotica = None
        pending_paths = []

    in_fence = False
    unclosed_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if line.lstrip().startswith("```"):
            if in_fence:
                in_fence = False
                unclosed_fence = False
            else:
                in_fence = True
                unclosed_fence = recover_unclosed_fence
            continue
        okf_date = _OKF_DATE_HEADING_RE.match(stripped)
        if okf_date and unclosed_fence:
            in_fence = False
            unclosed_fence = False
        if okf_date:
            flush_knotica()
            current_date = okf_date.group(1)
            continue
        if in_fence:
            continue
        okf_entry = _OKF_ENTRY_RE.match(line.strip())
        if okf_entry and current_date:
            flush_knotica()
            body = okf_entry.group("body").strip()
            entries.append(
                OkfLogEntry(
                    date=current_date,
                    kind=okf_entry.group("kind").strip(),
                    body=body,
                    touched_paths=_paths_from_body(body),
                    source_format="okf",
                )
            )
            continue
        knotica = _KNOTICA_LOG_ENTRY_RE.match(line.strip())
        if knotica:
            flush_knotica()
            pending_knotica = knotica
            continue
        bullet = _BULLET_RE.match(line.strip())
        if bullet and pending_knotica is not None:
            pending_paths.append(bullet.group("path").strip())
            continue
        if line.strip() == "":
            flush_knotica()

    flush_knotica()
    return entries


def _log_wikilink_paths(paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Paths safe to emit as wikilinks in ``log.md`` (concept pages only)."""
    return tuple(
        path
        for path in dict.fromkeys(paths)
        if path.endswith(".md") and not path.startswith(".") and "/." not in path
    )


def format_operation_log_entry(
    *,
    entry_date: str,
    op: str,
    topic: str,
    title: str,
    pages: tuple[str, ...] = (),
) -> OkfLogEntry:
    """Build one native OKF log bullet for a mutating Knotica operation."""
    kind = _map_operation_kind(op)
    body_core = f"{op} · {topic.strip()} — {title.strip()}"
    linkable = _log_wikilink_paths(pages)
    if linkable:
        links = ", ".join(f"[[{_wikilink_ref(path)}]]" for path in linkable)
        body = f"{body_core} ({links})"
    else:
        body = body_core
    return OkfLogEntry(
        date=entry_date,
        kind=kind,
        body=body,
        touched_paths=pages,
        source_format="okf",
    )


def prepend_operation_log(
    text: str,
    *,
    entry_date: str,
    op: str,
    topic: str,
    title: str,
    pages: tuple[str, ...] = (),
) -> str:
    """Insert a new operation entry at the top of ``log.md`` (newest first)."""
    new_entry = format_operation_log_entry(
        entry_date=entry_date,
        op=op,
        topic=topic,
        title=title,
        pages=pages,
    )
    new_md_paths = set(_log_wikilink_paths(pages))
    kept: list[OkfLogEntry] = []
    for entry in parse_log_entries(text):
        entry_paths = set(_log_wikilink_paths(entry.touched_paths or _paths_from_body(entry.body)))
        if new_md_paths and entry_paths & new_md_paths:
            continue
        kept.append(entry)
    entries = [new_entry, *kept]
    return _render_okf_log(text, entries)


def canonicalize_log(text: str) -> str:
    """Re-render log entries with a standard OKF preamble and fixed fences."""
    entries = parse_log_entries(text)
    if not entries:
        return text
    preamble = (
        "# Directory Update Log\n\n"
        "Append-only log of vault operations, newest first: one entry per mutating "
        "operation, written in the same commit as the operation itself. Native shape "
        "follows [[SCHEMA]] §3."
    )
    return _render_okf_log(preamble, entries)


def normalize_log_to_okf(text: str) -> LogNormalization:
    """Convert log content to OKF date-grouped format, newest first."""
    warnings: list[str] = []
    entries = parse_log_entries(text)
    if not entries:
        return LogNormalization(content=text, warnings=tuple(warnings), entries_parsed=0)

    if any(entry.source_format == "knotica" for entry in entries):
        warnings.append("converted legacy Knotica operation headings to OKF date groups")

    content = _render_okf_log(text, entries)
    return LogNormalization(content=content, warnings=tuple(warnings), entries_parsed=len(entries))


def iter_log_touched_paths(text: str) -> list[tuple[int, str, str]]:
    """Yield ``(line_number, entry_topic, touched_path)`` for every touched path."""
    rows = _iter_log_touched_paths(text, recover_unclosed_fence=False)
    if not rows and "```" in text:
        rows = _iter_log_touched_paths(text, recover_unclosed_fence=True)
    return rows


def _iter_log_touched_paths(
    text: str, *, recover_unclosed_fence: bool
) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    current_date: str | None = None
    current_topic: str | None = None
    pending_knotica: Match[str] | None = None
    in_fence = False
    unclosed_fence = False

    def topic_from_body(body: str) -> str | None:
        core = _body_without_links(body)
        match = _OP_BODY_RE.match(core)
        if match:
            return match.group("topic").strip()
        if " — " in core:
            return core.split(" — ", 1)[0].strip()
        return None

    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if line.lstrip().startswith("```"):
            if in_fence:
                in_fence = False
                unclosed_fence = False
            else:
                in_fence = True
                unclosed_fence = recover_unclosed_fence
            continue
        okf_date = _OKF_DATE_HEADING_RE.match(stripped)
        if okf_date and unclosed_fence:
            in_fence = False
            unclosed_fence = False
        if okf_date:
            current_date = okf_date.group(1)
            pending_knotica = None
            continue
        if in_fence:
            continue
        okf_entry = _OKF_ENTRY_RE.match(line.strip())
        if okf_entry and current_date:
            body = okf_entry.group("body").strip()
            current_topic = topic_from_body(body)
            for path in _log_wikilink_paths(_paths_from_body(body)):
                if current_topic:
                    rows.append((line_number, current_topic, path))
            continue
        knotica = _KNOTICA_LOG_ENTRY_RE.match(line.strip())
        if knotica:
            pending_knotica = knotica
            current_topic = knotica.group("topic").strip()
            continue
        bullet = _BULLET_RE.match(line.strip())
        if bullet and pending_knotica is not None and current_topic:
            rows.append((line_number, current_topic, bullet.group("path").strip()))

    return rows


def check_log_shape(text: str) -> list[str]:
    """Return warnings for non-OKF log structure."""
    warnings: list[str] = []
    if _KNOTICA_LOG_ENTRY_RE.search(text):
        warnings.append("log contains legacy Knotica operation headings")
    legacy_bracket_dates = re.findall(r"^## \[\d{4}-\d{2}-\d{2}\]", text, flags=re.MULTILINE)
    if legacy_bracket_dates:
        warnings.append("log uses old bracketed date headings")
    dates = [match.group(1) for match in _OKF_DATE_HEADING_RE.finditer(text)]
    if len(dates) > 1:
        sorted_dates = sorted(dates, reverse=True)
        if dates != sorted_dates:
            warnings.append("OKF date headings are not newest-first")
    return warnings


def okf_entry_to_knotica_fields(entry: OkfLogEntry) -> tuple[str, str, str, str, tuple[str, ...]]:
    """Map one parsed log entry to Knotica ``(date, op, topic, title, pages)``."""
    pages = entry.touched_paths or _paths_from_body(entry.body)
    core_body = _body_without_links(entry.body)

    if entry.source_format == "knotica":
        topic, _, title = core_body.partition(" — ")
        return entry.date, entry.kind, topic.strip(), title.strip(), pages

    match = _OP_BODY_RE.match(core_body)
    if match:
        return (
            entry.date,
            match.group("op"),
            match.group("topic").strip(),
            match.group("title").strip(),
            pages,
        )

    if " — " in core_body:
        topic, title = core_body.split(" — ", 1)
        op = _kind_to_op(entry.kind)
        return entry.date, op, topic.strip(), title.strip(), pages

    return entry.date, _kind_to_op(entry.kind), "", core_body.strip(), pages


def _render_okf_log(text: str, entries: list[OkfLogEntry]) -> str:
    preamble = _extract_preamble(text)

    by_date: dict[str, list[OkfLogEntry]] = {}
    for entry in entries:
        by_date.setdefault(entry.date, []).append(entry)

    lines: list[str] = []
    if preamble:
        lines.append(preamble)
        lines.append("")
    elif not lines:
        lines.append("# Directory Update Log")
        lines.append("")

    for day in sorted(by_date.keys(), reverse=True):
        lines.append(f"## {day}")
        for entry in by_date[day]:
            kind = _map_operation_kind(entry.kind)
            body = _render_entry_body(entry)
            lines.append(f"* **{kind}**: {body}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _extract_preamble(text: str) -> str:
    """Return log preamble lines before the first real date heading."""
    preamble_lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            preamble_lines.append(line)
            continue
        if not in_fence and _OKF_DATE_HEADING_RE.match(line.strip()):
            break
        if not in_fence and _KNOTICA_LOG_ENTRY_RE.match(line.strip()):
            break
        preamble_lines.append(line)
    return "\n".join(preamble_lines).rstrip()


def _render_entry_body(entry: OkfLogEntry) -> str:
    paths = entry.touched_paths or _paths_from_body(entry.body)
    core = _body_without_links(entry.body)
    if not paths:
        return entry.body
    linkable = _log_wikilink_paths(paths)
    if not linkable:
        return core
    links = ", ".join(f"[[{_wikilink_ref(path)}]]" for path in linkable)
    if _OP_BODY_RE.match(core):
        return f"{core} ({links})"
    if " — " in core:
        return f"{core} ({links})"
    return f"{entry.body} ({links})"


def _paths_from_body(body: str) -> tuple[str, ...]:
    wikilinks = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", body)
    if wikilinks:
        return tuple(_vault_path(ref) for ref in wikilinks)
    return tuple(match.group(2) for match in _PATH_LINK_RE.finditer(body))


def _wikilink_ref(path: str) -> str:
    """Render a vault path as a wikilink target (omit ``.md``; resolver adds it)."""
    return path[:-3] if path.endswith(".md") else path


def _vault_path(ref: str) -> str:
    """Normalize a wikilink target to a vault-relative ``.md`` path for existence checks."""
    return ref if ref.endswith(".md") else f"{ref}.md"


def _body_without_links(body: str) -> str:
    """Strip trailing wikilink or Markdown link parenthetical groups from a log bullet body."""
    core = body.strip()
    while True:
        if core.endswith(")") and " (" in core:
            inner = core[core.rfind(" (") + 2 : -1]
            if _PATH_LINK_RE.search(inner) or "[[" in inner:
                core = core[: core.rfind(" (")].strip()
                continue
        if core.endswith("]]") and " ([[" in core:
            core = core[: core.rfind(" (")].strip()
            continue
        break
    return core


def _map_operation_kind(op: str) -> str:
    if op in _REPAIR_OPS:
        return "Repair"
    if op in {"Update", "Repair"}:
        return op
    return "Update"


def _kind_to_op(kind: str) -> str:
    if kind in {
        "write_page",
        "store_source",
        "create_topic",
        "curate_example",
        "migrate",
        "guillotine",
        "repair",
        "okf",
    }:
        return kind
    if kind == "Repair":
        return "repair"
    return "write_page"

"""``knotica status`` -- deterministic vault counts (the flywheel progress bar).

Reports, per topic, the counts that show self-improvement momentum: content
pages, curated examples, and how many more curated examples the topic needs to
reach the DSPy compile-ready floor. Vault-wide it reports the totals, the last
recorded lint, and the count of unpushed commits. Every number is a pure read
over the resolved vault -- **no LLM**, no mutation, config resolved per call.

Output discipline (``cli.common``): the table (or ``--json``) is the payload on
stdout; messages go to stderr. Exit ``0`` on success, ``3`` when the vault is
unconfigured (mirrors the tool ``NOT_CONFIGURED`` contract). ``--topic`` scopes
to one topic; ``--wide`` renders full width, ignoring ``$COLUMNS``.
"""

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from knotica.cli.common import (
    EXIT_NOT_CONFIGURED,
    EXIT_SUCCESS,
    Console,
    common_parent,
    console_from_args,
    unconfigured,
)
from knotica.core.config import ConfigState, diagnose
from knotica.core.lint import LOG_PATH, RESERVED_TOP_LEVEL_NAMES
from knotica.core.links import iter_page_paths
from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.records import RecordParseError, parse_log_entries, parse_qa_jsonl
from knotica.core.schema import overlay_path
from knotica.core.vcs import GitError, VaultVcs
from knotica.store import LocalFSStore, VaultStore

__all__ = ["COMPILE_READY_MIN_EXAMPLES", "STATUS_JSON_SCHEMA_VERSION", "configure", "run"]

#: Stable version of the ``--json`` envelope (consumers branch on this).
STATUS_JSON_SCHEMA_VERSION = 1

#: Curated-example floor before a topic's ``qa.jsonl`` can seed a DSPy compile.
#: Deterministic single source of truth for the "N, M to compile-ready" readout;
#: no upstream constant defined this, so status owns it (see LEARNINGS).
COMPILE_READY_MIN_EXAMPLES = 20

#: Log ops that count as a lint run for the "last lint" readout.
_LINT_OPS = frozenset({"lint", "lint_check"})

#: Terminal width fallback when ``$COLUMNS`` is unset or unparseable.
_DEFAULT_COLUMNS = 80


@dataclass(frozen=True, slots=True)
class TopicCounts:
    """Per-topic progress numbers."""

    topic: str
    pages: int
    curated: int

    @property
    def to_compile_ready(self) -> int:
        """Curated examples still needed to reach the compile-ready floor."""
        return max(0, COMPILE_READY_MIN_EXAMPLES - self.curated)


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``status`` subcommand and its flags."""
    parser = subparsers.add_parser(
        "status",
        parents=[common_parent()],
        help="report deterministic vault counts",
        description="Report deterministic counts (pages, curated, unpushed) as a table or JSON.",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--topic", metavar="NAME", help="scope counts to one topic")
    parser.add_argument("--wide", action="store_true", help="show full output, ignoring $COLUMNS")
    return parser


def run(args: argparse.Namespace) -> int:
    """Resolve config fresh, gather counts, render, and return the exit code."""
    console = console_from_args(args)
    diagnosis = diagnose()
    if diagnosis.vault is None:
        return _report_unconfigured(
            console, diagnosis.state, diagnosis.detail, diagnosis.remediation
        )

    vault = diagnosis.vault
    store = LocalFSStore(vault.path)
    topics = _topic_counts(store, scope=args.topic)
    last_lint = _last_lint(store)
    unpushed = _unpushed(vault.path)

    if args.json:
        console.data(_json_payload(vault.path, topics, last_lint, unpushed))
    else:
        _render_table(console, vault.path, topics, last_lint, unpushed, args.wide)
    return EXIT_SUCCESS


def _report_unconfigured(
    console: Console, state: ConfigState, detail: str, remediation: str
) -> int:
    """Non-READY states all exit ``3``; render the state-specific remediation."""
    if state == ConfigState.UNCONFIGURED:
        return unconfigured(console)
    console.error(detail)
    if remediation:
        console.error(f"To fix: {remediation}")
    return EXIT_NOT_CONFIGURED


def _topic_counts(store: VaultStore, *, scope: str | None) -> list[TopicCounts]:
    """Gather per-topic page and curated-example counts (optionally one topic)."""
    topics = [scope] if scope else _topic_directories(store)
    return [
        TopicCounts(
            topic=topic, pages=_page_count(store, topic), curated=_curated_count(store, topic)
        )
        for topic in topics
    ]


def _topic_directories(store: VaultStore) -> list[str]:
    """Visible top-level directories that are topics (reserved names excluded)."""
    topics: list[str] = []
    for name in sorted(store.list_dir("")):
        if name.startswith(".") or name in RESERVED_TOP_LEVEL_NAMES:
            continue
        try:
            store.list_dir(name)
        except NotADirectoryError:
            continue  # a top-level file, not a topic directory
        topics.append(name)
    return topics


def _page_count(store: VaultStore, topic: str) -> int:
    """Count content pages under ``topic`` (its schema overlay is not a page)."""
    overlay = overlay_path(topic)
    try:
        return sum(1 for path in iter_page_paths(store, topic) if path != overlay)
    except NotADirectoryError:
        return 0


def _curated_count(store: VaultStore, topic: str) -> int:
    """Count curated examples in the topic's ``qa.jsonl`` (0 when absent)."""
    dataset = qa_dataset_path(topic)
    if not store.exists(dataset):
        return 0
    text = store.read_text(dataset)
    try:
        return len(parse_qa_jsonl(text))
    except RecordParseError:
        return sum(1 for line in text.splitlines() if line.strip())


def _last_lint(store: VaultStore) -> str | None:
    """Return the latest recorded lint date from ``log.md``, or ``None``."""
    if not store.exists(LOG_PATH):
        return None
    try:
        entries = parse_log_entries(store.read_text(LOG_PATH))
    except ValueError:
        return None
    lint_dates = [entry.date for entry in entries if entry.op in _LINT_OPS]
    return max(lint_dates) if lint_dates else None


def _unpushed(vault_path: Path) -> int | None:
    """Read-only count of commits ahead of the upstream (``None`` if no remote)."""
    try:
        return VaultVcs(vault_path).unpushed_count()
    except GitError:
        return None


def _json_payload(
    vault_path: Path, topics: list[TopicCounts], last_lint: str | None, unpushed: int | None
) -> str:
    """Build the stable ``--json`` envelope for the status readout."""
    import json

    payload = {
        "schema_version": STATUS_JSON_SCHEMA_VERSION,
        "vault": str(vault_path),
        "compile_ready_threshold": COMPILE_READY_MIN_EXAMPLES,
        "topics": [
            {
                "topic": t.topic,
                "pages": t.pages,
                "curated": t.curated,
                "to_compile_ready": t.to_compile_ready,
            }
            for t in topics
        ],
        "totals": {
            "topics": len(topics),
            "pages": sum(t.pages for t in topics),
            "curated": sum(t.curated for t in topics),
        },
        "last_lint": last_lint,
        "unpushed": unpushed,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _render_table(
    console: Console,
    vault_path: Path,
    topics: list[TopicCounts],
    last_lint: str | None,
    unpushed: int | None,
    wide: bool,
) -> None:
    """Print the counts table to stdout, truncating topic names to ``$COLUMNS``."""
    console.data(f"knotica status    vault: {vault_path}")
    console.data("")
    if not topics:
        console.data("no topics yet — create one with the create_topic tool")
    else:
        _render_rows(console, topics, wide)
    console.data("")
    _render_footer(console, topics, last_lint, unpushed)


def _render_rows(console: Console, topics: list[TopicCounts], wide: bool) -> None:
    """Render the aligned per-topic rows with a header."""
    fixed = len("  ") + len("  pages") + len("  curated") + len("  to-compile")
    budget = max(8, _columns(wide) - fixed)
    name_width = min(budget, max(len("topic"), *(len(t.topic) for t in topics)))
    header = f"  {'topic'.ljust(name_width)}  pages  curated  to-compile"
    console.data(header)
    for topic in topics:
        name = _truncate(topic.topic, name_width)
        console.data(
            f"  {name.ljust(name_width)}  {topic.pages:>5}  {topic.curated:>7}"
            f"  {topic.to_compile_ready:>10}"
        )


def _render_footer(
    console: Console, topics: list[TopicCounts], last_lint: str | None, unpushed: int | None
) -> None:
    """Render the vault-wide totals and the lint/remote status lines."""
    total_pages = sum(t.pages for t in topics)
    total_curated = sum(t.curated for t in topics)
    console.data(
        f"{len(topics)} topic(s), {total_pages} page(s), {total_curated} curated example(s)"
    )
    console.data(f"last lint: {last_lint or 'never'}")
    if unpushed is None:
        console.data("unpushed commits: n/a (no upstream configured)")
    else:
        console.data(f"unpushed commits: {unpushed}")


def _columns(wide: bool) -> int:
    """Resolve the render width from ``$COLUMNS`` (ignored under ``--wide``)."""
    if wide:
        return 10_000
    raw = os.environ.get("COLUMNS", "")
    try:
        return max(_DEFAULT_COLUMNS, int(raw)) if raw else _DEFAULT_COLUMNS
    except ValueError:
        return _DEFAULT_COLUMNS


def _truncate(text: str, width: int) -> str:
    """Truncate ``text`` to ``width`` with an ellipsis when it overflows."""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"

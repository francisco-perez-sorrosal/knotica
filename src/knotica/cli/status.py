"""``knotica status`` -- deterministic vault counts (the flywheel progress bar).

Reports, per topic, the counts that show self-improvement momentum: content
pages, curated examples, and how many more curated examples the topic needs to
reach the DSPy compile-ready floor. Vault-wide it reports the totals, the last
recorded lint, and the count of unpushed commits. Every number is a pure read
over the resolved vault -- **no LLM**, no mutation, config resolved per call.

Aggregation lives in :mod:`knotica.core.status` (shared with the ``wiki_status``
MCP tool); this module is the CLI adapter that renders the table / ``--json``.

Output discipline (``cli.common``): the table (or ``--json``) is the payload on
stdout; messages go to stderr. Exit ``0`` on success, ``3`` when the vault is
unconfigured (mirrors the tool ``NOT_CONFIGURED`` contract). ``--topic`` scopes
to one topic; ``--wide`` renders full width, ignoring ``$COLUMNS``.
"""

import argparse
import json
import os

from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_NOT_CONFIGURED,
    EXIT_SUCCESS,
    Console,
    common_parent,
    console_from_args,
    unconfigured,
)
from knotica.core.config import ConfigState, diagnose
from knotica.core.page import TopicNotFoundError
from knotica.core.status import (
    COMPILE_READY_MIN_EXAMPLES,
    STATUS_SCHEMA_VERSION,
    gather_wiki_status,
)
from knotica.store import LocalFSStore

__all__ = ["COMPILE_READY_MIN_EXAMPLES", "STATUS_JSON_SCHEMA_VERSION", "configure", "run"]

#: Alias kept for CLI tests / external consumers of the previous name.
STATUS_JSON_SCHEMA_VERSION = STATUS_SCHEMA_VERSION

#: Terminal width fallback when ``$COLUMNS`` is unset or unparseable.
_DEFAULT_COLUMNS = 80


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
    parser.add_argument(
        "--nudge",
        action="store_true",
        help="emit a plain-text SessionStart nudge (topics + non-zero attention items)",
    )
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
    try:
        payload = gather_wiki_status(store, vault.path, topic=args.topic or "")
    except TopicNotFoundError as error:
        console.error(str(error))
        return EXIT_ERROR

    if args.nudge:
        _render_nudge(console, payload)
    elif args.json:
        console.data(_cli_json(payload))
    else:
        _render_table(console, payload, args.wide)
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


def _cli_json(payload: dict) -> str:
    """CLI ``--json`` keeps the status-v1 field set (no gate/loop extras required)."""
    cli_payload = {
        "schema_version": payload["schema_version"],
        "vault": payload["vault"],
        "compile_ready_threshold": payload["compile_ready_threshold"],
        "topics": [
            {
                "topic": t["topic"],
                "pages": t["pages"],
                "curated": t["curated"],
                "to_compile_ready": t["to_compile_ready"],
            }
            for t in payload["topics"]
        ],
        "totals": {
            "topics": payload["totals"]["topics"],
            "pages": payload["totals"]["pages"],
            "curated": payload["totals"]["curated"],
        },
        "last_lint": payload["last_lint"],
        "unpushed": payload["unpushed"],
    }
    return json.dumps(cli_payload, ensure_ascii=False, indent=2)


def _render_nudge(console: Console, payload: dict) -> None:
    """Print the SessionStart nudge: topic list, then attention items if any.

    Reuses the already-assembled ``topics[].suggestions``/``compile_ready``
    fields from ``payload`` (the default ``summary`` view) -- no new
    aggregation, just a plain-text rendering for the hook to echo verbatim.
    Silent (prints nothing) when there are no topics and nothing needs
    attention, mirroring the other renderers' honest-empty-state discipline.
    """
    topics = payload["topics"]
    names = [t["topic"] for t in topics]
    if names:
        console.data(f"This vault covers topics: {', '.join(names)}")

    pending = sum(t["suggestions"]["pending"] for t in topics)
    refused = sum(t["suggestions"]["refused_awaiting_rework"] for t in topics)
    compile_ready = sum(1 for t in topics if t["compile_ready"])
    items = []
    if pending:
        items.append(f"{pending} pending suggestion(s)")
    if refused:
        items.append(f"{refused} refused-awaiting-rework")
    if compile_ready:
        items.append(f"{compile_ready} topic(s) compile-ready")
    if items:
        console.data("Needs attention: " + ", ".join(items))


def _render_table(console: Console, payload: dict, wide: bool) -> None:
    """Print the counts table to stdout, truncating topic names to ``$COLUMNS``."""
    vault_path = payload["vault"]
    topics = payload["topics"]
    console.data(f"knotica status    vault: {vault_path}")
    console.data("")
    if not topics:
        console.data("no topics yet — create one with the create_topic tool")
    else:
        _render_rows(console, topics, wide)
    console.data("")
    _render_footer(console, payload)


def _render_rows(console: Console, topics: list[dict], wide: bool) -> None:
    """Render the aligned per-topic rows with a header."""
    fixed = len("  ") + len("  pages") + len("  curated") + len("  to-compile")
    budget = max(8, _columns(wide) - fixed)
    name_width = min(budget, max(len("topic"), *(len(t["topic"]) for t in topics)))
    header = f"  {'topic'.ljust(name_width)}  pages  curated  to-compile"
    console.data(header)
    for topic in topics:
        name = _truncate(topic["topic"], name_width)
        console.data(
            f"  {name.ljust(name_width)}  {topic['pages']:>5}  {topic['curated']:>7}"
            f"  {topic['to_compile_ready']:>10}"
        )


def _render_footer(console: Console, payload: dict) -> None:
    """Render the vault-wide totals and the lint/remote status lines."""
    totals = payload["totals"]
    last_lint = payload["last_lint"]
    unpushed = payload["unpushed"]
    console.data(
        f"{totals['topics']} topic(s), {totals['pages']} page(s), "
        f"{totals['curated']} curated example(s)"
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

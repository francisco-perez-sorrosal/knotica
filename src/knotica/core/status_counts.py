"""Per-topic record counts — the substrate every status view reads from.

Four small readers that answer one question each: how many records of a given
kind does this topic have? They are pulled out of :mod:`knotica.core.status`
because **both** of its per-topic views need them and neither owns them --
``view="summary"`` renders all four, and ``view="attention"`` reads the
suggestion and gap counts under a much tighter budget. Leaving them inside one
view invites the other to grow its own copy.

Every reader here is a *small file read* with no git subprocess, no lock and no
mutation, which is what lets the attention view (dec-092) call them at all. Each
one is also corruption-tolerant in the same way: a malformed record is skipped,
never raised, because a status readout that dies on one bad line is worse than
one that under-reports by a line. Honest zeros when a file is absent.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from knotica.core.gap_classifier import gaps_path
from knotica.core.links import iter_page_paths
from knotica.core.records import (
    GAP_ORIGIN_MEASURED,
    GAP_ORIGIN_REPORTED,
    GAP_ORIGIN_RETRACTED,
    GapRecord,
    RecordParseError,
)
from knotica.core.schema import overlay_path
from knotica.core.status_lanes import is_refused, read_suggestion_records
from knotica.evals.golden import GoldenSetMissingError, load as load_golden
from knotica.store import VaultStore

__all__ = ["gap_block", "golden_count", "page_count", "suggestion_block"]


def suggestion_block(store: VaultStore, topic: str) -> dict[str, Any]:
    """The per-topic gap-fill queue summary for the ingest handoff (all-zero when empty).

    Counts each lifecycle status and surfaces ``approved_awaiting_ingest`` (the
    approved-but-not-yet-ingested backlog that matters for the interactive
    ingest handoff), ``refused_awaiting_rework`` (approved records whose most
    recent gate pass was refused -- still re-workable, not yet re-submitted),
    plus the newest ``proposed_at``. A single corrupt record never breaks the
    status readout (mirrors :func:`golden_count`).
    """
    counts = Counter[str]()
    refused_awaiting_rework = 0
    newest: str | None = None
    for record in read_suggestion_records(store, topic):
        counts[record.status] += 1
        if newest is None or record.proposed_at > newest:
            newest = record.proposed_at
        if record.status == "approved" and is_refused(record):
            refused_awaiting_rework += 1
    return {
        # Every suggestion ever recorded for the topic, whatever its status.
        # Free -- the counter already holds it -- and it is what lets a client
        # tell "discovery has never run here" apart from "discovery ran and
        # everything it proposed has already been dealt with". Those two look
        # identical through the per-status counts alone.
        "total": counts.total(),
        "pending": counts.get("pending", 0),
        "approved_awaiting_ingest": counts.get("approved", 0),
        "deferred": counts.get("deferred", 0),
        "rejected": counts.get("rejected", 0),
        "ingested": counts.get("ingested", 0),
        "newest_proposed_at": newest,
        "refused_awaiting_rework": refused_awaiting_rework,
    }


def gap_block(store: VaultStore, topic: str) -> dict[str, Any]:
    """The per-topic open-gap summary by provenance origin (all-zero when empty).

    Counts every *open* gap record (any fault_class) bucketed by ``origin``
    (``measured`` = eval-proven, ``reported`` = conversationally filed,
    ``retracted`` = guillotine-weakened) plus ``open_total``. Reads
    ``gaps.jsonl`` line-by-line and skips a malformed line rather than raising,
    so a single corrupt record never breaks the status readout (mirrors
    :func:`suggestion_block`). Honest zeros when the file is absent.
    """
    counts = Counter[str]()
    open_total = 0
    path = gaps_path(topic)
    if store.exists(path):
        for line in store.read_text(path).splitlines():
            if not line.strip():
                continue
            try:
                record = GapRecord.from_json_line(line)
            except (ValueError, RecordParseError):
                continue
            if record.status != "open":
                continue
            counts[record.origin] += 1
            open_total += 1
    return {
        GAP_ORIGIN_MEASURED: counts.get(GAP_ORIGIN_MEASURED, 0),
        GAP_ORIGIN_REPORTED: counts.get(GAP_ORIGIN_REPORTED, 0),
        GAP_ORIGIN_RETRACTED: counts.get(GAP_ORIGIN_RETRACTED, 0),
        "open_total": open_total,
    }


def golden_count(store: VaultStore, topic: str) -> int:
    try:
        return len(load_golden(store, topic))
    except GoldenSetMissingError:
        return 0
    except Exception:  # noqa: BLE001 — status stays readable on corrupt golden
        return 0


def page_count(store: VaultStore, topic: str) -> int:
    """Count content pages under ``topic`` (its schema overlay is not a page)."""
    overlay = overlay_path(topic)
    try:
        return sum(1 for path in iter_page_paths(store, topic) if path != overlay)
    except NotADirectoryError:
        return 0

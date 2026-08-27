"""``knotica learn`` -- the lane that turns sources into wiki pages.

Learn carries no CLI verbs, and that is a property of the design rather than a
gap: every stage on its rail is client-as-brain work -- the model reads a
source and writes the pages -- so there is nothing deterministic for an
adapter to execute. Rendering the lane as present-and-delegating is more
honest than omitting it, and it makes the handoff visible at the CLI.

Exits ``EXIT_SUCCESS`` with one line of guidance on stdout. Asking where a
lane's commands live is a question, not misuse, so it is never ``EXIT_MISUSE``.
"""

from __future__ import annotations

import argparse

from knotica.cli.common import EXIT_SUCCESS, LaneCommand, console_from_args, lane_rail

__all__ = ["configure", "run"]

_LANE = LaneCommand(lane="learn", summary="turn sources into wiki pages (client-driven)")

_GUIDANCE = (
    "Learn is a conversational protocol — run `/knotica:ingest` in Claude, "
    "or `knotica prompt ingest` to read its steps."
)

configure = _LANE.configure


def run(args: argparse.Namespace) -> int:
    """Point at the conversational surface that actually runs this lane."""
    console = console_from_args(args)
    console.data(_GUIDANCE)
    console.info(f"Rail: {lane_rail(_LANE.lane)}")
    return EXIT_SUCCESS

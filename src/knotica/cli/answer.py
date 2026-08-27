"""``knotica answer`` -- the lane that answers from the wiki, with citations.

Like Learn, Answer carries no CLI verbs: asking a question and grounding the
reply in cited pages is client-as-brain work, so there is nothing
deterministic for an adapter to execute. The lane is rendered as
present-and-delegating rather than omitted, so the handoff is visible at the
CLI instead of looking like a missing feature.

Exits ``EXIT_SUCCESS`` with one line of guidance on stdout -- never
``EXIT_MISUSE``, which would call the question itself a mistake.
"""

from __future__ import annotations

import argparse

from knotica.cli.common import EXIT_SUCCESS, LaneCommand, console_from_args, lane_rail

__all__ = ["configure", "run"]

_LANE = LaneCommand(lane="answer", summary="ask the wiki and get cited answers (client-driven)")

_GUIDANCE = (
    "Answer is a conversational protocol — run `/knotica:query` in Claude, "
    "or `knotica prompt query` to read its steps."
)

configure = _LANE.configure


def run(args: argparse.Namespace) -> int:
    """Point at the conversational surface that actually runs this lane."""
    console = console_from_args(args)
    console.data(_GUIDANCE)
    console.info(f"Rail: {lane_rail(_LANE.lane)}")
    return EXIT_SUCCESS

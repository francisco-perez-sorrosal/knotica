"""``knotica fill`` -- close diagnosed knowledge gaps with discovered sources.

The lane parser for Fill. Most of the lane's rail is client-driven (approving
a candidate and ingesting it happen in conversation); the one deterministic
verb the CLI can execute is source discovery, which ``gapfill`` registers here
as ``discover`` -- its own group level dropped, since the verb is already
unique within the lane.
"""

from __future__ import annotations

from knotica.cli.common import LaneCommand

__all__ = ["configure", "run"]

_LANE = LaneCommand(
    lane="fill",
    summary="close diagnosed knowledge gaps with discovered sources",
    members=("gapfill",),
)

configure = _LANE.configure
run = _LANE.run

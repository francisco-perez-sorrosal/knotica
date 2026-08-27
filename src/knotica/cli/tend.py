"""``knotica tend`` -- vault upkeep: health, format, schema, retraction.

The lane parser for Tend. Its four member modules keep their own group levels
where flattening would be ambiguous: ``repair`` means two different things
under health and format conformance, and the format verbs (``check``,
``export``) are too generic to stand alone at lane level. Both sites are
low-frequency, so the extra level is cheap there; everywhere else the verb is
unique and the group level is gone.

Unlike the other rails, Tend's stages are independent peers rather than an
ordered sequence -- the process model says so, and the lane's help renders it
that way.
"""

from __future__ import annotations

from knotica.cli.common import LaneCommand

__all__ = ["configure", "run"]

_LANE = LaneCommand(
    lane="tend",
    summary="vault upkeep: health checks, format conformance, schema updates",
    members=("doctor", "okf", "migrate", "guillotine"),
)

configure = _LANE.configure
run = _LANE.run

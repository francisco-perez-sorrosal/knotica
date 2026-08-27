"""``knotica improve`` -- measure a topic, optimize its program, promote it.

The lane parser for Improve. It owns no behavior: the four member modules
register their own parsers one level deeper, unchanged, and this module only
names the lane and routes to them.

Six leaves land here, from four modules -- ``compile`` contributes both
``compile`` and ``promote``, and ``datasets`` contributes ``bootstrap-train``
and ``freeze``. Those two modules drop their own group level on the way in:
once the lane is named, ``datasets`` and ``compile`` add nothing a reader
could not already infer, and the verbs are unique within the lane.
"""

from __future__ import annotations

from knotica.cli.common import LaneCommand

__all__ = ["configure", "run"]

_LANE = LaneCommand(
    lane="improve",
    summary="measure a topic, optimize its query program, promote the result",
    members=("eval", "loop", "compile", "datasets"),
)

configure = _LANE.configure
run = _LANE.run

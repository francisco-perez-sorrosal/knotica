"""Lane dispatcher ``learn`` -- turn a source into pages.

The action table, the call shape and the action list in this tool's description
are all generated from :data:`~knotica.core.process_model.LANE_MEMBERSHIP` by
:func:`~knotica.mcp_server.tools_dispatch_lane_common.register_lane_dispatcher`,
and each action routes to the same function object the flat tool of that name
registers -- so a verb's lane, its rail position and its call shape cannot
disagree with the declaration, and a lane call cannot drift from its flat
equivalent.
"""

from __future__ import annotations

from collections.abc import Mapping

from mcp.server.fastmcp import FastMCP

from knotica.mcp_server.tools_dispatch_lane_common import register_lane_dispatcher

__all__ = ["register_dispatch_learn_tools"]

_LANE = "learn"

#: Old action string -> the live one that replaced it. Empty: no action moved
#: when this lane was cut from the topical dispatchers, so there is nothing to
#: alias yet. See `tools_dispatch_lane_common`'s module docstring for the
#: mechanism this feeds -- a superseded call still reaches the live action's
#: handler and gets a `deprecation` note back, in the same turn.
SUPERSEDED_ACTIONS: Mapping[str, str] = {}

#: The lane's own prose, in the four-part shape every lane description takes:
#: what it does, ``Does NOT``, ``Requires``, ``Returns``. Named
#: ``*_DESCRIPTION`` so ``scripts/check_surface_consistency.py`` scans it --
#: the gate resolves every tool and action a description names, and a lane
#: purpose is the highest-traffic model-facing prose on the surface.
_PURPOSE_DESCRIPTION = (
    "Turn an outside source into wiki pages: open the topic, store the "
    "source's full text, write the pages it becomes, then curate an example "
    "from them. Rail: source, fetch / parse, pages, curate.\n"
    "Does NOT: fetch or convert the source, and does NOT compose the pages -- "
    "that cognition is yours, and this lane only persists what you pass and "
    "journals the progress. Does NOT close a gap the wiki already knows it has "
    "(that is `fill`).\n"
    "Requires: an explicit topic on every action; `learn action=create_topic` "
    "opens one that does not exist yet. `learn action=store_source` and `learn "
    "action=write_page` mutate the vault -- call them only after the user has "
    "explicitly confirmed the write, never from a detection pass.\n"
    "Returns: each action's own payload, unchanged from calling that verb "
    "directly -- the lane routes, it never reshapes a result."
)


def register_dispatch_learn_tools(mcp: FastMCP) -> None:
    """Register the ``learn`` lane dispatcher on ``mcp``."""
    register_lane_dispatcher(mcp, _LANE, _PURPOSE_DESCRIPTION)

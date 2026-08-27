"""Lane dispatcher ``fill`` -- work a knowledge gap through to a closed or quarantined source.

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

__all__ = ["register_dispatch_fill_tools"]

_LANE = "fill"

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
#: purpose is the highest-traffic model-facing prose on the surface. The billed
#: actions are spelled out in full rather than summarised as "billed,
#: two-phase": a model should hesitate at a name it can read, not at an
#: adjective.
_PURPOSE_DESCRIPTION = (
    "Close a knowledge gap with an outside source: read the open gaps, discover "
    "candidate sources, approve or reject one, ingest it inside a candidate "
    "session, then run the gate that merges or quarantines the result. Rail: "
    "gap, discover, approve, ingest, gate.\n"
    "Does NOT: write the source or the pages by itself -- you write them with "
    "`fill action=store_source` and `fill action=write_page`, passing the "
    "session's candidate handle each time. Does NOT tune the improvement loop "
    "(that is `improve`).\n"
    "Requires: an explicit topic; an open gap before `fill "
    "action=gapfill_discover`; an approved suggestion -- or a refused one "
    "being reworked -- before `fill action=source_ingest_open`; and that "
    "session's candidate handle on every write inside it. These actions SPEND MONEY and are two-phase: `fill "
    "action=gapfill_discover` and `fill action=loop loop_action=run_once`. "
    "Never pass confirm on the user's behalf -- the preview exists for them to "
    "approve first.\n"
    "Returns: each action's own payload, unchanged from calling that verb "
    "directly. A bare billed call spends nothing: it returns a preview plus a "
    "short-lived confirm_nonce, and only a second call passing that nonce as "
    "confirm executes."
)


def register_dispatch_fill_tools(mcp: FastMCP) -> None:
    """Register the ``fill`` lane dispatcher on ``mcp``."""
    register_lane_dispatcher(mcp, _LANE, _PURPOSE_DESCRIPTION)

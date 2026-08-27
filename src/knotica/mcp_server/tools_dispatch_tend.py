"""Lane dispatcher ``tend`` -- keep the vault itself healthy.

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

__all__ = ["register_dispatch_tend_tools"]

_LANE = "tend"

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
    "Keep the vault mechanically sound: run the health checks and repairs, "
    "check pages against the topic's schema, check and repair OKF conformance, "
    "and maintain the notes overlay as the prose beneath it moves. Independent "
    "checks, not a sequence -- each stands alone. Tend is mechanical and "
    "per-vault.\n"
    "Does NOT: make any measured claim about a topic's quality (that is "
    "`improve`), and does NOT migrate a schema overlay -- migration runs from "
    "the CLI (`knotica tend migrate`), not from here.\n"
    "Requires: a configured vault, and nothing else -- no prior stage and no "
    "watermark, since the checks are independent. The repairing and "
    "notes-mutating actions take mode=dry-run|apply and never fire from a "
    "detection pass: only after the user has explicitly confirmed the change.\n"
    "Returns: each action's own payload, unchanged from calling that verb "
    "directly -- a dry-run describes the change it would make without writing; "
    "only mode=apply commits."
)


def register_dispatch_tend_tools(mcp: FastMCP) -> None:
    """Register the ``tend`` lane dispatcher on ``mcp``."""
    register_lane_dispatcher(mcp, _LANE, _PURPOSE_DESCRIPTION)

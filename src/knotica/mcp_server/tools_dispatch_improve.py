"""Lane dispatcher ``improve`` -- measure a topic, then raise its bar.

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

__all__ = ["register_dispatch_improve_tools"]

_LANE = "improve"

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
#: actions are spelled out in full rather than summarised as "several are
#: billed": a model should hesitate at a name it can read, not at an adjective.
_PURPOSE_DESCRIPTION = (
    "Raise a topic's measured quality: instrument the sets the bar is measured "
    "on, observe the scalar history, gate the candidates, heal a refused gate "
    "by compiling a new prompt, promote the reviewed branch, then prove the "
    "change on a question. Improve is measured and per-topic. Rail: instrument, "
    "observe, gate, heal, promote, prove.\n"
    "Does NOT: repair the vault's mechanics or its schema conformance (that is "
    "`tend`), and does NOT bring in new source material (that is `learn`).\n"
    "Requires: an explicit topic, and a frozen held-out golden set before the "
    "gate can score anything. Two actions SPEND MONEY and are two-phase: "
    "`improve action=loop loop_action=run_eval` and `improve action=loop "
    "loop_action=run_once` -- a bare call spends nothing, returning a preview "
    "plus a short-lived confirm_nonce, and only a second call passing that "
    "nonce as confirm executes. Never pass confirm on the user's behalf -- the "
    "preview exists for them to approve first. Three more SPEND MONEY "
    "IMMEDIATELY, with no preview and no nonce: `improve action=compile "
    "compile_action=run`, `improve action=datasets "
    "datasets_action=bootstrap`, and `improve action=datasets "
    "datasets_action=bootstrap_train` -- call these only after the user has "
    "explicitly confirmed the spend in the conversation. `improve "
    "action=query` also calls a model.\n"
    "Returns: each action's own payload, unchanged from calling that verb "
    "directly."
)


def register_dispatch_improve_tools(mcp: FastMCP) -> None:
    """Register the ``improve`` lane dispatcher on ``mcp``."""
    register_lane_dispatcher(mcp, _LANE, _PURPOSE_DESCRIPTION)

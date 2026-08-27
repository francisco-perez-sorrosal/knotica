"""Lane dispatcher ``answer`` -- ask the wiki and react to what comes back.

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

__all__ = ["register_dispatch_answer_tools"]

_LANE = "answer"

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
    "Ask the wiki a question and react to the answer: the cited pages come back "
    "on the same call that answers, and the reaction -- curate the example, "
    "note what it got wrong, report what it could not cover -- closes the loop "
    "back into the corpus. Rail: ask, cite, react.\n"
    "Does NOT: tune the prompts that produced the answer (that is `improve`), "
    "and does NOT bring in material from outside the vault (that is `learn`).\n"
    "Requires: an explicit topic, and a question for `answer action=query`. "
    "`answer action=query` is the one action here that calls a model in the "
    "server process; the reacting actions mutate the vault, so call them only "
    "after the user has explicitly confirmed, never from a detection pass.\n"
    "Returns: each action's own payload, unchanged from calling that verb "
    "directly -- `query` answers with the pages it grounded the answer in, so "
    "no follow-up read is needed to cite it."
)


def register_dispatch_answer_tools(mcp: FastMCP) -> None:
    """Register the ``answer`` lane dispatcher on ``mcp``."""
    register_lane_dispatcher(mcp, _LANE, _PURPOSE_DESCRIPTION)

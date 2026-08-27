"""``knotica home`` -- what needs attention right now, across every topic.

The cross-topic inbox: the same plain-text rendering ``status --nudge``
produces, promoted to a lane of its own. Two contracts make it safe for a
SessionStart hook to call unconditionally:

* it exits ``0`` **always** -- an unconfigured install is an empty inbox, not
  an error, and a hook that branches on the exit code must not see a failure
  it cannot act on;
* emptiness is signalled by **empty stdout**, never by an exit code, so the
  caller can echo the payload verbatim when there is one and print nothing
  when there is not.

``status --nudge`` is kept permanently alongside this command (a shipped hook
depends on it); both render through :func:`knotica.cli.status.render_nudge`,
so the two surfaces cannot drift.
"""

from __future__ import annotations

import argparse

from knotica.cli import status
from knotica.cli.common import EXIT_SUCCESS, LaneCommand, console_from_args
from knotica.core.config import diagnose
from knotica.core.page import TopicNotFoundError
from knotica.core.status import gather_wiki_status
from knotica.store import LocalFSStore

__all__ = ["configure", "run"]

_LANE = LaneCommand(
    lane="home",
    summary="what needs attention right now, across every topic",
)

configure = _LANE.configure


def run(args: argparse.Namespace) -> int:
    """Render the cross-topic attention nudge; always exit ``EXIT_SUCCESS``."""
    console = console_from_args(args)
    diagnosis = diagnose()
    if diagnosis.vault is None:
        console.info(diagnosis.detail)
        if diagnosis.remediation:
            console.info(f"To fix: {diagnosis.remediation}")
        return EXIT_SUCCESS

    vault = diagnosis.vault
    try:
        payload = gather_wiki_status(
            LocalFSStore(vault.path), vault.path, topic="", view="attention"
        )
    except TopicNotFoundError as error:  # pragma: no cover -- whole-vault read, no topic filter
        console.error(str(error))
        return EXIT_SUCCESS

    status.render_nudge(console, payload, vault)
    return EXIT_SUCCESS

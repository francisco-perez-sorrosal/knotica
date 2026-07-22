"""``dispatch_telemetry`` -- the falsifier instrument backing the two-tier tool
surface's dispatcher-vs-alias adoption signal.

One smoke assertion per this checkpoint's cross-group coherence requirement:
a deprecated-alias invocation must actually reach the logger, not just update
an in-memory counter nothing reads. Full dispatcher-vs-thin-tool equivalence
is already proven in ``tests/test_dispatch_*.py`` and
``tests/test_server_tool_surface.py``.
"""

from __future__ import annotations

import logging

from knotica.mcp_server.dispatch_telemetry import DEPRECATED_ALIASES, record_deprecated_alias


def test_deprecated_alias_invocation_logs_the_alias_and_its_dispatcher_replacement(caplog) -> None:
    alias = "loop_run_once"
    with caplog.at_level(logging.INFO, logger="knotica.mcp_server.dispatch_telemetry"):
        record_deprecated_alias(alias)

    messages = [record.message for record in caplog.records]
    assert any(alias in message and DEPRECATED_ALIASES[alias] in message for message in messages)

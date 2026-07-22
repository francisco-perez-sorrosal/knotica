"""Structural tests for the MCP server's top-level ``instructions`` string.

``_INSTRUCTIONS`` (``mcp_server/server.py``) is the routing surface a
skill-less client (Claude Desktop) sees with zero other guidance. By
design it slims to three things only:

- the stable invariant guards that must survive even when the client never
  calls ``read_protocol`` (full-text-faithful, topic-always-explicit,
  deterministic-tools/client-does-the-cognition);
- a detection heuristic for routing a natural conversation to knotica;
- a pointer to ``read_protocol`` for the actual (evolvable) step sequences.

The enumerated ingest step sequence it used to carry (``store_source`` ->
write entity pages -> wikilink -> update index) is removed — that content is
evolvable and belongs solely in the vault protocol prompt
(``vault-template/.knotica/prompts/ingest.md``), which ``read_protocol``
serves. The tests below pin *what stays* (invariant phrases), *what leaves*
(the enumerated sequence), and *where it still lives* (the vault protocol
file) — so nothing becomes unreachable to any client tier.

Production import is deferred into a helper so collection stays green before
the paired implementer step lands (concurrent BDD/TDD RED handshake).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INGEST_PROTOCOL = REPO_ROOT / "vault-template" / ".knotica" / "prompts" / "ingest.md"

#: The enumerated ingest step markers the pre-slim ``_INSTRUCTIONS`` carried
#: verbatim ("Ingest is store_source -> write entity pages -> wikilink ->
#: update index") — the exact evolvable content the slim removes.
_REMOVED_STEP_MARKERS = (
    "store_source",
    "write entity pages",
    "wikilink",
    "update index",
)


def _instructions_text() -> str:
    from knotica.mcp_server.server import _INSTRUCTIONS

    return _INSTRUCTIONS


# ---------------------------------------------------------------------------
# What stays: the three stable invariant guards.
# ---------------------------------------------------------------------------


def test_instructions_state_the_full_text_faithful_invariant() -> None:
    text = _instructions_text().lower()
    assert "full text" in text
    assert "faithfully" in text


def test_instructions_state_topic_is_always_an_explicit_argument() -> None:
    text = _instructions_text().lower()
    assert re.search(r"topic[^.]*explicit|explicit[^.]*topic", text), (
        "the explicit-topic guard should read as one clause, not two "
        "unrelated mentions of 'topic' and 'explicit'"
    )


def test_instructions_state_tools_are_deterministic_and_client_does_the_cognition() -> None:
    text = _instructions_text().lower()
    assert "deterministic" in text
    assert "cognit" in text  # "cognitive work" / "the cognition" — either phrasing


# ---------------------------------------------------------------------------
# The pointer: read_protocol is where the evolvable step sequences live.
# ---------------------------------------------------------------------------


def test_instructions_point_to_read_protocol_for_step_sequences() -> None:
    assert "read_protocol" in _instructions_text()


# ---------------------------------------------------------------------------
# The detection heuristic: the routing half that was not present in the
# pre-slim instructions at all.
# ---------------------------------------------------------------------------


def test_instructions_state_a_detection_heuristic_for_routing_to_knotica() -> None:
    """When the conversation concerns a covered topic or a shared source, the
    instructions should point the model at the cheap scope-check
    (`wiki_status(view=scope)`), not just describe tools once summoned."""
    text = _instructions_text()
    assert "wiki_status" in text
    assert "scope" in text.lower()


# ---------------------------------------------------------------------------
# What leaves: no enumerated protocol step sequence.
# ---------------------------------------------------------------------------


def test_instructions_contain_no_enumerated_ingest_step_sequence() -> None:
    text = _instructions_text()
    for marker in _REMOVED_STEP_MARKERS:
        assert marker not in text, (
            f"{marker!r} is evolvable protocol content — it belongs only in "
            "the vault prompt read_protocol serves, not in the always-on "
            "server instructions"
        )


def test_instructions_contain_no_numbered_protocol_step_list() -> None:
    """A future regression that reintroduces a step list (`1. ... 2. ...`)
    should fail here even if it uses different wording than today's markers."""
    text = _instructions_text()
    assert not re.search(r"(?<!\S)[1-9]\.\s+\S", text)


# ---------------------------------------------------------------------------
# Superset check (pre-mortem #5): every removed step marker's *substance* is
# still reachable via the vault protocol file `read_protocol` serves — so
# nothing becomes unreachable to any client tier.
# ---------------------------------------------------------------------------


def test_removed_ingest_step_content_remains_reachable_via_read_protocol() -> None:
    """The enumerated step content that leaves `_INSTRUCTIONS` must still be
    taught, in full, by the vault protocol file
    `read_protocol(operation="ingest")` serves."""
    protocol_text = INGEST_PROTOCOL.read_text(encoding="utf-8")
    assert "store_source" in protocol_text
    assert "write_page" in protocol_text
    assert "wikilink" in protocol_text.lower()
    assert "index_entry" in protocol_text


# ---------------------------------------------------------------------------
# No boot-time vault read: construction is pure wiring.
# ---------------------------------------------------------------------------


def test_building_the_server_succeeds_with_no_vault_configured_anywhere(
    unconfigured_env: Path,
) -> None:
    """Registration only records tool metadata — an isolated home with no
    ``config.toml`` anywhere must not prevent the server from being built."""
    del unconfigured_env
    from knotica.mcp_server.server import _INSTRUCTIONS, _build_server

    server = _build_server()

    assert server.instructions == _INSTRUCTIONS


def test_building_the_server_touches_no_vault_path_that_was_never_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stronger boot-time-read proof than `unconfigured_env`: HOME points at
    a directory that was never created (no mkdir at all). If construction
    read any vault or config path eagerly it would have nothing to read from
    a directory that does not exist — succeeding proves it did not try."""
    nonexistent_home = tmp_path / "never-created"
    monkeypatch.setenv("HOME", str(nonexistent_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(nonexistent_home / ".config"))
    monkeypatch.delenv("KNOTICA_CONFIG", raising=False)

    from knotica.mcp_server.server import _build_server

    server = _build_server()

    assert server.instructions

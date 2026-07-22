"""Structural tests for the read/offer over-routing guard on mutating tools.

A skill-aware client detects wiki-relevant conversation on symptoms alone
(a factual question, a shared source) and, per the wiki-maintenance skill,
must route detection to *read or offer* only -- never a silent mutation.
Every commit stays user-gated. Whether a given conversational turn actually
warrants a mutation is the model's judgment and is not deterministically
testable; what *is* testable, mechanically, is:

- every tool capable of mutating the vault states a confirmation
  precondition in its own registered description, so the model is told
  -- at the point of deciding whether to call it -- that an unconfirmed
  detection pass must not invoke it;
- a genuinely read-only tool carries no such precondition (a negative
  control against the guard being pasted onto every description
  indiscriminately, which would blur read-only from mutating rather than
  distinguish them);
- the read paths detection is actually allowed to reach (the scope-check,
  the suggestions listing) perform zero git mutation when called.

This file only reads registered tool metadata and calls read-only tools --
it never asserts on what a model *decides* to call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from support.dispatch import build_full_server, list_tools
from support.vault import git_head_sha, git_status_porcelain

TOPIC = "agentic-systems"

#: The conversational-core tools that mutate the vault directly (each
#: extended, per the wiki-maintenance over-routing guard, with a
#: "never call this from detection alone" precondition on its own
#: description).
_DIRECT_MUTATING_TOOLS = (
    "write_page",
    "store_source",
    "create_topic",
    "curate_example",
    "suggestions_review",
    "source_ingest_open",
    "source_ingest_submit",
)

#: Dispatcher tools that expose at least one mutating action. ``arena`` is
#: deliberately excluded: both of its actions (status, history) are
#: read-only, so it has nothing to guard against.
_MUTATING_DISPATCHERS = (
    "loop",
    "branches",
    "compile",
    "datasets",
    "golden",
    "vault_health",
)

_MUTATING_TOOLS = _DIRECT_MUTATING_TOOLS + _MUTATING_DISPATCHERS

#: Read-only tools that must NOT carry the mutation-confirmation guard --
#: a negative control proving the guard is scoped to tools that actually
#: mutate, not pasted onto every description regardless of effect.
_READ_ONLY_CONTROLS = ("query", "wiki_status", "suggestions_read", "arena")


@pytest.fixture(scope="module")
def tool_descriptions() -> dict[str, str]:
    """``{tool_name: description}`` for every tool on the fully-wired server."""
    server = build_full_server()
    return {tool.name: (tool.description or "") for tool in list_tools(server)}


# ---------------------------------------------------------------------------
# Every mutating tool states a confirmation precondition.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", _MUTATING_TOOLS)
def test_mutating_tool_description_states_a_confirmation_precondition(
    tool_name: str, tool_descriptions: dict[str, str]
) -> None:
    """A model deciding whether to call a mutating tool from a detection pass
    must be told, in the tool's own description, that the call is gated on
    an explicit user confirmation -- so an unconfirmed detection degrades to
    a declined offer, never an unwanted commit."""
    assert tool_name in tool_descriptions, f"{tool_name!r} is not a registered tool"
    description = tool_descriptions[tool_name].lower()
    assert "confirm" in description, (
        f"{tool_name!r} description carries no confirmation precondition: "
        f"{tool_descriptions[tool_name]!r}"
    )


# ---------------------------------------------------------------------------
# Negative control: the guard is scoped, not indiscriminate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", _READ_ONLY_CONTROLS)
def test_read_only_tool_description_carries_no_confirmation_precondition(
    tool_name: str, tool_descriptions: dict[str, str]
) -> None:
    """A read-only tool has nothing to gate -- a description that pastes the
    same confirmation boilerplate onto it would blur the read/mutate
    distinction the guard exists to preserve."""
    assert tool_name in tool_descriptions, f"{tool_name!r} is not a registered tool"
    description = tool_descriptions[tool_name].lower()
    assert "confirm" not in description, (
        f"{tool_name!r} is read-only but its description carries confirmation "
        f"language meant for mutating tools: {tool_descriptions[tool_name]!r}"
    )


# ---------------------------------------------------------------------------
# The detection-reachable read paths are genuinely side-effect-free.
# ---------------------------------------------------------------------------


def test_scope_check_read_path_performs_no_git_mutation(
    vault_config: Path, template_vault: Path
) -> None:
    """``wiki_status(view="scope")`` is the cheap deterministic scope-check a
    detection pass runs before routing. Calling it must not move HEAD or
    dirty the working tree -- detection is read-only by construction, not
    merely by convention."""
    del vault_config
    from support.dispatch import call_tool, payload_of

    before_sha = git_head_sha(template_vault)
    before_status = git_status_porcelain(template_vault)

    server = build_full_server()
    result = call_tool(server, "wiki_status", {"view": "scope"})
    body = payload_of(result)

    assert "error" not in body
    assert git_head_sha(template_vault) == before_sha
    assert git_status_porcelain(template_vault) == before_status


def test_suggestions_read_path_performs_no_git_mutation(
    vault_config: Path, template_vault: Path
) -> None:
    """``suggestions_read`` is the other detection-adjacent read path --
    listing gap-fill suggestions to decide whether to *offer* a review must
    not itself mutate the vault."""
    del vault_config
    from support.dispatch import call_tool, payload_of

    before_sha = git_head_sha(template_vault)
    before_status = git_status_porcelain(template_vault)

    server = build_full_server()
    result = call_tool(server, "suggestions_read", {"topic": TOPIC})
    body = payload_of(result)

    assert "error" not in body
    assert git_head_sha(template_vault) == before_sha
    assert git_status_porcelain(template_vault) == before_status

"""RED-first equivalence suite for the six lane dispatchers.

The lane-dispatcher modules -- `mcp_server/tools_dispatch_home.py` ...
`_tend.py`, registered additively in `server.py` -- had not landed when this
file was written (the paired implementation ran concurrently). Every
dispatcher-owned symbol is imported lazily inside a helper or test body so
collection stays green and the first run fails with an
`ImportError`/`ModuleNotFoundError`, not a collection error -- the RED half
of the concurrent BDD/TDD handshake.

The equivalence table below is **derived from `LANE_MEMBERSHIP`**
(`knotica.core.process_model`), never hand-listed: a verb added to a lane
without a matching `_REPRESENTATIVE` entry fails loudly (`KeyError`) rather
than silently passing with no coverage.

Two API-shape assumptions are load bearing here because the dispatcher
modules did not exist to consult when this suite was authored -- both were
recorded in the pipeline's learnings log, and the plan wins on conflict:

1. Each lane module exports `register_dispatch_<lane>_tools`, mirroring
   every existing topical dispatcher's naming (`register_dispatch_loop_tools`
   and its eight siblings).
2. The lane dispatcher's own tool call takes `action=<verb>` as its
   selector. A verb that already owns a parameter literally named `action`
   (the eight verbs in `_VERBS_WITH_OWN_ACTION_PARAM` -- the prior wave's
   topical dispatchers, plus `suggestions_review`) forwards its own action
   value renamed to `<verb>_action` so the two concepts never collide in one
   flat kwargs dict.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from knotica.core.process_model import LANE_MEMBERSHIP
from support.dispatch import (
    TOPIC,
    build_dispatch_server,
    build_full_server,
    build_verb_server,
    call_tool,
    configure_default_vault,
    fresh_vault,
    list_tool_names,
    list_tools,
    payload_of,
)
from support.vault import run_git

# ---------------------------------------------------------------------------
# Derivation from the declaration -- never hand-listed.
# ---------------------------------------------------------------------------

#: Verbs the tiered-surface design keeps flat and unrenamed even though they
#: carry a `LANE_MEMBERSHIP` entry -- Tier 1's
#: `write_page`/`store_source`/`query`, plus the architect's amendment (the
#: four high-density verbs the client-as-brain calls mid-turn). These are
#: not dispatcher-absorbed, so the flat-tool removal never touches their
#: registration and they are excluded from the equivalence table.
_STAYS_FLAT_VERBS = frozenset(
    {
        "write_page",
        "store_source",
        "query",
        "curate_example",
        "gap_report",
        "note_capture",
        "ingest_progress",
    }
)

#: Verbs whose own call signature already has a parameter literally named
#: `action` -- the eight already-consolidated topical dispatchers from the
#: prior wave, plus `suggestions_review` (whose `action` picks
#: approve/reject/defer/mark_ingested). A lane dispatcher wrapping one of
#: these needs to keep its own `action` selector distinct from the verb's.
_VERBS_WITH_OWN_ACTION_PARAM = frozenset(
    {
        "datasets",
        "golden",
        "arena",
        "compile",
        "branches",
        "notes",
        "loop",
        "vault_health",
        "suggestions_review",
    }
)


def _dispatcher_absorbed_lane_pairs() -> list[tuple[str, str | None, str]]:
    """Every (verb, discriminator, lane) reachable through a lane dispatcher.

    Mechanically derived from `LANE_MEMBERSHIP` -- a verb added to a lane
    without a case here means this derivation missed it, which is a bug in
    this function, never a reason to hand-patch the result.
    """
    pairs: set[tuple[str, str | None, str]] = set()
    for (verb, discriminator), memberships in LANE_MEMBERSHIP.items():
        if verb in _STAYS_FLAT_VERBS:
            continue
        for lane, _stage_id, _narration in memberships:
            pairs.add((verb, discriminator, lane))
    return sorted(pairs, key=lambda triple: (triple[2], triple[0], triple[1] or ""))


_ABSORBED_LANE_PAIRS = _dispatcher_absorbed_lane_pairs()
_ABSORBED_LANE_PAIR_IDS = [
    f"{lane}-{verb}" + (f"[{discriminator}]" if discriminator else "")
    for verb, discriminator, lane in _ABSORBED_LANE_PAIRS
]


# ---------------------------------------------------------------------------
# Lane-dispatcher call shape (authoring-time assumptions -- see module docstring).
# ---------------------------------------------------------------------------


def _lane_register_fn(lane: str) -> Callable[[Any], None]:
    module = importlib.import_module(f"knotica.mcp_server.tools_dispatch_{lane}")
    return getattr(module, f"register_dispatch_{lane}_tools")  # type: ignore[no-any-return]


def _lane_dispatch_server(lane: str) -> Any:
    return build_dispatch_server(_lane_register_fn(lane))


def _lane_call_kwargs(verb: str, verb_kwargs: dict[str, Any]) -> dict[str, Any]:
    kwargs = dict(verb_kwargs)
    if verb in _VERBS_WITH_OWN_ACTION_PARAM and "action" in kwargs:
        kwargs[f"{verb}_action"] = kwargs.pop("action")
    kwargs["action"] = verb
    return kwargs


# ---------------------------------------------------------------------------
# Representative calls -- one per absorbed verb, reused across every lane it
# is a member of (routing equivalence does not depend on which lane's
# narration motivated the membership, only on the verb + its own arguments).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepresentativeCall:
    """One black-box call proving lane-dispatcher/flat-tool routing equivalence."""

    kwargs: dict[str, Any]
    mutating: bool = False
    seed: Callable[[Path], None] | None = None
    #: Top-level payload keys whose value is minted fresh on every call (a
    #: two-phase nonce, a write timestamp). They are dropped from both sides
    #: before comparison because the *flat* tool does not reproduce them
    #: either -- calling it twice differs here too, so an equality assertion
    #: over them tests the clock, not the routing.
    volatile: tuple[str, ...] = ()


_SUGGESTION_ID = "dispatch-equiv-suggestion"
_NOTE_ID = "dispatch-equiv-note"
_SEEDED_PAGE = "agent-memory"
_GAP_ID = "dispatch-equiv-gap"


def _seed_suggestion(vault: Path, *, suggestion_id: str, status: str) -> None:
    """Commit one suggestion record directly -- house pattern, see
    `test_mcp_suggestions.py::_seed_suggestions`."""
    from knotica.core.gapfill import suggestions_path
    from knotica.core.records import SuggestionRecord
    from knotica.core.transaction import VaultTransaction
    from knotica.store import LocalFSStore

    record = SuggestionRecord(
        suggestion_id=suggestion_id,
        topic=TOPIC,
        gap_id=f"gap-{suggestion_id}",
        qa_id=f"golden-{suggestion_id}",
        fault_class="genuine_gap",
        question="How does speculative decoding interact with draft-model verification?",
        reference_pages=("speculative-decoding",),
        rank=1,
        query_text="speculative decoding draft model verification",
        candidate={
            "url": f"https://arxiv.org/abs/{suggestion_id}",
            "title": "Accelerating LLM Inference with Speculative Decoding",
            "snippet": "We propose...",
            "source_provider": "fake",
            "doi": None,
            "citation_count": 412,
            "schema_version": 1,
        },
        status=status,
        proposed_at="2026-07-19T07:30:00Z",
        decided_at=None,
        decided_reason=None,
        ingested_at=None,
        detected_generation=42,
    )
    store = LocalFSStore(vault)
    path = suggestions_path(TOPIC)
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed suggestion for test") as txn:
        txn.write(path, record.to_json_line() + "\n")


def _seed_gap(vault: Path, *, gap_id: str, status: str) -> None:
    """Commit one gap record directly -- house pattern, see `_seed_suggestion`."""
    from knotica.core.gap_classifier import gaps_path
    from knotica.core.records import GapEvidence, GapRecord
    from knotica.core.transaction import VaultTransaction
    from knotica.store import LocalFSStore

    record = GapRecord(
        gap_id=gap_id,
        topic=TOPIC,
        qa_id=f"golden-{gap_id}",
        fault_class="genuine_gap",
        status=status,
        classifier_version=1,
        detected_generation=5,
        detected_at="2026-07-18T23:01:00Z",
        scalar_at_detection=0.9493,
        baseline_scalar=0.96,
        question="What does the seeded gap leave unanswered?",
        reference_pages=("speculative-decoding",),
        reference_pages_exist=False,
        evidence=GapEvidence(
            quality_delta=-0.12,
            qa_accuracy_delta=-0.12,
            citation_validity_delta=0.0,
            retrieval_trace=(),
            pages_added=(),
            pages_removed=(),
            prior_generation=4,
        ),
        manifest_ref=f"{TOPIC}/.knotica/eval-runs/gen-5/manifest.json",
    )
    store = LocalFSStore(vault)
    path = gaps_path(TOPIC)
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed gap for test") as txn:
        txn.write(path, record.to_json_line() + "\n")


def _seed_note(vault: Path, *, note_id: str, page: str) -> None:
    """Hand-place a `question`-intent note anchored on `page` -- house
    pattern, see `tests/core/notes/test_promote_note.py::_seed_note`. The
    `question` intent is required: `promote_note`'s `target=gap` path only
    accepts `GAP_ELIGIBLE_INTENTS`."""
    from knotica.core.notes.anchor import AnchorRecord, NoteDocument, serialize_note

    document = NoteDocument(
        id=note_id,
        topic=TOPIC,
        intent="question",
        created="2026-07-30T08:00:00Z",
        updated="2026-07-30T08:00:00Z",
        status="active",
        tags=(),
        body="a hand-authored note seeded for the lane-dispatcher equivalence proof",
        anchors=(
            AnchorRecord(
                page=page,
                heading="",
                fidelity="span",
                pinned_at="9f1a3c0",
                quote="a passage worth promoting.",
            ),
        ),
    )
    path = vault / "notes" / TOPIC / f"{note_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_note(document), encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: seed note {note_id}")


_REPRESENTATIVE: dict[tuple[str, str | None], RepresentativeCall] = {
    # `commit_sha` and the metrics `timestamp` below are both second-resolution
    # and both derived from wall-clock time, so the two calls this test makes --
    # one per surface, against two independently seeded vaults -- reproduce them
    # only when they happen to land inside the same second. Calling the *flat*
    # tool twice differs here too, which is what makes them volatile rather than
    # a routing difference.
    ("create_topic", None): RepresentativeCall(
        {"topic": "dispatch-equivalence-topic", "description": ""},
        mutating=True,
        volatile=("commit_sha",),
    ),
    ("ingest_activity_read", None): RepresentativeCall({"topic": TOPIC}),
    ("datasets", None): RepresentativeCall({"action": "inventory", "topic": TOPIC}),
    ("golden", None): RepresentativeCall({"action": "load", "topic": TOPIC}),
    ("baseline_probe", None): RepresentativeCall(
        {"topic": TOPIC}, mutating=True, volatile=("timestamp",)
    ),
    ("metrics_read", None): RepresentativeCall({"topic": TOPIC}),
    ("loop", None): RepresentativeCall(
        {"action": "set_baseline", "topic": TOPIC, "scalar": 0.5707}, mutating=True
    ),
    ("arena", None): RepresentativeCall(
        {"action": "status", "topic": TOPIC}, volatile=("updated_at",)
    ),
    # `action=run`, not `action=status`, for the reason
    # `test_server_tool_surface.py` already records: `status` crashes on an idle
    # topic (a pre-existing `compile_status_payload` bug, characterized in
    # `test_dispatch_compile.py`), and an *uncaught* handler exception is the one
    # outcome the two surfaces cannot render identically -- FastMCP prefixes it
    # with the name of the tool that was called, so the flat text says "compile"
    # and the lane text says "improve". `run` on a fresh vault hits the
    # deterministic, side-effect-free "no trainset" NOT_CONFIGURED floor, which
    # is a clean routing proof rather than a bug reproduction.
    ("compile", None): RepresentativeCall({"action": "run", "topic": TOPIC}),
    ("branches", None): RepresentativeCall({"action": "scoreboard", "topic": TOPIC}),
    ("prompt_diff", None): RepresentativeCall({"topic": TOPIC}),
    ("gaps_read", None): RepresentativeCall({"topic": TOPIC}),
    ("gapfill_discover", None): RepresentativeCall({"topic": TOPIC}, volatile=("confirm_nonce",)),
    ("review_gap", None): RepresentativeCall(
        {"topic": TOPIC, "gap_id": _GAP_ID, "decision": "dismiss", "reason": "not worth sourcing"},
        mutating=True,
        seed=lambda vault: _seed_gap(vault, gap_id=_GAP_ID, status="open"),
        volatile=("commit_sha", "decided_at"),
    ),
    ("suggestions_read", None): RepresentativeCall({"topic": TOPIC}),
    ("suggestions_review", None): RepresentativeCall(
        {"topic": TOPIC, "suggestion_id": _SUGGESTION_ID, "action": "defer", "mode": "dry-run"},
        seed=lambda vault: _seed_suggestion(vault, suggestion_id=_SUGGESTION_ID, status="pending"),
    ),
    ("source_ingest_open", None): RepresentativeCall(
        {"topic": TOPIC, "suggestion_id": _SUGGESTION_ID},
        mutating=True,
        seed=lambda vault: _seed_suggestion(vault, suggestion_id=_SUGGESTION_ID, status="approved"),
    ),
    ("source_ingest_submit", None): RepresentativeCall(
        # No open session exists for this id: both surfaces must reject it
        # identically. A not-found error is just as valid a routing-
        # equivalence proof as a success payload -- same input, same output.
        {"topic": TOPIC, "suggestion_id": "no-such-suggestion", "mode": "dry-run"}
    ),
    ("vault_health", None): RepresentativeCall({"action": "doctor", "topic": TOPIC}),
    ("lint_check", None): RepresentativeCall({"topic": TOPIC}),
    ("notes", None): RepresentativeCall({"action": "drift", "topic": TOPIC}),
    ("notes", "promote"): RepresentativeCall(
        {
            "action": "promote",
            "topic": TOPIC,
            "note_id": _NOTE_ID,
            "target": "gap",
            "question": "Does the seeded note's grounding page actually explain this?",
            "mode": "apply",
        },
        mutating=True,
        seed=lambda vault: _seed_note(vault, note_id=_NOTE_ID, page=_SEEDED_PAGE),
    ),
}

_missing = {(verb, disc) for verb, disc, _lane in _ABSORBED_LANE_PAIRS} - set(_REPRESENTATIVE)
assert not _missing, (
    f"LANE_MEMBERSHIP grew a dispatcher-absorbed verb with no representative "
    f"call registered: {sorted(_missing)}"
)


# ---------------------------------------------------------------------------
# The equivalence proof itself.
# ---------------------------------------------------------------------------


def _comparable(payload: Any, volatile: tuple[str, ...]) -> Any:
    """The payload minus the keys minted fresh on every call (see `volatile`).

    Recursive, because the values that cannot repeat are not always top level:
    a probe's persisted record carries its own `timestamp` one level down.
    """
    if not volatile:
        return payload
    if isinstance(payload, dict):
        return {
            key: _comparable(value, volatile)
            for key, value in payload.items()
            if key not in volatile
        }
    if isinstance(payload, list):
        return [_comparable(item, volatile) for item in payload]
    return payload


@pytest.mark.parametrize(
    ("verb", "discriminator", "lane"), _ABSORBED_LANE_PAIRS, ids=_ABSORBED_LANE_PAIR_IDS
)
def test_lane_dispatcher_action_matches_the_flat_tool_it_will_replace(
    verb: str,
    discriminator: str | None,
    lane: str,
    vault_config: Path,
    template_vault: Path,
    vault_seed: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del vault_config
    spec = _REPRESENTATIVE[(verb, discriminator)]

    if not spec.mutating:
        if spec.seed is not None:
            spec.seed(template_vault)
        old = payload_of(call_tool(build_verb_server(), verb, dict(spec.kwargs)))
        new = payload_of(
            call_tool(_lane_dispatch_server(lane), lane, _lane_call_kwargs(verb, spec.kwargs))
        )
        assert _comparable(new, spec.volatile) == _comparable(old, spec.volatile), (
            f"{lane}(action={verb!r}) must reproduce {verb}()'s payload exactly"
        )
        return

    vault_a = fresh_vault(vault_seed, tmp_path, f"{lane}-{verb}-a")
    vault_b = fresh_vault(vault_seed, tmp_path, f"{lane}-{verb}-b")
    if spec.seed is not None:
        spec.seed(vault_a)
        spec.seed(vault_b)

    configure_default_vault(monkeypatch, tmp_path, f"{lane}-{verb}-a", vault_a)
    old = payload_of(call_tool(build_verb_server(), verb, dict(spec.kwargs)))

    configure_default_vault(monkeypatch, tmp_path, f"{lane}-{verb}-b", vault_b)
    new = payload_of(
        call_tool(_lane_dispatch_server(lane), lane, _lane_call_kwargs(verb, spec.kwargs))
    )

    assert _comparable(new, spec.volatile) == _comparable(old, spec.volatile), (
        f"{lane}(action={verb!r}) must reproduce {verb}()'s payload exactly"
    )


# ---------------------------------------------------------------------------
# `home` is the router, not a process lane -- the declaration gives it no
# stage rail and no `LANE_MEMBERSHIP` entries, so its action table cannot be
# generated the way the other five are.
# ---------------------------------------------------------------------------


def test_home_lane_declares_no_lane_stage_memberships() -> None:
    """Declaration-only invariant -- holds independent of the dispatchers.

    Documents *why* the registration must special-case `home`: if this ever
    stops being true, a `LANE_MEMBERSHIP`-driven table would silently become
    generatable for `home` too, and the special case would need removing
    rather than papering over.
    """
    lanes_with_memberships = {
        lane for memberships in LANE_MEMBERSHIP.values() for lane, _stage, _narration in memberships
    }
    assert "home" not in lanes_with_memberships


def test_home_dispatcher_registers_a_tool_without_claiming_a_lane_stage() -> None:
    server = _lane_dispatch_server("home")
    names = {tool.name for tool in list_tools(server)}
    assert "home" in names


# ---------------------------------------------------------------------------
# Action tables are generated from the declaration, never hand-maintained
# (REQ-09c): mutating `LANE_MEMBERSHIP` must change the rendered table.
# ---------------------------------------------------------------------------


def test_tend_action_table_is_derived_from_lane_membership_not_hand_maintained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import MappingProxyType

    from knotica.core import process_model

    probe_key = ("_equivalence_probe_verb", None)
    probe_membership = frozenset({("tend", "doctor", "a probe verb injected only for this test")})
    mutated = dict(process_model.LANE_MEMBERSHIP)
    mutated[probe_key] = probe_membership
    monkeypatch.setattr(process_model, "LANE_MEMBERSHIP", MappingProxyType(mutated))

    tend_module = importlib.import_module("knotica.mcp_server.tools_dispatch_tend")
    try:
        importlib.reload(tend_module)
        server = build_dispatch_server(tend_module.register_dispatch_tend_tools)
        tools = {tool.name: tool for tool in list_tools(server)}
        rendered = f"{tools['tend'].description or ''} {tools['tend'].inputSchema}"
        assert "_equivalence_probe_verb" in rendered
    finally:
        # Restore the real table under the real declaration -- module reload
        # is process-global state and must not leak into later tests.
        importlib.reload(tend_module)


# ---------------------------------------------------------------------------
# Registration shape: six lanes, and nothing they absorbed.
# ---------------------------------------------------------------------------


def test_server_registers_all_six_lane_dispatchers_and_none_of_the_verbs_they_absorbed() -> None:
    """The lanes are registered, and the verbs they wrap are not.

    The additive phase (lanes alongside the flat surface, 41 registrations) is
    over: the operator-tier verbs are reachable only as lane actions now, so
    the same assertion that pinned the additive count pins the removal instead.
    `test_server_tool_surface.py` carries the ceiling; this one carries the
    membership half — every absorbed verb gone, every lane present.
    """
    names = set(list_tool_names(build_full_server()))
    for lane in ("home", "learn", "answer", "improve", "fill", "tend"):
        assert lane in names, f"lane dispatcher {lane!r} not registered"
    absorbed = {verb for verb, _discriminator, _lane in _ABSORBED_LANE_PAIRS}
    assert not (names & absorbed), (
        f"verb(s) a lane absorbed are still registered flat: {sorted(names & absorbed)}"
    )
    assert len(names) == 21, (
        "the published surface is the 13 Tier-1 conversational tools, the two "
        f"unlaned Tier-2 tools and the six lanes; got {len(names)}: {sorted(names)}"
    )

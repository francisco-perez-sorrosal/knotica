"""Tier-1 preservation + one-way-door invariants for the lane-surface rename.

RED-first: the operator-tier flat tools this module expects to be *gone* are
still registered when this file is first collected — the removal is a
separate, paired implementation step. Every assertion below states the
post-removal truth; the ones that depend on the removal having landed are
documented as such and are expected to fail until it does.

Four independent guarantees, each with its own mechanical proof rather than a
prose assurance:

1. **Tier-1 preservation** -- the conversational-core tools keep their exact
   flat names across the rename (no removed name, no alias).
2. **No alias layer** -- an absorbed operator verb's flat registration is
   gone outright, never renamed-and-kept.
3. **Prompt/on-disk name stability** -- the four MCP prompts and the vault's
   own persisted filenames (branch prefixes, dataset files, prompt overlay
   directory) are untouched by a rename that only ever targets the *tool*
   surface.
4. **Dual-reachability regression net** -- the seven verbs that are
   deliberately BOTH a flat Tier-1 tool AND a lane-dispatcher action (the
   architect's conversational/operator amendment) must route identically
   through either path. `test_lane_dispatchers.py`'s equivalence suite
   explicitly excludes these seven, so without this module they would ship
   with no regression coverage on the lane side at all.

The Tier-1 name list below is a plan-derived judgement call, recorded
inline: the pipeline's own prose describes the amendment inconsistently as
widening the flat tier "from nine to fourteen," but its own concrete
enumeration -- the nine lane-less primitives plus the four named high-density
verbs -- sums to thirteen, not fourteen, and every other artifact in the
pipeline (the equivalence suite's own `_STAYS_FLAT_VERBS`, the process
model's `VERB_CLASSIFICATION`) agrees with thirteen. This module treats
thirteen as authoritative and records the discrepancy rather than inventing
an unnamed fourteenth tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from knotica.core.branch_namespaces import (
    CANDIDATE_BRANCH_PREFIX,
    COMPILE_BRANCH_PREFIX,
    QUARANTINE_BRANCH_PREFIX,
    RESULT_BRANCH_PREFIX,
    WIP_BRANCH_PREFIX,
)
from knotica.core.gap_classifier import gaps_path
from knotica.core.gapfill import suggestions_path
from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.process_model import LANE_MEMBERSHIP
from knotica.core.prompts import PROMPTS_DIR
from knotica.evals.golden import golden_dataset_path
from support.dispatch import (
    TOPIC,
    build_full_server,
    call_tool,
    configure_default_vault,
    fresh_vault,
    list_tool_names,
    payload_of,
)
from test_lane_dispatchers import (
    RepresentativeCall,
    _comparable,
    _lane_call_kwargs,
    _lane_dispatch_server,
)
from test_lane_dispatchers import _STAYS_FLAT_VERBS as _STAYS_FLAT_VERBS

# ---------------------------------------------------------------------------
# 1. Tier-1 preservation -- flat, unrenamed, present after the rename.
# ---------------------------------------------------------------------------

#: The nine lane-less primitives, plus the architect's amendment (the four
#: high-density verbs the client-as-brain calls mid-turn). Thirteen names --
#: see the module docstring for why this is thirteen, not the "fourteen" the
#: plan's own prose states.
TIER_1_FLAT_TOOLS = frozenset(
    {
        "search",
        "read_page",
        "list_topics",
        "list_links",
        "read_protocol",
        "write_page",
        "store_source",
        "query",
        "wiki_status",
        "curate_example",
        "gap_report",
        "note_capture",
        "ingest_progress",
    }
)

#: The two unlaned Tier-2 tools -- not lane-prefixed, not part of the
#: conversational Tier-1 core, but equally exempt from the flat-tool removal.
UNLANED_TIER_2_TOOLS = frozenset({"vault", "open_dashboard"})

#: Every verb `LANE_MEMBERSHIP` declares that is NOT one of the seven kept
#: flat -- these are the operator-tier verbs the paired removal step deletes
#: as standalone flat registrations (their behavior survives only through a
#: lane dispatcher's action table from that point on).
_ABSORBED_OPERATOR_VERBS = frozenset(
    verb for verb, _discriminator in LANE_MEMBERSHIP if verb not in _STAYS_FLAT_VERBS
)

assert TIER_1_FLAT_TOOLS.isdisjoint(_ABSORBED_OPERATOR_VERBS), (
    "a name cannot be both preserved flat and slated for removal"
)


@pytest.mark.parametrize("name", sorted(TIER_1_FLAT_TOOLS))
def test_tier_one_verb_is_registered_flat_under_its_current_name(
    name: str, vault_config: Path, template_vault: Path
) -> None:
    del vault_config, template_vault
    assert name in list_tool_names(build_full_server())


# ---------------------------------------------------------------------------
# 2. No alias layer -- an absorbed verb's flat name is gone, not renamed.
# ---------------------------------------------------------------------------


def test_no_absorbed_operator_verb_is_registered_under_any_alias(
    vault_config: Path, template_vault: Path
) -> None:
    """RED until the paired removal step lands.

    `dec-050`'s no-alias-layer decision means an absorbed verb's flat
    registration disappears outright -- it never reappears under a
    lane-prefixed or otherwise renamed tool name. The exact-name check here
    is the direct proof; `test_server_tool_surface.py`'s ceiling assertion
    is the budget check that would also catch a *differently*-named alias
    (any alias registration pushes the total surface over the ceiling).
    """
    del vault_config, template_vault
    names = set(list_tool_names(build_full_server()))
    still_present = names & _ABSORBED_OPERATOR_VERBS
    assert not still_present, (
        f"operator verb(s) still registered flat after the rename should have "
        f"removed them outright (no alias layer): {sorted(still_present)}"
    )


# ---------------------------------------------------------------------------
# 3a. The four MCP prompts keep their names.
# ---------------------------------------------------------------------------


def test_four_mcp_prompts_keep_their_registered_names(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config, template_vault
    from test_mcp_prompts import prompt_names

    assert set(prompt_names(server=build_full_server())) == {"ingest", "query", "lint", "curate"}


# ---------------------------------------------------------------------------
# 3b. No on-disk vault name changes. Each assertion imports the
# live constant/function that owns the name rather than restating the
# literal independently, so a real rename to the source would be caught
# here instead of silently passing a stale hardcoded copy.
# ---------------------------------------------------------------------------


def test_branch_prefix_namespace_is_unchanged_by_the_lane_rename() -> None:
    assert CANDIDATE_BRANCH_PREFIX == "loop/c/"
    assert RESULT_BRANCH_PREFIX == "loop/r/"
    assert QUARANTINE_BRANCH_PREFIX == "loop/x/"
    assert WIP_BRANCH_PREFIX == "loop/wip/"
    assert COMPILE_BRANCH_PREFIX == "compile/"


def test_flywheel_trainset_filename_is_unchanged_by_the_lane_rename() -> None:
    assert qa_dataset_path(TOPIC).endswith("/qa.jsonl")


def test_frozen_golden_set_filename_is_unchanged_by_the_lane_rename() -> None:
    assert golden_dataset_path(TOPIC).endswith("/golden.jsonl")


def test_gapfill_suggestions_filename_is_unchanged_by_the_lane_rename() -> None:
    assert suggestions_path(TOPIC).endswith("/suggestions.jsonl")


def test_gap_queue_filename_is_unchanged_by_the_lane_rename() -> None:
    assert gaps_path(TOPIC).endswith("/gaps.jsonl")


def test_prompt_overlay_directory_is_unchanged_by_the_lane_rename() -> None:
    assert PROMPTS_DIR == ".knotica/prompts"


def test_vault_template_prompt_filenames_match_the_four_mcp_prompt_names() -> None:
    """Cross-checks the on-disk prompt filenames against the registered prompt names -- the four on-disk prompt files this
    project ships are named after the four prompts the surface still serves."""
    template_root = Path(__file__).resolve().parent.parent / "vault-template" / PROMPTS_DIR
    assert {path.stem for path in template_root.glob("*.md")} == {
        "ingest",
        "query",
        "lint",
        "curate",
    }


# ---------------------------------------------------------------------------
# 4. Dual-reachability regression net -- the review-WARN follow-up.
#
# `test_lane_dispatchers.py`'s equivalence suite deliberately excludes every
# verb in `_STAYS_FLAT_VERBS` (they are not "dispatcher-absorbed" -- they
# keep their own flat registration). But six of those seven ALSO carry a
# `LANE_MEMBERSHIP` entry and so also appear in a lane's generated action
# table (`INTERFACE_DESIGN.md`'s own worked `fill` example lists
# `report_gap`/`gap_report` this way). Both paths ship permanently, so both
# need a routing-equivalence proof -- this closes exactly the gap the other
# suite's exclusion leaves open.
# ---------------------------------------------------------------------------

_SEEDED_PAGE = "agent-memory"

#: One representative call per stays-flat, lane-reachable verb, reused
#: across every lane it is a member of -- same rationale as the absorbed-verb
#: suite: routing equivalence does not depend on which lane's narration
#: motivated the membership, only on the verb and its own arguments. `query`
#: is handled by a dedicated test below (it needs an LLM-facade patch, not a
#: vault fixture).
_STAYS_FLAT_REPRESENTATIVE: dict[str, RepresentativeCall] = {
    "write_page": RepresentativeCall(
        {
            "topic": TOPIC,
            "page": "lane-equivalence-proof",
            "content": "# Lane Equivalence Proof\n\nWritten to prove flat/lane routing agree.\n",
            "summary": "lane-equivalence regression net",
        },
        mutating=True,
        volatile=("commit_sha",),
    ),
    "store_source": RepresentativeCall(
        {
            "topic": TOPIC,
            "citation_key": "laneequivalenceproof2026",
            "title": "Lane Equivalence Proof Source",
            "content": "Source content used only to prove flat/lane routing agree.",
            "source_url": "https://example.invalid/lane-equivalence-proof",
        },
        mutating=True,
        volatile=("commit_sha",),
    ),
    "curate_example": RepresentativeCall(
        {
            "topic": TOPIC,
            "query": "Does lane routing change curate_example's stored record?",
            "answer": "No -- both paths write the identical qa.jsonl record.",
            "verdict": "good",
            "pages_used": [_SEEDED_PAGE],
        },
        mutating=True,
        volatile=("commit_sha",),
    ),
    "gap_report": RepresentativeCall(
        {
            "topic": TOPIC,
            "question": "Does lane routing change gap_report's stored record?",
        },
        mutating=True,
        # `gap_id`/`qa_id` are derived server-side; not asserted deterministic
        # here, so excluded from the equality rather than assumed stable.
        volatile=("gap_id", "qa_id"),
    ),
    "note_capture": RepresentativeCall(
        {"topic": TOPIC, "note": "Lane-equivalence regression net note."},
        mutating=True,
        # `path` and `commit` are both clock-derived and the two legs run
        # against two independent vaults: the note filename embeds a
        # `YYYYMMDD-HHMMSS` stamp and git hashes the commit timestamp, so
        # either flips whenever the pair straddles a second boundary. Comparing
        # them makes the test pass or fail on timing rather than on routing.
        # Everything that carries meaning -- topic, intent, the resolved
        # anchors, placement, `written`, `duplicate` -- is still compared.
        volatile=("note_id", "created", "updated", "path", "commit"),
    ),
    "ingest_progress": RepresentativeCall(
        {
            "topic": TOPIC,
            "stage": "resolve_topic",
            "title": "lane-equivalence regression net",
            "run_id": "lane-equivalence-regression-run",
        },
        mutating=True,
        volatile=("recorded_at", "timestamp", "event_id"),
    ),
}


def _stays_flat_lane_pairs() -> list[tuple[str, str]]:
    """Every (verb, lane) pair for a stays-flat verb that also carries a
    `LANE_MEMBERSHIP` entry -- mechanically derived so a verb added to a lane
    later is not silently left uncovered."""
    pairs: set[tuple[str, str]] = set()
    for (verb, _discriminator), memberships in LANE_MEMBERSHIP.items():
        if verb not in _STAYS_FLAT_REPRESENTATIVE:
            continue
        for lane, _stage_id, _narration in memberships:
            pairs.add((verb, lane))
    return sorted(pairs)


_STAYS_FLAT_LANE_PAIRS = _stays_flat_lane_pairs()
_STAYS_FLAT_LANE_PAIR_IDS = [f"{lane}-{verb}" for verb, lane in _STAYS_FLAT_LANE_PAIRS]

_missing_representative = {
    verb
    for verb, _discriminator in LANE_MEMBERSHIP
    if verb in _STAYS_FLAT_VERBS and verb != "query" and verb not in _STAYS_FLAT_REPRESENTATIVE
}
assert not _missing_representative, (
    f"stays-flat, lane-reachable verb with no representative call registered: "
    f"{sorted(_missing_representative)}"
)


@pytest.mark.parametrize(("verb", "lane"), _STAYS_FLAT_LANE_PAIRS, ids=_STAYS_FLAT_LANE_PAIR_IDS)
def test_lane_action_matches_the_flat_tool_for_a_verb_that_stays_flat(
    verb: str,
    lane: str,
    vault_config: Path,
    template_vault: Path,
    vault_seed: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del vault_config, template_vault
    spec = _STAYS_FLAT_REPRESENTATIVE[verb]

    vault_a = fresh_vault(vault_seed, tmp_path, f"stays-flat-{lane}-{verb}-a")
    vault_b = fresh_vault(vault_seed, tmp_path, f"stays-flat-{lane}-{verb}-b")

    configure_default_vault(monkeypatch, tmp_path, f"stays-flat-{lane}-{verb}-a", vault_a)
    flat = payload_of(call_tool(build_full_server(), verb, dict(spec.kwargs)))

    configure_default_vault(monkeypatch, tmp_path, f"stays-flat-{lane}-{verb}-b", vault_b)
    routed = payload_of(
        call_tool(_lane_dispatch_server(lane), lane, _lane_call_kwargs(verb, spec.kwargs))
    )

    assert _comparable(routed, spec.volatile) == _comparable(flat, spec.volatile), (
        f"{lane}(action={verb!r}) must reproduce the flat {verb}() tool's payload, "
        "even though the flat registration is kept permanently"
    )


def test_query_lane_action_matches_the_flat_tool_for_every_membership_lane(
    vault_config: Path, template_vault: Path
) -> None:
    """`query` is Tier 1 (flat, unrenamed) but also a member of `answer` and
    `improve` -- both routes must reach the identical wiki-answer facade
    call. Patches the facade (house pattern, see `test_mcp_query.py`) so this
    is a routing proof, not a live-LLM call."""
    del vault_config, template_vault
    from knotica.core.query_engine import QueryResult

    def _fake_answer(store: Any, topic: str, question: str, **_kwargs: Any) -> QueryResult:
        return QueryResult(
            answer="lane and flat routing must agree",
            citations=["lane-equivalence-proof"],
            pages_used=[f"{TOPIC}/{_SEEDED_PAGE}.md"],
            topic=topic,
            question=question,
        )

    kwargs = {"topic": TOPIC, "question": "Does lane routing change query's answer?"}
    membership_lanes = {
        lane
        for (verb, _discriminator), memberships in LANE_MEMBERSHIP.items()
        if verb == "query"
        for lane, _stage_id, _narration in memberships
    }
    assert membership_lanes, "query has no LANE_MEMBERSHIP entry to prove dual-reachability against"

    with patch("knotica.mcp_server.tools_query.answer_question", side_effect=_fake_answer):
        flat = payload_of(call_tool(build_full_server(), "query", dict(kwargs)))
        for lane in sorted(membership_lanes):
            routed = payload_of(
                call_tool(_lane_dispatch_server(lane), lane, _lane_call_kwargs("query", kwargs))
            )
            assert routed == flat, f"{lane}(action='query') diverged from the flat query() call"

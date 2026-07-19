"""Behavioral tests for the four-way fault classifier's ordered decision cascade.

Derived from ``SYSTEMS_PLAN.md`` §Architecture > Data Flow (the ordered,
first-match decision procedure) and §Acceptance Criteria — never from the
implementation. ``classify_regression`` is pure set logic over an
already-loaded v2 manifest dict, a topic's frozen golden set, and clone-store
page existence; it must not swallow exceptions (the loop hook at the calling
boundary owns the try/except).

RED-first: ``knotica.core.gap_classifier`` does not exist yet when this file is
written (paired implementer step lands concurrently) — every test below
imports it lazily inside the test body so collection succeeds and the first
run fails with ``ModuleNotFoundError``, not a collection error.

Zero network. A real ``template_vault`` git checkout stands in for the eval
clone; page existence and the golden set are real files, not mocks (mocking
would hide exactly the boundary the classifier reads from).
"""

import json
from pathlib import Path

import pytest

from knotica.core.page import page_path
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore
from support.vault import git_commit_count, git_head_sha

TOPIC = "agentic-systems"
FIXTURES = Path(__file__).parent / "fixtures" / "gapfill"

# The persisted fault-class strings are frozen by the gap-record schema and its
# behavioral spec — asserted as literal values, not via a symbol import, so the
# test pins the wire contract rather than one classifier-internal naming choice.
GENUINE_GAP = "genuine_gap"
GENERATION_FAULT = "generation_fault"
DILUTION = "dilution"
RETRIEVAL_FAULT = "retrieval_fault"
_KNOWLEDGE_CAUSE = frozenset({GENUINE_GAP, DILUTION})


def _classify_regression():
    from knotica.core.gap_classifier import classify_regression

    return classify_regression


def _write_page(vault: Path, page_name: str, body: str = "# stub\n") -> None:
    path = vault / page_path(TOPIC, page_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _freeze_golden_question(
    vault: Path, *, query: str, answer: str, pages_used: tuple[str, ...]
) -> str:
    """Freeze one held-out question and return its deterministic golden id."""
    from knotica.evals.golden import freeze, load, GoldenSetFloorWarning

    store = LocalFSStore(vault)
    with pytest.warns(GoldenSetFloorWarning):
        freeze(
            store,
            vault,
            TOPIC,
            [{"question": query, "reference_answer": answer, "pages_used": list(pages_used)}],
        )
    return load(store, TOPIC)[0].id


def _manifest(*, qa_id: str, current_trace: list[str], per_id: dict) -> dict:
    """A minimal v2 manifest dict carrying only what the classifier reads."""
    return {
        "manifest_schema_version": 2,
        "generation": 5,
        "per_example": [{"id": qa_id, "pages": current_trace}],
        "held_out_delta": {
            "ids_added": [],
            "ids_removed": [],
            "prior_generation": 4,
            "scalar_delta": -0.1,
            "per_id": {qa_id: per_id},
        },
    }


def _per_id(**overrides) -> dict:
    payload = {
        "quality_delta": -0.3,
        "qa_accuracy_delta": -0.3,
        "citation_validity_delta": 0.0,
        "pages_added": [],
        "pages_removed": [],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# The four-class ordered cascade
# ---------------------------------------------------------------------------


def test_reference_page_absent_from_clone_classifies_genuine_gap(template_vault: Path):
    classify_regression = _classify_regression()
    store = LocalFSStore(template_vault)
    qa_id = _freeze_golden_question(
        template_vault,
        query="Does the vault have a page on quantum retrieval augmentation?",
        answer="No, that concept does not appear in this vault.",
        pages_used=("nonexistent-page",),
    )
    manifest = _manifest(qa_id=qa_id, current_trace=[], per_id=_per_id())

    result = classify_regression(
        store=store,
        topic=TOPIC,
        clone_root=template_vault,
        generation=5,
        manifest=manifest,
        regressed_ids=[qa_id],
    )

    verdict = next(v for v in result.verdicts if v.qa_id == qa_id)
    assert verdict.fault_class == GENUINE_GAP
    assert result.route == "REDIRECT"


def test_reference_page_present_and_in_current_trace_classifies_generation_fault(
    template_vault: Path,
):
    classify_regression = _classify_regression()
    store = LocalFSStore(template_vault)
    _write_page(template_vault, "react")
    qa_id = _freeze_golden_question(
        template_vault,
        query="What does the react page say about acting and reasoning?",
        answer="It interleaves reasoning traces with actions.",
        pages_used=("react",),
    )
    manifest = _manifest(qa_id=qa_id, current_trace=["react"], per_id=_per_id())

    result = classify_regression(
        store=store,
        topic=TOPIC,
        clone_root=template_vault,
        generation=5,
        manifest=manifest,
        regressed_ids=[qa_id],
    )

    verdict = next(v for v in result.verdicts if v.qa_id == qa_id)
    assert verdict.fault_class == GENERATION_FAULT
    assert result.route == "HEAL", "a generation-fault id keeps the arena heal, never redirects"


def test_synthetic_displaced_reference_with_a_new_competitor_classifies_dilution(
    template_vault: Path,
):
    """SYNTHETIC fixture (hand-built manifest, no live-vault data involved).

    The ordered-precedence case: the reference page is absent from the current
    trace AND a fresh competitor page appeared. Kept as a clearly-labeled
    synthetic case since no generation pair in the live corpus exhibits both a
    removed and an added page for the same regressed question — the classifier
    still needs cascade coverage for this branch, honestly constructed rather
    than derived from real manifests.
    """
    classify_regression = _classify_regression()
    store = LocalFSStore(template_vault)
    _write_page(template_vault, "react")
    qa_id = _freeze_golden_question(
        template_vault,
        query="What does the react page say about acting and reasoning?",
        answer="It interleaves reasoning traces with actions.",
        pages_used=("react",),
    )
    manifest = _manifest(
        qa_id=qa_id,
        current_trace=["some-other-page"],
        per_id=_per_id(pages_removed=["react"], pages_added=["some-other-page"]),
    )

    result = classify_regression(
        store=store,
        topic=TOPIC,
        clone_root=template_vault,
        generation=5,
        manifest=manifest,
        regressed_ids=[qa_id],
    )

    verdict = next(v for v in result.verdicts if v.qa_id == qa_id)
    assert verdict.fault_class == DILUTION
    assert result.route == "REDIRECT"


def test_displaced_reference_without_a_new_competitor_classifies_retrieval_fault(
    template_vault: Path,
):
    """Same 'absent from trace' shape as dilution, but no fresh page displaced it."""
    classify_regression = _classify_regression()
    store = LocalFSStore(template_vault)
    _write_page(template_vault, "react")
    qa_id = _freeze_golden_question(
        template_vault,
        query="What does the react page say about acting and reasoning?",
        answer="It interleaves reasoning traces with actions.",
        pages_used=("react",),
    )
    manifest = _manifest(
        qa_id=qa_id,
        current_trace=["some-other-page"],
        per_id=_per_id(pages_removed=[], pages_added=[]),
    )

    result = classify_regression(
        store=store,
        topic=TOPIC,
        clone_root=template_vault,
        generation=5,
        manifest=manifest,
        regressed_ids=[qa_id],
    )

    verdict = next(v for v in result.verdicts if v.qa_id == qa_id)
    assert verdict.fault_class == RETRIEVAL_FAULT
    assert result.route == "HEAL", "retrieval_fault is never persisted and never redirects"


def test_reference_free_question_is_unclassified_and_routes_to_heal(template_vault: Path):
    """A golden question with no localizable reference page can't be attributed."""
    classify_regression = _classify_regression()
    store = LocalFSStore(template_vault)
    qa_id = _freeze_golden_question(
        template_vault,
        query="What is the vault's overall tone?",
        answer="Technical and concise.",
        pages_used=(),
    )
    manifest = _manifest(qa_id=qa_id, current_trace=["react"], per_id=_per_id())

    result = classify_regression(
        store=store,
        topic=TOPIC,
        clone_root=template_vault,
        generation=5,
        manifest=manifest,
        regressed_ids=[qa_id],
    )

    verdict = next(v for v in result.verdicts if v.qa_id == qa_id)
    assert verdict.fault_class not in _KNOWLEDGE_CAUSE | {GENERATION_FAULT, RETRIEVAL_FAULT}, (
        "a reference-free id must not be attributed to any of the four causes"
    )
    assert result.route == "HEAL", (
        "any non-knowledge-cause verdict keeps the arena heal (skip only on all-knowledge-cause)"
    )


# ---------------------------------------------------------------------------
# Real-fixture check: does the live corpus's own historical regression support
# "dilution" at all, or does the substrate actually say "generation fault"?
#
# Diffing the two frozen manifests directly: across all 25 golden ids, zero
# show any page added or removed between gen-2 and gen-3 (retrieval is
# byte-identical for every question) -- there is no natural "reference page
# displaced by a new competitor" case in this pair of generations. The three
# ids whose quality score dropped (gen-2 -> gen-3) did so with an *unchanged*
# retrieval trace. That is a generation fault by definition, not a dilution --
# the substrate refutes the dilution folklore for this historical regression;
# it does not support it.
# ---------------------------------------------------------------------------


def test_historically_regressed_question_with_unchanged_retrieval_classifies_generation_fault(
    template_vault: Path,
):
    """Real fixture, no synthesis: retrieval trace is identical gen-2 -> gen-3.

    ``golden-72c431a0cdbd7ead`` (the ReAct-prompting question) genuinely
    regressed in quality (0.93 -> 0.86) between the two frozen manifests while
    its retrieval trace stayed byte-identical -- confirmed below by diffing
    the real fixture files, not asserted. "react" is one of the pages the real
    trace actually and consistently retrieved for this question in both
    generations, so it stands in as the reference page here (the corpus's own
    ``golden.jsonl`` names a more specific, never-retrieved page for this
    question -- using the page genuinely present in the unchanged trace keeps
    this test's claim honest: same retrieval, worse score, must be a
    generation fault).
    """
    classify_regression = _classify_regression()
    qa_id = "golden-72c431a0cdbd7ead"
    gen2 = json.loads((FIXTURES / "gen-2-manifest.json").read_text(encoding="utf-8"))
    gen3 = json.loads((FIXTURES / "gen-3-manifest.json").read_text(encoding="utf-8"))
    example_gen2 = next(e for e in gen2["per_example"] if e["id"] == qa_id)
    example_gen3 = next(e for e in gen3["per_example"] if e["id"] == qa_id)
    assert set(example_gen2["pages"]) == set(example_gen3["pages"]), (
        "fixture sanity: this question's retrieval trace must be unchanged across the two "
        "real generations -- that is the whole point of this test"
    )
    assert example_gen3["quality"] < example_gen2["quality"], (
        "fixture sanity: the real quality score must have genuinely dropped"
    )
    assert "react" in example_gen3["pages"], "fixture sanity: 'react' must be a real trace member"

    store = LocalFSStore(template_vault)
    _write_page(template_vault, "react")
    # The exact real query + answer text reproduces this question's real
    # deterministic golden id (`_golden_id` hashes the pair) -- not a
    # coincidence, so the frozen record lines up with `qa_id` above.
    frozen_id = _freeze_golden_question(
        template_vault,
        query=example_gen2["question"],
        answer=(
            "Smaller finetuned models trained on ReAct-format trajectories beat larger "
            "prompted models on HotpotQA."
        ),
        pages_used=("react",),
    )
    assert frozen_id == qa_id, "fixture sanity: the frozen record must be this real question"
    manifest = dict(gen3)
    manifest["held_out_delta"] = {
        "ids_added": [],
        "ids_removed": [],
        "prior_generation": 2,
        "scalar_delta": example_gen3["quality"] - example_gen2["quality"],
        "per_id": {
            qa_id: {
                "quality_delta": example_gen3["quality"] - example_gen2["quality"],
                "qa_accuracy_delta": example_gen3["qa_accuracy"] - example_gen2["qa_accuracy"],
                "citation_validity_delta": (
                    example_gen3["citation_validity"] - example_gen2["citation_validity"]
                ),
                # Real trace diff: no page was added or removed for this id.
                "pages_removed": [],
                "pages_added": [],
            }
        },
    }

    result = classify_regression(
        store=store,
        topic=TOPIC,
        clone_root=template_vault,
        generation=3,
        manifest=manifest,
        regressed_ids=[qa_id],
    )

    verdict = next(v for v in result.verdicts if v.qa_id == qa_id)
    assert verdict.fault_class == GENERATION_FAULT, (
        "an unchanged retrieval trace with a real quality drop is a generation fault, not a "
        "dilution -- the classifier must not blame retrieval for a real substrate regression "
        "where nothing about retrieval actually changed"
    )
    assert result.route == "HEAL"


# ---------------------------------------------------------------------------
# The classifier never swallows its own exceptions -- a failure inside it must
# propagate uncaught. (The loop hook's own try/except, which falls through to
# the arena heal on any such failure, is proven separately at the loop's
# integration boundary, not here.)
# ---------------------------------------------------------------------------


def test_a_failure_reading_the_golden_set_propagates_uncaught(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
):
    classify_regression = _classify_regression()
    store = LocalFSStore(template_vault)

    def _raising_load(store, topic):
        raise RuntimeError("golden set unreadable")

    monkeypatch.setattr("knotica.evals.golden.load", _raising_load)
    manifest = _manifest(qa_id="golden-whatever", current_trace=[], per_id=_per_id())

    with pytest.raises(RuntimeError, match="golden set unreadable"):
        classify_regression(
            store=store,
            topic=TOPIC,
            clone_root=template_vault,
            generation=5,
            manifest=manifest,
            regressed_ids=["golden-whatever"],
        )


# ---------------------------------------------------------------------------
# write_gap_records: dedup guard + own-transaction commit isolation
# ---------------------------------------------------------------------------


def _genuine_gap_verdict(qa_id: str):
    from knotica.core.gap_classifier import FaultClass, GapVerdict

    return GapVerdict(
        qa_id=qa_id,
        fault_class=FaultClass.GENUINE_GAP,
        question="Does the vault cover quantum retrieval augmentation?",
        reference_pages=("nonexistent-page",),
        refs_exist=False,
        quality_delta=-0.4,
        qa_accuracy_delta=-0.4,
        citation_validity_delta=0.0,
        retrieval_trace=(),
        pages_added=(),
        pages_removed=(),
    )


def _build_records(qa_id: str):
    from knotica.core.gap_classifier import build_gap_records

    return build_gap_records(
        [_genuine_gap_verdict(qa_id)],
        topic=TOPIC,
        generation=5,
        scalar_at_detection=0.5,
        baseline_scalar=0.9,
        prior_generation=4,
        clock=lambda: "2026-07-18T00:00:00Z",
    )


def test_two_consecutive_classifications_of_the_same_regression_write_one_gap_record(
    template_vault: Path,
):
    """The dedup guard: re-detecting the same open (qa_id, fault_class) is a no-op write."""
    from knotica.core.gap_classifier import gaps_path, write_gap_records

    store = LocalFSStore(template_vault)
    records = _build_records("golden-repeat")

    write_gap_records(store, template_vault, TOPIC, records)
    write_gap_records(store, template_vault, TOPIC, records)  # same regression, next cycle

    persisted = store.read_text(gaps_path(TOPIC)).strip().splitlines()
    assert len(persisted) == 1, (
        "a second detection of an already-open (qa_id, fault_class) pair must not append a "
        "duplicate record"
    )


def test_gap_record_write_lands_in_exactly_one_commit_touching_only_the_gaps_file(
    template_vault: Path,
):
    """The write is its own commit -- never piggybacked on the loop-state or metrics commit."""
    from knotica.core.gap_classifier import gaps_path, write_gap_records

    store = LocalFSStore(template_vault)
    before_count = git_commit_count(template_vault)
    before_sha = git_head_sha(template_vault)
    records = _build_records("golden-isolated")

    write_gap_records(store, template_vault, TOPIC, records)

    after_count = git_commit_count(template_vault)
    assert after_count == before_count + 1, (
        "a gap-record write must land in exactly one new commit, not zero and not more than one"
    )
    vcs = VaultVcs(template_vault)
    changed = vcs.changed_paths(before_sha, vcs.head_sha())
    # Every VaultTransaction also appends its own audit-trail entry to log.md --
    # that is the standard one-commit-per-operation shape, not piggybacking.
    # The claim under test is narrower: no *other* content or state file (the
    # loop-state or metrics record) rides along in the same commit.
    assert set(changed) == {gaps_path(TOPIC), "log.md"}, (
        "the gap-record commit must touch only gaps.jsonl (plus its own log.md entry) -- never "
        "bundled with a loop-state or metrics write in the same commit"
    )


@pytest.mark.parametrize("bad_topic", ["", "a/b", "..", ".", "  ", "x/../y"])
def test_gaps_path_rejects_topics_that_are_not_a_single_clean_segment(bad_topic: str):
    """The vault-relative gaps path must never be constructible from a topic
    carrying separators or traversal segments -- a hostile or corrupted topic
    string fails fast instead of escaping the topic directory. (Leading and
    trailing slashes are deliberately normalized away, not rejected.)"""
    from knotica.core.gap_classifier import gaps_path

    with pytest.raises(ValueError):
        gaps_path(bad_topic)


def test_gaps_path_builds_the_expected_topic_relative_path():
    from knotica.core.gap_classifier import gaps_path

    assert gaps_path("agentic-systems") == "agentic-systems/.knotica/gaps/gaps.jsonl"

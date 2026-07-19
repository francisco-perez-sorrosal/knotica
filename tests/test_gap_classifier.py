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


def _added_id_manifest(
    *, qa_id: str, qa_accuracy: float, quality: float, current_trace: list[str]
) -> dict:
    """A v2 manifest where ``qa_id`` is newly frozen: it appears in
    ``held_out_delta.ids_added`` and has no ``per_id`` entry at all -- there is
    no prior generation to diff it against."""
    return {
        "manifest_schema_version": 2,
        "generation": 5,
        "per_example": [
            {
                "id": qa_id,
                "pages": current_trace,
                "qa_accuracy": qa_accuracy,
                "quality": quality,
            }
        ],
        "held_out_delta": {
            "ids_added": [qa_id],
            "ids_removed": [],
            "prior_generation": 4,
            "scalar_delta": -0.1,
            "per_id": {},
        },
    }


def _added_id_floor() -> float:
    from knotica.core.gap_classifier import ADDED_ID_FAILING_FLOOR

    return ADDED_ID_FAILING_FLOOR


def _records_from(verdicts) -> list:
    from knotica.core.gap_classifier import build_gap_records

    return build_gap_records(
        verdicts,
        topic=TOPIC,
        generation=5,
        scalar_at_detection=0.5,
        baseline_scalar=0.9,
        prior_generation=4,
        clock=lambda: "2026-07-19T00:00:00Z",
    )


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
# Added-id classification: a newly frozen golden question (no prior
# generation, hence no per_id delta) that fails a floor on its own current
# scores enters the same cascade as a per-id regressor.
# ---------------------------------------------------------------------------


def test_added_id_failing_floor_with_reference_absent_classifies_genuine_gap(
    template_vault: Path,
):
    """A newly frozen probing question the current generation fails, whose
    reference page does not exist in the clone, must classify as a genuine
    gap with honest zero-delta evidence -- there is no prior generation to
    diff it against, so nothing is fabricated."""
    from knotica.core.gap_classifier import regressed_ids_from_manifest

    classify_regression = _classify_regression()
    store = LocalFSStore(template_vault)
    floor = _added_id_floor()
    qa_id = _freeze_golden_question(
        template_vault,
        query="Does the vault have a page on quantum retrieval augmentation?",
        answer="No, that concept does not appear in this vault.",
        pages_used=("nonexistent-page",),
    )
    manifest = _added_id_manifest(
        qa_id=qa_id, qa_accuracy=floor - 0.1, quality=floor - 0.1, current_trace=[]
    )

    regressed = regressed_ids_from_manifest(manifest)
    assert qa_id in regressed, "an added id failing the floor must enter the regressed set"

    result = classify_regression(
        store=store,
        topic=TOPIC,
        clone_root=template_vault,
        generation=5,
        manifest=manifest,
        regressed_ids=regressed,
    )

    verdict = next(v for v in result.verdicts if v.qa_id == qa_id)
    assert verdict.fault_class == GENUINE_GAP
    assert (verdict.quality_delta, verdict.qa_accuracy_delta, verdict.citation_validity_delta) == (
        0.0,
        0.0,
        0.0,
    ), "an added id has no prior generation to diff against -- its evidence must be honestly zero"
    assert result.route == "REDIRECT"

    records = _records_from(result.verdicts)
    assert len(records) == 1, "a genuine gap must produce exactly one persisted gap record"
    assert records[0].evidence.quality_delta == 0.0, (
        "the persisted record must carry the same honest zero-delta evidence as the verdict"
    )


def test_added_id_failing_floor_with_reference_present_and_in_trace_classifies_generation_fault(
    template_vault: Path,
):
    """A newly frozen question the model fails despite its reference page
    being live and actually retrieved is a generation fault -- the prompt is
    at fault, not missing knowledge -- and must never be persisted."""
    from knotica.core.gap_classifier import regressed_ids_from_manifest

    classify_regression = _classify_regression()
    store = LocalFSStore(template_vault)
    floor = _added_id_floor()
    _write_page(template_vault, "react")
    qa_id = _freeze_golden_question(
        template_vault,
        query="What does the react page say about acting and reasoning?",
        answer="It interleaves reasoning traces with actions.",
        pages_used=("react",),
    )
    manifest = _added_id_manifest(
        qa_id=qa_id, qa_accuracy=floor - 0.1, quality=floor - 0.1, current_trace=["react"]
    )

    regressed = regressed_ids_from_manifest(manifest)
    assert qa_id in regressed

    result = classify_regression(
        store=store,
        topic=TOPIC,
        clone_root=template_vault,
        generation=5,
        manifest=manifest,
        regressed_ids=regressed,
    )

    verdict = next(v for v in result.verdicts if v.qa_id == qa_id)
    assert verdict.fault_class == GENERATION_FAULT

    records = _records_from(result.verdicts)
    assert records == [], "a generation-fault verdict must never be persisted as a gap record"


def test_added_id_scoring_above_floor_is_never_classified(template_vault: Path):
    """A healthy new golden question -- one the current generation actually
    answers well -- must never enter the regressed set; a healthy new
    question is not a gap and produces no verdict at all."""
    from knotica.core.gap_classifier import regressed_ids_from_manifest

    floor = _added_id_floor()
    qa_id = _freeze_golden_question(
        template_vault,
        query="What does the react page say about acting and reasoning?",
        answer="It interleaves reasoning traces with actions.",
        pages_used=("react",),
    )
    manifest = _added_id_manifest(
        qa_id=qa_id, qa_accuracy=floor + 0.1, quality=floor + 0.1, current_trace=["react"]
    )

    regressed = regressed_ids_from_manifest(manifest)

    assert qa_id not in regressed, "a healthy new question must never be classified as a gap"


def test_added_id_floor_boundary_is_exclusive(template_vault: Path):
    """Scoring exactly at the floor is healthy, not failing; scoring even
    slightly below it fails -- pinned against the ``<`` comparison the
    ``ADDED_ID_FAILING_FLOOR`` constant's own docstring declares ("at or
    above the floor" is not a gap), not an assumed direction."""
    from knotica.core.gap_classifier import ADDED_ID_FAILING_FLOOR, regressed_ids_from_manifest

    at_floor_id = "golden-at-floor"
    below_floor_id = "golden-below-floor"
    manifest = {
        "manifest_schema_version": 2,
        "generation": 5,
        "per_example": [
            {
                "id": at_floor_id,
                "pages": [],
                "qa_accuracy": ADDED_ID_FAILING_FLOOR,
                "quality": ADDED_ID_FAILING_FLOOR,
            },
            {
                "id": below_floor_id,
                "pages": [],
                "qa_accuracy": ADDED_ID_FAILING_FLOOR - 0.01,
                "quality": ADDED_ID_FAILING_FLOOR - 0.01,
            },
        ],
        "held_out_delta": {
            "ids_added": [at_floor_id, below_floor_id],
            "ids_removed": [],
            "prior_generation": 4,
            "scalar_delta": -0.1,
            "per_id": {},
        },
    }

    regressed = regressed_ids_from_manifest(manifest)

    assert at_floor_id not in regressed, "scoring exactly at the floor must not count as failing"
    assert below_floor_id in regressed, "scoring below the floor must count as failing"


def test_per_id_regression_classification_is_unaffected_by_added_id_extension(
    template_vault: Path,
):
    """Extending eligibility to ``ids_added`` must not change the existing
    per-id regression predicate for ids that were already scored in a prior
    generation -- a pure regression test for the original cascade entrypoint."""
    from knotica.core.gap_classifier import regressed_ids_from_manifest

    regressed_qa_id = "golden-regressed"
    healthy_qa_id = "golden-healthy"
    manifest = {
        "manifest_schema_version": 2,
        "generation": 5,
        "per_example": [
            {"id": regressed_qa_id, "pages": [], "qa_accuracy": 0.9, "quality": 0.6},
            {"id": healthy_qa_id, "pages": [], "qa_accuracy": 0.9, "quality": 0.9},
        ],
        "held_out_delta": {
            "ids_added": [],
            "ids_removed": [],
            "prior_generation": 4,
            "scalar_delta": -0.1,
            "per_id": {
                regressed_qa_id: _per_id(quality_delta=-0.3, qa_accuracy_delta=0.0),
                healthy_qa_id: _per_id(quality_delta=0.0, qa_accuracy_delta=0.0),
            },
        },
    }

    regressed = regressed_ids_from_manifest(manifest)

    assert regressed == [regressed_qa_id], (
        "per_id regression detection (quality_delta < 0 or qa_accuracy_delta < 0) must remain "
        "unaffected -- only the eligibility set for ids_added grows"
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

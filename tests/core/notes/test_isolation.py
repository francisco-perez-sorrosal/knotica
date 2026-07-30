"""Standing proof that a populated ``notes/`` tree cannot move a score-facing surface.

Three separate proofs, each closing a distinct way the guarantee could fail
silently:

1. **The sharpest adversarial case.** ``core.lint._vault_link_map`` walks the
   *whole* vault, so a wikilink from anywhere -- including a real, captured
   note -- is a candidate inbound edge for the orphan check. A note that links
   to an otherwise-orphaned page must not suppress its ``PAGE_ORPHANED``
   finding. Phase 0 closed this with a source-family filter in
   ``_check_orphans``; this module proves the filter holds against a note
   produced by the real ``capture_note`` path, confirming the target page is
   a genuine, verified orphan *before* the note exists (a test that passes
   because the page was never orphaned in the first place would look
   identical to one that passes because the filter works).
2. **The broad characterization.** A vault with several notes across two
   topics -- mixed intents, mixed anchor fidelities, one anchor whose target
   page is later deleted -- must read identically, on the surfaces that feed
   the eval scalar, to the same vault with ``notes/`` deleted outright.
3. **Loop silence at scale.** A realistic burst of captures across two topics
   must still report no content change to the loop's own change detector --
   generalizing the single-capture proof already pinned in
   ``tests/core/notes/test_capture_note.py``.

**What this module actually proves, stated plainly.** The eval scalar's
composite legs, per the design's own accounting, are ``qa_accuracy``,
``citation_validity``, and ``lint_violations``. Recomputing the first two for
real requires a live judge model call against synthesized answers -- an LLM
dependency this module deliberately does not take on. Two structurally
different guarantees stand in for them instead, and are not the same
strength of proof:

- ``lint_violations`` is exercised *directly and dynamically*, via
  ``lint_vault`` -- this is the one leg with a real, previously-confirmed
  leak vector (the whole-vault link walk), so it gets the strongest test:
  before/after equality against an adversarial fixture.
- ``qa_accuracy`` and ``citation_validity`` are proven *structurally*
  instead: both are computed only from a topic's *entity page* corpus
  (``evals.golden.entity_pages``, which walks ``iter_page_paths(store,
  topic)`` -- strictly under ``<topic>/``). Because notes live under the
  disjoint top-level ``notes/<topic>/`` directory, they cannot appear in that
  corpus by construction, not by a filter that could regress. This module
  confirms the corpus is unchanged with and without ``notes/``, which is the
  strongest claim obtainable without a model call, but it is a proof that the
  input to those legs is untouched -- not a re-run of the legs' own
  computation.
- ``_count_content_pages`` (the ``L_ref`` denominator that normalizes
  ``lint_violations``) is exercised directly for the same reason
  ``lint_violations`` is: it is a plain page count, cheap to recompute, and
  worth pinning alongside the leg it scales.

Everything here runs against ``template_vault``, never the real vault.
"""

import shutil
from collections.abc import Mapping
from pathlib import Path

from knotica.core.lint import LintCheck, Violation, lint_vault
from knotica.core.loop import LoopRunner
from knotica.core.operations.capture_note import capture_note
from knotica.core.operations.create_topic import create_topic
from knotica.core.vcs import VaultVcs
from knotica.evals.golden import entity_pages
from knotica.evals.harness import _count_content_pages
from knotica.store import LocalFSStore
from support.vault import git_head_sha, run_git

TOPIC = "agentic-systems"
SECOND_TOPIC = "isolation-second-topic"
ORPHAN_STEM = "isolation-proof-orphan"
ORPHAN_PAGE = f"{TOPIC}/{ORPHAN_STEM}.md"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _lint(vault: Path, topic: str = "") -> list[Violation]:
    return lint_vault(LocalFSStore(vault), topic)


def _checks(violations: list[Violation]) -> set[LintCheck]:
    return {v.check for v in violations}


def _capture(vault: Path, topic: str, note: str, **fields: object) -> Mapping[str, object]:
    """Invoke the real capture path and require it to succeed.

    Every note this module writes must land -- a failed capture here would
    mean the fixture itself is broken, not that isolation was ever tested.
    """
    fields.setdefault("pages", ())
    result = capture_note(LocalFSStore(vault), vault, VaultVcs(vault), topic, note, **fields)
    assert isinstance(result, Mapping)
    assert "error" not in result, f"expected a successful capture, got {result!r}"
    return result


def _seed_page(vault: Path, relpath: str, content: str, message: str) -> None:
    target = vault / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", message)


def _plant_genuine_orphan(vault: Path) -> None:
    """A content page nothing in the vault links to yet.

    Mirrors the template's own ``lonely.md`` orphan fixture shape --
    ``sources: [wang2024awm]`` cites a source the template already stores, so
    the page carries no incidental ``CITATION_UNRESOLVED`` finding of its own.
    """
    _seed_page(
        vault,
        ORPHAN_PAGE,
        "---\n"
        f"type: concept\ntopic: {TOPIC}\ncreated: 2026-07-29\n"
        "updated: 2026-07-29\nconfidence: medium\nsources: [wang2024awm]\n"
        "status: active\ntags: [isolation-proof]\n---\n\n# Isolation proof orphan\n",
        "test: seed the genuinely orphaned page",
    )


def _unreachable_evaluate(*_args: object, **_kwargs: object) -> None:
    raise AssertionError(
        "evaluate must not be called -- a note-authored commit must never trigger "
        "an observation, let alone a real, billed eval run"
    )


# ---------------------------------------------------------------------------
# 1. The sharpest adversarial case: a real note's wikilink must not de-orphan
# ---------------------------------------------------------------------------


def test_a_wikilink_inside_a_real_captured_note_does_not_deorphan_its_target(
    template_vault: Path,
) -> None:
    _plant_genuine_orphan(template_vault)
    before = _checks(_lint(template_vault, TOPIC))
    assert LintCheck.PAGE_ORPHANED in before, (
        "fixture sanity: the planted page must be a genuine, confirmed orphan before "
        "any note exists -- otherwise a passing assertion below would prove nothing"
    )

    result = _capture(
        template_vault,
        TOPIC,
        f"Revisiting this later: [[{TOPIC}/{ORPHAN_STEM}]] is the passage I keep coming back to.",
    )
    assert "path" in result, "sanity: the capture must have written a note file"

    after = _checks(_lint(template_vault, TOPIC))
    assert LintCheck.PAGE_ORPHANED in after, (
        "a wikilink inside a real, capture_note-authored note must not de-orphan its "
        "target page -- if this fails, core.lint._check_orphans's note-family source "
        "filter has regressed and the eval scalar's lint_violations leg is contaminated"
    )


# ---------------------------------------------------------------------------
# 2. Full characterization: byte-identical with and without notes/
# ---------------------------------------------------------------------------


def _seed_mixed_notes_content(vault: Path) -> None:
    """Seed the ordinary KB content the mixed-notes fixture anchors against.

    Deliberately separate from :func:`_capture_mixed_notes` below: the
    "without notes" comparison baseline is cloned from the vault *after* this
    step but *before* any note exists, so both variants share byte-identical
    KB content and the only difference between them is the populated
    ``notes/`` tree itself -- never an incidental log.md entry or page that
    only one variant has.
    """
    exact_page = f"{TOPIC}/isolation-exact-quote.md"
    _seed_page(
        vault,
        exact_page,
        "# Isolation exact quote\n\nnothing about the notes overlay moves the eval scalar.\n",
        "test: seed the exact-quote target page",
    )
    create_topic(LocalFSStore(vault), vault, SECOND_TOPIC)


def _capture_mixed_notes(vault: Path) -> None:
    """Populate ``notes/`` across two topics with mixed intents and fidelities.

    Fidelity is a downstream consequence of what ``capture_note`` can verify
    against working-tree text, not a knob this helper sets directly -- each
    call below reproduces one outcome from the capture contract pinned in
    ``tests/core/notes/test_capture_note.py``. None of the note bodies embeds
    wikilink syntax -- that adversarial case is
    ``test_a_wikilink_inside_a_real_captured_note_does_not_deorphan_its_target``'s
    job alone, so it stays isolated to one focused test.
    """
    exact_page = f"{TOPIC}/isolation-exact-quote.md"
    _capture(
        vault,
        TOPIC,
        "worth keeping verbatim",
        quote="nothing about the notes overlay moves the eval scalar",
        pages=[exact_page],
        intent="reflection",
    )  # span fidelity
    _capture(
        vault,
        TOPIC,
        "a reflection whose quote never occurred anywhere real",
        quote="a quote that does not occur anywhere in the vault",
        pages=[exact_page],
        intent="dispute",
    )  # degraded to page fidelity
    _capture(
        vault, TOPIC, "just a thought, nothing to anchor at all", intent="question"
    )  # topic fidelity, no page claimed
    _capture(
        vault,
        SECOND_TOPIC,
        "citing a page that was never actually created",
        quote="doesn't matter, the claimed page never existed",
        pages=[f"{SECOND_TOPIC}/never-existed.md"],
        intent="gap",
    )  # topic fidelity, anchor names a page that does not exist


def test_eval_scored_legs_are_byte_identical_with_and_without_a_populated_notes_tree(
    template_vault: Path, tmp_path: Path
) -> None:
    _seed_mixed_notes_content(template_vault)

    without_notes = tmp_path / "vault-without-notes"
    shutil.copytree(template_vault, without_notes)

    _capture_mixed_notes(template_vault)
    assert (template_vault / "notes").is_dir(), "sanity: the capture burst must have created notes/"
    assert not (without_notes / "notes").exists(), (
        "sanity: the comparison baseline must never have had a notes/ tree at all"
    )

    for topic in (TOPIC, SECOND_TOPIC):
        with_notes_violations = _lint(template_vault, topic)
        without_notes_violations = _lint(without_notes, topic)
        assert with_notes_violations == without_notes_violations, (
            f"lint_violations leg moved for topic {topic!r} once notes/ was populated"
        )

        with_notes_count = _count_content_pages(LocalFSStore(template_vault), topic)
        without_notes_count = _count_content_pages(LocalFSStore(without_notes), topic)
        assert with_notes_count == without_notes_count, (
            f"the content-page count normalizing lint_violations moved for topic {topic!r}"
        )

        with_notes_entities = [p.path for p in entity_pages(LocalFSStore(template_vault), topic)]
        without_notes_entities = [p.path for p in entity_pages(LocalFSStore(without_notes), topic)]
        assert with_notes_entities == without_notes_entities, (
            f"the entity-page corpus feeding qa_accuracy/citation_validity moved for "
            f"topic {topic!r}"
        )


# ---------------------------------------------------------------------------
# 2b. Deleting a note: the other direction of the same guarantee
# ---------------------------------------------------------------------------


def test_deleting_a_captured_note_leaves_the_scored_topic_lint_untouched(
    template_vault: Path,
) -> None:
    """Removing a note is as ordinary as writing one, and must cost nothing.

    Every capture appends an entry to the vault-root ``log.md`` stamped with
    the note's *KB* topic, and the log's touched paths are checked under that
    topic's lint. Deleting the note in Obsidian -- or renaming it, which is a
    delete plus an add -- therefore has a straight path into the scored
    ``lint_violations`` leg unless the log check ignores unscored paths. The
    loop stays correctly asleep at the deletion, so a regression here lands
    silently and first surfaces as an unearned drop at some later, unrelated
    eval.
    """
    before = _lint(template_vault, TOPIC)
    assert before == [], "fixture sanity: the topic must lint clean before any note exists"

    result = _capture(template_vault, TOPIC, "a private reflection nobody else should score")
    assert _lint(template_vault, TOPIC) == [], "capturing a note moved the scored topic's lint"

    note_path = result["path"]
    assert isinstance(note_path, str)
    (template_vault / note_path).unlink()
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: the user deletes their own note in Obsidian")

    assert _lint(template_vault, TOPIC) == [], (
        "deleting a note raised a violation against the scored topic -- the note's log "
        "entry is being checked as if the unscored file it names were part of the wiki"
    )


# ---------------------------------------------------------------------------
# 3. Loop silence at scale
# ---------------------------------------------------------------------------


def test_loop_silence_scales_across_several_captures_and_two_topics(
    template_vault: Path,
) -> None:
    # Scaffold the second topic before the measured window: creating a topic
    # writes ordinary content-page files, which *should* wake the loop -- and
    # must not be mistaken for a note-caused wake-up inside the assertion below.
    create_topic(LocalFSStore(template_vault), template_vault, SECOND_TOPIC)
    initial_sha = git_head_sha(template_vault)

    _capture(template_vault, TOPIC, "first reflection in a busy capture afternoon")
    _capture(template_vault, TOPIC, "a second, unrelated thought", intent="question")
    _capture(
        template_vault, SECOND_TOPIC, "a dispute filed against the second topic", intent="dispute"
    )

    final_sha = git_head_sha(template_vault)
    assert final_sha != initial_sha, "sanity: the capture burst must have produced real commits"

    runner = LoopRunner(template_vault, TOPIC, evaluate=_unreachable_evaluate, arena_enabled=False)
    assert runner._content_changed_since(initial_sha, final_sha) is False, (
        "three captures spanning two topics must still classify as unscored content in "
        "aggregate -- a realistic capture burst, not just one isolated commit, is the "
        "case that must never wake the loop"
    )

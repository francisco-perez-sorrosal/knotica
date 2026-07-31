"""The seventh contamination hunt -- Phase 2's mutating operations against the
notes/KB boundary.

Phase 1 found six contamination leaks, every one the same shape: the folder
layout excludes notes from scored surfaces *structurally*, but anything
copying a note's path, text, or identity into shared state routes around
that guarantee. Phase 2 adds three mutating operations (`reanchor`, `detach`,
`archive`) and a bridge that deliberately crosses out of the notes layer
(`promote_note`) -- a seventh leak was expected.

**This file does not re-prove the guarantee already pinned elsewhere.**
`tests/core/notes/test_reanchor_note.py` and `tests/core/notes/test_promote_note.py`
each already carry per-operation contamination tests -- one distinctive
phrase per operation, checked against `log.md` and every commit subject (or,
for `promote_note`, `qa.jsonl`/`gaps.jsonl`). Duplicating those here would
re-test structure, not hunt for a gap. What is missing, and what this file
adds:

1. **A single note's full operation lifecycle** -- capture, reanchor, detach,
   archive, and *both* `promote_note` targets, run back to back on one note
   carrying two distinct verbatim KB phrases and a private body -- proving
   the no-leak guarantee holds across the *whole* sequence, not just any one
   operation in isolation.
2. **The standing eval-scalar characterization** (SYSTEMS_PLAN's own
   "load-bearing test"): a note whose body *and* every anchor's quote carry
   `[[wikilink]]` syntax pointing at a genuinely orphaned KB page, taken
   through the full operation lifecycle, must never move `lint_vault`'s
   violation set or the topic's content-page count -- and the orphan must
   stay reported. `tests/test_score_isolation_characterization.py` proves
   this for a *raw-written* note; this proves it survives real operations.
3. **Cross-operation adversarial `pages_used`** -- a real `detach()` call
   (not a hand-placed anchor) must remove a page from `promote_note`'s
   grounding set, and a note whose only anchor was really detached must be
   rejected, not silently promoted.
4. **Topic enumeration stays clean** across the full lifecycle -- no
   operation here may cause a phantom topic to appear.

`reconcile.py` (zero-writes) and the `dispatch_telemetry` guard on the MCP
dispatcher actions are out of scope: neither has landed yet (both depend on
later steps in this wave).
"""

import shutil
from pathlib import Path

from knotica.core.gap_classifier import gaps_path
from knotica.core.lint import LintCheck, Violation, lint_vault
from knotica.core.operations.capture_note import capture_note
from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.operations.promote_note import promote_note
from knotica.core.operations.reanchor_note import archive, detach, reanchor
from knotica.core.records import parse_qa_jsonl
from knotica.core.status import gather_wiki_status
from knotica.core.vcs import VaultVcs
from knotica.evals.harness import _count_content_pages
from knotica.store import LocalFSStore
from support.vault import git_commit_subjects, run_git

TOPIC = "agentic-systems"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _seed_page(vault: Path, relpath: str, content: str, message: str) -> None:
    target = vault / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", message)


def _success(result: dict[str, object]) -> dict[str, object]:
    assert "error" not in result, f"expected success, got an error envelope: {result!r}"
    return result


def _note_path(note_id: str) -> str:
    return f"notes/{TOPIC}/{note_id}.md"


def _read(vault: Path, relpath: str) -> str:
    return (vault / relpath).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. A single note's full operation lifecycle -- the consolidated proof.
# ---------------------------------------------------------------------------


def test_a_notes_full_operation_lifecycle_never_leaks_anchor_quotes_body_or_path(
    template_vault: Path,
):
    """`capture` -> `reanchor` -> `detach` -> `archive` -> `promote(trainset)` ->
    `promote(gap)`, all on one note. Two distinct, verbatim-KB-page phrases
    (the anchor of record's quote, and the reanchor's replacement quote on a
    different page) and one distinct, private note body must never appear in
    `log.md`, any commit subject, `qa.jsonl`, or `gaps.jsonl`. The note's own
    path must never appear in either of those files either -- except inside
    `gaps.jsonl`'s `reported_reason`, the one deliberate exception the design
    names explicitly.
    """
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    quote_of_record = "the shoggoth's rlhf smile is not alignment, it is occlusion"
    reanchor_quote = "goodhart's law wearing an alignment researcher's badge"
    private_body = "a private reflection on rlhf sycophancy that must never surface anywhere public"

    page_one = f"{TOPIC}/lifecycle-quote-one.md"
    _seed_page(
        template_vault, page_one, f"# Page one\n\n{quote_of_record}.\n", "test: seed page one"
    )
    page_two = f"{TOPIC}/lifecycle-quote-two.md"
    _seed_page(
        template_vault, page_two, f"# Page two\n\n{reanchor_quote}.\n", "test: seed page two"
    )

    captured = _success(
        capture_note(
            store,
            template_vault,
            vcs,
            TOPIC,
            private_body,
            quote=quote_of_record,
            pages=[page_one],
            intent="question",
        )
    )
    note_id = captured["note_id"]
    assert isinstance(note_id, str)

    _success(
        reanchor(store, template_vault, vcs, TOPIC, note_id, 0, page=page_two, quote=reanchor_quote)
    )
    _success(detach(store, template_vault, vcs, TOPIC, note_id, 1))
    _success(archive(store, template_vault, vcs, TOPIC, note_id))
    _success(
        promote_note(
            store,
            template_vault,
            TOPIC,
            note_id,
            "trainset",
            question="A clean grounded question, unrelated to the note's own text.",
            answer="A clean grounded answer, unrelated to the note's own text.",
        )
    )
    _success(
        promote_note(
            store,
            template_vault,
            TOPIC,
            note_id,
            "gap",
            question="A different clean question, also unrelated to the note's own text.",
        )
    )

    forbidden = (quote_of_record, reanchor_quote, private_body, _note_path(note_id))

    log_text = _read(template_vault, "log.md")
    for phrase in forbidden:
        assert phrase not in log_text, f"{phrase!r} leaked into log.md across the full lifecycle"

    for subject in git_commit_subjects(template_vault):
        for phrase in forbidden:
            assert phrase not in subject, f"{phrase!r} leaked into commit subject {subject!r}"

    qa_text = _read(template_vault, qa_dataset_path(TOPIC))
    for phrase in forbidden:
        assert phrase not in qa_text, f"{phrase!r} leaked into qa.jsonl"

    gaps_text = store.read_text(gaps_path(TOPIC))
    for phrase in (quote_of_record, reanchor_quote, private_body):
        assert phrase not in gaps_text, f"{phrase!r} leaked into gaps.jsonl"
    assert _note_path(note_id) in gaps_text, (
        "the note pointer in reported_reason is the one deliberate exception across the "
        "whole lifecycle -- it must be present here"
    )


# ---------------------------------------------------------------------------
# 2. The standing eval-scalar characterization -- wikilinks, real operations.
# ---------------------------------------------------------------------------

_ORPHAN_STEM = "contamination-hunt-orphan"
_ORPHAN_PAGE = f"{TOPIC}/{_ORPHAN_STEM}.md"


def _lint(vault: Path, topic: str) -> list[Violation]:
    return lint_vault(LocalFSStore(vault), topic)


def _checks(violations: list[Violation]) -> set[LintCheck]:
    return {violation.check for violation in violations}


def _plant_unlinked_page(vault: Path) -> None:
    """A genuine orphan: no page in the template links to it (mirrors
    `tests/test_score_isolation_characterization.py`'s `plant_unlinked_page`).
    """
    _seed_page(
        vault,
        _ORPHAN_PAGE,
        "---\n"
        f"type: concept\ntopic: {TOPIC}\ncreated: 2026-07-30\n"
        "updated: 2026-07-30\nconfidence: medium\nsources: [wang2024awm]\n"
        "status: active\ntags: [demo]\n---\n\n# Contamination hunt orphan\n",
        "test: plant orphan page",
    )


def test_note_wikilinks_surviving_the_full_operation_lifecycle_never_move_lint_or_page_count(
    template_vault: Path, tmp_path: Path
):
    """A note whose body *and* both of its anchors' quotes carry `[[wikilink]]`
    syntax pointing at a genuinely orphaned page -- taken through capture,
    reanchor, detach, and archive -- must never move a single lint finding or
    the topic's content-page count, and the orphan must stay reported. This
    is the real-operation counterpart to `test_score_isolation_characterization.py`,
    which pins the same guarantee for a raw-written note.
    """
    _plant_unlinked_page(template_vault)
    # The anchor pages are seeded into the *baseline* too, before `violations_before`
    # is computed -- their own frontmatter is deliberately minimal (irrelevant to this
    # hunt) and must contribute identical noise on both sides of the comparison. The
    # only difference the assertion below is entitled to see is the note itself.
    anchor_page = f"{TOPIC}/wikilink-lifecycle-anchor.md"
    anchor_sentence = f"This passage cites [[{TOPIC}/{_ORPHAN_STEM}]] directly."
    _seed_page(
        template_vault,
        anchor_page,
        f"# Anchor page\n\n{anchor_sentence}\n",
        "test: seed anchor page",
    )
    reanchor_page = f"{TOPIC}/wikilink-lifecycle-reanchor.md"
    reanchor_sentence = f"A second passage also names [[{TOPIC}/{_ORPHAN_STEM}]]."
    _seed_page(
        template_vault,
        reanchor_page,
        f"# Reanchor page\n\n{reanchor_sentence}\n",
        "test: seed reanchor page",
    )
    violations_before = _lint(template_vault, TOPIC)
    pages_before = _count_content_pages(LocalFSStore(template_vault), TOPIC)
    assert LintCheck.PAGE_ORPHANED in _checks(violations_before), (
        "fixture sanity: the planted page must be a genuine orphan before any note exists"
    )

    vault = tmp_path / "vault-wikilink-lifecycle"
    shutil.copytree(template_vault, vault)
    store = LocalFSStore(vault)
    vcs = VaultVcs(vault)

    note_body = f"My private reflection also links [[{TOPIC}/{_ORPHAN_STEM}]] for my own reference."

    captured = _success(
        capture_note(
            store, vault, vcs, TOPIC, note_body, quote=anchor_sentence, pages=[anchor_page]
        )
    )
    note_id = captured["note_id"]
    assert isinstance(note_id, str)
    _success(
        reanchor(store, vault, vcs, TOPIC, note_id, 0, page=reanchor_page, quote=reanchor_sentence)
    )
    _success(detach(store, vault, vcs, TOPIC, note_id, 1))
    _success(archive(store, vault, vcs, TOPIC, note_id))

    violations_after = _lint(vault, TOPIC)
    pages_after = _count_content_pages(store, TOPIC)

    assert violations_after == violations_before, (
        "a note carrying [[wikilink]] syntax in its body and every anchor's quote, taken "
        "through capture/reanchor/detach/archive, must not move a single lint finding"
    )
    assert pages_after == pages_before
    assert LintCheck.PAGE_ORPHANED in _checks(violations_after), (
        "the orphaned page must still be reported orphaned -- a note's wikilinks must never "
        "count as an inbound edge, however many operations have touched the note"
    )


# ---------------------------------------------------------------------------
# 3. Cross-operation adversarial pages_used -- real reanchor/detach, not
#    hand-placed anchors.
# ---------------------------------------------------------------------------


def test_a_real_detach_excludes_that_pages_grounding_from_a_later_promotion(
    template_vault: Path,
):
    """Two independent, real anchors on two different pages; detaching one
    through the actual `detach()` operation must remove exactly that page
    from `promote_note`'s grounding set -- built from real operation output,
    not a hand-placed `AnchorRecord`.
    """
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    page_a = f"{TOPIC}/adversarial-detach-a.md"
    _seed_page(template_vault, page_a, "# A\n\nonly passage on page A.\n", "test: seed A")
    page_b = f"{TOPIC}/adversarial-detach-b.md"
    _seed_page(template_vault, page_b, "# B\n\nonly passage on page B.\n", "test: seed B")

    captured = _success(
        capture_note(
            store,
            template_vault,
            vcs,
            TOPIC,
            "a reflection spanning two pages",
            quote="only passage on page A.",
            pages=[page_a],
            intent="question",
        )
    )
    note_id = captured["note_id"]
    assert isinstance(note_id, str)
    _success(
        reanchor(
            store,
            template_vault,
            vcs,
            TOPIC,
            note_id,
            0,
            page=page_b,
            quote="only passage on page B.",
        )
    )
    # Index 0 (page A) is a distinct supersession group from index 1 (page B)
    # -- both are live until this detach targets page A's anchor specifically.
    _success(detach(store, template_vault, vcs, TOPIC, note_id, 0))

    result = _success(
        promote_note(
            store,
            template_vault,
            TOPIC,
            note_id,
            "trainset",
            question="Does the detached page still ground this question?",
            answer="No -- only page B grounds it.",
        )
    )
    assert result["appended"] is True

    records = parse_qa_jsonl(_read(template_vault, qa_dataset_path(TOPIC)))
    assert len(records) == 1
    assert records[0].pages_used == (page_b,), (
        "page A's anchor was really detached (not hand-placed) -- it must not ground the "
        "promoted question, and page B must be the only grounding page"
    )


def test_promoting_a_note_whose_only_anchor_was_really_detached_is_rejected(
    template_vault: Path,
):
    """A note with exactly one anchor, detached through the real `detach()`
    operation, has zero live grounding pages left -- `promote_note(target=
    "trainset")` must reject it, never silently promote with an empty
    `pages_used`.
    """
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    page = f"{TOPIC}/adversarial-all-detached.md"
    _seed_page(
        template_vault, page, "# Page\n\na passage that will be detached.\n", "test: seed page"
    )

    captured = _success(
        capture_note(
            store,
            template_vault,
            vcs,
            TOPIC,
            "a reflection about to lose its only anchor",
            quote="a passage that will be detached.",
            pages=[page],
            intent="question",
        )
    )
    note_id = captured["note_id"]
    assert isinstance(note_id, str)
    _success(detach(store, template_vault, vcs, TOPIC, note_id, 0))

    result = promote_note(
        store,
        template_vault,
        TOPIC,
        note_id,
        "trainset",
        question="Can a fully detached note ground a question?",
        answer="No.",
    )

    assert "error" in result
    error = result["error"]
    assert isinstance(error, dict)
    assert error["code"] == "INVALID_ARGUMENT"
    assert parse_qa_jsonl(_read(template_vault, qa_dataset_path(TOPIC))) == [], (
        "a rejected promotion must append nothing"
    )


# ---------------------------------------------------------------------------
# 4. Topic enumeration stays clean across the full lifecycle.
# ---------------------------------------------------------------------------


def test_running_every_phase_2_operation_creates_no_phantom_topic(template_vault: Path):
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    before = gather_wiki_status(store, template_vault, view="scope")["topics"]

    page = f"{TOPIC}/topic-enumeration-target.md"
    _seed_page(template_vault, page, "# Page\n\na passage worth noting.\n", "test: seed page")
    captured = _success(
        capture_note(
            store,
            template_vault,
            vcs,
            TOPIC,
            "a reflection exercising every phase 2 operation",
            quote="a passage worth noting.",
            pages=[page],
            intent="question",
        )
    )
    note_id = captured["note_id"]
    assert isinstance(note_id, str)
    # Accept-projection reanchor: same page, no drift -- supersedes index 0.
    _success(reanchor(store, template_vault, vcs, TOPIC, note_id, 0))
    _success(detach(store, template_vault, vcs, TOPIC, note_id, 1))
    _success(archive(store, template_vault, vcs, TOPIC, note_id))
    _success(
        promote_note(
            store,
            template_vault,
            TOPIC,
            note_id,
            "gap",
            question="Does this operation sequence conjure a phantom topic?",
        )
    )

    after = gather_wiki_status(LocalFSStore(template_vault), template_vault, view="scope")["topics"]

    assert before == [TOPIC]
    assert after == before, (
        "notes/, .knotica/datasets/, and .knotica/gaps/ must all stay invisible to topic "
        "enumeration -- no phantom topic may appear after a note's full operation lifecycle"
    )

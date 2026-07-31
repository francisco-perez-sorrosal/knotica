"""Behavioral contract of `promote_note` -- the eval bridge crossing out of the
notes layer (dec-059).

Two destinations, both human-gated (an explicit call, never automatic), each
landing exactly one commit through an already-shipped operation this module
reuses rather than re-implements: `target="trainset"` calls
`core.operations.curate_example.curate_example`; `target="gap"` calls
`core.gapfill.report_gap`. `target="golden"` always rejects -- `freeze()`
enforces trainset/golden disjointness, so that destination is a one-way door
that belongs behind `golden_review`, not this action.

**The load-bearing behavior in this file**: `pages_used` for a trainset
promotion is *always* derived server-side from the note's currently-live
anchored pages (`knotica.core.notes.anchor.live_anchors` -- the newest record
per distinct page, excluding any page whose own newest record is detached),
never from a caller-supplied value and never from the note's own path. There
is no `pages_used` parameter on `promote_note` at all -- a caller cannot
inject a note path because there is nowhere to inject it. A note whose live
anchors resolve to zero non-empty page paths (every page's chain detached, or
the only anchor is `topic`-fidelity with no page at all) has nothing to
ground an eval question in and is a typed rejection, not a silent empty
`pages_used`.

Gaps filed through `target="gap"` reuse the existing `origin="reported"` gap
shape (no fourth origin) and are offered only for notes whose `intent` is
`dispute`, `gap`, or `question` -- never a plain `reflection`. Provenance
survives via `GapRecord.reported_reason`, built here as
`f"note:{path}#{anchor_index}"`.

**Contamination.** Promotion writes into `qa.jsonl` or the gap queue, both
scored-adjacent surfaces. The note's own *body* (the user's private
reflection) and the note's own *path* must never reach either file, `log.md`,
or any commit subject -- only the caller-supplied `question`/`answer` and the
note's anchored KB page paths are legitimate content there. The one
deliberate exception is the gap pointer in `reported_reason`, which *is* the
note's path by design and belongs nowhere else.

Fixture notes are hand-placed via `serialize_note` (mirroring
`test_reanchor_note.py`'s technique) so every anchor history below --
including a page whose entire chain has been detached, and a `topic`-fidelity
anchor with no page at all -- is constructed exactly, without depending on
`capture_note`'s own resolution logic.

**Interface assumed** (no production code exists yet; this is the contract
the implementer must satisfy):
``promote_note(store, vault_root, topic, note_id, target, *, question="",
answer="", verdict="good") -> dict[str, object]``. No `pages_used` parameter
(derived internally) and no `vcs` parameter (unlike `reanchor_note.py`,
nothing here re-projects a quote against the live page). See the dispatch
report for what is deliberately left untested: idempotent-vs-error on a
*second* promotion carrying a *different* question, any `promoted:`
frontmatter recording the crossing, and `question` defaulting to the note's
own body -- all flagged as open ambiguities rather than guessed at here.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from knotica.core.gap_classifier import gaps_path
from knotica.core.notes.anchor import AnchorRecord, NoteDocument, serialize_note
from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.records import parse_gaps_jsonl, parse_qa_jsonl
from knotica.store import LocalFSStore
from support.vault import git_commit_count, git_commit_subjects, parse_knotica_commit, run_git

TOPIC = "agentic-systems"


# ---------------------------------------------------------------------------
# Deferred-import call helper -- the RED trigger for this whole file
# ---------------------------------------------------------------------------


def _promote(
    vault: Path,
    topic: str,
    note_id: str,
    target: str,
    *,
    question: str = "",
    answer: str = "",
    verdict: str = "good",
) -> Mapping[str, object]:
    """Invoke `promote_note`; imported lazily so collection succeeds before the module exists."""
    from knotica.core.operations.promote_note import promote_note

    result = promote_note(
        LocalFSStore(vault),
        vault,
        topic,
        note_id,
        target,
        question=question,
        answer=answer,
        verdict=verdict,
    )
    assert isinstance(result, Mapping), f"expected an envelope mapping, got {result!r}"
    return result


def _success(result: Mapping[str, object]) -> Mapping[str, object]:
    assert "error" not in result, f"expected success, got an error envelope: {result!r}"
    return result


def _error_code(result: Mapping[str, object]) -> str:
    assert "error" in result, f"expected a failure envelope, got success: {result!r}"
    error = result["error"]
    assert isinstance(error, Mapping)
    return str(error["code"])


def _error_message(result: Mapping[str, object]) -> str:
    assert "error" in result, f"expected a failure envelope, got success: {result!r}"
    error = result["error"]
    assert isinstance(error, Mapping)
    return str(error["message"])


# ---------------------------------------------------------------------------
# Fixture helpers -- notes hand-placed with a controlled anchor history
# ---------------------------------------------------------------------------


def _seed_page(vault: Path, relpath: str, content: str, message: str) -> None:
    target = vault / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", message)


def _note_path(note_id: str) -> str:
    return f"notes/{TOPIC}/{note_id}.md"


def _seed_note(
    vault: Path,
    note_id: str,
    anchors: tuple[AnchorRecord, ...],
    *,
    intent: str = "reflection",
    body: str = "a hand-authored note seeded directly for a test fixture",
) -> None:
    """Hand-place a note with a given anchor history and intent -- no dependency
    on `capture_note`'s own resolution logic, so a page's chain can be
    constructed already-detached or already topic-fidelity-only.
    """
    document = NoteDocument(
        id=note_id,
        topic=TOPIC,
        intent=intent,
        created="2026-07-30T08:00:00Z",
        updated="2026-07-30T08:00:00Z",
        status="active",
        tags=(),
        body=body,
        anchors=anchors,
    )
    path = vault / _note_path(note_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_note(document), encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: seed note {note_id} with {len(anchors)} anchor(s)")


def _pinned(page: str, quote: str, *, pinned_at: str = "9f1a3c0") -> AnchorRecord:
    return AnchorRecord(page=page, heading="", fidelity="span", pinned_at=pinned_at, quote=quote)


def _detached(page: str, quote: str, *, pinned_at: str = "a3f9c21") -> AnchorRecord:
    return AnchorRecord(
        page=page, heading="", fidelity="span", pinned_at=pinned_at, quote=quote, kind="detached"
    )


def _topic_fidelity(quote: str, *, pinned_at: str = "9f1a3c0") -> AnchorRecord:
    return AnchorRecord(page="", heading="", fidelity="topic", pinned_at=pinned_at, quote=quote)


def _read_qa_records(vault: Path) -> list:
    path = vault / qa_dataset_path(TOPIC)
    if not path.exists():
        return []
    return parse_qa_jsonl(path.read_text(encoding="utf-8"))


def _read_gap_records(vault: Path) -> list:
    store = LocalFSStore(vault)
    path = gaps_path(TOPIC)
    if not store.exists(path):
        return []
    return parse_gaps_jsonl(store.read_text(path))


# ---------------------------------------------------------------------------
# target=trainset -- pages_used derivation is the load-bearing behavior
# ---------------------------------------------------------------------------


def test_promote_to_trainset_derives_pages_used_only_from_the_live_anchor(template_vault: Path):
    """A page whose chain ends detached must not ground the question; an
    independent, still-live page must -- never both, and never the note's
    own path."""
    note_id = "20260730-100000-promote-superseded-page"
    page_a = f"{TOPIC}/promote-superseded-a.md"
    page_b = f"{TOPIC}/promote-live-b.md"
    _seed_page(
        template_vault, page_a, "# A\n\nthe passage on the superseded page.\n", "test: seed A"
    )
    _seed_page(template_vault, page_b, "# B\n\nthe passage on the live page.\n", "test: seed B")
    _seed_note(
        template_vault,
        note_id,
        (
            _pinned(page_a, "the passage on the superseded page."),
            _detached(page_a, "the passage on the superseded page."),
            _pinned(page_b, "the passage on the live page."),
        ),
        intent="question",
    )

    _success(
        _promote(
            template_vault,
            TOPIC,
            note_id,
            "trainset",
            question="Does the superseded page still ground this question?",
            answer="No -- only the live page grounds it.",
        )
    )

    records = _read_qa_records(template_vault)
    assert len(records) == 1
    assert records[0].pages_used == (page_b,), (
        "the detached page must be excluded and the live page included -- never both"
    )
    assert _note_path(note_id) not in records[0].pages_used, (
        "the note's own path must never appear in pages_used -- it selects zero KB pages"
    )


def test_promoting_a_note_with_no_live_anchors_to_trainset_is_rejected(template_vault: Path):
    note_id = "20260730-100100-promote-all-detached"
    page_a = f"{TOPIC}/promote-all-detached-a.md"
    _seed_page(template_vault, page_a, "# A\n\na passage that will be detached.\n", "test: seed A")
    _seed_note(
        template_vault,
        note_id,
        (
            _pinned(page_a, "a passage that will be detached."),
            _detached(page_a, "a passage that will be detached."),
        ),
        intent="question",
    )
    commits_before = git_commit_count(template_vault)

    result = _promote(
        template_vault,
        TOPIC,
        note_id,
        "trainset",
        question="Can a fully detached note ground a question?",
        answer="No.",
    )

    assert _error_code(result) == "INVALID_ARGUMENT", (
        "a note with no live anchored page has nothing to ground the question in"
    )
    assert _read_qa_records(template_vault) == [], "a rejected promotion must append nothing"
    assert git_commit_count(template_vault) == commits_before, (
        "a rejected promotion makes no commit"
    )


def test_promoting_a_topic_fidelity_only_note_to_trainset_is_rejected(template_vault: Path):
    """A `topic`-fidelity anchor is live (never detached) but names no page at
    all -- it must still be rejected, not silently promoted with an empty
    `pages_used`."""
    note_id = "20260730-100200-promote-topic-fidelity-only"
    _seed_note(
        template_vault,
        note_id,
        (_topic_fidelity("the passage that provoked the note, pinned at topic level only"),),
        intent="question",
    )
    commits_before = git_commit_count(template_vault)

    result = _promote(
        template_vault,
        TOPIC,
        note_id,
        "trainset",
        question="Can a topic-only anchor ground a question?",
        answer="No.",
    )

    assert _error_code(result) == "INVALID_ARGUMENT"
    assert _read_qa_records(template_vault) == []
    assert git_commit_count(template_vault) == commits_before


def test_promote_to_trainset_makes_exactly_one_commit_reusing_curate_example(
    template_vault: Path,
):
    note_id = "20260730-100300-promote-one-commit"
    page = f"{TOPIC}/promote-one-commit.md"
    _seed_page(template_vault, page, "# Page\n\na passage worth promoting.\n", "test: seed page")
    _seed_note(
        template_vault, note_id, (_pinned(page, "a passage worth promoting."),), intent="question"
    )
    commits_before = git_commit_count(template_vault)

    _success(
        _promote(
            template_vault,
            TOPIC,
            note_id,
            "trainset",
            question="Is this passage promotable?",
            answer="Yes.",
        )
    )

    assert git_commit_count(template_vault) == commits_before + 1, (
        "the bridge is one VaultTransaction, one commit -- no extra write on top of "
        "curate_example's own"
    )
    parsed = parse_knotica_commit(git_commit_subjects(template_vault)[0])
    assert parsed is not None, "the commit subject must follow the frozen grammar"
    assert parsed["op"] == "curate_example", (
        "the commit must show curate_example's own op -- reuse, not a re-implementation"
    )


def test_promoting_the_identical_question_twice_to_trainset_is_a_no_op(template_vault: Path):
    note_id = "20260730-100400-promote-idempotent"
    page = f"{TOPIC}/promote-idempotent.md"
    _seed_page(
        template_vault, page, "# Page\n\na passage worth promoting once.\n", "test: seed page"
    )
    _seed_note(
        template_vault,
        note_id,
        (_pinned(page, "a passage worth promoting once."),),
        intent="question",
    )
    _success(
        _promote(
            template_vault,
            TOPIC,
            note_id,
            "trainset",
            question="Is repeated promotion a duplicate?",
            answer="No -- it is a no-op.",
        )
    )
    commits_after_first = git_commit_count(template_vault)

    _success(
        _promote(
            template_vault,
            TOPIC,
            note_id,
            "trainset",
            question="Is repeated promotion a duplicate?",
            answer="No -- it is a no-op.",
        )
    )

    assert len(_read_qa_records(template_vault)) == 1, (
        "promoting the identical (query, answer, verdict) twice must not append a second "
        "line, mirroring curate_example's own content-hash idempotency"
    )
    assert git_commit_count(template_vault) == commits_after_first, (
        "a no-op promotion makes no commit"
    )


# ---------------------------------------------------------------------------
# target=golden -- always rejected
# ---------------------------------------------------------------------------


def test_promote_target_golden_always_rejects_with_invalid_argument(template_vault: Path):
    note_id = "20260730-100500-promote-golden"
    page = f"{TOPIC}/promote-golden.md"
    _seed_page(template_vault, page, "# Page\n\na passage.\n", "test: seed page")
    _seed_note(template_vault, note_id, (_pinned(page, "a passage."),), intent="question")
    commits_before = git_commit_count(template_vault)

    result = _promote(template_vault, TOPIC, note_id, "golden", question="Any question at all?")

    assert _error_code(result) == "INVALID_ARGUMENT"
    assert _error_message(result) == (
        "promoting to the held-out (golden) set is deferred: trainset and golden must "
        "stay disjoint, so the choice is one-way and needs its own review gate"
    ), "the interface design's error grammar text is the documented, executable interface"
    assert git_commit_count(template_vault) == commits_before, (
        "golden always rejects before any write"
    )


# ---------------------------------------------------------------------------
# target=gap -- intent-gated
# ---------------------------------------------------------------------------


def test_promote_target_gap_on_a_reflection_note_is_rejected(template_vault: Path):
    note_id = "20260730-100600-promote-gap-reflection"
    page = f"{TOPIC}/promote-gap-reflection.md"
    _seed_page(template_vault, page, "# Page\n\na passage.\n", "test: seed page")
    _seed_note(template_vault, note_id, (_pinned(page, "a passage."),), intent="reflection")
    commits_before = git_commit_count(template_vault)

    result = _promote(template_vault, TOPIC, note_id, "gap", question="Is the wiki wrong here?")

    assert _error_code(result) == "INVALID_ARGUMENT"
    assert _error_message(result) == (
        "filing a gap needs a note whose intent is dispute, gap, or question; this one is "
        "a reflection"
    )
    assert _read_gap_records(template_vault) == []
    assert git_commit_count(template_vault) == commits_before


@pytest.mark.parametrize("intent", ["dispute", "gap", "question"])
def test_promote_target_gap_on_an_opted_in_intent_files_a_reported_gap(
    template_vault: Path, intent: str
):
    note_id = f"20260730-100700-promote-gap-{intent}"
    page = f"{TOPIC}/promote-gap-{intent}.md"
    _seed_page(template_vault, page, "# Page\n\na disputed passage.\n", "test: seed page")
    _seed_note(template_vault, note_id, (_pinned(page, "a disputed passage."),), intent=intent)
    question = f"Is the wiki wrong about the {intent} passage?"

    _success(_promote(template_vault, TOPIC, note_id, "gap", question=question))

    gaps = _read_gap_records(template_vault)
    assert len(gaps) == 1
    assert gaps[0].origin == "reported", "a note-filed gap reuses the existing reported origin"
    assert gaps[0].question == question, (
        "the filed gap carries the caller's derived question, never the note body"
    )
    assert gaps[0].reference_pages == (page,)
    assert gaps[0].reported_reason == f"note:{_note_path(note_id)}#0", (
        "provenance is a note pointer -- topic-relative path and the anchor of record's "
        "0-based index -- for a single-anchor note that index is unambiguously 0"
    )


def test_promoting_the_identical_question_twice_to_gap_is_a_no_op(template_vault: Path):
    note_id = "20260730-100800-promote-gap-idempotent"
    page = f"{TOPIC}/promote-gap-idempotent.md"
    _seed_page(template_vault, page, "# Page\n\na passage worth filing once.\n", "test: seed page")
    _seed_note(
        template_vault, note_id, (_pinned(page, "a passage worth filing once."),), intent="gap"
    )
    question = "Does refiling the identical question spam the queue?"
    _success(_promote(template_vault, TOPIC, note_id, "gap", question=question))
    commits_after_first = git_commit_count(template_vault)

    _success(_promote(template_vault, TOPIC, note_id, "gap", question=question))

    assert len(_read_gap_records(template_vault)) == 1, (
        "re-filing the identical question must not spam the queue, mirroring report_gap's "
        "own (qa_id, fault_class) open-dedup"
    )
    assert git_commit_count(template_vault) == commits_after_first


# ---------------------------------------------------------------------------
# Contamination -- the note body and the note path must never leak
# ---------------------------------------------------------------------------


def test_promote_to_trainset_never_leaks_the_notes_body_or_path(template_vault: Path):
    distinctive_body = "the shoggoth wears a smiley-face mask over an emergent objective"
    note_id = "20260730-100900-promote-contamination-trainset"
    page = f"{TOPIC}/promote-contamination-trainset.md"
    _seed_page(
        template_vault, page, "# Page\n\na clean, unremarkable passage.\n", "test: seed page"
    )
    _seed_note(
        template_vault,
        note_id,
        (_pinned(page, "a clean, unremarkable passage."),),
        intent="question",
        body=distinctive_body,
    )

    _success(
        _promote(
            template_vault,
            TOPIC,
            note_id,
            "trainset",
            question="A clean question, unrelated to the note body.",
            answer="A clean answer, unrelated to the note body.",
        )
    )

    qa_text = (template_vault / qa_dataset_path(TOPIC)).read_text(encoding="utf-8")
    assert distinctive_body not in qa_text, (
        "the note's private reflection must never reach qa.jsonl"
    )
    assert _note_path(note_id) not in qa_text, "the note's own path must never reach qa.jsonl"
    log_text = (template_vault / "log.md").read_text(encoding="utf-8")
    assert distinctive_body not in log_text
    assert _note_path(note_id) not in log_text
    for subject in git_commit_subjects(template_vault):
        assert distinctive_body not in subject
        assert _note_path(note_id) not in subject


def test_promote_target_gap_never_leaks_the_notes_body_and_carries_the_path_only_in_reported_reason(
    template_vault: Path,
):
    distinctive_body = "reward hacking is Goodhart's law wearing a lab coat"
    note_id = "20260730-101000-promote-contamination-gap"
    page = f"{TOPIC}/promote-contamination-gap.md"
    _seed_page(
        template_vault, page, "# Page\n\na clean, unremarkable passage.\n", "test: seed page"
    )
    _seed_note(
        template_vault,
        note_id,
        (_pinned(page, "a clean, unremarkable passage."),),
        intent="dispute",
        body=distinctive_body,
    )

    _success(
        _promote(
            template_vault,
            TOPIC,
            note_id,
            "gap",
            question="A clean question, unrelated to the note body.",
        )
    )

    store = LocalFSStore(template_vault)
    gaps_text = store.read_text(gaps_path(TOPIC))
    assert distinctive_body not in gaps_text, (
        "the note's private reflection must never reach gaps.jsonl"
    )
    assert _note_path(note_id) in gaps_text, (
        "the note pointer in reported_reason is the one deliberate exception -- it must be "
        "present here"
    )
    log_text = (template_vault / "log.md").read_text(encoding="utf-8")
    assert distinctive_body not in log_text
    assert _note_path(note_id) not in log_text, (
        "the note pointer belongs only in reported_reason, never in log.md"
    )
    for subject in git_commit_subjects(template_vault):
        assert distinctive_body not in subject
        assert _note_path(note_id) not in subject, (
            "the note pointer belongs only in reported_reason, never in a commit subject"
        )


def test_promoting_to_trainset_without_a_question_is_rejected(template_vault: Path):
    """An eval example with no question is not an eval example.

    The error grammar in the interface design names this case explicitly, but
    nothing under ``src/`` enforced it: a promotion with the schema defaults for
    every optional argument appended ``{"query": "", "answer": ""}`` to the
    trainset and committed it.
    """
    note_id = "20260730-100600-promote-no-question"
    page = f"{TOPIC}/promote-no-question.md"
    _seed_page(template_vault, page, "# A\n\na live grounding passage.\n", "test: seed page")
    _seed_note(
        template_vault,
        note_id,
        (_pinned(page, "a live grounding passage."),),
        intent="reflection",
    )
    commits_before = git_commit_count(template_vault)

    result = _promote(template_vault, TOPIC, note_id, "trainset", question="   ", answer="No.")

    assert _error_code(result) == "INVALID_ARGUMENT"
    assert "question" in _error_message(result)
    assert _read_qa_records(template_vault) == [], "a rejected promotion must append nothing"
    assert git_commit_count(template_vault) == commits_before, (
        "a rejected promotion makes no commit"
    )


def test_promoting_to_trainset_without_an_answer_is_rejected(template_vault: Path):
    """A trainset record whose answer is empty but whose verdict says ``good``
    asserts that an empty string was a good answer -- it silently poisons the
    substrate the eval instrument trains on."""
    note_id = "20260730-100700-promote-no-answer"
    page = f"{TOPIC}/promote-no-answer.md"
    _seed_page(template_vault, page, "# A\n\nanother live grounding passage.\n", "test: seed page")
    _seed_note(
        template_vault,
        note_id,
        (_pinned(page, "another live grounding passage."),),
        intent="question",
    )
    commits_before = git_commit_count(template_vault)

    result = _promote(
        template_vault, TOPIC, note_id, "trainset", question="Does this ground?", answer=""
    )

    assert _error_code(result) == "INVALID_ARGUMENT"
    assert "answer" in _error_message(result)
    assert _read_qa_records(template_vault) == []
    assert git_commit_count(template_vault) == commits_before


def test_promoting_to_gap_without_a_question_is_rejected(template_vault: Path):
    """The gap arm reaches an outbound discovery query, so an empty question
    there is worse than a useless record."""
    note_id = "20260730-100800-gap-no-question"
    page = f"{TOPIC}/gap-no-question.md"
    _seed_page(template_vault, page, "# A\n\na disputed passage.\n", "test: seed page")
    _seed_note(template_vault, note_id, (_pinned(page, "a disputed passage."),), intent="dispute")
    commits_before = git_commit_count(template_vault)

    result = _promote(template_vault, TOPIC, note_id, "gap", question="")

    assert _error_code(result) == "INVALID_ARGUMENT"
    assert "question" in _error_message(result)
    assert git_commit_count(template_vault) == commits_before

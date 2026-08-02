"""Behavioral contract of the post-merge reconciliation pass.

``core.notes.reconcile`` answers one question for a topic's drift queue: *did
anything change under a note since it was last resolved?* It is lazy and
git-derived -- no vault lock, no note-file write, no commit, and no
``core.loop.py`` call site. It is a notification accelerator, never a
correctness dependency: if it is skipped or never runs, lazy read-time
resolution (``core.notes.store.list_notes``) still gives the correct answer
on its own.

For every anchor whose *current* (HEAD-resolved) status is a drift-queue
member -- ``fuzzy``, ``orphaned``, or ``anchor-invalid`` -- the pass resolves
the same anchor a second time against the page's content one revision
earlier (the previous commit that touched the anchor's page) and reports
both outcomes as one transition record: ``(note_id, anchor_index, before,
after, rewritten_at, rewritten_by)``. Anchors currently resolving ``exact``,
``shifted``, or ``unanchored`` are not queue members and are skipped
entirely -- bounding the derivation to queue members, not a full re-scan of
every anchor on every page, is what keeps a topic-wide pass affordable.

``anchor-invalid`` is a fixed point of this comparison, not a genuine
before/after pair: the quote was never present in the historical blob the
anchor claims, so nothing about the *page* changed it, and it carries no
rewrite attribution (``rewritten_at``/``rewritten_by`` are both ``None``)
regardless of how much or how little history the page has.
"""

from datetime import datetime
from pathlib import Path

import pytest
from knotica.core.notes.anchor import AnchorRecord, NoteDocument, serialize_note
from knotica.core.notes_config import DEFAULT_COMPLETE_ORPHAN_THRESHOLD, DEFAULT_GUESS_THRESHOLD
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore
from support.vault import git_commit_count, git_status_porcelain, run_git

TOPIC = "agentic-systems"


def _anchor(**overrides: object) -> AnchorRecord:
    defaults: dict[str, object] = {
        "page": f"{TOPIC}/notes-target.md",
        "heading": "",
        "fidelity": "span",
        "pinned_at": "0000000",
        "quote": "a quote that will not matter for this test",
        "start": None,
    }
    defaults.update(overrides)
    return AnchorRecord(**defaults)


def _note(note_id: str, **overrides: object) -> NoteDocument:
    defaults: dict[str, object] = {
        "id": note_id,
        "topic": TOPIC,
        "intent": "reflection",
        "created": "2026-01-01T09:00:00Z",
        "updated": "2026-01-01T09:00:00Z",
        "status": "active",
        "tags": (),
        "body": "A loose thought.",
        "anchors": (_anchor(),),
        "skipped_anchor_count": 0,
    }
    defaults.update(overrides)
    return NoteDocument(**defaults)


def _write_note(vault: Path, note_id: str, document: NoteDocument) -> Path:
    """Write ``document`` under ``notes/<topic>/<id>.md`` -- no commit."""
    path = vault / "notes" / document.topic / f"{note_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_note(document), encoding="utf-8")
    return path


def _commit_all(vault: Path, message: str) -> str:
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", message)
    return run_git(vault, "rev-parse", "HEAD").strip()


def _write_and_commit_page(vault: Path, relpath: str, content: str, message: str) -> str:
    (vault / relpath).parent.mkdir(parents=True, exist_ok=True)
    (vault / relpath).write_text(content, encoding="utf-8")
    return _commit_all(vault, message)


# ---------------------------------------------------------------------------
# The headline transition: exact -> fuzzy
# ---------------------------------------------------------------------------


def test_a_page_rewritten_so_an_exact_anchor_becomes_fuzzy_reports_the_transition(
    template_vault: Path,
):
    from knotica.core.notes.reconcile import reconcile_notes

    quote = "the model has no persistent notion of the goal it is optimizing for"
    paraphrase = "the model retains no persistent notion of the goal it is optimizing for"
    page_relpath = f"{TOPIC}/agent-memory.md"
    original_page = (
        "Preface paragraph about incentives and Goodhart's law in machine learning.\n\n"
        f"{quote}\n\n"
        "Closing thoughts on the phenomenon."
    )
    page_sha = _write_and_commit_page(
        template_vault, page_relpath, original_page, "test: seed agent-memory page"
    )
    _write_note(
        template_vault,
        "20260101-090000-headline-note",
        _note(
            "20260101-090000-headline-note",
            anchors=(_anchor(page=page_relpath, pinned_at=page_sha, quote=quote),),
        ),
    )
    _commit_all(template_vault, "test: capture headline-note")
    rewritten_page = original_page.replace(quote, paraphrase)
    assert quote not in rewritten_page
    _write_and_commit_page(
        template_vault, page_relpath, rewritten_page, "vault: reword the agent-memory passage"
    )
    # A later, unrelated commit becomes the new HEAD without touching the page again --
    # rewritten_at/rewritten_by must attribute to the actual rewrite, not to whatever
    # commit happens to be HEAD when reconciliation runs.
    _write_and_commit_page(
        template_vault,
        f"{TOPIC}/unrelated-scratch.md",
        "Unrelated content.\n",
        "test: unrelated edit to a different page",
    )
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    transitions = reconcile_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=0.52,
        complete_orphan_threshold=0.35,
    )

    transition = next(t for t in transitions if t.note_id == "20260101-090000-headline-note")
    assert transition.anchor_index == 0
    assert transition.before == "exact"
    assert transition.after == "fuzzy"
    assert transition.rewritten_by == "vault: reword the agent-memory passage"
    datetime.fromisoformat(transition.rewritten_at)


# ---------------------------------------------------------------------------
# exact -> orphaned
# ---------------------------------------------------------------------------


def test_a_page_rewritten_past_recognition_reports_an_exact_to_orphaned_transition(
    template_vault: Path,
):
    from knotica.core.notes.reconcile import reconcile_notes

    quote = "the model learns to satisfy the metric rather than the goal"
    page_relpath = f"{TOPIC}/incentives.md"
    original_page = f"Preface.\n\n{quote}\n\nClosing thoughts."
    page_sha = _write_and_commit_page(
        template_vault, page_relpath, original_page, "test: seed page for orphaned test"
    )
    _write_note(
        template_vault,
        "20260101-090000-orphaned-test-note",
        _note(
            "20260101-090000-orphaned-test-note",
            anchors=(_anchor(page=page_relpath, pinned_at=page_sha, quote=quote),),
        ),
    )
    _commit_all(template_vault, "test: capture orphaned-test-note")
    rewritten_page = (
        "Preface.\n\nThis paragraph was rewritten entirely and no longer contains "
        "anything resembling the original wording.\n\nClosing thoughts."
    )
    _write_and_commit_page(
        template_vault, page_relpath, rewritten_page, "vault: replace the page entirely"
    )
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    transitions = reconcile_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    transition = next(t for t in transitions if t.note_id == "20260101-090000-orphaned-test-note")
    assert transition.before == "exact"
    assert transition.after == "orphaned"
    assert transition.rewritten_by == "vault: replace the page entirely"


# ---------------------------------------------------------------------------
# Already orphaned before and after: still a queue member, still reported
# ---------------------------------------------------------------------------


def test_an_anchor_already_orphaned_before_and_after_is_still_reported_as_a_queue_member(
    template_vault: Path,
):
    from knotica.core.notes.reconcile import reconcile_notes

    quote = "the model learns to satisfy the metric rather than the goal"
    page_relpath = f"{TOPIC}/double-orphan.md"
    original_page = f"Preface.\n\n{quote}\n\nClosing thoughts."
    page_sha = _write_and_commit_page(
        template_vault, page_relpath, original_page, "test: seed page for double-orphan test"
    )
    _write_note(
        template_vault,
        "20260101-090000-double-orphan-note",
        _note(
            "20260101-090000-double-orphan-note",
            anchors=(_anchor(page=page_relpath, pinned_at=page_sha, quote=quote),),
        ),
    )
    _commit_all(template_vault, "test: capture double-orphan-note")
    first_rewrite = (
        "Preface.\n\nThis paragraph was rewritten entirely and no longer contains "
        "anything resembling the original wording.\n\nClosing thoughts."
    )
    _write_and_commit_page(
        template_vault, page_relpath, first_rewrite, "vault: first rewrite (still orphaned)"
    )
    second_rewrite = first_rewrite + "\n\nA further, unrelated addendum appended after the fact."
    _write_and_commit_page(
        template_vault, page_relpath, second_rewrite, "vault: second rewrite (still orphaned)"
    )
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    transitions = reconcile_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    transition = next(t for t in transitions if t.note_id == "20260101-090000-double-orphan-note")
    assert transition.before == "orphaned"
    assert transition.after == "orphaned"
    assert transition.rewritten_by == "vault: second rewrite (still orphaned)"


# ---------------------------------------------------------------------------
# exact / shifted / unanchored are not queue members: no transition at all
# ---------------------------------------------------------------------------


def test_anchors_resolving_exact_shifted_or_unanchored_produce_no_transition(
    template_vault: Path,
):
    from knotica.core.notes.reconcile import reconcile_notes

    exact_page = f"{TOPIC}/exact-page.md"
    exact_sha = _write_and_commit_page(
        template_vault,
        exact_page,
        "An unremarkable sentence never touched again.\n",
        "test: seed exact page",
    )
    _write_note(
        template_vault,
        "20260101-090000-exact-note",
        _note(
            "20260101-090000-exact-note",
            anchors=(
                _anchor(
                    page=exact_page,
                    pinned_at=exact_sha,
                    quote="An unremarkable sentence never touched again.",
                ),
            ),
        ),
    )

    quote = "optimization pressure finds the cheapest path to a metric"
    shifted_page = f"{TOPIC}/shifted-page.md"
    shifted_sha = _write_and_commit_page(
        template_vault,
        shifted_page,
        f"# Scratch Page\n\nThe kernel of the idea was that {quote}.\n",
        "test: seed shifted page",
    )
    _write_note(
        template_vault,
        "20260101-091500-shifted-note",
        _note(
            "20260101-091500-shifted-note",
            anchors=(_anchor(page=shifted_page, pinned_at=shifted_sha, quote=quote),),
        ),
    )

    _write_note(
        template_vault,
        "20260101-093000-unanchored-note",
        _note(
            "20260101-093000-unanchored-note",
            anchors=(_anchor(page="", pinned_at="0000000", quote=""),),
        ),
    )
    _commit_all(template_vault, "test: capture exact, shifted, and unanchored notes")
    _write_and_commit_page(
        template_vault,
        shifted_page,
        "# Scratch Page\n\n"
        "A new opening paragraph, added later, has nothing to do with the original text.\n\n"
        f"The kernel of the idea was that {quote}.\n",
        "vault: prepend a paragraph to the shifted page",
    )
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    transitions = reconcile_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert transitions == ()


# ---------------------------------------------------------------------------
# anchor-invalid: reported, but with no rewrite attribution
# ---------------------------------------------------------------------------


def test_an_anchor_invalid_anchor_is_reported_with_no_rewrite_attribution(
    template_vault: Path,
):
    from knotica.core.notes.reconcile import reconcile_notes

    page_relpath = f"{TOPIC}/actively-maintained.md"
    page_sha = _write_and_commit_page(
        template_vault,
        page_relpath,
        "# Actively Maintained\n\nNone of this text matches the anchor's quote.\n",
        "test: seed actively-maintained page",
    )
    # The page has genuine rewrite history -- proving the missing rewrite
    # attribution below is a fixed anchor-invalid contract, not an artifact
    # of the page never having been touched again.
    _write_and_commit_page(
        template_vault,
        page_relpath,
        "# Actively Maintained\n\nStill none of this text matches the anchor's quote, "
        "even after an unrelated later edit.\n",
        "vault: unrelated later edit to the same page",
    )
    _write_note(
        template_vault,
        "20260101-090000-forged-anchor-note",
        _note(
            "20260101-090000-forged-anchor-note",
            anchors=(
                _anchor(
                    page=page_relpath,
                    pinned_at=page_sha,
                    quote="a quote that was never in the historical blob",
                ),
            ),
        ),
    )
    _commit_all(template_vault, "test: capture forged-anchor-note")
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    transitions = reconcile_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    transition = next(t for t in transitions if t.note_id == "20260101-090000-forged-anchor-note")
    assert transition.before == "anchor-invalid"
    assert transition.after == "anchor-invalid"
    assert transition.rewritten_at is None
    assert transition.rewritten_by is None


# ---------------------------------------------------------------------------
# Degraded mode: a page with no prior commit does not crash reconciliation
# ---------------------------------------------------------------------------


def test_a_page_touched_only_once_in_its_history_degrades_gracefully_without_raising(
    template_vault: Path,
):
    from knotica.core.notes.reconcile import reconcile_notes

    page_relpath = f"{TOPIC}/single-touch-page.md"
    # This is the only commit that will ever touch this page -- there is no
    # "previous commit that touched it" for reconciliation to diff against.
    _write_and_commit_page(
        template_vault,
        page_relpath,
        "# Single Touch\n\nSome real content that has nothing to do with the anchor's quote.\n",
        "test: seed single-touch page",
    )
    _write_note(
        template_vault,
        "20260101-090000-single-touch-note",
        _note(
            "20260101-090000-single-touch-note",
            anchors=(
                _anchor(
                    page=page_relpath,
                    pinned_at="0000000",
                    quote="a quote that was never in the historical blob",
                ),
            ),
        ),
    )
    _commit_all(template_vault, "test: capture single-touch-note")
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    transitions = reconcile_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    transition = next(t for t in transitions if t.note_id == "20260101-090000-single-touch-note")
    assert transition.after == "anchor-invalid"
    assert transition.rewritten_at is None
    assert transition.rewritten_by is None


# ---------------------------------------------------------------------------
# A multi-anchor note reports each anchor independently
# ---------------------------------------------------------------------------


def test_a_multi_anchor_note_reports_each_drifted_anchor_independently(template_vault: Path):
    from knotica.core.notes.reconcile import reconcile_notes

    quote_fuzzy = "the model has no persistent notion of the goal it is optimizing for"
    paraphrase = "the model retains no persistent notion of the goal it is optimizing for"
    page_one = f"{TOPIC}/page-one.md"
    original_one = (
        "Preface paragraph about incentives and Goodhart's law in machine learning.\n\n"
        f"{quote_fuzzy}\n\n"
        "Closing thoughts on the phenomenon."
    )
    page_one_sha = _write_and_commit_page(
        template_vault, page_one, original_one, "test: seed page one"
    )

    quote_orphaned = "network latency spikes correlate strongly with the nightly batch export job"
    page_two = f"{TOPIC}/page-two.md"
    original_two = f"Intro.\n\n{quote_orphaned}\n\nOutro."
    page_two_sha = _write_and_commit_page(
        template_vault, page_two, original_two, "test: seed page two"
    )

    _write_note(
        template_vault,
        "20260101-090000-multi-anchor-note",
        _note(
            "20260101-090000-multi-anchor-note",
            anchors=(
                _anchor(page=page_one, pinned_at=page_one_sha, quote=quote_fuzzy),
                _anchor(page=page_two, pinned_at=page_two_sha, quote=quote_orphaned),
            ),
        ),
    )
    _commit_all(template_vault, "test: capture multi-anchor-note")
    _write_and_commit_page(
        template_vault,
        page_one,
        original_one.replace(quote_fuzzy, paraphrase),
        "vault: reword page one",
    )
    _write_and_commit_page(
        template_vault,
        page_two,
        "Intro.\n\nA gardening club meets every Tuesday to discuss heirloom tomato "
        "cultivation techniques.\n\nOutro.",
        "vault: replace page two entirely",
    )
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    transitions = reconcile_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=0.55,
        complete_orphan_threshold=0.35,
    )

    note_transitions = {
        t.anchor_index: t for t in transitions if t.note_id == "20260101-090000-multi-anchor-note"
    }
    assert set(note_transitions) == {0, 1}
    assert note_transitions[0].before == "exact"
    assert note_transitions[0].after == "fuzzy"
    assert note_transitions[1].before == "exact"
    assert note_transitions[1].after == "orphaned"


# ---------------------------------------------------------------------------
# Read-only proof
# ---------------------------------------------------------------------------


def test_reconciliation_never_writes_to_the_vault(
    template_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from knotica.core.notes.reconcile import reconcile_notes

    quote = "the model learns to satisfy the metric rather than the goal"
    page_relpath = f"{TOPIC}/read-only-check.md"
    original_page = f"Preface.\n\n{quote}\n\nClosing thoughts."
    page_sha = _write_and_commit_page(
        template_vault, page_relpath, original_page, "test: seed page for read-only check"
    )
    _write_note(
        template_vault,
        "20260101-090000-read-only-note",
        _note(
            "20260101-090000-read-only-note",
            anchors=(_anchor(page=page_relpath, pinned_at=page_sha, quote=quote),),
        ),
    )
    _commit_all(template_vault, "test: capture read-only-note")
    rewritten_page = (
        "Preface.\n\nThis paragraph was rewritten entirely and no longer contains "
        "anything resembling the original wording.\n\nClosing thoughts."
    )
    _write_and_commit_page(
        template_vault, page_relpath, rewritten_page, "vault: replace the page entirely"
    )

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("reconcile_notes must never acquire the vault lock")

    monkeypatch.setattr("knotica.core.lock.vault_lock", _boom)
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    commits_before = git_commit_count(template_vault)

    reconcile_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert git_commit_count(template_vault) == commits_before, (
        "reconciling a topic's notes must never create a commit"
    )
    assert git_status_porcelain(template_vault) == "", (
        "reconciling a topic's notes must never leave the working tree dirty"
    )


def test_passing_a_precomputed_listing_yields_identical_transitions(template_vault: Path):
    """The listing parameter changes cost, never results.

    The drift-queue read path resolves the topic to find queue members and then
    calls this; letting it re-resolve internally made a drift open resolve every
    anchor twice. Handing the listing in is only safe if the two forms are
    indistinguishable in output.
    """
    from knotica.core.notes.reconcile import reconcile_notes
    from knotica.core.notes.store import list_notes

    quote = "the model has no persistent notion of the goal it is optimizing for"
    paraphrase = "the model retains no persistent notion of the goal it is optimizing for"
    page_relpath = f"{TOPIC}/agent-memory.md"
    original_page = f"Preface paragraph.\n\n{quote}\n\nClosing thoughts."
    page_sha = _write_and_commit_page(
        template_vault, page_relpath, original_page, "test: seed agent-memory page"
    )
    _write_note(
        template_vault,
        "20260101-090000-headline-note",
        _note(
            "20260101-090000-headline-note",
            anchors=(_anchor(page=page_relpath, pinned_at=page_sha, quote=quote),),
        ),
    )
    _commit_all(template_vault, "test: capture headline-note")
    _write_and_commit_page(
        template_vault,
        page_relpath,
        original_page.replace(quote, paraphrase),
        "vault: reword the agent-memory passage",
    )
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    self_sufficient = reconcile_notes(
        store, vcs, TOPIC, guess_threshold=0.52, complete_orphan_threshold=0.35
    )
    handed_in = reconcile_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=0.52,
        complete_orphan_threshold=0.35,
        listing=list_notes(store, vcs, TOPIC, guess_threshold=0.52, complete_orphan_threshold=0.35),
    )

    assert handed_in == self_sufficient
    assert handed_in, "the fixture must produce at least one transition to compare"


def test_a_wholesale_replacement_is_reported_as_superseded(template_vault: Path):
    """The dominant orphan source gets its own label, not "your passage moved".

    Phase 3 measured one such event supplying 85% of all observed orphaning,
    indistinguishable at the review surface from an ordinary reword.
    """
    from knotica.core.notes.reconcile import reconcile_notes

    quote = "the model has no persistent notion of the goal it is optimizing for"
    page_relpath = f"{TOPIC}/agent-memory.md"
    original_page = (
        f"# Agent memory\n\n## Persistence\n\n{quote}\n\n## Consequences\n\nContext is lost."
    )
    page_sha = _write_and_commit_page(
        template_vault, page_relpath, original_page, "test: seed agent-memory page"
    )
    _write_note(
        template_vault,
        "20260101-090000-headline-note",
        _note(
            "20260101-090000-headline-note",
            anchors=(_anchor(page=page_relpath, pinned_at=page_sha, quote=quote),),
        ),
    )
    _commit_all(template_vault, "test: capture headline-note")
    _write_and_commit_page(
        template_vault,
        page_relpath,
        "# Retrieval benchmarks\n\n## Corpus\n\nDocuments are sampled from a snapshot.\n",
        "vault: replace the page wholesale",
    )

    transitions = reconcile_notes(
        LocalFSStore(template_vault),
        VaultVcs(template_vault),
        TOPIC,
        guess_threshold=0.52,
        complete_orphan_threshold=0.35,
    )

    transition = next(t for t in transitions if t.note_id == "20260101-090000-headline-note")
    assert transition.superseded, "a wholesale replacement must be labelled superseded"


def test_an_ordinary_reword_is_not_reported_as_superseded(template_vault: Path):
    from knotica.core.notes.reconcile import reconcile_notes

    quote = "the model has no persistent notion of the goal it is optimizing for"
    paraphrase = "the model retains no persistent notion of the goal it is optimizing for"
    page_relpath = f"{TOPIC}/agent-memory.md"
    original_page = (
        f"# Agent memory\n\n## Persistence\n\n{quote}\n\n## Consequences\n\nContext is lost."
    )
    page_sha = _write_and_commit_page(
        template_vault, page_relpath, original_page, "test: seed agent-memory page"
    )
    _write_note(
        template_vault,
        "20260101-090000-headline-note",
        _note(
            "20260101-090000-headline-note",
            anchors=(_anchor(page=page_relpath, pinned_at=page_sha, quote=quote),),
        ),
    )
    _commit_all(template_vault, "test: capture headline-note")
    _write_and_commit_page(
        template_vault,
        page_relpath,
        original_page.replace(quote, paraphrase),
        "vault: reword the passage",
    )

    transitions = reconcile_notes(
        LocalFSStore(template_vault),
        VaultVcs(template_vault),
        TOPIC,
        guess_threshold=0.52,
        complete_orphan_threshold=0.35,
    )

    transition = next(t for t in transitions if t.note_id == "20260101-090000-headline-note")
    assert not transition.superseded, "a reword must not be labelled superseded"

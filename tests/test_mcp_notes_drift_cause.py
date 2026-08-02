"""The drift queue tells a replaced page apart from a reworded one.

Phase 3 measured a single wholesale supersession supplying **85% of all
observed orphaning**, and the review surface could not distinguish it from an
ordinary reword. Both arrived as "orphaned", and the ladder's best guess pointed
into content unrelated to the anchored passage -- so the queue invited the
reader to re-anchor a note onto an arbitrary span of a page that no longer
discussed the subject.

This module pins the contract *at the MCP boundary* rather than in the core,
because that is where the dashboard reads it: `NotesDriftView` renders a
different, non-actionable treatment when `cause` is `superseded`, and suppresses
the alternatives radio group. A core-level test would not catch the payload
silently dropping the field.

Lives in its own file rather than in `test_mcp_notes.py` deliberately: that
module sits at its file-size ratchet baseline (2506 lines, may shrink but never
grow), so appending here is the direction `td-030`'s paydown wants anyway --
split by concern.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knotica.core.notes.anchor import AnchorRecord, NoteDocument, serialize_note
from support.dispatch import TOPIC, build_full_server, call_tool, payload_of
from support.vault import git_head_sha, run_git

#: Shared preface and closing lines, so an *ordinary rewrite* keeps a high page
#: similarity while the passage itself changes -- the control case.
_PREFACE = "Some preface text unrelated to anything specific happening later on here."
_CLOSING = "Trailing remarks closing out this particular section for now."

_QUOTE = "reward hacking undermines interpretability in deployed agent systems"


def _write_page(vault: Path, relpath: str, text: str, message: str) -> str:
    path = vault / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", message)
    return git_head_sha(vault)


def _write_note(vault: Path, note_id: str, page: str, pinned_at: str, quote: str) -> None:
    document = NoteDocument(
        id=note_id,
        topic=TOPIC,
        intent="reflection",
        created="2026-01-01T09:00:00Z",
        updated="2026-01-01T09:00:00Z",
        status="active",
        tags=(),
        body="a note the user made against this passage",
        anchors=(
            AnchorRecord(
                page=page,
                heading="Failure modes",
                fidelity="span",
                pinned_at=pinned_at,
                quote=quote,
                start=None,
            ),
        ),
    )
    path = vault / "notes" / TOPIC / f"{note_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_note(document), encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: capture {note_id}")


def _historical() -> str:
    return f"# Agent notes\n\n{_PREFACE}\n\n## Failure modes\n\n{_QUOTE}\n\n{_CLOSING}\n"


def _drift_item(server: Any, note_id: str) -> dict[str, Any]:
    body = payload_of(call_tool(server, "notes", {"action": "drift", "topic": TOPIC}))
    for item in body["items"]:
        if item["note"]["note_id"] == note_id:
            return item
    raise AssertionError(f"{note_id} is not in the drift queue: {body['items']}")


def _seed(vault: Path, note_id: str, replacement: str) -> None:
    page = f"{TOPIC}/drift-cause-target.md"
    sha = _write_page(vault, page, _historical(), "test: seed the anchored page")
    _write_note(vault, note_id, page, sha, _QUOTE)
    _write_page(vault, page, replacement, "test: change the anchored page")


def test_a_page_replaced_wholesale_reports_cause_superseded(
    vault_config: Path, template_vault: Path
) -> None:
    """Nothing of the original survives -- not the prose, not a single heading."""
    note_id = "20260101-090000-superseded-note"
    _seed(
        template_vault,
        note_id,
        "# Retrieval benchmarks\n\n"
        "## Corpus construction\n\n"
        "Documents are sampled from a fixed snapshot and deduplicated by content hash.\n\n"
        "## Scoring\n\n"
        "Relevance is graded by pooled human judgement over the top k returned results.\n",
    )

    drift = _drift_item(build_full_server(), note_id)["drift"]

    assert drift["cause"] == "superseded"
    assert drift["alternatives"] == [], (
        "a replaced page has no passage worth guessing at -- offering one invites "
        "a re-anchor onto unrelated content"
    )


def test_an_ordinary_reword_reports_cause_rewritten(
    vault_config: Path, template_vault: Path
) -> None:
    """The control: the page is edited, its structure and subject intact.

    Without this, a classifier that simply always said "superseded" would pass
    the case above.
    """
    note_id = "20260101-090100-reworded-note"
    _seed(
        template_vault,
        note_id,
        f"# Agent notes\n\n{_PREFACE}\n\n## Failure modes\n\n"
        "reward hacking severely undermines interpretability in deployed agent systems\n\n"
        f"{_CLOSING}\n",
    )

    drift = _drift_item(build_full_server(), note_id)["drift"]

    assert drift["cause"] == "rewritten"


def test_every_drift_item_carries_a_cause(vault_config: Path, template_vault: Path) -> None:
    """The dashboard reads this field on every item; it is never optional server-side.

    `NoteDrift.cause` is typed optional in the dashboard only so an older server
    degrades gracefully -- that is a compatibility affordance, not licence for
    this server to omit it.
    """
    note_id = "20260101-090200-cause-present-note"
    _seed(template_vault, note_id, "# Wholly different\n\n## New section\n\nUnrelated prose.\n")

    body = payload_of(call_tool(build_full_server(), "notes", {"action": "drift", "topic": TOPIC}))

    assert body["items"], "the fixture must put at least one anchor in the queue"
    for item in body["items"]:
        assert item["drift"]["cause"] in {"rewritten", "superseded"}

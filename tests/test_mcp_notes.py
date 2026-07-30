"""Behavioral tests for the MCP notes surface (`note_capture` + `notes`) over
an in-memory client session.

Like `test_mcp_write.py`, this drives the fully-wired FastMCP server through
the SDK's in-memory transport (`mcp.shared.memory` via `support.dispatch`) so
the assertions pin the *wire* contract a real MCP client sees, not the
`core.operations.capture_note` / `core.notes.store` functions the tools
delegate to.

Phase 1 scope, pinned here:

- `note_capture` (flat, conversational): schema requires only `topic`/`note`;
  `intent`/`pages`/`tags` default; anchoring never fails the call -- an
  unresolvable claim degrades the pin and rides back as an `ANCHOR_DEGRADED`
  warning on a *success* envelope, never an error.
- `notes` (dispatcher, operator): registers exactly `list`/`read` in this
  phase. The other five actions from the full design (`drift`, `reanchor`,
  `detach`, `promote`, `archive`) are Phase 2 and must be rejected with
  `INVALID_ARGUMENT` rather than silently accepted or no-op'd.
- `notes action=list`'s `status_counts` carries only the statuses Phase 1's
  resolver actually produces (`exact`, `shifted`, `orphaned`, `unanchored`) --
  no `fuzzy` key, which is a Phase 2 capability.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from knotica.core.notes.anchor import AnchorRecord, NoteDocument, serialize_note
from knotica.core.status import gather_wiki_status
from knotica.store import LocalFSStore
from support.dispatch import TOPIC, build_full_server, call_tool, payload_of, tool_schema
from support.vault import git_head_sha, run_git

#: The Phase 1 error-code subset for the notes surface. `ANCHOR_DEGRADED` is
#: warning-only (never the sole content of a failure envelope) but is listed
#: here for completeness of the grammar this file exercises.
ERROR_CODES = frozenset(
    {
        "ANCHOR_DEGRADED",
        "NOTE_NOT_FOUND",
        "TOPIC_NOT_FOUND",
        "INVALID_ARGUMENT",
        "LOCK_BUSY",
        "GIT_ERROR",
    }
)

#: `<YYYYMMDD-HHMMSS>-<slug>`, slug optional (empty note text has none).
NOTE_ID_RE = re.compile(r"^\d{8}-\d{6}(-[a-z0-9]+)*$")

#: The five actions the full design names that Phase 1 must not accept.
PHASE_TWO_ACTIONS = ("drift", "reanchor", "detach", "promote", "archive")


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------


def capture(server: Any, topic: str, note: str, **kwargs: Any) -> Any:
    return call_tool(server, "note_capture", {"topic": topic, "note": note, **kwargs})


def notes_call(server: Any, action: str, **kwargs: Any) -> Any:
    return call_tool(server, "notes", {"action": action, **kwargs})


def assert_success(result: Any) -> dict[str, Any]:
    body = payload_of(result)
    assert isinstance(body, dict), f"success envelope must be an object: {body!r}"
    assert "error" not in body, f"expected success, got error envelope: {body!r}"
    assert getattr(result, "isError", False) is False, "a success payload must not set isError"
    return body


def error_of(result: Any) -> dict[str, Any]:
    body = payload_of(result)
    assert isinstance(body, dict), f"error envelope must be an object, got {body!r}"
    assert "error" in body, f"expected a failure envelope, got success: {body!r}"
    assert getattr(result, "isError", False) is True, "an error payload must set isError=True"
    return body["error"]


def assert_error_shape(err: dict[str, Any], code: str) -> None:
    assert set(err) >= {"code", "message", "fix", "retryable"}, (
        f"error object missing contract fields: {err!r}"
    )
    assert err["code"] in ERROR_CODES, f"code not in the Phase 1 subset: {err['code']!r}"
    assert err["code"] == code, f"expected {code}, got {err['code']!r}"
    assert isinstance(err["retryable"], bool), "retryable must be a bool"
    assert isinstance(err["message"], str) and err["message"]
    assert isinstance(err["fix"], str) and err["fix"]


# ---------------------------------------------------------------------------
# note_capture -- schema
# ---------------------------------------------------------------------------


def test_note_capture_schema_requires_only_topic_and_note() -> None:
    """A capture must be callable with the user's words and nothing else --
    every other field defaults so the one-shot handshake never blocks on a
    missing argument."""
    schema = tool_schema(build_full_server(), "note_capture")
    assert set(schema.get("required", [])) == {"topic", "note"}
    props = schema["properties"]
    assert props["intent"]["default"] == "reflection"
    assert props["pages"]["default"] == []
    assert props["tags"]["default"] == []


# ---------------------------------------------------------------------------
# note_capture -- happy path
# ---------------------------------------------------------------------------


def test_note_capture_happy_path_applies_defaults_and_returns_the_wire_envelope(
    vault_config: Path, template_vault: Path
) -> None:
    """A bare capture (topic + note only, no quote/pages) succeeds, defaults
    intent to reflection, and returns the full wire envelope INTERFACE_DESIGN
    §2 describes -- crucially a pre-composed `placement` sentence, not raw
    data the caller must assemble."""
    del template_vault
    server = build_full_server()
    body = assert_success(capture(server, TOPIC, "just Goodhart with extra steps."))

    assert body["topic"] == TOPIC
    assert NOTE_ID_RE.match(body["note_id"]), f"unexpected note_id shape: {body['note_id']!r}"
    assert body["path"] == f"notes/{TOPIC}/{body['note_id']}.md"
    assert body["intent"] == "reflection", "intent must default to reflection"
    assert isinstance(body["placement"], str) and body["placement"], (
        "placement must be a non-empty pre-composed sentence, produced by the "
        "server -- not left for the caller to derive from fidelity/status"
    )
    assert body["written"] is True
    assert body["duplicate"] is False
    assert isinstance(body.get("commit"), str) and body["commit"]
    assert isinstance(body.get("anchors"), list)
    assert isinstance(body.get("alternatives"), list)
    # No quote was supplied, so this is not a degraded capture: no
    # ANCHOR_DEGRADED warning should ride back.
    warnings = body.get("warnings", [])
    assert not any(w.get("code") == "ANCHOR_DEGRADED" for w in warnings), (
        f"a quote-less capture is a clean success, not a degraded one: {warnings!r}"
    )


# ---------------------------------------------------------------------------
# note_capture -- degraded path is a SUCCESS envelope
# ---------------------------------------------------------------------------


def test_note_capture_degraded_anchor_is_a_success_envelope_with_a_warning(
    vault_config: Path, template_vault: Path
) -> None:
    """A capture whose claimed page does not exist in the topic still writes
    the note -- degrading the anchor and carrying an ANCHOR_DEGRADED warning
    on a *success* envelope. Surfacing this as an error would tell the model
    (and, through it, the user) that a note it actually saved was lost --
    the feature's worst failure mode dressed as a warning."""
    del template_vault
    server = build_full_server()
    result = capture(
        server,
        TOPIC,
        "worth revisiting later.",
        quote="a sentence that certainly does not exist anywhere in the vault",
        pages=[f"{TOPIC}/this-page-does-not-exist"],
    )

    assert getattr(result, "isError", False) is False, (
        "a degraded anchor must never flip isError -- the note is still saved"
    )
    body = assert_success(result)
    warnings = body.get("warnings", [])
    assert any(w.get("code") == "ANCHOR_DEGRADED" for w in warnings), (
        f"expected an ANCHOR_DEGRADED warning on the degraded capture, got {warnings!r}"
    )
    assert body["written"] is True, "the note must still be written despite the degraded anchor"


# ---------------------------------------------------------------------------
# notes dispatcher -- Phase 1 action scope
# ---------------------------------------------------------------------------


def test_notes_list_succeeds_as_a_phase_one_action(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    server = build_full_server()
    assert_success(notes_call(server, "list", topic=TOPIC))


@pytest.mark.parametrize("action", PHASE_TWO_ACTIONS)
def test_notes_dispatcher_rejects_phase_two_actions_as_invalid_argument(
    action: str, vault_config: Path, template_vault: Path
) -> None:
    """Phase 2 actions must fail loudly with INVALID_ARGUMENT -- not be
    silently accepted, and not silently no-op."""
    del template_vault
    server = build_full_server()
    err = error_of(notes_call(server, action, topic=TOPIC, note_id="anything"))
    assert_error_shape(err, "INVALID_ARGUMENT")


# ---------------------------------------------------------------------------
# notes action=list -- pagination / filter wire contract
# ---------------------------------------------------------------------------


def test_notes_list_defaults_to_empty_with_status_counts_and_no_fuzzy_key(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    server = build_full_server()
    body = assert_success(notes_call(server, "list", topic=TOPIC))

    assert body["notes"] == []
    assert body["total_count"] == 0
    assert body["has_more"] is False
    assert body["next_cursor"] == ""
    assert set(body["status_counts"]) == {"exact", "shifted", "orphaned", "unanchored"}, (
        "Phase 1's resolver never produces 'fuzzy' (that ladder rung is Phase 2); "
        f"got keys {sorted(body['status_counts'])}"
    )
    assert set(body["intent_counts"]) == {"reflection", "dispute", "gap", "question"}


def test_notes_list_status_filter_accepts_unanchored_and_counts_a_quote_less_capture(
    vault_config: Path, template_vault: Path
) -> None:
    """A quote-less, page-less capture never pointed at anything, so it must
    bucket -- and be filterable and countable -- as `unanchored`, never
    `orphaned` (which would claim something was lost)."""
    server = build_full_server()
    captured = assert_success(capture(server, TOPIC, "a purely topical reflection"))

    unanchored_only = assert_success(notes_call(server, "list", topic=TOPIC, status="unanchored"))
    ids = {note["note_id"] for note in unanchored_only["notes"]}
    assert captured["note_id"] in ids
    assert unanchored_only["status_counts"]["unanchored"] >= 1
    assert unanchored_only["status_counts"]["orphaned"] == 0

    orphaned_only = assert_success(notes_call(server, "list", topic=TOPIC, status="orphaned"))
    assert captured["note_id"] not in {note["note_id"] for note in orphaned_only["notes"]}


def test_notes_list_intent_filter_excludes_notes_of_a_different_intent(
    vault_config: Path, template_vault: Path
) -> None:
    server = build_full_server()
    captured = assert_success(
        capture(server, TOPIC, "a reflection worth keeping", intent="reflection")
    )

    dispute_only = assert_success(notes_call(server, "list", topic=TOPIC, intent="dispute"))
    assert dispute_only["notes"] == [], "a reflection note must not appear under intent=dispute"

    reflection_only = assert_success(notes_call(server, "list", topic=TOPIC, intent="reflection"))
    ids = {note["note_id"] for note in reflection_only["notes"]}
    assert captured["note_id"] in ids, "the captured reflection must appear under its own filter"


# ---------------------------------------------------------------------------
# notes action=read
# ---------------------------------------------------------------------------


def test_notes_read_returns_the_note_just_captured(
    vault_config: Path, template_vault: Path
) -> None:
    server = build_full_server()
    captured = assert_success(capture(server, TOPIC, "a note worth reading back"))

    body = assert_success(notes_call(server, "read", topic=TOPIC, note_id=captured["note_id"]))
    assert body["note_id"] == captured["note_id"]
    assert body["topic"] == TOPIC


def test_notes_read_unknown_note_id_is_note_not_found(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    server = build_full_server()
    err = error_of(
        notes_call(server, "read", topic=TOPIC, note_id="20260101-000000-does-not-exist")
    )
    assert_error_shape(err, "NOTE_NOT_FOUND")


# ---------------------------------------------------------------------------
# Cross-layer agreement: "drifted" means `orphaned` only, everywhere
# ---------------------------------------------------------------------------
#
# `wiki_status` (core/status.py), the `notes` dispatcher, and the dashboard's
# NotesPane each derive a drifted count from the same resolved-anchor data.
# Nothing forces them to agree except discipline, and they have drifted apart
# before (F-02): the dispatcher folded `anchor-invalid` into `orphaned`, and
# the pane summed `shifted + orphaned`. This test pins the dispatcher side of
# the reconciliation against `wiki_status`'s own count for the identical
# vault state, so a future edit to either side that reintroduces a mismatch
# fails here rather than surfacing as two contradictory numbers on one screen.


def _forged_anchor(**overrides: object) -> AnchorRecord:
    defaults: dict[str, object] = {
        "page": f"{TOPIC}/forged-target.md",
        "heading": "",
        "fidelity": "span",
        "pinned_at": "0000000",
        "quote": "a quote that will not matter for this test",
        "start": None,
    }
    defaults.update(overrides)
    return AnchorRecord(**defaults)


def _forged_note(note_id: str, **overrides: object) -> NoteDocument:
    defaults: dict[str, object] = {
        "id": note_id,
        "topic": TOPIC,
        "intent": "reflection",
        "created": "2026-01-01T09:00:00Z",
        "updated": "2026-01-01T09:00:00Z",
        "status": "active",
        "tags": (),
        "body": "A loose thought.",
        "anchors": (_forged_anchor(),),
        "skipped_anchor_count": 0,
    }
    defaults.update(overrides)
    return NoteDocument(**defaults)


def _write_forged_note(vault: Path, note_id: str, document: NoteDocument) -> None:
    path = vault / "notes" / TOPIC / f"{note_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_note(document), encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: seed {note_id}")


def test_dispatcher_drifted_count_agrees_with_wiki_status_across_a_mixed_bucket(
    vault_config: Path, template_vault: Path
) -> None:
    """One genuinely orphaned note plus one anchor-invalid (hand-forged) note:
    both layers must report exactly 1 drifted, never 2 -- proving the
    dispatcher no longer folds `anchor-invalid` into `orphaned`."""
    orphan_page = f"{TOPIC}/orphan-page.md"
    (template_vault / orphan_page).parent.mkdir(parents=True, exist_ok=True)
    (template_vault / orphan_page).write_text(
        "# Orphan Page\n\nsome text that will disappear before HEAD.\n", encoding="utf-8"
    )
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: seed orphan page")
    orphan_sha = git_head_sha(template_vault)
    _write_forged_note(
        template_vault,
        "20260101-090000-orphaned-note",
        _forged_note(
            "20260101-090000-orphaned-note",
            anchors=(
                _forged_anchor(
                    page=orphan_page,
                    pinned_at=orphan_sha,
                    quote="some text that will disappear before HEAD",
                ),
            ),
        ),
    )
    (template_vault / orphan_page).write_text(
        "# Orphan Page\n\nEntirely different content now.\n", encoding="utf-8"
    )
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: rewrite the orphan page, losing the quote")

    forged_page = f"{TOPIC}/forged-target.md"
    (template_vault / forged_page).write_text(
        "# Forged target\n\nNone of this text matches the anchor's quote.\n", encoding="utf-8"
    )
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: seed forged-target page")
    forged_sha = git_head_sha(template_vault)
    _write_forged_note(
        template_vault,
        "20260101-091000-forged-note",
        _forged_note(
            "20260101-091000-forged-note",
            anchors=(
                _forged_anchor(
                    page=forged_page,
                    pinned_at=forged_sha,
                    quote="a quote that was never in the historical blob",
                ),
            ),
        ),
    )

    server = build_full_server()
    dispatcher_body = assert_success(notes_call(server, "list", topic=TOPIC))
    status_payload = gather_wiki_status(LocalFSStore(template_vault), template_vault, topic=TOPIC)

    assert dispatcher_body["status_counts"]["orphaned"] == 1, (
        "the anchor-invalid note must not inflate the dispatcher's orphaned "
        f"bucket: got {dispatcher_body['status_counts']!r}"
    )
    assert status_payload["totals"]["notes"]["drifted"] == 1
    assert (
        dispatcher_body["status_counts"]["orphaned"] == status_payload["totals"]["notes"]["drifted"]
    ), "the dispatcher and wiki_status must agree on the drifted count for the same vault state"

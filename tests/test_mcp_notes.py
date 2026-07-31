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
- `notes action=list`'s `status_counts` carries every status the resolver can
  produce, including Phase 2's `fuzzy` rung (`exact`, `unanchored`, `shifted`,
  `fuzzy`, `orphaned`) -- and the `status` filter accepts the same set.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from knotica.core.gap_classifier import gaps_path
from knotica.core.notes.anchor import (
    AnchorRecord,
    NoteDocument,
    effective_anchor,
    parse_note,
    serialize_note,
)
from knotica.core.notes.resolve import Projection
from knotica.core.notes.store import ResolvedNote
from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.operations.reanchor_note import detach as core_detach
from knotica.core.records import GapRecord, QARecord, parse_gaps_jsonl, parse_qa_jsonl
from knotica.core.status import gather_wiki_status
from knotica.core.vcs import VaultVcs
from knotica.mcp_server.tools_dispatch_notes import (
    _ACTIONS,
    _ANCHOR_STATUSES,
    _LEAST_SEVERE_ANCHOR_STATUS,
    _MOST_SEVERE_ANCHOR_STATUS,
    _drift_status,
    _status_counts,
)
from knotica.mcp_server.tools_dispatch_notes_common import _NOTES_SORT
from knotica.search.cursor import decode_cursor
from knotica.store import LocalFSStore
from support.dispatch import (
    TOPIC,
    build_full_server,
    call_tool,
    list_tools,
    payload_of,
    tool_schema,
)
from support.vault import git_commit_count, git_head_sha, run_git

#: The error-code subset the notes surface can produce. `ANCHOR_DEGRADED` is
#: warning-only (never the sole content of a failure envelope) but is listed
#: here for completeness of the grammar this file exercises. `PAGE_NOT_FOUND`
#: joins now that `reanchor` can target a page that no longer exists.
ERROR_CODES = frozenset(
    {
        "ANCHOR_DEGRADED",
        "NOTE_NOT_FOUND",
        "TOPIC_NOT_FOUND",
        "INVALID_ARGUMENT",
        "PAGE_NOT_FOUND",
        "LOCK_BUSY",
        "GIT_ERROR",
    }
)

#: `<YYYYMMDD-HHMMSS>-<slug>`, slug optional (empty note text has none).
NOTE_ID_RE = re.compile(r"^\d{8}-\d{6}(-[a-z0-9]+)*$")

#: The full seven-action design this dispatcher now registers, in the same
#: order `_ACTIONS` in `tools_dispatch_notes.py` declares them. Each action
#: has its own behavioral coverage elsewhere in this file; this tuple exists
#: to pin the *complete* surface in one place (see
#: `test_notes_dispatcher_accepts_exactly_the_seven_designed_actions` below).
ALL_NOTES_ACTIONS = ("list", "read", "drift", "reanchor", "detach", "promote", "archive")


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
# note_capture -- structured alternatives on an ambiguous multi-page match
#
# Unlike the drift queue's alternatives, every capture-time match is verbatim
# -- there is nothing to score, so the shape carries only `page`/`heading`,
# never an `overlap` (that would imply a similarity comparison that never
# ran) and never a `quote` (identical on every matched page by construction).
# ---------------------------------------------------------------------------


def _seed_capture_page(vault: Path, relpath: str, content: str, message: str) -> None:
    target = vault / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", message)


def test_note_capture_alternatives_carry_page_and_heading_for_each_matched_page(
    vault_config: Path, template_vault: Path
) -> None:
    """A quote matching several claimed pages degrades to topic fidelity
    (Phase 1 behavior, unchanged) AND now hands back one structured runner-up
    per matched page, in claimed order -- exactly what `_plan_anchor`'s prose
    warning already names, just reaching the caller as data this time."""
    quote = "the reward signal and the intended goal quietly diverge"
    page_a = f"{TOPIC}/wire-multi-match-a.md"
    page_b = f"{TOPIC}/wire-multi-match-b.md"
    _seed_capture_page(template_vault, page_a, f"# Reward Divergence\n\n{quote}.\n", "test: seed A")
    _seed_capture_page(template_vault, page_b, f"# Goal Misalignment\n\n{quote}.\n", "test: seed B")
    server = build_full_server()

    result = capture(
        server,
        TOPIC,
        "this keeps coming up in two places",
        quote=quote,
        pages=[page_a, page_b],
    )

    assert getattr(result, "isError", False) is False, (
        "a multi-page match is still a success -- the anchor degrades, the call does not fail"
    )
    body = assert_success(result)
    warnings = body.get("warnings", [])
    assert any(w.get("code") == "ANCHOR_DEGRADED" for w in warnings), (
        f"the existing degradation warning must ride alongside the new structured data, "
        f"not be replaced by it: {warnings!r}"
    )
    assert body["alternatives"] == [
        {"page": page_a, "heading": "Reward Divergence"},
        {"page": page_b, "heading": "Goal Misalignment"},
    ]


def test_note_capture_alternatives_entries_never_carry_an_overlap_key(
    vault_config: Path, template_vault: Path
) -> None:
    """The ruling most likely to be "helpfully" undone by a later change that
    pattern-matches on the drift queue's `{page, heading, overlap}` shape:
    every capture-time alternative matched the quote verbatim, so there is no
    similarity score to carry, and none must sneak in."""
    quote = "specification gaming shows up whenever the metric is a proxy for the goal"
    page_a = f"{TOPIC}/overlap-guard-a.md"
    page_b = f"{TOPIC}/overlap-guard-b.md"
    _seed_capture_page(template_vault, page_a, f"# Gaming\n\n{quote}.\n", "test: seed A")
    _seed_capture_page(template_vault, page_b, f"# Proxy Goals\n\n{quote}.\n", "test: seed B")
    server = build_full_server()

    body = assert_success(
        capture(
            server,
            TOPIC,
            "the same specification-gaming point twice",
            quote=quote,
            pages=[page_a, page_b],
        )
    )

    alternatives = body["alternatives"]
    assert len(alternatives) == 2, "sanity: both claimed pages must have matched"
    assert all(set(entry) == {"page", "heading"} for entry in alternatives), (
        "an alternative entry carries more than page/heading -- an overlap or quote key "
        f"snuck in, but every capture-time match is exact so there is nothing to score: "
        f"{alternatives!r}"
    )


def test_note_capture_alternatives_is_empty_when_exactly_one_claimed_page_matches(
    vault_config: Path, template_vault: Path
) -> None:
    quote = "an argument that only lives on one of the candidate pages"
    page_a = f"{TOPIC}/wire-single-match-a.md"
    page_b = f"{TOPIC}/wire-single-match-b.md"
    _seed_capture_page(template_vault, page_a, f"# Single Match\n\n{quote}.\n", "test: seed A")
    _seed_capture_page(
        template_vault, page_b, "# Unrelated\n\nNothing here matches.\n", "test: seed B"
    )
    server = build_full_server()

    body = assert_success(
        capture(
            server,
            TOPIC,
            "unambiguous, not the multi-page case",
            quote=quote,
            pages=[page_a, page_b],
        )
    )

    assert body["alternatives"] == [], "exactly one match is the happy path, not an ambiguity"


def test_note_capture_alternatives_is_empty_when_no_quote_is_supplied(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    server = build_full_server()

    body = assert_success(capture(server, TOPIC, "a purely topical reflection"))

    assert body["alternatives"] == [], "nothing was claimed, so there is nothing to offer"


# ---------------------------------------------------------------------------
# notes dispatcher -- Phase 1 action scope
# ---------------------------------------------------------------------------


def test_notes_list_succeeds_as_a_phase_one_action(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    server = build_full_server()
    assert_success(notes_call(server, "list", topic=TOPIC))


def test_notes_dispatcher_accepts_exactly_the_seven_designed_actions(
    vault_config: Path, template_vault: Path
) -> None:
    """The full notes design names seven actions; this pins the *complete*
    surface at once -- exactly these seven are registered, and anything
    else is still rejected with INVALID_ARGUMENT rather than silently
    accepted or no-op'd. Supersedes the old parametrized rejection test
    that walked a shrinking `PHASE_TWO_ACTIONS` list: that approach would
    have parametrized to zero cases once `promote`/`archive` (the last two)
    landed, which reads as coverage while asserting nothing."""
    del template_vault
    assert _ACTIONS == ALL_NOTES_ACTIONS

    server = build_full_server()
    err = error_of(notes_call(server, "not-a-real-action", topic=TOPIC, note_id="anything"))
    assert_error_shape(err, "INVALID_ARGUMENT")


# ---------------------------------------------------------------------------
# notes action=list -- pagination / filter wire contract
# ---------------------------------------------------------------------------


def test_notes_list_defaults_to_empty_with_status_counts_including_fuzzy_key(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    server = build_full_server()
    body = assert_success(notes_call(server, "list", topic=TOPIC))

    assert body["notes"] == []
    assert body["total_count"] == 0
    assert body["has_more"] is False
    assert body["next_cursor"] == ""
    assert set(body["status_counts"]) == {"exact", "shifted", "fuzzy", "orphaned", "unanchored"}, (
        "the resolver's fuzzy rung must have a bucket in status_counts even when "
        f"nothing has been captured yet -- got keys {sorted(body['status_counts'])}"
    )
    assert set(body["intent_counts"]) == {"reflection", "dispute", "gap", "question", "other"}


def test_notes_list_status_filter_accepts_fuzzy(vault_config: Path, template_vault: Path) -> None:
    """The wire-level `status` filter must accept `fuzzy` -- Phase 1's argument
    validation rejected it as an unrecognized value, which is now wrong."""
    del template_vault
    server = build_full_server()
    body = assert_success(notes_call(server, "list", topic=TOPIC, status="fuzzy"))
    assert body["status_filter"] == "fuzzy"


def test_notes_list_status_filter_rejects_an_unknown_value(
    vault_config: Path, template_vault: Path
) -> None:
    """A status the resolver has never produced must still fail loudly, with
    the same typed shape `fuzzy`'s addition did not disturb."""
    del template_vault
    server = build_full_server()
    err = error_of(notes_call(server, "list", topic=TOPIC, status="bogus"))
    assert_error_shape(err, "INVALID_ARGUMENT")


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


# ---------------------------------------------------------------------------
# notes action=list -- topic validation must agree with `note_capture`
# ---------------------------------------------------------------------------


def test_notes_list_rejects_a_nonexistent_topic_as_topic_not_found(
    vault_config: Path, template_vault: Path
) -> None:
    """A mistyped topic must fail loudly, never return a confident empty
    listing -- `note_capture` already rejects the identical input this way."""
    del template_vault
    server = build_full_server()
    err = error_of(notes_call(server, "list", topic="no-such-topic"))
    assert_error_shape(err, "TOPIC_NOT_FOUND")


def test_notes_list_rejects_a_dot_segment_topic_as_topic_not_found(
    vault_config: Path, template_vault: Path
) -> None:
    """`..` must never resolve to the vault root and walk every page in it."""
    del template_vault
    server = build_full_server()
    err = error_of(notes_call(server, "list", topic=".."))
    assert_error_shape(err, "TOPIC_NOT_FOUND")


# ---------------------------------------------------------------------------
# notes action=list -- intent_counts must never under-report the topic total
# ---------------------------------------------------------------------------


def _fixture_text(name: str) -> str:
    fixtures = Path(__file__).resolve().parent / "fixtures" / "notes"
    return (fixtures / name).read_text(encoding="utf-8")


def _seed_fixture_note(vault: Path, note_id: str, fixture_name: str) -> None:
    path = vault / "notes" / TOPIC / f"{note_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_fixture_text(fixture_name), encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: seed {note_id}")


def test_notes_list_intent_counts_carries_an_other_bucket_for_an_unknown_intent(
    vault_config: Path, template_vault: Path
) -> None:
    """A hand-typed note with an out-of-enum intent must stay counted and
    filterable -- never silently dropped from `intent_counts`."""
    server = build_full_server()
    captured = assert_success(capture(server, TOPIC, "a note with a known intent"))
    hand_authored = _forged_note("20260101-080000-musing", intent="musing", anchors=())
    _write_forged_note(template_vault, "20260101-080000-musing", hand_authored)

    body = assert_success(notes_call(server, "list", topic=TOPIC))

    assert body["total_count"] == 2
    assert sum(body["intent_counts"].values()) == 2, (
        f"intent_counts must sum to total_count, got {body['intent_counts']!r}"
    )
    assert body["intent_counts"]["other"] == 1

    other_only = assert_success(notes_call(server, "list", topic=TOPIC, intent="other"))
    ids = {note["note_id"] for note in other_only["notes"]}
    assert ids == {"20260101-080000-musing"}
    assert captured["note_id"] not in ids


# ---------------------------------------------------------------------------
# notes action=read -- skipped_anchor_count must reach the wire
# ---------------------------------------------------------------------------


def test_notes_read_surfaces_skipped_anchor_count_for_a_malformed_bullet(
    vault_config: Path, template_vault: Path
) -> None:
    """A hand-authored note with one valid anchor and one unparseable bullet
    must report the malformed count -- otherwise a person gets no feedback
    that their bullet did not parse."""
    note_id = "20260705-133045-half-good-half-broken"
    _seed_fixture_note(template_vault, note_id, "broken_anchor.md")
    server = build_full_server()

    body = assert_success(notes_call(server, "read", topic=TOPIC, note_id=note_id))

    assert body["skipped_anchor_count"] == 1
    assert len(body["anchors"]) == 1, "the one well-formed anchor must still survive"


# ---------------------------------------------------------------------------
# The anchor-status ladder's order is load-bearing, so it is asserted
#
# `_ANCHOR_STATUSES` is consumed four ways at once -- membership filter,
# severity ladder, `status_counts` key set, and accepted `status` filter values
# -- and only the second of those cares about order. A later rung (a fuzzy
# match, say) appended to the tuple silently becomes the most severe status in
# the vault and re-buckets every note that carries one; inserted elsewhere it
# silently changes which bucket a multi-anchor note reports. Neither breaks
# anything on its own. These pin the ladder's ends and its behavior so that a
# reordering fails a test instead of a listing.
# ---------------------------------------------------------------------------


def _note_with_statuses(*statuses: str) -> ResolvedNote:
    anchor = AnchorRecord(
        page=f"{TOPIC}/agent-memory.md",
        heading="",
        fidelity="span",
        pinned_at="9f1a3c0",
        quote="the passage the note was written against",
    )
    document = NoteDocument(
        id="20260730-140000-multi-anchor",
        topic=TOPIC,
        intent="reflection",
        created="2026-07-30T14:00:00Z",
        updated="2026-07-30T14:00:00Z",
        status="active",
        tags=(),
        body="a note carrying anchors in more than one bucket",
        anchors=(anchor,) * len(statuses),
    )
    return ResolvedNote(
        document=document,
        path=f"notes/{TOPIC}/{document.id}.md",
        resolved_anchors=tuple(
            (
                anchor,
                Projection(status=status, fidelity="topic", span=None, score=None, best_guess=None),
            )
            for status in statuses
        ),
    )


def test_the_anchor_status_ladder_runs_from_least_to_most_severe() -> None:
    assert _ANCHOR_STATUSES[0] == _LEAST_SEVERE_ANCHOR_STATUS
    assert _ANCHOR_STATUSES[-1] == _MOST_SEVERE_ANCHOR_STATUS, (
        "a status was appended past the severe end of the ladder -- it is now the most "
        "severe bucket in the vault and every note carrying one has been re-bucketed"
    )
    assert _ANCHOR_STATUSES == ("exact", "unanchored", "shifted", "fuzzy", "orphaned"), (
        "the ladder's order is the precedence _drift_status walks; changing it changes "
        "which bucket every multi-anchor note reports -- fuzzy belongs between shifted "
        "and orphaned, not at either end"
    )


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("exact", "unanchored"), "unanchored"),
        (("exact", "shifted"), "shifted"),
        (("unanchored", "shifted"), "shifted"),
        (("exact", "orphaned"), "orphaned"),
        (("shifted", "orphaned"), "orphaned"),
        (("exact",), "exact"),
        (("shifted", "fuzzy"), "fuzzy"),
        (
            ("fuzzy", "orphaned"),
            "orphaned",
        ),  # the appended-not-inserted canary: if `fuzzy` were appended past
        # `orphaned` instead of inserted before it, this note would report
        # `fuzzy` here instead of `orphaned`, failing this case specifically.
    ],
)
def test_a_note_reports_the_most_severe_bucket_any_of_its_anchors_is_in(
    statuses: tuple[str, ...], expected: str
) -> None:
    assert _drift_status(_note_with_statuses(*statuses)) == expected


def test_status_counts_gains_a_fuzzy_key_and_sums_across_the_bucketable_statuses() -> None:
    """``status_counts`` must carry a ``fuzzy`` bucket and never double-count or
    drop a note -- each note contributes to exactly one bucket, so the counts
    must sum to the number of bucketable notes."""
    notes = (
        _note_with_statuses("exact"),
        _note_with_statuses("fuzzy"),
        _note_with_statuses("fuzzy"),
        _note_with_statuses("orphaned"),
    )

    counts = _status_counts(notes)

    assert set(counts) == set(_ANCHOR_STATUSES), (
        f"status_counts must have exactly one key per ladder status, got {sorted(counts)}"
    )
    assert counts["fuzzy"] == 2
    assert counts["exact"] == 1
    assert counts["orphaned"] == 1
    assert sum(counts.values()) == len(notes), (
        "every note above carries exactly one bucketable status -- the counts must "
        "sum to the note count, neither double-counting nor dropping one"
    )


# ---------------------------------------------------------------------------
# notes action=drift -- the review queue
#
# Membership is `fuzzy ∪ orphaned ∪ anchor-invalid` (R2, plus the ruling that
# corrects Q1): `exact` and `shifted` self-healed and `unanchored` never
# pointed at anything to lose -- none of the three belong in a human's review
# queue. `anchor-invalid` is a data-integrity outcome, not resolver-measured
# drift, but it still needs a human to look at it, so it rides in the same
# queue with its own `invalid_count` breakdown rather than its own bucket --
# and `total_count` counts it too, since pagination (`next_cursor`/
# `has_more`/`total_count`) is one contract with `items`.
# ---------------------------------------------------------------------------


def _seed_exact_note(vault: Path, note_id: str) -> None:
    """A note whose page is untouched since it was pinned -- resolves `exact`."""
    quote = "an unmodified passage the resolver finds exactly where it was pinned"
    page = f"{TOPIC}/drift-exact-target.md"
    _seed_capture_page(vault, page, f"# Exact target\n\n{quote}\n", "test: seed exact target")
    page_sha = git_head_sha(vault)
    _write_forged_note(
        vault,
        note_id,
        _forged_note(
            note_id, anchors=(_forged_anchor(page=page, pinned_at=page_sha, quote=quote),)
        ),
    )


def _seed_shifted_note(vault: Path, note_id: str) -> None:
    """A note whose quote survives verbatim but at a new offset -- resolves `shifted`."""
    quote = "a claim whose position on the page moves after this note was pinned"
    page = f"{TOPIC}/drift-shifted-target.md"
    _seed_capture_page(vault, page, f"# Shifted target\n\n{quote}\n", "test: seed shifted target")
    page_sha = git_head_sha(vault)
    _write_forged_note(
        vault,
        note_id,
        _forged_note(
            note_id, anchors=(_forged_anchor(page=page, pinned_at=page_sha, quote=quote),)
        ),
    )
    _seed_capture_page(
        vault,
        page,
        f"# Shifted target\n\nA new preface paragraph inserted before the pinned quote.\n\n{quote}\n",
        "test: shift the quote's position on the page",
    )


def _seed_fuzzy_note(vault: Path, note_id: str) -> tuple[str, str, str]:
    """A note reworded by a near-verbatim paraphrase (one word's case flips)
    so its real similarity score clears the default `guess_threshold` (0.75)
    with a wide margin -- resolves `fuzzy`, mirroring `test_status_notes.py`'s
    fuzzy fixture. Returns ``(quote, page, reword_commit_message)``.
    """
    quote = "the mechanism that makes this claim true"
    page = f"{TOPIC}/drift-fuzzy-target.md"
    original = (
        "# Fuzzy target\n\n"
        "An introductory sentence sits here for structure.\n\n"
        f"{quote}.\n\n"
        "A closing sentence rounds things out.\n"
    )
    _seed_capture_page(vault, page, original, "test: seed fuzzy target")
    page_sha = git_head_sha(vault)
    _write_forged_note(
        vault,
        note_id,
        _forged_note(
            note_id, anchors=(_forged_anchor(page=page, pinned_at=page_sha, quote=quote),)
        ),
    )
    paraphrase = "The mechanism that makes this claim true"
    reworded = original.replace(quote, paraphrase)
    assert quote not in reworded, "fixture must not leave the verbatim quote behind"
    reword_message = "test: reword the fuzzy target passage"
    _seed_capture_page(vault, page, reworded, reword_message)
    return quote, page, reword_message


def _seed_total_orphan_note(vault: Path, note_id: str) -> str:
    """A note whose quote shares zero vocabulary with the page's rewritten
    content: keyword-candidate generation returns nothing, and no heading
    survives either, so the resolver reports the honest ``0.0`` floor score
    with ``best_guess: None``. Mirrors `test_resolve.py`'s zero-candidate,
    no-surviving-heading fixture verbatim. Returns the anchor's quote.
    """
    quote = "reward hacking undermines interpretability"
    heading = "## Failure modes"
    page = f"{TOPIC}/drift-orphan-target.md"
    historical = (
        "# Agent notes\n\n"
        "Some preface text unrelated to anything specific happening later on here.\n\n"
        f"{heading}\n\n"
        f"{quote}\n\n"
        "Trailing remarks closing out this particular section for now.\n"
    )
    head = (
        "# Agent notes\n\n"
        "Some preface text unrelated to anything specific happening later on here.\n\n"
        "## Known issues (renamed)\n\n"
        "a brief note about scheduling logistics for next quarter's planning cycle\n\n"
        "Trailing remarks closing out this particular section for now.\n"
    )
    _seed_capture_page(vault, page, historical, "test: seed orphan target")
    page_sha = git_head_sha(vault)
    _write_forged_note(
        vault,
        note_id,
        _forged_note(
            note_id, anchors=(_forged_anchor(page=page, pinned_at=page_sha, quote=quote),)
        ),
    )
    _seed_capture_page(
        vault, page, head, "test: rewrite the orphan target, losing all vocabulary overlap"
    )
    return quote


def _seed_surviving_heading_orphan_note(vault: Path, note_id: str) -> str:
    """A note whose passage was deleted outright while its enclosing heading
    survived, and whose quote shares zero vocabulary with the rewritten page.

    Candidate generation returns nothing, so no similarity is measured at all --
    but rung 8 still fires on the surviving heading and must supply *some*
    score to satisfy the resolver's own nullability invariant. The value it
    supplies is `guess_threshold - CLAMP_EPSILON`, a **ceiling**, so this is
    the case that reported the highest confidence in the whole queue while
    having the least evidence in it. Returns the anchor's quote.
    """
    quote = "hyperbolic embeddings preserve ultrametric hierarchy under quantisation"
    heading = "## Retrieval"
    page = f"{TOPIC}/drift-surviving-heading-target.md"
    historical = (
        "# Agent notes\n\n"
        "Some preface text unrelated to anything specific happening later on here.\n\n"
        f"{heading}\n\n"
        f"{quote}\n\n"
        "Trailing remarks closing out this particular section for now.\n"
    )
    head = (
        "# Agent notes\n\n"
        "Some preface text unrelated to anything specific happening later on here.\n\n"
        f"{heading}\n\n"
        "a brief note about scheduling logistics for next quarter's planning cycle\n\n"
        "Trailing remarks closing out this particular section for now.\n"
    )
    _seed_capture_page(vault, page, historical, "test: seed surviving-heading target")
    page_sha = git_head_sha(vault)
    _write_forged_note(
        vault,
        note_id,
        _forged_note(
            note_id, anchors=(_forged_anchor(page=page, pinned_at=page_sha, quote=quote),)
        ),
    )
    _seed_capture_page(vault, page, head, "test: delete the passage but keep its enclosing heading")
    return quote


def _seed_anchor_invalid_note(vault: Path, note_id: str) -> str:
    """A note whose anchor is corrupt: the quote was never present in the
    historical blob the anchor claims to pin. Returns the anchor's quote.
    """
    quote = "a quote that was never in the historical blob"
    page = f"{TOPIC}/drift-invalid-target.md"
    _seed_capture_page(
        vault,
        page,
        "# Invalid target\n\nNone of this text matches the anchor's quote.\n",
        "test: seed invalid target",
    )
    page_sha = git_head_sha(vault)
    _write_forged_note(
        vault,
        note_id,
        _forged_note(
            note_id, anchors=(_forged_anchor(page=page, pinned_at=page_sha, quote=quote),)
        ),
    )
    return quote


def _item_for(body: dict[str, Any], note_id: str) -> dict[str, Any]:
    """The one `{note, drift}` item in a drift payload belonging to `note_id`."""
    return next(item for item in body["items"] if item["note"]["note_id"] == note_id)


def test_drift_queue_holds_only_fuzzy_orphaned_and_anchor_invalid_notes(
    vault_config: Path, template_vault: Path
) -> None:
    """Per R2 (plus the ruling correcting Q1): the review queue is `fuzzy ∪
    orphaned ∪ anchor-invalid`. `exact` and `shifted` self-healed and
    `unanchored` never pointed at anything -- none of the three belong in a
    human's review queue."""
    server = build_full_server()
    exact_id = "20260101-090000-drift-exact-note"
    shifted_id = "20260101-090100-drift-shifted-note"
    fuzzy_id = "20260101-090200-drift-fuzzy-note"
    orphan_id = "20260101-090300-drift-orphan-note"
    invalid_id = "20260101-090400-drift-invalid-note"
    _seed_exact_note(template_vault, exact_id)
    _seed_shifted_note(template_vault, shifted_id)
    _seed_fuzzy_note(template_vault, fuzzy_id)
    _seed_total_orphan_note(template_vault, orphan_id)
    _seed_anchor_invalid_note(template_vault, invalid_id)
    unanchored = assert_success(
        capture(server, TOPIC, "a purely topical drift-queue-membership control")
    )

    body = assert_success(notes_call(server, "drift", topic=TOPIC))

    queue_ids = {item["note"]["note_id"] for item in body["items"]}
    assert queue_ids == {fuzzy_id, orphan_id, invalid_id}, (
        f"exact/shifted/unanchored must never appear in the review queue, got {queue_ids!r}"
    )
    assert exact_id not in queue_ids
    assert shifted_id not in queue_ids
    assert unanchored["note_id"] not in queue_ids
    assert body["total_count"] == 3
    assert body["invalid_count"] == 1


def test_drift_total_count_includes_anchor_invalid_while_wiki_status_drifted_excludes_it(
    vault_config: Path, template_vault: Path
) -> None:
    """Pagination (`next_cursor`/`has_more`/`total_count`) is one contract
    with `items` -- `total_count` must equal `len(items)`, the full queue
    including corruption, or a topic with any `anchor-invalid` note
    mis-paginates. `invalid_count` is the breakdown. `wiki_status`'s
    `drifted` badge counts `fuzzy + orphaned` only (R2) -- the queue header
    and the badge disagree **by design**, not by bug, so both are pinned
    together here."""
    orphan_id = "20260101-090000-count-orphan-note"
    invalid_id = "20260101-090100-count-invalid-note"
    _seed_total_orphan_note(template_vault, orphan_id)
    _seed_anchor_invalid_note(template_vault, invalid_id)
    server = build_full_server()

    body = assert_success(notes_call(server, "drift", topic=TOPIC))
    status_payload = gather_wiki_status(LocalFSStore(template_vault), template_vault, topic=TOPIC)

    assert len(body["items"]) == 2
    assert body["total_count"] == 2, "total_count must count the anchor-invalid item too"
    assert body["invalid_count"] == 1
    assert status_payload["totals"]["notes"]["drifted"] == 1, (
        "wiki_status excludes anchor-invalid from `drifted` -- the header and the "
        "badge disagree by design"
    )


def test_drift_item_pinned_quote_is_always_populated_even_for_a_total_orphan(
    vault_config: Path, template_vault: Path
) -> None:
    """The historical text is never withheld -- even a total orphan (no guess
    offered at all) still ships the passage that was originally pinned, so a
    human reviewing the queue always has something to compare against."""
    orphan_id = "20260101-090000-quote-orphan-note"
    quote = _seed_total_orphan_note(template_vault, orphan_id)
    server = build_full_server()

    body = assert_success(notes_call(server, "drift", topic=TOPIC))

    drift = _item_for(body, orphan_id)["drift"]
    assert drift["pinned_quote"] == quote
    assert drift["live_quote"] == "", (
        "a total orphan has nothing live to show -- live_quote must be empty, "
        "never a guess dressed up as the current text"
    )


def test_drift_alternatives_carries_at_least_one_entry_when_overlap_clears_the_floor(
    vault_config: Path, template_vault: Path
) -> None:
    """A fuzzy match's score clears `complete_orphan_threshold` by construction
    (it already cleared the stricter `guess_threshold`), so the queue must
    offer at least one alternative -- the architect's "an orphan never ships
    with zero guesses above that floor" constraint, exercised at its
    strongest case."""
    fuzzy_id = "20260101-090000-alt-fuzzy-note"
    _quote, page, _message = _seed_fuzzy_note(template_vault, fuzzy_id)
    server = build_full_server()

    body = assert_success(notes_call(server, "drift", topic=TOPIC))

    alternatives = _item_for(body, fuzzy_id)["drift"]["alternatives"]
    assert len(alternatives) >= 1, "a scored-above-floor match must offer at least one alternative"
    for alt in alternatives:
        assert set(alt) == {"page", "heading", "overlap"}, (
            f"a drift alternative carries page/heading/overlap -- unlike capture's, it "
            f"was genuinely scored: {alt!r}"
        )
        assert alt["page"] == page, "candidate generation only ever searches the anchor's own page"
        assert isinstance(alt["overlap"], int | float)


def test_drift_alternatives_is_empty_when_overlap_falls_below_the_floor(
    vault_config: Path, template_vault: Path
) -> None:
    """A garbage guess is worse than none: when the best candidate scores
    below `complete_orphan_threshold`, `alternatives` must be empty, not a
    padded-out low-confidence guess."""
    orphan_id = "20260101-090000-alt-orphan-note"
    _seed_total_orphan_note(template_vault, orphan_id)
    server = build_full_server()

    body = assert_success(notes_call(server, "drift", topic=TOPIC))

    assert _item_for(body, orphan_id)["drift"]["alternatives"] == []


def test_drift_reports_no_overlap_when_only_the_heading_survived(
    vault_config: Path, template_vault: Path
) -> None:
    """A deleted passage must not out-rank a drifted one.

    When candidate generation finds nothing but the enclosing heading survives,
    the resolver reports `guess_threshold - CLAMP_EPSILON` internally -- a
    ceiling, not a measurement. Surfacing it as `overlap` rendered "74% of the
    pinned passage survives" for a passage that survives not at all, ranking it
    above a genuine near-match on the same page. The queue must report no
    overlap here, while still offering the surviving section as a guess: the
    heading match is structural evidence and stands on its own.
    """
    orphan_id = "20260101-090000-heading-survived-note"
    _seed_surviving_heading_orphan_note(template_vault, orphan_id)
    server = build_full_server()

    body = assert_success(notes_call(server, "drift", topic=TOPIC))

    drift = _item_for(body, orphan_id)["drift"]
    assert drift["overlap"] is None, (
        "nothing was comparable, so there is no survival percentage to report -- "
        "reporting the clamp ceiling here shows the least evidence as the most confidence"
    )
    assert len(drift["alternatives"]) == 1, (
        "the surviving heading is structural evidence and is still offered as a guess"
    )
    assert drift["alternatives"][0]["overlap"] is None, (
        "a structural guess carries no measured overlap"
    )


def test_drift_anchor_invalid_item_carries_the_raw_quote_and_no_candidate_search(
    vault_config: Path, template_vault: Path
) -> None:
    """`anchor-invalid` means the quote was never in the historical blob --
    there is no trustworthy position to seed a candidate search from, so no
    search runs. The item still carries the raw recorded quote (so the human
    reviewing sees what the note claims), and carries no rewrite attribution:
    nothing about the page caused this record's corruption."""
    invalid_id = "20260101-090000-shape-invalid-note"
    quote = _seed_anchor_invalid_note(template_vault, invalid_id)
    server = build_full_server()

    body = assert_success(notes_call(server, "drift", topic=TOPIC))

    drift = _item_for(body, invalid_id)["drift"]
    assert drift["anchor_index"] == 0
    assert drift["pinned_quote"] == quote
    assert drift["live_quote"] == ""
    # `None`, never `0` -- "nothing was comparable" is a different claim from
    # "0% of it survived", and a renderer that cannot tell them apart shows a
    # number the resolver never measured.
    assert drift["overlap"] is None, (
        "no candidate search ran for this record, so there is no overlap to report"
    )
    assert drift["alternatives"] == []
    # Provisional on representation (an omitted key vs. an explicit null) --
    # either satisfies "carries no rewrite attribution"; `.get` accepts both.
    # Empty string, not an omitted key: every other "not applicable" value in
    # this payload family is `""` (`page`, `heading`, `live_quote`,
    # `next_cursor`), and introducing key-omission for two fields would put a
    # second convention inside the same object. The dashboard branches on
    # falsiness either way.
    assert drift["rewritten_at"] == ""
    assert drift["rewritten_by"] == ""


def test_drift_item_carries_rewrite_attribution_for_a_genuinely_reconciled_page(
    vault_config: Path, template_vault: Path
) -> None:
    """A queue member whose page really was rewritten since the anchor was
    pinned must carry `rewritten_at`/`rewritten_by` from the reconciliation
    pass -- the audit trail a human needs to trust the queue entry."""
    fuzzy_id = "20260101-090000-rewrite-fuzzy-note"
    _quote, _page, reword_message = _seed_fuzzy_note(template_vault, fuzzy_id)
    server = build_full_server()

    body = assert_success(notes_call(server, "drift", topic=TOPIC))

    drift = _item_for(body, fuzzy_id)["drift"]
    rewritten_at = drift.get("rewritten_at")
    assert isinstance(rewritten_at, str) and rewritten_at
    datetime.fromisoformat(rewritten_at)  # must not raise -- ISO 8601
    assert drift.get("rewritten_by") == reword_message


def test_drift_pagination_splits_queue_members_across_pages_using_the_shared_cursor_contract(
    vault_config: Path, template_vault: Path
) -> None:
    """Pagination must reuse `list`'s opaque-cursor contract, not invent a
    second scheme: the minted cursor decodes under the same sort tag, and
    every queue member is reachable across pages exactly once -- neither
    dropped nor duplicated at the page boundary."""
    orphan_id = "20260101-090000-page-orphan-note"
    fuzzy_id = "20260101-090100-page-fuzzy-note"
    invalid_id = "20260101-090200-page-invalid-note"
    _seed_total_orphan_note(template_vault, orphan_id)
    _seed_fuzzy_note(template_vault, fuzzy_id)
    _seed_anchor_invalid_note(template_vault, invalid_id)
    server = build_full_server()

    first_page = assert_success(notes_call(server, "drift", topic=TOPIC, limit=2))
    assert len(first_page["items"]) == 2
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] != ""
    assert first_page["total_count"] == 3
    assert decode_cursor(first_page["next_cursor"]).sort == _NOTES_SORT, (
        "drift's cursor must reuse the notes listing's sort contract, not a second scheme"
    )

    second_page = assert_success(
        notes_call(server, "drift", topic=TOPIC, limit=2, cursor=first_page["next_cursor"])
    )
    assert len(second_page["items"]) == 1
    assert second_page["has_more"] is False
    assert second_page["next_cursor"] == ""
    assert second_page["total_count"] == 3

    seen_ids = {item["note"]["note_id"] for item in first_page["items"]} | {
        item["note"]["note_id"] for item in second_page["items"]
    }
    assert seen_ids == {orphan_id, fuzzy_id, invalid_id}, (
        "every queue member must appear exactly once across both pages"
    )


def test_drift_queue_is_empty_when_the_topic_has_no_queue_member_notes(
    vault_config: Path, template_vault: Path
) -> None:
    _seed_exact_note(template_vault, "20260101-090000-only-exact-note")
    server = build_full_server()

    body = assert_success(notes_call(server, "drift", topic=TOPIC))

    assert body["items"] == []
    assert body["total_count"] == 0
    assert body["invalid_count"] == 0
    assert body["has_more"] is False
    assert body["next_cursor"] == ""


def test_drift_never_writes_or_commits(vault_config: Path, template_vault: Path) -> None:
    """`drift` is read-only throughout: no lock, no note-file write, no
    commit -- unlike `reanchor`/`detach`/`promote`/`archive`, it carries no
    `mode` parameter at all because there is nothing here to gate."""
    _seed_fuzzy_note(template_vault, "20260101-090000-readonly-fuzzy-note")
    _seed_anchor_invalid_note(template_vault, "20260101-090100-readonly-invalid-note")
    before_sha = git_head_sha(template_vault)
    before_count = git_commit_count(template_vault)
    server = build_full_server()

    assert_success(notes_call(server, "drift", topic=TOPIC))
    assert_success(notes_call(server, "drift", topic=TOPIC, limit=1))

    assert git_head_sha(template_vault) == before_sha, "drift must never commit"
    assert git_commit_count(template_vault) == before_count


# ---------------------------------------------------------------------------
# notes action=reanchor|detach -- dry-run/apply mode pair, mirroring
# `suggestions_review`'s pattern exactly: `mode=dry-run` (the schema default)
# previews the transition without writing and returns a decision envelope
# (`decision_id`, `summary`, `context`, `options`, `provenance`,
# `reason_required`); `mode=apply` performs exactly one commit. Both actions
# address one anchor at a time by its 0-based index into the note's
# append-only history; only a *live* target is addressable -- an index that
# is out of range or already superseded/detached is `INVALID_ARGUMENT`
# before any write, mirroring the operation-level negative-space case in
# `tests/core/notes/test_reanchor_note.py`, now exercised through the wire.
#
# `context`/`provenance`'s exact key sets are not specified anywhere this
# phase's design documents reach, so these tests assert only their presence
# and type -- the weakest defensible contract, not a guessed shape.
# ---------------------------------------------------------------------------


def _seed_anchored_note(server: Any, vault: Path, page: str, quote: str) -> str:
    """Capture a note pinned at span fidelity to `page`/`quote` via the real
    conversational tool -- so its anchor at index 0 is exactly what a real
    capture would produce, mirroring `test_reanchor_note.py`'s own fixture
    precedent."""
    _seed_capture_page(vault, page, f"# Seed page\n\n{quote}.\n", f"test: seed {page}")
    captured = assert_success(
        capture(server, TOPIC, "a reflection worth revisiting", quote=quote, pages=[page])
    )
    note_id = captured["note_id"]
    assert isinstance(note_id, str)
    return note_id


def _read_note_document(vault: Path, note_id: str) -> NoteDocument:
    text = (vault / f"notes/{TOPIC}/{note_id}.md").read_text(encoding="utf-8")
    document, error = parse_note(text)
    assert error is None, f"the note must parse cleanly, got error: {error!r}"
    assert document is not None
    return document


def _assert_decision_envelope_shape(body: dict[str, Any]) -> None:
    """The uniform decision-envelope shape every dry-run gate in this codebase
    returns (`suggestions_review` is the precedent) -- present and typed, not
    a guessed set of keys inside `context`/`provenance`."""
    assert isinstance(body.get("decision_id"), str) and body["decision_id"]
    assert isinstance(body.get("summary"), str) and body["summary"]
    assert isinstance(body.get("context"), dict)
    assert isinstance(body.get("options"), list) and body["options"]
    assert isinstance(body.get("provenance"), dict)
    assert isinstance(body.get("reason_required"), bool)


# ---------------------------------------------------------------------------
# reanchor -- dry-run preview, decision envelope, apply, accept-projection
# ---------------------------------------------------------------------------


def test_notes_reanchor_dry_run_previews_without_writing(
    vault_config: Path, template_vault: Path
) -> None:
    page = f"{TOPIC}/reanchor-dry-run-original.md"
    quote = "the passage this reanchor dry-run targets"
    server = build_full_server()
    note_id = _seed_anchored_note(server, template_vault, page, quote)
    new_page = f"{TOPIC}/reanchor-dry-run-new.md"
    _seed_capture_page(
        template_vault, new_page, "# New\n\nthe corrected passage.\n", "test: seed new page"
    )
    before_sha = git_head_sha(template_vault)
    before_commits = git_commit_count(template_vault)

    body = assert_success(
        notes_call(
            server,
            "reanchor",
            topic=TOPIC,
            note_id=note_id,
            anchor=0,
            page=new_page,
            quote="the corrected passage.",
            mode="dry-run",
        )
    )

    assert body["mode"] == "dry-run"
    assert git_head_sha(template_vault) == before_sha, "a dry-run must never commit"
    assert git_commit_count(template_vault) == before_commits


def test_notes_reanchor_dry_run_returns_a_decision_envelope(
    vault_config: Path, template_vault: Path
) -> None:
    page = f"{TOPIC}/reanchor-envelope-original.md"
    quote = "a passage worth confirming through the decision envelope"
    server = build_full_server()
    note_id = _seed_anchored_note(server, template_vault, page, quote)
    new_page = f"{TOPIC}/reanchor-envelope-new.md"
    _seed_capture_page(
        template_vault, new_page, "# New\n\nthe corrected passage.\n", "test: seed new page"
    )

    body = assert_success(
        notes_call(
            server,
            "reanchor",
            topic=TOPIC,
            note_id=note_id,
            anchor=0,
            page=new_page,
            quote="the corrected passage.",
            mode="dry-run",
        )
    )

    _assert_decision_envelope_shape(body)


def test_notes_reanchor_apply_makes_exactly_one_commit_and_appends_the_new_anchor(
    vault_config: Path, template_vault: Path
) -> None:
    page = f"{TOPIC}/reanchor-apply-original.md"
    quote = "the passage this apply-mode reanchor targets"
    server = build_full_server()
    note_id = _seed_anchored_note(server, template_vault, page, quote)
    new_page = f"{TOPIC}/reanchor-apply-new.md"
    _seed_capture_page(
        template_vault, new_page, "# New\n\nthe corrected passage.\n", "test: seed new page"
    )
    before_commits = git_commit_count(template_vault)

    body = assert_success(
        notes_call(
            server,
            "reanchor",
            topic=TOPIC,
            note_id=note_id,
            anchor=0,
            page=new_page,
            quote="the corrected passage.",
            mode="apply",
        )
    )

    assert body["mode"] == "apply"
    assert git_commit_count(template_vault) == before_commits + 1, (
        "apply must make exactly one commit"
    )
    assert isinstance(body.get("commit"), str) and body["commit"]
    assert body["commit"] == git_head_sha(template_vault)

    document = _read_note_document(template_vault, note_id)
    assert len(document.anchors) == 2, "apply must append, never replace"
    assert document.anchors[0].quote == quote, (
        "the original anchor of record must survive byte-identical"
    )
    assert document.anchors[1].kind == "reanchored"
    assert document.anchors[1].page == new_page


def test_notes_reanchor_apply_with_no_page_or_quote_accepts_the_projected_match(
    vault_config: Path, template_vault: Path
) -> None:
    """`INTERFACE_DESIGN.md` §1: `page` and `quote` empty means "accept the
    projected match" -- the drift queue's one-click accept, not a separate
    code path from the explicit-arguments case above."""
    page = f"{TOPIC}/reanchor-accept-projection.md"
    quote = "a passage that has not drifted at all"
    server = build_full_server()
    note_id = _seed_anchored_note(server, template_vault, page, quote)
    before_commits = git_commit_count(template_vault)

    body = assert_success(
        notes_call(server, "reanchor", topic=TOPIC, note_id=note_id, anchor=0, mode="apply")
    )

    assert body["mode"] == "apply"
    assert git_commit_count(template_vault) == before_commits + 1

    document = _read_note_document(template_vault, note_id)
    assert len(document.anchors) == 2, "accepting the projection still appends, like any reanchor"
    accepted = document.anchors[1]
    assert accepted.kind == "reanchored"
    assert accepted.page == page, "with nothing drifted, the accepted page is unchanged"
    assert accepted.quote == quote


def test_notes_reanchor_targeting_one_anchor_leaves_a_different_pages_anchor_untouched(
    vault_config: Path, template_vault: Path
) -> None:
    """A note may carry more than one independent anchor, each resolved on
    its own page. Correcting one through the dispatcher must never touch, or
    silently un-live, the other -- the wire-level counterpart to
    `test_reanchor_note.py`'s op-level precedent."""
    note_id = "20260101-093000-wire-two-independent-pages"
    page_a = _forged_anchor(page=f"{TOPIC}/wire-multi-anchor-a.md", quote="the passage on page A")
    page_b = _forged_anchor(
        page=f"{TOPIC}/wire-multi-anchor-b.md",
        quote="the passage on page B, never touched by this reanchor",
    )
    _write_forged_note(template_vault, note_id, _forged_note(note_id, anchors=(page_a, page_b)))
    new_page = f"{TOPIC}/wire-multi-anchor-a-corrected.md"
    _seed_capture_page(
        template_vault,
        new_page,
        "# Corrected\n\nthe corrected passage on page A.\n",
        "test: seed corrected page",
    )
    server = build_full_server()

    assert_success(
        notes_call(
            server,
            "reanchor",
            topic=TOPIC,
            note_id=note_id,
            anchor=0,
            page=new_page,
            quote="the corrected passage on page A.",
            mode="apply",
        )
    )

    document = _read_note_document(template_vault, note_id)
    assert document.anchors[0] == page_a, "the targeted anchor itself must stay byte-unchanged"
    assert document.anchors[1] == page_b, (
        "page B's anchor must stay byte-unchanged by a reanchor targeting page A"
    )
    assert document.anchors[2].kind == "reanchored"


# ---------------------------------------------------------------------------
# detach -- dry-run preview, decision envelope, apply
# ---------------------------------------------------------------------------


def test_notes_detach_dry_run_previews_without_writing(
    vault_config: Path, template_vault: Path
) -> None:
    page = f"{TOPIC}/detach-dry-run-target.md"
    quote = "a passage this detach dry-run targets"
    server = build_full_server()
    note_id = _seed_anchored_note(server, template_vault, page, quote)
    before_sha = git_head_sha(template_vault)
    before_commits = git_commit_count(template_vault)

    body = assert_success(
        notes_call(server, "detach", topic=TOPIC, note_id=note_id, anchor=0, mode="dry-run")
    )

    assert body["mode"] == "dry-run"
    assert git_head_sha(template_vault) == before_sha, "a dry-run must never commit"
    assert git_commit_count(template_vault) == before_commits


def test_notes_detach_dry_run_returns_a_decision_envelope(
    vault_config: Path, template_vault: Path
) -> None:
    page = f"{TOPIC}/detach-envelope-target.md"
    quote = "a passage worth confirming a detach through the decision envelope"
    server = build_full_server()
    note_id = _seed_anchored_note(server, template_vault, page, quote)

    body = assert_success(
        notes_call(server, "detach", topic=TOPIC, note_id=note_id, anchor=0, mode="dry-run")
    )

    _assert_decision_envelope_shape(body)


def test_notes_detach_apply_makes_exactly_one_commit_and_appends_a_terminal_record(
    vault_config: Path, template_vault: Path
) -> None:
    page = f"{TOPIC}/detach-apply-target.md"
    quote = "a passage this apply-mode detach targets"
    server = build_full_server()
    note_id = _seed_anchored_note(server, template_vault, page, quote)
    before_commits = git_commit_count(template_vault)

    body = assert_success(
        notes_call(server, "detach", topic=TOPIC, note_id=note_id, anchor=0, mode="apply")
    )

    assert body["mode"] == "apply"
    assert git_commit_count(template_vault) == before_commits + 1, (
        "apply must make exactly one commit"
    )
    assert isinstance(body.get("commit"), str) and body["commit"]
    assert body["commit"] == git_head_sha(template_vault)

    document = _read_note_document(template_vault, note_id)
    assert len(document.anchors) == 2
    assert document.anchors[-1].kind == "detached"
    assert effective_anchor(document) is None


# ---------------------------------------------------------------------------
# mode defaults to dry-run -- the mechanical half of the read/offer guard.
# `mode=apply` must never fire from detection alone; the schema default is
# the part of that guarantee this file can actually assert mechanically.
# ---------------------------------------------------------------------------


def test_notes_dispatcher_schema_mode_defaults_to_dry_run() -> None:
    schema = tool_schema(build_full_server(), "notes")
    assert schema["properties"]["mode"]["default"] == "dry-run"


def test_notes_dispatcher_description_states_the_read_offer_guard_once_mutating() -> None:
    """Every mutating dispatcher in this codebase states its confirmation
    precondition in its own registered description (see
    `test_tool_description_guards.py`'s `_MUTATING_DISPATCHERS`) -- `notes`
    now exposes `reanchor`/`detach` and must join that guarded set, using the
    same verbatim guard clause `suggestions_review` and `INTERFACE_DESIGN.md`
    §1's own draft description both use.

    (Once this goes green, `test_tool_description_guards.py` must move
    `notes` from its `_READ_ONLY_CONTROLS` negative control into
    `_MUTATING_DISPATCHERS` -- that file is out of this step's declared
    scope, so the move is left for whoever wires the actions.)
    """
    descriptions = {tool.name: (tool.description or "") for tool in list_tools(build_full_server())}
    assert "notes" in descriptions
    description = descriptions["notes"].lower()
    assert "never fires from detection alone" in description, (
        f"the notes dispatcher now exposes mutating actions and must state the same "
        f"read/offer guard clause every other mutating dispatcher carries: "
        f"{descriptions['notes']!r}"
    )


# ---------------------------------------------------------------------------
# Error grammar -- NOTE_NOT_FOUND, INVALID_ARGUMENT (anchor not live),
# PAGE_NOT_FOUND -- each with its code and an actionable fix.
#
# The out-of-range/not-live cases below assert on `fix` as well as `code`:
# right now, *every* call to `reanchor`/`detach` is rejected `INVALID_ARGUMENT`
# by the dispatcher's action-enum check (action not yet registered), which
# would make a code-only assertion pass vacuously regardless of the anchor
# argument. `_live_target`'s rejection carries no custom `fix`, so it falls
# back to the code's `DEFAULT_FIX` ("Correct the named argument and call
# again.") -- distinct from the action-enum check's custom fix ("Pass action
# as one of: ..."). Asserting on `fix` is what makes these tests fail for the
# *new* behavior rather than passing for the old one.
# ---------------------------------------------------------------------------

_ANCHOR_NOT_LIVE_FIX = "Correct the named argument and call again."


def test_notes_reanchor_out_of_range_anchor_index_is_rejected_before_any_write(
    vault_config: Path, template_vault: Path
) -> None:
    page = f"{TOPIC}/reanchor-out-of-range.md"
    quote = "the only passage this note has"
    server = build_full_server()
    note_id = _seed_anchored_note(server, template_vault, page, quote)
    before_commits = git_commit_count(template_vault)

    err = error_of(
        notes_call(
            server,
            "reanchor",
            topic=TOPIC,
            note_id=note_id,
            anchor=5,
            page=page,
            quote=quote,
            mode="apply",
        )
    )

    assert_error_shape(err, "INVALID_ARGUMENT")
    assert err["fix"] == _ANCHOR_NOT_LIVE_FIX, (
        f"expected the anchor-liveness rejection's default fix, not the action-enum "
        f"check's fix -- got {err['fix']!r}"
    )
    assert git_commit_count(template_vault) == before_commits, (
        "a rejected reanchor must make no commit"
    )


def test_notes_detach_out_of_range_anchor_index_is_rejected_before_any_write(
    vault_config: Path, template_vault: Path
) -> None:
    page = f"{TOPIC}/detach-out-of-range.md"
    quote = "the only passage this note has"
    server = build_full_server()
    note_id = _seed_anchored_note(server, template_vault, page, quote)
    before_commits = git_commit_count(template_vault)

    err = error_of(
        notes_call(server, "detach", topic=TOPIC, note_id=note_id, anchor=5, mode="apply")
    )

    assert_error_shape(err, "INVALID_ARGUMENT")
    assert err["fix"] == _ANCHOR_NOT_LIVE_FIX, (
        f"expected the anchor-liveness rejection's default fix, not the action-enum "
        f"check's fix -- got {err['fix']!r}"
    )
    assert git_commit_count(template_vault) == before_commits


def test_notes_reanchor_an_already_detached_anchor_is_rejected_with_invalid_argument(
    vault_config: Path, template_vault: Path
) -> None:
    """Mirrors the operation-level append-only negative-space case, now
    exercised through the dispatcher: a detached anchor is no longer live, so
    re-anchoring it must be rejected before any write."""
    page = f"{TOPIC}/reanchor-after-detach.md"
    quote = "a passage detached before a correction is attempted through the wire"
    server = build_full_server()
    note_id = _seed_anchored_note(server, template_vault, page, quote)
    detach_result = core_detach(
        LocalFSStore(template_vault), template_vault, VaultVcs(template_vault), TOPIC, note_id, 0
    )
    assert "error" not in detach_result, f"fixture setup failed: {detach_result!r}"
    new_page = f"{TOPIC}/reanchor-after-detach-new.md"
    _seed_capture_page(
        template_vault, new_page, "# New\n\nthe new passage.\n", "test: seed new page"
    )
    before_commits = git_commit_count(template_vault)

    err = error_of(
        notes_call(
            server,
            "reanchor",
            topic=TOPIC,
            note_id=note_id,
            anchor=0,
            page=new_page,
            quote="the new passage.",
            mode="apply",
        )
    )

    assert_error_shape(err, "INVALID_ARGUMENT")
    assert err["fix"] == _ANCHOR_NOT_LIVE_FIX, (
        f"expected the anchor-liveness rejection's default fix, not the action-enum "
        f"check's fix -- got {err['fix']!r}"
    )
    assert git_commit_count(template_vault) == before_commits, (
        "a rejected reanchor must make no commit"
    )


def test_notes_detach_an_already_detached_anchor_is_rejected_with_invalid_argument(
    vault_config: Path, template_vault: Path
) -> None:
    page = f"{TOPIC}/detach-twice-through-wire.md"
    quote = "a passage detached, then detached again through the wire"
    server = build_full_server()
    note_id = _seed_anchored_note(server, template_vault, page, quote)
    detach_result = core_detach(
        LocalFSStore(template_vault), template_vault, VaultVcs(template_vault), TOPIC, note_id, 0
    )
    assert "error" not in detach_result, f"fixture setup failed: {detach_result!r}"
    before_commits = git_commit_count(template_vault)

    err = error_of(
        notes_call(server, "detach", topic=TOPIC, note_id=note_id, anchor=0, mode="apply")
    )

    assert_error_shape(err, "INVALID_ARGUMENT")
    assert err["fix"] == _ANCHOR_NOT_LIVE_FIX, (
        f"expected the anchor-liveness rejection's default fix, not the action-enum "
        f"check's fix -- got {err['fix']!r}"
    )
    assert git_commit_count(template_vault) == before_commits


def test_notes_reanchor_unknown_note_id_is_note_not_found(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    server = build_full_server()
    err = error_of(
        notes_call(
            server,
            "reanchor",
            topic=TOPIC,
            note_id="20260101-000000-never-captured",
            anchor=0,
            page=f"{TOPIC}/somewhere.md",
            quote="whatever",
            mode="apply",
        )
    )
    assert_error_shape(err, "NOTE_NOT_FOUND")


def test_notes_detach_unknown_note_id_is_note_not_found(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    server = build_full_server()
    err = error_of(
        notes_call(
            server,
            "detach",
            topic=TOPIC,
            note_id="20260101-000000-never-captured",
            anchor=0,
            mode="apply",
        )
    )
    assert_error_shape(err, "NOTE_NOT_FOUND")


def test_notes_reanchor_targeting_a_page_that_no_longer_exists_fails_with_page_not_found(
    vault_config: Path, template_vault: Path
) -> None:
    page = f"{TOPIC}/reanchor-page-gone-original.md"
    quote = "the passage before the page vanished"
    server = build_full_server()
    note_id = _seed_anchored_note(server, template_vault, page, quote)
    missing_page = f"{TOPIC}/reanchor-page-gone-target.md"
    before_commits = git_commit_count(template_vault)

    err = error_of(
        notes_call(
            server,
            "reanchor",
            topic=TOPIC,
            note_id=note_id,
            anchor=0,
            page=missing_page,
            quote="whatever the passage was",
            mode="apply",
        )
    )

    assert_error_shape(err, "PAGE_NOT_FOUND")
    assert git_commit_count(template_vault) == before_commits, (
        "a rejected reanchor must make no commit"
    )


def test_notes_reanchor_page_not_found_fix_text_names_detach_as_the_fallback(
    vault_config: Path, template_vault: Path
) -> None:
    """`INTERFACE_DESIGN.md` §8's error grammar table gives this row a
    fallback the generic `PAGE_NOT_FOUND` fix lacks: a user pointing at a
    deleted page can keep the note without an anchor by detaching instead."""
    page = f"{TOPIC}/reanchor-fix-text-original.md"
    quote = "a passage before the page vanishes"
    server = build_full_server()
    note_id = _seed_anchored_note(server, template_vault, page, quote)
    missing_page = f"{TOPIC}/reanchor-fix-text-missing.md"

    err = error_of(
        notes_call(
            server,
            "reanchor",
            topic=TOPIC,
            note_id=note_id,
            anchor=0,
            page=missing_page,
            quote="whatever the passage was",
            mode="apply",
        )
    )

    assert_error_shape(err, "PAGE_NOT_FOUND")
    assert "detach" in err["fix"], (
        f"the fix text must name `notes action=detach` as the fallback for a deleted "
        f"reanchor target; got {err['fix']!r}"
    )


# ---------------------------------------------------------------------------
# notes action=promote|archive -- the last two actions the full design names.
# Neither operates per-anchor: `promote`
# (`core.operations.promote_note.promote_note`) crosses the notes/KB boundary,
# grounding a caller's question in the note's currently-*live* anchored pages
# and writing into `qa.jsonl` or the gap queue; `archive`
# (`core.operations.reanchor_note.archive`) only flips frontmatter `status`
# and never touches `## Anchors` at all. Both still follow the same
# dry-run/apply mode pair every mutating action in this dispatcher uses, and
# both are pinned here at the wire level only -- the operations themselves
# already have their own behavioral suites
# (`tests/core/notes/test_promote_note.py`,
# `tests/core/notes/test_reanchor_note.py`'s `archive` section).
#
# Non-vacuity, the same trap as the `reanchor`/`detach` section above:
# before this step wired them, every call to `promote`/`archive` was
# rejected `INVALID_ARGUMENT` by the dispatcher's action-enum check, which
# would have made a code-only assertion pass vacuously. `target=golden` and
# `target=gap`-on-a-reflection assert the exact `message` from
# `INTERFACE_DESIGN.md` §8 -- already shipped, verbatim, inside
# `core.operations.promote_note`, so this is a pin, not a guess. The
# bad-target/bad-mode/bad-verdict tests assert the rejection's `fix` is
# distinct from the action-enum check's own fix text, since none of those
# three carries the same message the action-enum check does.
#
# `context`/`provenance`'s exact key sets are unspecified for these two
# actions too (same reasoning as `reanchor`/`detach` above), so only
# presence/type is asserted, never a guessed shape.
# ---------------------------------------------------------------------------


def _note_path(note_id: str) -> str:
    return f"notes/{TOPIC}/{note_id}.md"


def _read_qa_records(vault: Path) -> list[QARecord]:
    path = vault / qa_dataset_path(TOPIC)
    if not path.exists():
        return []
    return parse_qa_jsonl(path.read_text(encoding="utf-8"))


def _read_gap_records(vault: Path) -> list[GapRecord]:
    store = LocalFSStore(vault)
    path = gaps_path(TOPIC)
    if not store.exists(path):
        return []
    return parse_gaps_jsonl(store.read_text(path))


# ---------------------------------------------------------------------------
# promote -- dry-run preview, decision envelope, apply to each live
# destination (`trainset`/`gap`)
# ---------------------------------------------------------------------------


def test_notes_promote_dry_run_previews_without_writing(
    vault_config: Path, template_vault: Path
) -> None:
    note_id = "20260101-100000-promote-dry-run-preview"
    anchor = _forged_anchor(page=f"{TOPIC}/promote-dry-run-preview.md", quote="a grounded claim")
    _write_forged_note(
        template_vault, note_id, _forged_note(note_id, intent="question", anchors=(anchor,))
    )
    server = build_full_server()
    before_sha = git_head_sha(template_vault)
    before_commits = git_commit_count(template_vault)

    body = assert_success(
        notes_call(
            server,
            "promote",
            topic=TOPIC,
            note_id=note_id,
            target="trainset",
            question="Does this dry-run write anything?",
            answer="No, dry-run never writes.",
            mode="dry-run",
        )
    )

    assert body["mode"] == "dry-run"
    assert git_head_sha(template_vault) == before_sha, "a dry-run must never commit"
    assert git_commit_count(template_vault) == before_commits
    assert _read_qa_records(template_vault) == [], "a dry-run must not append to qa.jsonl"


def test_notes_promote_dry_run_returns_a_decision_envelope(
    vault_config: Path, template_vault: Path
) -> None:
    note_id = "20260101-100100-promote-dry-run-envelope"
    anchor = _forged_anchor(page=f"{TOPIC}/promote-dry-run-envelope.md", quote="a grounded claim")
    _write_forged_note(
        template_vault, note_id, _forged_note(note_id, intent="question", anchors=(anchor,))
    )
    server = build_full_server()

    body = assert_success(
        notes_call(
            server,
            "promote",
            topic=TOPIC,
            note_id=note_id,
            target="trainset",
            question="Does this preview return a decision envelope?",
            answer="Yes.",
            mode="dry-run",
        )
    )

    _assert_decision_envelope_shape(body)


def test_notes_promote_apply_to_trainset_appends_exactly_one_curated_example(
    vault_config: Path, template_vault: Path
) -> None:
    note_id = "20260101-100200-promote-apply-trainset"
    page = f"{TOPIC}/promote-apply-trainset.md"
    anchor = _forged_anchor(page=page, quote="a claim worth grounding an eval question in")
    _write_forged_note(
        template_vault, note_id, _forged_note(note_id, intent="reflection", anchors=(anchor,))
    )
    server = build_full_server()
    before_commits = git_commit_count(template_vault)
    question = "Does the grounded claim answer this question?"

    body = assert_success(
        notes_call(
            server,
            "promote",
            topic=TOPIC,
            note_id=note_id,
            target="trainset",
            question=question,
            answer="Yes, per the anchored page.",
            verdict="good",
            mode="apply",
        )
    )

    assert body["mode"] == "apply"
    assert body["committed"] is True
    assert git_commit_count(template_vault) == before_commits + 1, (
        "apply must make exactly one commit"
    )

    records = _read_qa_records(template_vault)
    assert len(records) == 1
    assert records[0].query == question
    assert records[0].pages_used == (page,), (
        "grounding must come from the note's own live anchor, never a caller-supplied path"
    )
    assert records[0].answer == "Yes, per the anchored page."
    assert records[0].verdict == "good"


def test_notes_promote_apply_to_gap_on_an_opted_in_intent_files_a_reported_gap(
    vault_config: Path, template_vault: Path
) -> None:
    note_id = "20260101-100300-promote-apply-gap"
    page = f"{TOPIC}/promote-apply-gap.md"
    anchor = _forged_anchor(page=page, quote="a passage the wiki may be wrong about")
    _write_forged_note(
        template_vault, note_id, _forged_note(note_id, intent="dispute", anchors=(anchor,))
    )
    server = build_full_server()
    before_commits = git_commit_count(template_vault)
    question = "Is the wiki wrong about this passage?"

    body = assert_success(
        notes_call(
            server,
            "promote",
            topic=TOPIC,
            note_id=note_id,
            target="gap",
            question=question,
            mode="apply",
        )
    )

    assert body["mode"] == "apply"
    assert body["committed"] is True
    assert git_commit_count(template_vault) == before_commits + 1

    gaps = _read_gap_records(template_vault)
    assert len(gaps) == 1
    assert gaps[0].origin == "reported", "a note-filed gap reuses the existing reported origin"
    assert gaps[0].question == question, (
        "the filed gap carries the caller's question, never the note's own body"
    )
    assert gaps[0].reference_pages == (page,)
    assert gaps[0].reported_reason == f"note:{_note_path(note_id)}#0", (
        "provenance is a note pointer -- topic-relative path and the anchor of record's "
        "0-based index -- matching the operation-level contract this wires into"
    )


# ---------------------------------------------------------------------------
# promote -- target routing: `golden` always rejects, `gap` is intent-gated
# ---------------------------------------------------------------------------


def test_notes_promote_target_golden_always_rejects_with_invalid_argument(
    vault_config: Path, template_vault: Path
) -> None:
    note_id = "20260101-100400-promote-golden"
    anchor = _forged_anchor(page=f"{TOPIC}/promote-golden.md", quote="any grounded claim")
    _write_forged_note(
        template_vault, note_id, _forged_note(note_id, intent="question", anchors=(anchor,))
    )
    server = build_full_server()
    before_commits = git_commit_count(template_vault)

    err = error_of(
        notes_call(
            server,
            "promote",
            topic=TOPIC,
            note_id=note_id,
            target="golden",
            question="Any question at all?",
        )
    )

    assert_error_shape(err, "INVALID_ARGUMENT")
    assert err["message"] == (
        "promoting to the held-out (golden) set is deferred: trainset and golden must "
        "stay disjoint, so the choice is one-way and needs its own review gate"
    ), "the interface design's error grammar text is the documented, executable interface"
    assert git_commit_count(template_vault) == before_commits, (
        "golden always rejects before any write"
    )


def test_notes_promote_target_gap_on_a_reflection_note_is_rejected(
    vault_config: Path, template_vault: Path
) -> None:
    note_id = "20260101-100500-promote-gap-reflection"
    anchor = _forged_anchor(page=f"{TOPIC}/promote-gap-reflection.md", quote="a loose thought")
    _write_forged_note(
        template_vault, note_id, _forged_note(note_id, intent="reflection", anchors=(anchor,))
    )
    server = build_full_server()
    before_commits = git_commit_count(template_vault)

    err = error_of(
        notes_call(
            server,
            "promote",
            topic=TOPIC,
            note_id=note_id,
            target="gap",
            question="Is the wiki wrong here?",
        )
    )

    assert_error_shape(err, "INVALID_ARGUMENT")
    assert err["message"] == (
        "filing a gap needs a note whose intent is dispute, gap, or question; this one is "
        "a reflection"
    )
    assert git_commit_count(template_vault) == before_commits
    assert _read_gap_records(template_vault) == []


# ---------------------------------------------------------------------------
# promote -- `question`: required at this boundary, with a defaulting path
# ---------------------------------------------------------------------------


def test_notes_promote_with_an_explicit_question_uses_it_verbatim(
    vault_config: Path, template_vault: Path
) -> None:
    """`question` is required at this boundary; supplying it explicitly must
    win, regardless of the note's own intent or body."""
    note_id = "20260101-100600-promote-explicit-question"
    page = f"{TOPIC}/promote-explicit-question.md"
    anchor = _forged_anchor(page=page, quote="a stray thought, not phrased as a question")
    _write_forged_note(
        template_vault,
        note_id,
        _forged_note(
            note_id,
            intent="reflection",
            body="A stray thought, not itself a question.",
            anchors=(anchor,),
        ),
    )
    server = build_full_server()
    explicit_question = "Does explicit `question` win over the note's own body?"

    assert_success(
        notes_call(
            server,
            "promote",
            topic=TOPIC,
            note_id=note_id,
            target="trainset",
            question=explicit_question,
            answer="Yes.",
            mode="apply",
        )
    )

    records = _read_qa_records(template_vault)
    assert len(records) == 1
    assert records[0].query == explicit_question, (
        "the caller-supplied question must be used verbatim, never the note's own body"
    )


def test_notes_promote_defaults_the_question_from_the_notes_own_text_when_it_already_is_a_question(
    vault_config: Path, template_vault: Path
) -> None:
    """`INTERFACE_DESIGN.md` §1's schema: `question` "[d]efaults to the
    note's own text when the note already is a question" -- read here as
    intent `question`, the same enum value `notes action=list`'s `intent`
    filter and `promote target=gap`'s intent gate both key off. This is the
    dispatcher's own defaulting logic (`promote_note` itself takes no
    default -- its `question` parameter is a plain pass-through per
    `tests/core/notes/test_promote_note.py`'s own interface note), so this
    test flags exactly what it assumes about "the note's own text":
    `NoteDocument.body`, verbatim, not some other derived string."""
    note_id = "20260101-100700-promote-default-question"
    page = f"{TOPIC}/promote-default-question.md"
    anchor = _forged_anchor(page=page, quote="the grounding for the note's own question")
    note_text = "Does removing the reward-shaping term change convergence speed?"
    _write_forged_note(
        template_vault,
        note_id,
        _forged_note(note_id, intent="question", body=note_text, anchors=(anchor,)),
    )
    server = build_full_server()

    assert_success(
        notes_call(
            server,
            "promote",
            topic=TOPIC,
            note_id=note_id,
            target="trainset",
            answer="Some grounded answer.",
            mode="apply",
        )
    )

    records = _read_qa_records(template_vault)
    assert len(records) == 1
    assert records[0].query == note_text, (
        "with no `question` argument and an intent already 'question', the dispatcher must "
        "default to the note's own text rather than reject or write an empty query"
    )


# ---------------------------------------------------------------------------
# promote -- `pages_used` structurally cannot be a caller argument; a note
# with no live grounding page rejects rather than promoting empty-handed
# ---------------------------------------------------------------------------


def test_notes_dispatcher_schema_has_no_pages_used_property() -> None:
    """`promote`'s grounding pages are derived server-side from the note's
    live anchors (`core.operations.promote_note._grounding_pages`) -- there
    must be no `pages_used`-shaped parameter a caller could use to inject an
    arbitrary path. A structural guarantee, not merely a validated-away one."""
    schema = tool_schema(build_full_server(), "notes")
    assert "pages_used" not in schema["properties"]


def test_notes_promote_a_note_with_no_live_pages_is_rejected(
    vault_config: Path, template_vault: Path
) -> None:
    note_id = "20260101-100800-promote-no-live-pages"
    detached_anchor = _forged_anchor(
        page=f"{TOPIC}/promote-no-live-pages.md",
        quote="a passage no longer grounding anything",
        kind="detached",
    )
    _write_forged_note(
        template_vault,
        note_id,
        _forged_note(note_id, intent="question", anchors=(detached_anchor,)),
    )
    server = build_full_server()
    before_commits = git_commit_count(template_vault)

    err = error_of(
        notes_call(
            server,
            "promote",
            topic=TOPIC,
            note_id=note_id,
            target="trainset",
            question="Can a fully detached note ground a question?",
            answer="No.",
            mode="apply",
        )
    )

    assert_error_shape(err, "INVALID_ARGUMENT")
    assert "no live anchored page" in err["message"], (
        f"the wire-level rejection must surface the same reason the operation gives, not a "
        f"generic message: {err['message']!r}"
    )
    assert git_commit_count(template_vault) == before_commits


# ---------------------------------------------------------------------------
# promote -- error grammar: unknown note_id, bad target, bad verdict
# ---------------------------------------------------------------------------


def test_notes_promote_unknown_note_id_is_note_not_found(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    server = build_full_server()

    err = error_of(
        notes_call(
            server,
            "promote",
            topic=TOPIC,
            note_id="20260101-000000-never-captured",
            target="trainset",
            question="Does an unknown note_id fail cleanly?",
            answer="It must.",
            mode="apply",
        )
    )

    assert_error_shape(err, "NOTE_NOT_FOUND")


def test_notes_promote_bad_target_is_rejected_with_invalid_argument(
    vault_config: Path, template_vault: Path
) -> None:
    """`target` outside the enum routes through `promote_note`'s own
    already-shipped validation, which carries no custom `fix` -- so this
    pins the code's `DEFAULT_FIX` fallback (`_ANCHOR_NOT_LIVE_FIX`, the same
    constant the `reanchor`/`detach` section above already names), not a
    guess: `err(ErrorCode.INVALID_ARGUMENT, "...")` with no `fix=` argument
    always falls back to it."""
    note_id = "20260101-100900-promote-bad-target"
    anchor = _forged_anchor(page=f"{TOPIC}/promote-bad-target.md", quote="anything")
    _write_forged_note(
        template_vault, note_id, _forged_note(note_id, intent="question", anchors=(anchor,))
    )
    server = build_full_server()
    before_commits = git_commit_count(template_vault)

    err = error_of(
        notes_call(
            server,
            "promote",
            topic=TOPIC,
            note_id=note_id,
            target="nonsense",
            question="Whatever",
            mode="apply",
        )
    )

    assert_error_shape(err, "INVALID_ARGUMENT")
    assert err["fix"] == _ANCHOR_NOT_LIVE_FIX, (
        f"expected `promote_note`'s own target-validation fallback fix, not the "
        f"action-enum check's fix -- got {err['fix']!r}"
    )
    assert git_commit_count(template_vault) == before_commits


def test_notes_promote_bad_verdict_is_rejected_with_invalid_argument(
    vault_config: Path, template_vault: Path
) -> None:
    """`verdict` is not validated anywhere below the dispatcher today --
    `curate_example` accepts any string verbatim -- so this pins a
    wire-level contract this step's own plan calls for, not a reuse of
    existing validation. The exact `fix` wording is therefore genuinely
    unspecified by any design document; only the discriminator that proves
    the rejection is verdict-shaped, not the pre-wiring action-enum
    rejection, is asserted."""
    note_id = "20260101-101100-promote-bad-verdict"
    anchor = _forged_anchor(page=f"{TOPIC}/promote-bad-verdict.md", quote="anything")
    _write_forged_note(
        template_vault, note_id, _forged_note(note_id, intent="question", anchors=(anchor,))
    )
    server = build_full_server()
    before_commits = git_commit_count(template_vault)

    err = error_of(
        notes_call(
            server,
            "promote",
            topic=TOPIC,
            note_id=note_id,
            target="trainset",
            question="Whatever",
            answer="Whatever",
            verdict="maybe",
            mode="apply",
        )
    )

    assert_error_shape(err, "INVALID_ARGUMENT")
    assert "Pass action as one of" not in err["fix"], (
        f"the rejection must come from verdict validation, not the pre-wiring action-enum "
        f"check -- got {err['fix']!r}"
    )
    assert git_commit_count(template_vault) == before_commits
    assert _read_qa_records(template_vault) == []


def test_notes_dispatcher_schema_target_defaults_to_trainset() -> None:
    schema = tool_schema(build_full_server(), "notes")
    assert schema["properties"]["target"]["default"] == "trainset"


# ---------------------------------------------------------------------------
# archive -- dry-run preview, decision envelope, apply, idempotency, and the
# "never deletes" guarantee. Takes no `anchor` index at all: it flips
# frontmatter `status` and touches `## Anchors` not at all.
# ---------------------------------------------------------------------------


def test_notes_archive_dry_run_previews_without_writing(
    vault_config: Path, template_vault: Path
) -> None:
    note_id = "20260101-101200-archive-dry-run-preview"
    _write_forged_note(template_vault, note_id, _forged_note(note_id))
    server = build_full_server()
    before_sha = git_head_sha(template_vault)
    before_commits = git_commit_count(template_vault)

    body = assert_success(
        notes_call(server, "archive", topic=TOPIC, note_id=note_id, mode="dry-run")
    )

    assert body["mode"] == "dry-run"
    assert git_head_sha(template_vault) == before_sha, "a dry-run must never commit"
    assert git_commit_count(template_vault) == before_commits
    assert _read_note_document(template_vault, note_id).status == "active", (
        "a dry-run must not flip status"
    )


def test_notes_archive_dry_run_returns_a_decision_envelope(
    vault_config: Path, template_vault: Path
) -> None:
    note_id = "20260101-101300-archive-dry-run-envelope"
    _write_forged_note(template_vault, note_id, _forged_note(note_id))
    server = build_full_server()

    body = assert_success(
        notes_call(server, "archive", topic=TOPIC, note_id=note_id, mode="dry-run")
    )

    _assert_decision_envelope_shape(body)


def test_notes_archive_apply_makes_exactly_one_commit_and_sets_status_to_archived(
    vault_config: Path, template_vault: Path
) -> None:
    note_id = "20260101-101400-archive-apply"
    original = _forged_note(note_id)
    _write_forged_note(template_vault, note_id, original)
    server = build_full_server()
    before_commits = git_commit_count(template_vault)

    body = assert_success(notes_call(server, "archive", topic=TOPIC, note_id=note_id, mode="apply"))

    assert body["mode"] == "apply"
    assert body["committed"] is True
    assert body["written"] is True
    assert "anchor_index" not in body, "archive touches no anchor and must carry no anchor index"
    assert git_commit_count(template_vault) == before_commits + 1, (
        "apply must make exactly one commit"
    )

    document = _read_note_document(template_vault, note_id)
    assert document.status == "archived"
    assert document.anchors == original.anchors, "archiving is a frontmatter-only change"


def test_notes_archive_apply_twice_is_idempotent_and_makes_no_second_commit(
    vault_config: Path, template_vault: Path
) -> None:
    note_id = "20260101-101500-archive-twice"
    _write_forged_note(template_vault, note_id, _forged_note(note_id))
    server = build_full_server()
    assert_success(notes_call(server, "archive", topic=TOPIC, note_id=note_id, mode="apply"))
    before_second_commits = git_commit_count(template_vault)

    second = assert_success(
        notes_call(server, "archive", topic=TOPIC, note_id=note_id, mode="apply")
    )

    assert git_commit_count(template_vault) == before_second_commits, (
        "archiving an already-archived note must be a no-op, not a second commit"
    )
    assert second["written"] is False, "the second call changed nothing -- it must say so"
    assert second["duplicate"] is True, (
        "a caller must be able to tell 'archived it' from 'it was already archived', using "
        "the same written/duplicate vocabulary capture_note already returns -- not a new flag"
    )


def test_notes_archive_never_deletes_the_note_file(
    vault_config: Path, template_vault: Path
) -> None:
    note_id = "20260101-101600-archive-never-deletes"
    _write_forged_note(template_vault, note_id, _forged_note(note_id))
    server = build_full_server()

    assert_success(notes_call(server, "archive", topic=TOPIC, note_id=note_id, mode="apply"))

    assert (template_vault / _note_path(note_id)).exists(), (
        "the server never deletes a note; archiving must only flip status"
    )


def test_notes_archive_unknown_note_id_is_note_not_found(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    server = build_full_server()

    err = error_of(
        notes_call(
            server, "archive", topic=TOPIC, note_id="20260101-000000-never-captured", mode="apply"
        )
    )

    assert_error_shape(err, "NOTE_NOT_FOUND")


# ---------------------------------------------------------------------------
# promote and archive share the same `mode` validation as reanchor/detach
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["promote", "archive"])
def test_notes_promote_or_archive_bad_mode_is_rejected_with_invalid_argument(
    action: str, vault_config: Path, template_vault: Path
) -> None:
    note_id = f"20260101-101700-{action}-bad-mode"
    anchor = _forged_anchor(page=f"{TOPIC}/{action}-bad-mode.md", quote="anything")
    _write_forged_note(
        template_vault, note_id, _forged_note(note_id, intent="question", anchors=(anchor,))
    )
    server = build_full_server()
    before_commits = git_commit_count(template_vault)
    kwargs: dict[str, Any] = {"topic": TOPIC, "note_id": note_id, "mode": "sideways"}
    if action == "promote":
        kwargs.update(target="trainset", question="Whatever", answer="Whatever")

    err = error_of(notes_call(server, action, **kwargs))

    assert_error_shape(err, "INVALID_ARGUMENT")
    assert err["fix"] == "Pass mode as one of: dry-run, apply.", (
        f"expected `_validate_mode`'s own fix, not the action-enum check's fix -- got "
        f"{err['fix']!r}"
    )
    assert git_commit_count(template_vault) == before_commits

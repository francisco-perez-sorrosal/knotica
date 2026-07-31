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

from knotica.core.notes.anchor import AnchorRecord, NoteDocument, serialize_note
from knotica.core.notes.resolve import Projection
from knotica.core.notes.store import ResolvedNote
from knotica.core.status import gather_wiki_status
from knotica.mcp_server.tools_dispatch_notes import (
    _ANCHOR_STATUSES,
    _LEAST_SEVERE_ANCHOR_STATUS,
    _MOST_SEVERE_ANCHOR_STATUS,
    _drift_status,
    _status_counts,
)
from knotica.mcp_server.tools_dispatch_notes_actions import _NOTES_SORT
from knotica.search.cursor import decode_cursor
from knotica.store import LocalFSStore
from support.dispatch import TOPIC, build_full_server, call_tool, payload_of, tool_schema
from support.vault import git_commit_count, git_head_sha, run_git

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

#: The remaining four actions the full design names that this phase must not
#: yet accept. `drift` was wired (see the `notes action=drift` section below)
#: and dropped from this tuple -- it now succeeds instead of rejecting.
PHASE_TWO_ACTIONS = ("reanchor", "detach", "promote", "archive")


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
    assert drift["overlap"] == 0
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

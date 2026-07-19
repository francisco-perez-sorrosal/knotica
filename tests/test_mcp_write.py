"""Behavioral tests for the MCP write band over an in-memory client session.

These drive the FastMCP server through the official SDK's in-memory transport
(``mcp.shared.memory.create_connected_server_and_client_session``) so the
assertions are made over the *wire* contract a real MCP client sees -- not the
internal ``core.operations`` functions the tools delegate to. What is pinned
here is the observable protocol-band write contract from INTERFACE_DESIGN:

- happy paths: ``create_topic`` scaffolds a new topic (``existed=false`` + one
  commit); ``write_page`` writes a page file (``changed=true`` + one commit);
  ``store_source`` persists under ``sources/<topic>/<key>``; ``curate_example``
  appends to ``qa.jsonl`` (``appended=true`` + an ``example_count``);
- the load-bearing seam: a tool call produces the IDENTICAL git commit + log.md
  behavior as calling the delegated ``core.operations`` function directly on an
  identical twin vault -- exactly one commit per effective op, same commit
  subject grammar, same log entry;
- idempotency (§1.5): re-``create_topic`` -> ``existed=true`` and no new commit;
  identical re-``write_page`` -> ``changed=false`` and no commit; ``store_source``
  same key+content -> no-op success (no commit), same key + DIFFERENT content ->
  ``SOURCE_EXISTS``; duplicate ``curate_example`` -> ``appended=false``;
- negative paths ride in the result content as ``{"error": {code, message, fix,
  retryable}}`` with ``isError=True`` (§1.4): ``write_page`` to a missing topic
  -> ``TOPIC_NOT_FOUND``; targeting a reserved bookkeeping file -> ``RESERVED_NAME``;
  bad frontmatter -> ``INVALID_FRONTMATTER``; a reserved ``create_topic`` name
  -> ``RESERVED_NAME``;
- ``SECRET_SCRUBBED`` is a **warning on a success result**, not an error: a write
  whose content carries a token-shaped secret succeeds and carries a ``warnings``
  entry (the write still lands).
- an additive ``candidate`` handle threads through both ``store_source`` and
  ``write_page``: a whole client-driven ingest chain (source, then a page)
  sharing one open candidate lands both commits on that candidate's worktree
  branch, never touching the canonical default branch.

Async coroutines are driven from sync test bodies via ``anyio.run`` (mcp depends
on anyio; there is no pytest async plugin configured). Production imports of the
server are deferred into helpers so collection succeeds while the paired impl
step is still in flight (RED handshake: ImportError until the adapter lands).
"""

import json
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import pytest

from knotica.core.vcs import VaultVcs
from support.vault import (
    git_commit_count,
    git_status_porcelain,
    parse_knotica_commit,
    parse_log_entries,
    run_git,
)

# The §1.4 error-code enum, mirrored as wire strings. A failure result's
# error.code must be one of exactly these.
ERROR_CODES = frozenset(
    {
        "NOT_CONFIGURED",
        "TOPIC_NOT_FOUND",
        "PAGE_NOT_FOUND",
        "RESERVED_NAME",
        "SOURCE_EXISTS",
        "INVALID_FRONTMATTER",
        "SECRET_SCRUBBED",
        "LOCK_BUSY",
        "GIT_ERROR",
        "INVALID_CURSOR",
    }
)

# agentic-systems exists in the template; a fresh page name that does not.
TOPIC = "agentic-systems"
NEW_PAGE = "planning-loops"

VALID_PAGE = """\
---
type: concept
topic: agentic-systems
created: 2026-07-03
updated: 2026-07-03
confidence: high
sources: [yao2022react]
status: active
tags: [reasoning, planning]
---

# Planning Loops

Deliberate interleaving of planning and acting.
"""

# Missing every required core field except type/topic -> fails frontmatter schema.
INVALID_PAGE = """\
---
type: concept
topic: agentic-systems
---

# Broken page
"""

# A GitHub-token-shaped secret the conservative scrub must redact loudly.
_REAL_KEY = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
SECRET_PAGE = VALID_PAGE.replace(
    "Deliberate interleaving of planning and acting.",
    f"Someone pasted a token {_REAL_KEY} into the notes.",
)


# ---------------------------------------------------------------------------
# Harness -- deferred imports keep collection green pre-impl.
# ---------------------------------------------------------------------------


def _build_server() -> Any:
    """Construct a fresh server instance (factory preferred, singleton fallback)."""
    from knotica.mcp_server import server as server_mod

    if hasattr(server_mod, "build_server"):
        return server_mod.build_server()
    return server_mod.mcp


async def _call(server: Any, tool: str, args: dict[str, Any]) -> Any:
    """Open an in-memory session against ``server`` and call one tool."""
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool(tool, args)


def call_tool(tool: str, args: dict[str, Any]) -> Any:
    """Sync entry point: build a server and call one tool."""
    return anyio.run(_call, _build_server(), tool, args)


def run(coro_factory: Callable[[], Awaitable[Any]]) -> Any:
    """Drive an arbitrary coroutine factory from a sync test body."""
    return anyio.run(coro_factory)


# ---------------------------------------------------------------------------
# Result-envelope extraction (mirrors the read-band harness for independence).
# ---------------------------------------------------------------------------


def payload_of(result: Any) -> Any:
    """Return the tool's result payload as a Python object (dict/list)."""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError(f"result carried no structured or text payload: {result!r}")


def error_of(result: Any) -> dict[str, Any]:
    """Assert the result is a failure envelope and return its error object."""
    body = payload_of(result)
    assert isinstance(body, dict), f"error envelope must be an object, got {body!r}"
    assert "error" in body, f"expected a failure envelope, got success: {body!r}"
    assert getattr(result, "isError", False) is True, "an error payload must set isError=True"
    return body["error"]


def assert_success(result: Any) -> dict[str, Any]:
    """Assert the result is a success envelope (no error key) and return it."""
    body = payload_of(result)
    assert isinstance(body, dict), f"success envelope must be an object: {body!r}"
    assert "error" not in body, f"expected success, got error envelope: {body!r}"
    assert getattr(result, "isError", False) is False, "a success payload must not set isError"
    return body


def assert_error_shape(err: dict[str, Any], code: str) -> None:
    """Assert the §1.4 error object shape and its expected non-retryable code."""
    assert set(err) >= {"code", "message", "fix", "retryable"}, (
        f"error object missing contract fields: {err!r}"
    )
    assert err["code"] in ERROR_CODES, f"code not in the §1.4 enum: {err['code']!r}"
    assert err["code"] == code, f"expected {code}, got {err['code']!r}"
    assert isinstance(err["retryable"], bool), "retryable must be a bool"
    # None of these write-band failures are lock contention: all non-retryable.
    assert err["retryable"] is False, f"expected a non-retryable failure: {err!r}"
    assert isinstance(err["message"], str) and err["message"]
    assert isinstance(err["fix"], str) and err["fix"]


# ---------------------------------------------------------------------------
# Happy paths -- each mutating tool lands its effect and returns its pointer.
# ---------------------------------------------------------------------------


def test_create_topic_scaffolds_a_new_topic_with_one_commit(
    vault_config: Path, template_vault: Path
) -> None:
    """create_topic on a new name reports existed=false and makes one commit."""
    before = git_commit_count(template_vault)
    body = assert_success(call_tool("create_topic", {"topic": "robotics"}))

    assert body["existed"] is False, f"a fresh topic must report existed=false: {body!r}"
    assert (template_vault / "robotics").is_dir(), "the topic directory must exist on disk"
    assert git_commit_count(template_vault) == before + 1, "exactly one commit for a new topic"


def test_write_page_writes_the_page_file_and_reports_changed(
    vault_config: Path, template_vault: Path
) -> None:
    """write_page into an existing topic lands the file, changed=true, one commit."""
    before = git_commit_count(template_vault)
    body = assert_success(
        call_tool(
            "write_page",
            {
                "topic": TOPIC,
                "page": NEW_PAGE,
                "content": VALID_PAGE,
                "summary": "add planning loops",
            },
        )
    )

    assert body["changed"] is True, f"a new page must report changed=true: {body!r}"
    assert (template_vault / TOPIC / f"{NEW_PAGE}.md").is_file(), "the page file must exist"
    assert git_commit_count(template_vault) == before + 1, "exactly one commit for the write"


def test_store_source_persists_under_sources_topic_key(
    vault_config: Path, template_vault: Path
) -> None:
    """store_source persists the raw source under sources/<topic>/<key>.md."""
    before = git_commit_count(template_vault)
    assert_success(
        call_tool(
            "store_source",
            {
                "topic": TOPIC,
                "citation_key": "smith2025demo",
                "title": "A Demo Source, arXiv 2501.00000",
                "content": "# Demo source\n\nFetched and converted markdown.",
                "source_url": "https://arxiv.org/abs/2501.00000",
            },
        )
    )

    assert (template_vault / "sources" / TOPIC / "smith2025demo.md").is_file()
    assert git_commit_count(template_vault) == before + 1, "exactly one commit for the source"


def test_curate_example_appends_to_dataset_with_a_count(
    vault_config: Path, template_vault: Path
) -> None:
    """curate_example appends to qa.jsonl, reporting appended=true and a count."""
    before = git_commit_count(template_vault)
    body = assert_success(
        call_tool(
            "curate_example",
            {
                "topic": TOPIC,
                "query": "What is agent workflow memory?",
                "answer": "A mechanism that induces reusable workflows from past trajectories.",
                "verdict": "good",
            },
        )
    )

    assert body["appended"] is True, f"a fresh example must report appended=true: {body!r}"
    assert isinstance(body["example_count"], int) and body["example_count"] >= 1
    assert git_commit_count(template_vault) == before + 1, "exactly one commit for the example"


# ---------------------------------------------------------------------------
# The load-bearing seam -- a tool call is behaviorally identical to the direct
# core.operations call: same commit subject grammar, same log entry, one commit.
# ---------------------------------------------------------------------------


def _twin_of(vault: Path, tmp_path: Path) -> Path:
    """A byte-identical, independently-git copy of ``vault`` for the direct call."""
    twin = tmp_path / "twin-vault"
    shutil.copytree(vault, twin)
    return twin


def _last_subject(vault: Path) -> str:
    return run_git(vault, "log", "-1", "--format=%s").strip()


def test_create_topic_tool_matches_direct_core_commit_and_log(
    vault_config: Path, template_vault: Path, tmp_path: Path
) -> None:
    """The create_topic tool commits and logs identically to the direct core call.

    The tool is a thin adapter over ``core.operations.create_topic``: the same
    op run against a twin vault must yield the same commit subject (frozen
    grammar), the same log.md entry, and exactly one commit either way.
    """
    twin = _twin_of(template_vault, tmp_path)
    tool_before = git_commit_count(template_vault)
    twin_before = git_commit_count(twin)

    assert_success(call_tool("create_topic", {"topic": "robotics"}))

    from knotica.core.operations.create_topic import create_topic
    from knotica.store.local import LocalFSStore

    create_topic(store=LocalFSStore(twin), vault_root=twin, topic="robotics")

    assert git_commit_count(template_vault) == tool_before + 1, "tool path: one commit"
    assert git_commit_count(twin) == twin_before + 1, "direct path: one commit"

    tool_subject = _last_subject(template_vault)
    twin_subject = _last_subject(twin)
    assert tool_subject == twin_subject, "commit subjects must match the direct call verbatim"
    assert parse_knotica_commit(tool_subject) is not None, "subject must match the frozen grammar"

    tool_log = parse_log_entries((template_vault / "log.md").read_text(encoding="utf-8"))
    twin_log = parse_log_entries((twin / "log.md").read_text(encoding="utf-8"))
    assert tool_log[-1].op == twin_log[-1].op == "create_topic"
    assert tool_log[-1].topic == twin_log[-1].topic
    assert tool_log[-1].title == twin_log[-1].title


def test_write_page_tool_matches_direct_core_commit_and_log(
    vault_config: Path, template_vault: Path, tmp_path: Path
) -> None:
    """The write_page tool commits and logs identically to the direct core call."""
    twin = _twin_of(template_vault, tmp_path)
    tool_before = git_commit_count(template_vault)
    twin_before = git_commit_count(twin)

    args = {
        "topic": TOPIC,
        "page": NEW_PAGE,
        "content": VALID_PAGE,
        "summary": "add planning loops",
    }
    assert_success(call_tool("write_page", args))

    from knotica.core.operations.write_page import write_page
    from knotica.store.local import LocalFSStore

    write_page(store=LocalFSStore(twin), vault_root=twin, **args)

    assert git_commit_count(template_vault) == tool_before + 1, "tool path: one commit"
    assert git_commit_count(twin) == twin_before + 1, "direct path: one commit"
    assert _last_subject(template_vault) == _last_subject(twin), "commit subjects must match"

    tool_log = parse_log_entries((template_vault / "log.md").read_text(encoding="utf-8"))
    twin_log = parse_log_entries((twin / "log.md").read_text(encoding="utf-8"))
    assert tool_log[-1].op == twin_log[-1].op == "write_page"
    assert tool_log[-1].title == twin_log[-1].title


# ---------------------------------------------------------------------------
# Idempotency by result-state (§1.5) -- a no-op mutating call makes no commit.
# ---------------------------------------------------------------------------


def test_recreating_an_existing_topic_is_a_no_op(vault_config: Path, template_vault: Path) -> None:
    """A second create_topic on the same name reports existed=true, no new commit."""
    assert_success(call_tool("create_topic", {"topic": "robotics"}))
    after_first = git_commit_count(template_vault)

    body = assert_success(call_tool("create_topic", {"topic": "robotics"}))
    assert body["existed"] is True, f"re-create must report existed=true: {body!r}"
    assert git_commit_count(template_vault) == after_first, "a re-create must make no new commit"


def test_rewriting_identical_page_content_is_a_no_op(
    vault_config: Path, template_vault: Path
) -> None:
    """An identical re-write reports changed=false and makes no new commit."""
    args = {
        "topic": TOPIC,
        "page": NEW_PAGE,
        "content": VALID_PAGE,
        "summary": "add planning loops",
    }
    assert_success(call_tool("write_page", args))
    after_first = git_commit_count(template_vault)

    body = assert_success(call_tool("write_page", args))
    assert body["changed"] is False, f"an identical re-write must report changed=false: {body!r}"
    assert git_commit_count(template_vault) == after_first, "an identical re-write makes no commit"


def test_storing_same_source_key_and_content_is_a_no_op(
    vault_config: Path, template_vault: Path
) -> None:
    """Re-storing the same citation_key with identical content is a no-op success."""
    args = {
        "topic": TOPIC,
        "citation_key": "smith2025demo",
        "title": "A Demo Source, arXiv 2501.00000",
        "content": "# Demo source\n\nFetched and converted markdown.",
        "source_url": "https://arxiv.org/abs/2501.00000",
    }
    assert_success(call_tool("store_source", args))
    after_first = git_commit_count(template_vault)

    assert_success(call_tool("store_source", args))
    assert git_commit_count(template_vault) == after_first, "an identical re-store makes no commit"


def test_storing_same_key_different_content_returns_source_exists(
    vault_config: Path, template_vault: Path
) -> None:
    """The same citation_key with different content fails (immutable) as SOURCE_EXISTS."""
    base = {
        "topic": TOPIC,
        "citation_key": "smith2025demo",
        "title": "A Demo Source, arXiv 2501.00000",
        "source_url": "https://arxiv.org/abs/2501.00000",
    }
    assert_success(
        call_tool("store_source", {**base, "content": "# Demo source\n\nOriginal body."})
    )
    after_first = git_commit_count(template_vault)

    result = call_tool("store_source", {**base, "content": "# Demo source\n\nDIFFERENT body."})
    assert_error_shape(error_of(result), code="SOURCE_EXISTS")
    assert git_commit_count(template_vault) == after_first, "a rejected re-store makes no commit"


def test_duplicate_curated_example_is_not_appended(
    vault_config: Path, template_vault: Path
) -> None:
    """Re-submitting an identical example reports appended=false and makes no commit."""
    args = {
        "topic": TOPIC,
        "query": "What is agent workflow memory?",
        "answer": "A mechanism that induces reusable workflows.",
        "verdict": "good",
    }
    first = assert_success(call_tool("curate_example", args))
    after_first = git_commit_count(template_vault)

    body = assert_success(call_tool("curate_example", args))
    assert body["appended"] is False, f"a duplicate example must report appended=false: {body!r}"
    assert body["example_count"] == first["example_count"], "the count must not advance on a dup"
    assert git_commit_count(template_vault) == after_first, "a duplicate makes no commit"


# ---------------------------------------------------------------------------
# Negative paths -- structured error envelopes in the result content (§1.4).
# ---------------------------------------------------------------------------


def test_write_page_to_missing_topic_returns_topic_not_found(
    vault_config: Path, template_vault: Path
) -> None:
    """write_page into a topic with no directory returns TOPIC_NOT_FOUND, no commit."""
    before = git_commit_count(template_vault)
    result = call_tool(
        "write_page",
        {"topic": "no-such-topic", "page": NEW_PAGE, "content": VALID_PAGE, "summary": "x"},
    )
    assert_error_shape(error_of(result), code="TOPIC_NOT_FOUND")
    assert git_commit_count(template_vault) == before, "a rejected write makes no commit"


@pytest.mark.parametrize("reserved", ["index.md", "log.md", "SCHEMA.md"])
def test_write_page_targeting_a_reserved_file_returns_reserved_name(
    reserved: str, vault_config: Path, template_vault: Path
) -> None:
    """Targeting a reserved bookkeeping file as the page returns RESERVED_NAME."""
    before = git_commit_count(template_vault)
    result = call_tool(
        "write_page",
        {"topic": TOPIC, "page": reserved, "content": VALID_PAGE, "summary": "x"},
    )
    assert_error_shape(error_of(result), code="RESERVED_NAME")
    assert git_commit_count(template_vault) == before, "a rejected write makes no commit"


def test_write_page_invalid_frontmatter_returns_invalid_frontmatter(
    vault_config: Path, template_vault: Path
) -> None:
    """Content that fails the frontmatter schema returns INVALID_FRONTMATTER, no commit."""
    before = git_commit_count(template_vault)
    result = call_tool(
        "write_page",
        {"topic": TOPIC, "page": NEW_PAGE, "content": INVALID_PAGE, "summary": "x"},
    )
    assert_error_shape(error_of(result), code="INVALID_FRONTMATTER")
    assert git_commit_count(template_vault) == before, "a rejected write makes no commit"


def test_create_topic_with_reserved_name_returns_reserved_name(
    vault_config: Path, template_vault: Path
) -> None:
    """A create_topic whose name collides with a reserved top-level name is refused."""
    before = git_commit_count(template_vault)
    result = call_tool("create_topic", {"topic": "sources"})
    assert_error_shape(error_of(result), code="RESERVED_NAME")
    assert git_commit_count(template_vault) == before, "a rejected create makes no commit"


# ---------------------------------------------------------------------------
# SECRET_SCRUBBED rides as a warning on a *successful* write, never an error.
# ---------------------------------------------------------------------------


def test_write_page_with_a_secret_succeeds_and_warns(
    vault_config: Path, template_vault: Path
) -> None:
    """A write whose content carries a token-shaped secret succeeds with a warning.

    The redaction is loud: the write lands (changed=true, one commit) and the
    success result carries a ``warnings`` entry flagging SECRET_SCRUBBED -- it is
    NOT an error envelope.
    """
    before = git_commit_count(template_vault)
    body = assert_success(
        call_tool(
            "write_page",
            {"topic": TOPIC, "page": NEW_PAGE, "content": SECRET_PAGE, "summary": "add page"},
        )
    )

    assert git_commit_count(template_vault) == before + 1, "the scrubbed write still commits"
    assert "SECRET_SCRUBBED" in json.dumps(body), (
        f"the secret redaction must surface as a warning on the success result: {body!r}"
    )


# ---------------------------------------------------------------------------
# Candidate-scoped ingest chain -- store_source then write_page sharing one
# open candidate handle both land on that candidate's worktree branch.
# ---------------------------------------------------------------------------


def _approved_suggestion(vault: Path, store) -> str:
    """Build one gap-fill suggestion and drive it to ``approved`` through the
    real decision state machine (never hand-forged), returning its id."""
    from knotica.core import gapfill
    from knotica.core.records import GapEvidence, GapRecord
    from knotica.core.transaction import VaultTransaction
    from knotica.discovery.records import SourceCandidate

    evidence = GapEvidence(
        quality_delta=-0.5,
        qa_accuracy_delta=-0.5,
        citation_validity_delta=0.0,
        retrieval_trace=(),
        pages_added=(),
        pages_removed=(),
        prior_generation=4,
    )
    gap = GapRecord(
        gap_id="gap-candidate-mcp-chain",
        topic=TOPIC,
        qa_id="golden-candidate-mcp-chain",
        fault_class="genuine_gap",
        status="open",
        classifier_version=1,
        detected_generation=5,
        detected_at="2026-07-18T23:01:00Z",
        scalar_at_detection=0.9493,
        baseline_scalar=0.96,
        question="What closes this gap?",
        reference_pages=("agent-workflow-memory",),
        reference_pages_exist=False,
        evidence=evidence,
        manifest_ref="agentic-systems/.knotica/eval-runs/gen-5/manifest.json",
    )
    source_candidate = SourceCandidate(
        url="https://arxiv.org/abs/2409.07429",
        title="Agent Workflow Memory",
        snippet="We propose inducing reusable workflows from past experience...",
        source_provider="fake",
        doi="10.48550/arXiv.2409.07429",
        citation_count=12,
    )
    records = gapfill.build_suggestion_records(
        gap, [source_candidate], proposer_version=1, clock=lambda: "2026-07-19T00:00:00Z"
    )
    path = gapfill.suggestions_path(TOPIC)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed suggestions for test") as txn:
        txn.write(path, body)
    suggestion_id = records[0].suggestion_id
    gapfill.apply_decision(store, vault, TOPIC, suggestion_id, decision="approve")
    return suggestion_id


def _open_candidate(vault: Path) -> str:
    """Open a real ingest session (a private worktree + WIP branch) and return
    its opaque candidate handle, exactly as a client receives it from
    ``source_ingest_open``."""
    from knotica.core.source_ingest import open_ingest
    from knotica.store.local import LocalFSStore

    store = LocalFSStore(vault)
    suggestion_id = _approved_suggestion(vault, store)
    handle = open_ingest(store, vault, TOPIC, suggestion_id)
    return handle.candidate


def test_candidate_scoped_store_source_then_write_page_share_the_worktree_branch(
    vault_config: Path, template_vault: Path
) -> None:
    """A store_source and write_page call sharing one open candidate handle
    both commit onto that candidate's worktree branch -- the canonical vault's
    default branch and working tree never move across either call."""
    candidate = _open_candidate(template_vault)
    vcs = VaultVcs(template_vault)
    canonical_head_before = vcs.head_sha()
    wip_tip_after_open = vcs.ref_sha(candidate)

    assert_success(
        call_tool(
            "store_source",
            {
                "topic": TOPIC,
                "citation_key": "candidate-mcp-source",
                "title": "Candidate MCP source",
                "content": "# Candidate source\n\nFetched via the MCP write band.",
                "source_url": "https://arxiv.org/abs/2409.07429",
                "candidate": candidate,
            },
        )
    )
    tip_after_source = vcs.ref_sha(candidate)
    assert tip_after_source != wip_tip_after_open, (
        "store_source with an open candidate must commit onto the worktree branch"
    )

    assert_success(
        call_tool(
            "write_page",
            {
                "topic": TOPIC,
                "page": "candidate-mcp-page",
                "content": VALID_PAGE,
                "summary": "add candidate-scoped page",
                "candidate": candidate,
            },
        )
    )
    tip_after_page = vcs.ref_sha(candidate)
    assert tip_after_page != tip_after_source, (
        "write_page with the same open candidate must land as a second commit on the same branch"
    )

    assert vcs.head_sha() == canonical_head_before, "the canonical default branch must never move"
    assert git_status_porcelain(template_vault) == "", "the canonical working tree must stay clean"
    assert not (template_vault / "sources" / TOPIC / "candidate-mcp-source.md").exists(), (
        "the candidate-scoped source must never appear in the canonical working tree"
    )
    assert not (template_vault / TOPIC / "candidate-mcp-page.md").exists(), (
        "the candidate-scoped page must never appear in the canonical working tree"
    )

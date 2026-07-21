"""Behavioral contract tests for the MCP source-ingest session tools.

Derived from ``INTERFACE_DESIGN.md``'s ingest-scoping section -- never from the
implementation. Two tools front an approved gap-fill suggestion's isolated
ingest session: ``source_ingest_open`` (open/resume the candidate context) and
``source_ingest_submit`` (dry-run readiness check | apply -> hand the
candidate to the gate and return its verdict). Drives the FastMCP server
through the official in-memory transport so assertions pin the *wire*
contract, matching ``test_mcp_suggestions.py``.

RED-first: ``knotica.mcp_server.tools_source_ingest`` does not exist yet when
this file is written (the paired implementer step lands the module
concurrently) -- every production symbol tied to that module is resolved
lazily inside a helper so collection succeeds and the first run fails with an
import/registration error, not a collection error. This file was written
without reading the implementer's code.

The already-shipped ``source_ingest`` session module gives every ingest a
worktree checked out on a deterministic WIP branch name
(``source_ingest.wip_branch_name``), computed purely from ``(topic,
suggestion_id)``. Content is seeded directly onto that worktree via a raw git
commit -- the same technique the sibling gate-contract test suite uses -- so
these tests never depend on the (separately staged) candidate-scoped
``store_source``/``write_page`` write path. Locating the worktree this way
also sidesteps a genuine open question about what string the wire-level
``candidate`` handle itself contains (the interface sketch and the shipped
core module disagree on whether it is the private WIP name or the eventual
public candidate name) -- this file deliberately does not pin that string's
shape, only that it round-trips unchanged across a re-open.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import anyio
import pytest

from knotica.core import source_ingest
from knotica.core.errors import ErrorCode
from knotica.core.loop import wrap_harness_result
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.transaction import VaultTransaction
from knotica.core.vcs import VaultVcs
from knotica.evals.harness import EvalRunResult
from support.vault import run_git

TOPIC = "agentic-systems"

ERROR_CODES = frozenset(code.value for code in ErrorCode)


# ---------------------------------------------------------------------------
# MCP call harness (mirrors test_mcp_suggestions.py -- each tool test file
# duplicates this small seam per the project's established convention)
# ---------------------------------------------------------------------------


def _build_server() -> Any:
    from knotica.mcp_server import server as server_mod

    if hasattr(server_mod, "build_server"):
        return server_mod.build_server()
    return server_mod.mcp


async def _call(server: Any, tool: str, args: dict[str, Any]) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool(tool, args)


def call_tool(tool: str, args: dict[str, Any], *, server: Any | None = None) -> Any:
    srv = server if server is not None else _build_server()
    return anyio.run(_call, srv, tool, args)


def payload_of(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError(f"result carried no structured or text payload: {result!r}")


def error_of(result: Any) -> dict[str, Any]:
    body = payload_of(result)
    assert isinstance(body, dict) and "error" in body
    assert getattr(result, "isError", False) is True
    return body["error"]


def assert_success(result: Any) -> Any:
    body = payload_of(result)
    if isinstance(body, dict):
        assert "error" not in body, f"expected success, got error: {body!r}"
    assert getattr(result, "isError", False) is False
    return body


def assert_error_shape(err: dict[str, Any], code: str | None = None) -> None:
    assert set(err) >= {"code", "message", "fix", "retryable"}
    assert err["code"] in ERROR_CODES
    assert isinstance(err["retryable"], bool)
    if code is not None:
        assert err["code"] == code


# ---------------------------------------------------------------------------
# Suggestion fixture builders -- a real approved (or deliberately pending)
# suggestion, driven through the real gapfill decision state machine, never
# hand-forged into a status the machine itself would refuse to reach.
# ---------------------------------------------------------------------------


def _gap_record(*, gap_id: str, qa_id: str):
    from knotica.core.records import GapEvidence, GapRecord

    evidence = GapEvidence(
        quality_delta=-0.5,
        qa_accuracy_delta=-0.5,
        citation_validity_delta=0.0,
        retrieval_trace=(),
        pages_added=(),
        pages_removed=(),
        prior_generation=4,
    )
    return GapRecord(
        gap_id=gap_id,
        topic=TOPIC,
        qa_id=qa_id,
        fault_class="genuine_gap",
        status="open",
        classifier_version=1,
        detected_generation=5,
        detected_at="2026-07-18T23:01:00Z",
        scalar_at_detection=0.9493,
        baseline_scalar=0.96,
        question=f"What is the retrieval augmentation story for {qa_id}?",
        reference_pages=("agent-workflow-memory",),
        reference_pages_exist=False,
        evidence=evidence,
        manifest_ref="agentic-systems/.knotica/eval-runs/gen-5/manifest.json",
    )


def _candidate_source():
    from knotica.discovery.records import SourceCandidate

    return SourceCandidate(
        url="https://arxiv.org/abs/2409.07429",
        title="Agent Workflow Memory",
        snippet="We propose inducing reusable workflows from past experience...",
        source_provider="fake",
        doi="10.48550/arXiv.2409.07429",
        citation_count=12,
    )


def _seed_suggestions(vault: Path, records) -> None:
    from knotica.core.gapfill import suggestions_path
    from knotica.store import LocalFSStore

    store = LocalFSStore(vault)
    path = suggestions_path(TOPIC)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed suggestions for test") as txn:
        txn.write(path, body)


def _pending_suggestion(vault: Path, *, qa_id: str, gap_id: str) -> str:
    from knotica.core import gapfill

    gap = _gap_record(gap_id=gap_id, qa_id=qa_id)
    records = gapfill.build_suggestion_records(
        gap, [_candidate_source()], proposer_version=1, clock=lambda: "2026-07-19T00:00:00Z"
    )
    _seed_suggestions(vault, records)
    return records[0].suggestion_id


def _approved_suggestion(vault: Path, *, qa_id: str, gap_id: str) -> str:
    from knotica.core import gapfill
    from knotica.store import LocalFSStore

    suggestion_id = _pending_suggestion(vault, qa_id=qa_id, gap_id=gap_id)
    gapfill.apply_decision(LocalFSStore(vault), vault, TOPIC, suggestion_id, decision="approve")
    return suggestion_id


# ---------------------------------------------------------------------------
# Worktree content seeding -- write directly onto the ingest's own WIP branch
# rather than through the (not-yet-built) candidate-scoped write tools.
# ---------------------------------------------------------------------------


def _worktree_path_for_suggestion(vault_root: Path, suggestion_id: str) -> Path:
    branch = source_ingest.wip_branch_name(TOPIC, suggestion_id)
    entry = next(e for e in VaultVcs(vault_root).list_worktrees() if e.get("branch") == branch)
    return Path(entry["path"])


def _commit_source_and_page(vault_root: Path, suggestion_id: str) -> None:
    """Seed one source file and one page directly onto the ingest's WIP
    worktree, committed there -- proves the candidate is lint-clean-adjacent
    and content-bearing for the readiness checks without touching the
    candidate-scoped store_source/write_page path (out of this step's scope)."""
    worktree = _worktree_path_for_suggestion(vault_root, suggestion_id)
    source_path = worktree / "sources" / TOPIC / "ingest-fixture.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("raw source body\n", encoding="utf-8")
    page_path = worktree / TOPIC / "ingested-page.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text("# Ingested Page\n\ningested body\n", encoding="utf-8")
    VaultVcs(worktree).commit_paths(
        [f"sources/{TOPIC}/ingest-fixture.md", f"{TOPIC}/ingested-page.md"],
        f"knotica(test_seed): {TOPIC} — ingest fixture content",
    )


# ---------------------------------------------------------------------------
# Eval fakes -- zero network, call-counted (mirrors test_source_gate.py's
# harness stub conventions over a real cloned worktree, plus a call counter
# so the idempotent-resubmit test can prove the seam did NOT fire again).
# ---------------------------------------------------------------------------


def _fake_evaluate(scalar: float):
    """A plain pass/fail stub -- no diagnostic manifest, for the merge scenario."""
    calls: list[float] = []

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        calls.append(scalar)
        dest = Path(tempfile.mkdtemp(prefix="knotica-source-ingest-mcp-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-19T00:00:00Z",
            generation=1,
            harness_version="fake-source-ingest-mcp",
            scalar=float(scalar),
            components=MetricsComponents(
                qa_accuracy=float(scalar),
                citation_validity=1.0,
                lint_violations=0.0,
                token_cost=0.0,
            ),
            n_examples=1,
            corpus_ref=f"git:{clone.head_sha()}",
            artifact_ref=None,
        )
        return wrap_harness_result(EvalRunResult(record=record, clone_root=clone.root))

    return _evaluate, calls


def _fake_evaluate_with_manifest(scalar: float, *, generation: int, n_regressed: int):
    """A regressing stub that also writes a v2 diagnostic manifest onto the
    clone, mirroring ``gap_classifier.py``'s ``held_out_delta``/``per_example``
    schema -- the substrate the refusal diff is read from."""
    calls: list[float] = []
    per_id = {
        f"golden-dilute-{index:02d}": {
            "quality_delta": -0.3,
            "qa_accuracy_delta": -0.3,
            "citation_validity_delta": 0.0,
            "pages_added": [],
            "pages_removed": [],
        }
        for index in range(n_regressed)
    }

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        calls.append(scalar)
        dest = Path(tempfile.mkdtemp(prefix="knotica-source-ingest-mcp-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        manifest = {
            "manifest_schema_version": 2,
            "generation": generation,
            "per_example": [{"id": qa_id, "pages": []} for qa_id in per_id],
            "held_out_delta": {
                "ids_added": [],
                "ids_removed": [],
                "prior_generation": generation - 1,
                "scalar_delta": scalar - 0.8,
                "per_id": per_id,
            },
        }
        manifest_dir = clone.root / topic / ".knotica" / "eval-runs" / f"gen-{generation}"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        run_git(clone.root, "add", "-A")
        run_git(clone.root, "commit", "-m", f"eval: write gen-{generation} manifest")
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-19T00:00:00Z",
            generation=generation,
            harness_version="fake-source-ingest-mcp",
            scalar=float(scalar),
            components=MetricsComponents(
                qa_accuracy=float(scalar),
                citation_validity=1.0,
                lint_violations=0.0,
                token_cost=0.0,
            ),
            n_examples=1,
            corpus_ref=f"git:{clone.head_sha()}",
            artifact_ref=None,
        )
        return wrap_harness_result(EvalRunResult(record=record, clone_root=clone.root))

    return _evaluate, calls


def _patch_harness_evaluate(monkeypatch: pytest.MonkeyPatch, fn) -> None:
    """Stub the production eval callable everywhere the apply path might bind
    it, so no network/LLM call ever happens under test. Patches both the
    defining module (covers an attribute-access call style) and the tool
    module's own imported name, if present (covers a ``from ... import``
    call style) -- the concurrent implementer's exact binding choice is not
    yet known."""
    import importlib

    monkeypatch.setattr("knotica.core.loop.harness_evaluate", fn)
    tools_mod = importlib.import_module("knotica.mcp_server.tools_source_ingest")
    if hasattr(tools_mod, "harness_evaluate"):
        monkeypatch.setattr(tools_mod, "harness_evaluate", fn)


# ---------------------------------------------------------------------------
# source_ingest_open
# ---------------------------------------------------------------------------


def test_source_ingest_open_on_an_approved_suggestion_returns_the_candidate_envelope(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    suggestion_id = _approved_suggestion(template_vault, qa_id="golden-open", gap_id="gap-open")

    body = assert_success(
        call_tool("source_ingest_open", {"topic": TOPIC, "suggestion_id": suggestion_id})
    )

    assert body["topic"] == TOPIC
    assert body["suggestion_id"] == suggestion_id
    assert body["state"] == "created"
    assert isinstance(body["candidate"], str) and body["candidate"]
    resume = body["resume"]
    assert resume["source_present"] is False
    assert resume["pages_present"] == []
    provenance = body["provenance"]
    assert provenance["suggestion_id"] == suggestion_id
    assert provenance["gap_id"] == "gap-open"
    assert provenance["qa_id"] == "golden-open"


def test_source_ingest_open_on_a_non_approved_suggestion_is_refused_with_actionable_guidance(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    suggestion_id = _pending_suggestion(
        template_vault, qa_id="golden-pending", gap_id="gap-pending"
    )

    err = error_of(
        call_tool("source_ingest_open", {"topic": TOPIC, "suggestion_id": suggestion_id})
    )

    assert_error_shape(err, code="SUGGESTION_NOT_APPROVED")
    assert err["fix"], "the refusal must carry an actionable next step, not a bare code"


def test_reopening_an_already_open_ingest_is_idempotent_and_resumes(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    suggestion_id = _approved_suggestion(template_vault, qa_id="golden-resume", gap_id="gap-resume")
    first = assert_success(
        call_tool("source_ingest_open", {"topic": TOPIC, "suggestion_id": suggestion_id})
    )

    second = assert_success(
        call_tool("source_ingest_open", {"topic": TOPIC, "suggestion_id": suggestion_id})
    )

    assert second["candidate"] == first["candidate"], (
        "re-opening the same suggestion must resolve the same handle, never mint a second one"
    )
    assert second["state"] == "resumed"
    wip_branch = source_ingest.wip_branch_name(TOPIC, suggestion_id)
    matching_worktrees = [
        e for e in VaultVcs(template_vault).list_worktrees() if e.get("branch") == wip_branch
    ]
    assert len(matching_worktrees) == 1, "opening twice must never create a second worktree"


# ---------------------------------------------------------------------------
# source_ingest_submit -- argument validation
# ---------------------------------------------------------------------------


def test_bad_mode_on_source_ingest_submit_is_invalid_argument_not_invalid_cursor(
    vault_config: Path, template_vault: Path
) -> None:
    """A bad mode is an argument-shape problem, not a pagination-cursor problem
    -- it must carry its own code even before any suggestion/candidate state is
    consulted (mode validation runs first)."""
    del vault_config

    err = error_of(
        call_tool(
            "source_ingest_submit",
            {"topic": TOPIC, "suggestion_id": "does-not-matter", "mode": "yolo"},
        )
    )

    assert_error_shape(err, code="INVALID_ARGUMENT")
    assert "mode" in err["fix"].lower()


# ---------------------------------------------------------------------------
# source_ingest_submit -- dry-run
# ---------------------------------------------------------------------------


def test_submit_dry_run_reports_readiness_without_mutating_the_vault(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    suggestion_id = _approved_suggestion(template_vault, qa_id="golden-dry", gap_id="gap-dry")
    call_tool("source_ingest_open", {"topic": TOPIC, "suggestion_id": suggestion_id})
    _commit_source_and_page(template_vault, suggestion_id)
    call_tool("loop_set_baseline", {"topic": TOPIC, "scalar": 0.80})
    wip_branch = source_ingest.wip_branch_name(TOPIC, suggestion_id)
    default_head_before = run_git(template_vault, "rev-parse", "HEAD").strip()
    wip_tip_before = run_git(template_vault, "rev-parse", wip_branch).strip()
    worktrees_before = VaultVcs(template_vault).list_worktrees()

    body = assert_success(
        call_tool(
            "source_ingest_submit",
            {"topic": TOPIC, "suggestion_id": suggestion_id, "mode": "dry-run"},
        )
    )

    assert body["mode"] == "dry-run"
    assert isinstance(body["lint_clean"], bool)
    assert isinstance(body["would_evaluate"], bool)
    assert body["source_present"] is True
    assert any("ingested-page" in page for page in body["pages_present"]), body["pages_present"]
    assert body["gate_eligible"] is True, "a frozen baseline must make the topic gate-eligible"

    default_head_after = run_git(template_vault, "rev-parse", "HEAD").strip()
    wip_tip_after = run_git(template_vault, "rev-parse", wip_branch).strip()
    assert default_head_after == default_head_before, "a dry-run must never move the default branch"
    assert wip_tip_after == wip_tip_before, "a dry-run must never commit onto the WIP branch itself"
    assert VaultVcs(template_vault).list_worktrees() == worktrees_before, (
        "a dry-run must never create, remove, or publish any worktree"
    )


# ---------------------------------------------------------------------------
# source_ingest_submit -- apply
# ---------------------------------------------------------------------------


def test_submit_apply_with_no_committed_content_fails_with_actionable_guidance(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    suggestion_id = _approved_suggestion(template_vault, qa_id="golden-empty", gap_id="gap-empty")
    call_tool("source_ingest_open", {"topic": TOPIC, "suggestion_id": suggestion_id})
    call_tool("loop_set_baseline", {"topic": TOPIC, "scalar": 0.80})

    err = error_of(
        call_tool(
            "source_ingest_submit",
            {"topic": TOPIC, "suggestion_id": suggestion_id, "mode": "apply"},
        )
    )

    assert_error_shape(err, code="INVALID_ARGUMENT")
    assert err["fix"], "the refusal must tell the caller to open and write content first"


def test_submit_apply_on_a_candidate_that_closes_the_gap_returns_a_merged_verdict(
    vault_config: Path, template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del vault_config
    suggestion_id = _approved_suggestion(template_vault, qa_id="golden-merge", gap_id="gap-merge")
    call_tool("source_ingest_open", {"topic": TOPIC, "suggestion_id": suggestion_id})
    _commit_source_and_page(template_vault, suggestion_id)
    call_tool("loop_set_baseline", {"topic": TOPIC, "scalar": 0.80})
    evaluate, calls = _fake_evaluate(0.95)
    _patch_harness_evaluate(monkeypatch, evaluate)

    body = assert_success(
        call_tool(
            "source_ingest_submit",
            {"topic": TOPIC, "suggestion_id": suggestion_id, "mode": "apply"},
        )
    )

    assert set(body) >= {
        "mode",
        "verdict",
        "topic",
        "suggestion_id",
        "merged_ref",
        "scalar",
        "baseline_scalar",
        "suggestion_status",
        "ingested_at",
        "committed",
    }
    assert body["mode"] == "apply"
    assert body["verdict"] == "merged"
    assert body["topic"] == TOPIC
    assert body["suggestion_id"] == suggestion_id
    assert isinstance(body["merged_ref"], str) and body["merged_ref"].startswith("loop/r/")
    assert body["scalar"] == pytest.approx(0.95)
    assert body["baseline_scalar"] == pytest.approx(0.80)
    assert body["suggestion_status"] == "ingested"
    assert body["ingested_at"] is not None
    assert body["committed"] is True
    assert calls == [0.95], "the eval seam must fire exactly once for this apply"


def test_submit_apply_on_a_candidate_that_regresses_returns_a_refused_verdict_with_a_bounded_diff(
    vault_config: Path, template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del vault_config
    suggestion_id = _approved_suggestion(template_vault, qa_id="golden-refuse", gap_id="gap-refuse")
    call_tool("source_ingest_open", {"topic": TOPIC, "suggestion_id": suggestion_id})
    _commit_source_and_page(template_vault, suggestion_id)
    call_tool("loop_set_baseline", {"topic": TOPIC, "scalar": 0.80})
    evaluate, calls = _fake_evaluate_with_manifest(0.40, generation=1, n_regressed=12)
    _patch_harness_evaluate(monkeypatch, evaluate)

    body = assert_success(
        call_tool(
            "source_ingest_submit",
            {"topic": TOPIC, "suggestion_id": suggestion_id, "mode": "apply"},
        )
    )

    assert set(body) >= {
        "mode",
        "verdict",
        "topic",
        "suggestion_id",
        "refused_ref",
        "scalar",
        "baseline_scalar",
        "diff_summary",
        "regressed_questions",
        "suggestion_status",
    }
    assert body["mode"] == "apply"
    assert body["verdict"] == "refused"
    assert body["refused_ref"] == f"loop/x/{TOPIC}/source-{suggestion_id[:8]}"
    assert body["scalar"] == pytest.approx(0.40)
    assert body["baseline_scalar"] == pytest.approx(0.80)
    assert isinstance(body["diff_summary"], str) and body["diff_summary"]
    regressed = body["regressed_questions"]
    assert isinstance(regressed, list)
    assert 0 < len(regressed) <= 10, (
        f"the refusal diff must be bounded to at most 10 questions, got {len(regressed)}"
    )
    assert body["suggestion_status"] == "approved", (
        "a refused source stays approved and re-workable -- a human decides what happens next"
    )
    assert calls == [0.40], "the eval seam must fire exactly once for this apply"


def test_resubmitting_an_already_gated_candidate_returns_the_prior_verdict_without_reevaluating(
    vault_config: Path, template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del vault_config
    suggestion_id = _approved_suggestion(template_vault, qa_id="golden-idem", gap_id="gap-idem")
    call_tool("source_ingest_open", {"topic": TOPIC, "suggestion_id": suggestion_id})
    _commit_source_and_page(template_vault, suggestion_id)
    call_tool("loop_set_baseline", {"topic": TOPIC, "scalar": 0.80})
    evaluate, calls = _fake_evaluate(0.95)
    _patch_harness_evaluate(monkeypatch, evaluate)
    first = assert_success(
        call_tool(
            "source_ingest_submit",
            {"topic": TOPIC, "suggestion_id": suggestion_id, "mode": "apply"},
        )
    )

    second = assert_success(
        call_tool(
            "source_ingest_submit",
            {"topic": TOPIC, "suggestion_id": suggestion_id, "mode": "apply"},
        )
    )

    assert second == first, (
        "re-submitting an already-gated candidate must return the exact prior verdict"
    )
    assert calls == [0.95], "a re-submit must never invoke the eval seam a second time"

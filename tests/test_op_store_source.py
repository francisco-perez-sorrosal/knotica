"""Behavioral contract tests for ``knotica.core.operations.store_source``.

``store_source`` persists a raw source *immutably* under
``sources/<topic>/<citation_key>.md`` with provenance frontmatter and exactly
one git commit. The behaviors pinned here (vault constitution, INTERFACE_DESIGN
§1.3–1.5, frozen provenance record):

1. **Immutable persistence** — the file lands under ``sources/<topic>/`` with a
   provenance record whose ``sha256`` is the body-only digest of the stored
   content, whose ``source_type`` is recorded, and whose ``origin_url`` matches
   the passed ``source_url``.
2. **One commit, frozen grammar** — exactly one commit + one log entry, and the
   ``title`` argument flows into both the commit subject and the log entry.
3. **Immutability guard** — the same ``citation_key`` with *different* content
   returns a ``SOURCE_EXISTS`` failure envelope and writes nothing.
4. **Idempotent re-store** — the same key with identical content is a no-op
   success: zero new commits, ``changed=False``.
5. **Candidate-scoped writes** — an additive ``candidate`` handle from an open
   ingest session routes the write onto that candidate's worktree branch
   instead of the canonical default branch, leaving the canonical vault's
   working tree and ``head_sha()`` untouched; an unopened/unknown handle fails
   fast with an actionable error pointing back at opening an ingest first.

Operations are config-agnostic: they take an already-resolved ``store`` and
``vault_root`` and return a result envelope rather than raising. The production
``operations`` package is imported inside each test (deferred), so collection
succeeds while the paired implementer's work is still in flight.
"""

from pathlib import Path

from knotica.core.records import body_sha256, parse_log_entries, parse_source_document
from knotica.core.vcs import VaultVcs
from support.vault import (
    git_commit_count,
    git_head_sha,
    git_status_porcelain,
    parse_knotica_commit,
    run_git,
)

TOPIC = "agentic-systems"
CITATION_KEY = "yao2022react"
SOURCE_PATH = f"sources/{TOPIC}/{CITATION_KEY}.md"
SOURCE_URL = "https://arxiv.org/html/2210.03629"
TITLE = "ReAct: Synergizing Reasoning and Acting, arXiv 2210.03629"

# Secret-free research body: the conservative scrub is a no-op on it, so the
# recorded digest and the stored body stay identical (no redaction rewrite).
SOURCE_BODY = """\
# ReAct

ReAct interleaves reasoning traces and task actions in large language models,
letting the model plan, act, and observe in a single loop.
"""


def _store_source(vault: Path, **kwargs):
    """Call the operation under test with a real store bound to ``vault``.

    Deferred import keeps collection green until the paired implementation lands.
    """
    from knotica.core.operations.store_source import store_source
    from knotica.store.local import LocalFSStore

    return store_source(store=LocalFSStore(vault), vault_root=vault, **kwargs)


def _store_default(vault: Path):
    return _store_source(
        vault,
        topic=TOPIC,
        citation_key=CITATION_KEY,
        title=TITLE,
        content=SOURCE_BODY,
        source_url=SOURCE_URL,
        source_type="markdown",
    )


def _error_code(result) -> object:
    if isinstance(result, dict):
        return result.get("error", {}).get("code")
    return None


def _read(vault: Path, relpath: str) -> str:
    return (vault / relpath).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Immutable persistence + provenance
# ---------------------------------------------------------------------------


def test_source_is_persisted_under_the_sources_tree(template_vault):
    _store_default(template_vault)

    assert (template_vault / SOURCE_PATH).exists()


def test_provenance_records_origin_type_and_body_digest(template_vault):
    _store_source(
        template_vault,
        topic=TOPIC,
        citation_key=CITATION_KEY,
        title=TITLE,
        content=SOURCE_BODY,
        source_url=SOURCE_URL,
        source_type="pdf",
    )

    provenance, body = parse_source_document(_read(template_vault, SOURCE_PATH))

    assert provenance.topic == TOPIC
    assert provenance.citation_key == CITATION_KEY
    assert provenance.origin_url == SOURCE_URL
    assert provenance.source_type == "pdf"
    # The recorded digest is the body-only sha256 of exactly the stored body.
    assert provenance.sha256 == body_sha256(body)


def test_stored_body_round_trips_to_the_passed_content(template_vault):
    _store_default(template_vault)

    _provenance, body = parse_source_document(_read(template_vault, SOURCE_PATH))
    assert body == SOURCE_BODY


# ---------------------------------------------------------------------------
# 2. One commit + frozen grammar; title flows through
# ---------------------------------------------------------------------------


def test_store_makes_exactly_one_commit_with_a_clean_tree(template_vault):
    before = git_commit_count(template_vault)

    _store_default(template_vault)

    assert git_commit_count(template_vault) == before + 1
    assert git_status_porcelain(template_vault) == ""


def test_title_flows_into_commit_and_log_grammar(template_vault):
    _store_default(template_vault)

    subject = run_git(template_vault, "log", "-1", "--format=%s").strip()
    parsed = parse_knotica_commit(subject)
    assert parsed is not None, f"commit subject not in knotica grammar: {subject!r}"
    assert parsed["op"] == "store_source"
    assert parsed["topic"] == TOPIC
    assert parsed["title"] == TITLE

    newest = parse_log_entries(_read(template_vault, "log.md"))[-1]
    assert newest.op == "store_source"
    assert newest.title == TITLE


# ---------------------------------------------------------------------------
# 3. Immutability guard -- same key, different content -> SOURCE_EXISTS
# ---------------------------------------------------------------------------


def test_conflicting_restore_fails_with_source_exists_and_writes_nothing(template_vault):
    _store_default(template_vault)
    after_first = git_commit_count(template_vault)
    original = _read(template_vault, SOURCE_PATH)

    result = _store_source(
        template_vault,
        topic=TOPIC,
        citation_key=CITATION_KEY,
        title=TITLE,
        content=SOURCE_BODY + "\nAn edited, conflicting version.\n",
        source_url=SOURCE_URL,
        source_type="pdf",
    )

    assert _error_code(result) == "SOURCE_EXISTS"
    assert git_commit_count(template_vault) == after_first
    assert _read(template_vault, SOURCE_PATH) == original


def test_store_source_reflows_pdf_line_wraps(template_vault: Path) -> None:
    wrapped = """\
# Section

The past two years have witnessed capable language models
(LLMs) into powerful AI agents.
"""
    result = _store_source(
        template_vault,
        topic=TOPIC,
        citation_key="pdf-wrap-test",
        title="PDF wrap test",
        content=wrapped,
        source_url=SOURCE_URL,
        source_type="pdf",
    )
    assert _error_code(result) is None
    _provenance, body = parse_source_document(
        _read(template_vault, "sources/agentic-systems/pdf-wrap-test.md")
    )
    assert "language models (LLMs) into powerful" in body
    assert "models\n(LLMs)" not in body


# ---------------------------------------------------------------------------
# 4. Idempotent re-store -- same key, identical content -> no-op success
# ---------------------------------------------------------------------------


def test_identical_restore_makes_no_new_commit(template_vault):
    _store_default(template_vault)
    after_first = git_commit_count(template_vault)
    head_after_first = git_head_sha(template_vault)

    result = _store_default(template_vault)

    assert git_commit_count(template_vault) == after_first
    assert git_head_sha(template_vault) == head_after_first
    assert _error_code(result) is None
    assert result["changed"] is False


# ---------------------------------------------------------------------------
# 5. Candidate-scoped writes -- an open ingest's worktree branch, never the
#    canonical default branch
# ---------------------------------------------------------------------------

CANDIDATE_CITATION_KEY = "candidate-source"
CANDIDATE_SOURCE_PATH = f"sources/{TOPIC}/{CANDIDATE_CITATION_KEY}.md"


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
        gap_id="gap-candidate-write",
        topic=TOPIC,
        qa_id="golden-candidate-write",
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
        url=SOURCE_URL,
        title=TITLE,
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


def _worktree_dir(vault: Path, candidate: str) -> Path:
    """Locate the worktree checkout backing an open candidate handle."""
    entry = next(wt for wt in VaultVcs(vault).list_worktrees() if wt.get("branch") == candidate)
    return Path(entry["path"])


def test_store_source_with_an_open_candidate_lands_on_the_worktree_branch(template_vault):
    candidate = _open_candidate(template_vault)
    vcs = VaultVcs(template_vault)
    wip_tip_before = vcs.ref_sha(candidate)

    _store_source(
        template_vault,
        topic=TOPIC,
        citation_key=CANDIDATE_CITATION_KEY,
        title=TITLE,
        content=SOURCE_BODY,
        source_url=SOURCE_URL,
        source_type="markdown",
        candidate=candidate,
    )

    assert vcs.ref_sha(candidate) != wip_tip_before, (
        "the write must land as a new commit on the candidate's worktree branch"
    )
    worktree = _worktree_dir(template_vault, candidate)
    assert (worktree / CANDIDATE_SOURCE_PATH).exists(), (
        "the source must be written into the worktree's checkout, not the canonical vault"
    )


def test_store_source_with_an_open_candidate_leaves_the_canonical_vault_untouched(template_vault):
    candidate = _open_candidate(template_vault)
    vcs = VaultVcs(template_vault)
    canonical_head_before = vcs.head_sha()
    canonical_branch_before = vcs.current_branch()

    _store_source(
        template_vault,
        topic=TOPIC,
        citation_key=CANDIDATE_CITATION_KEY,
        title=TITLE,
        content=SOURCE_BODY,
        source_url=SOURCE_URL,
        source_type="markdown",
        candidate=candidate,
    )

    assert vcs.head_sha() == canonical_head_before, "the canonical default-branch ref must not move"
    assert vcs.current_branch() == canonical_branch_before
    assert git_status_porcelain(template_vault) == "", "the canonical working tree must stay clean"
    assert not (template_vault / CANDIDATE_SOURCE_PATH).exists(), (
        "a candidate-scoped source must never appear in the canonical working tree"
    )


def test_store_source_with_an_unopened_candidate_handle_fails_and_points_to_open(template_vault):
    unopened = f"loop/wip/{TOPIC}/source-deadbeef"

    result = _store_source(
        template_vault,
        topic=TOPIC,
        citation_key=CANDIDATE_CITATION_KEY,
        title=TITLE,
        content=SOURCE_BODY,
        source_url=SOURCE_URL,
        source_type="markdown",
        candidate=unopened,
    )

    assert _error_code(result) == "SUGGESTION_NOT_FOUND"
    fix = result["error"]["fix"]
    assert "source_ingest_open" in fix, (
        f"the fix text must direct the caller to open first: {fix!r}"
    )
    assert not (template_vault / CANDIDATE_SOURCE_PATH).exists()

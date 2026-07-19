"""Behavioral contract tests for ``knotica.core.operations.write_page``.

``write_page`` is the transactional fat-write: one indivisible unit that
secret-scrubs the content, writes the page atomically, appends ``log.md``,
optionally upserts the page's line in the root ``index.md`` catalog, and makes
exactly one git commit. The behaviors pinned here (from the vault constitution,
INTERFACE_DESIGN §1.3–1.5, and the mutation-discipline design):

1. **Happy path** — a valid page is written, one commit is made, one log entry
   is appended (frozen grammar), and the page content lands verbatim.
2. **Index upsert** — when ``index_entry`` is supplied, the page's catalog line
   is added to root ``index.md`` in the *same* commit as the page; every other
   catalog line is left untouched.
3. **Reserved-name refusal** — targeting ``index.md`` / ``log.md`` / ``SCHEMA.md``
   as the page returns a ``RESERVED_NAME`` failure envelope and writes nothing.
4. **Invalid frontmatter** — content that fails the core frontmatter schema
   returns an ``INVALID_FRONTMATTER`` failure envelope and makes no commit.
5. **Idempotency by result-state** — an identical re-write is a no-op: zero new
   commits, HEAD unchanged, ``changed=False``.
6. **Secret scrub** — real credential content is redacted-in-content and the
   redaction is surfaced (loud, never silent); a false-positive corpus of
   legitimate token-like research strings is committed verbatim, unflagged.
7. **Candidate-scoped writes** — an additive ``candidate`` handle from an open
   ingest session routes the write onto that candidate's worktree branch
   instead of the canonical default branch, leaving the canonical vault's
   working tree and ``head_sha()`` untouched; an unopened/unknown handle fails
   fast with an actionable error pointing back at opening an ingest first.

Operations are config-agnostic: they take an already-resolved ``store`` and
``vault_root`` and return a result envelope (a success pointer or a
``{"error": {...}}`` failure) rather than raising. The production ``operations``
package is imported inside each test (deferred), so collection succeeds while
the paired implementer's work is still in flight.
"""

from pathlib import Path

import pytest

from knotica.core.records import parse_log_entries
from knotica.core.vcs import VaultVcs
from support.vault import (
    git_commit_count,
    git_head_sha,
    git_status_porcelain,
    parse_knotica_commit,
    run_git,
)

TOPIC = "agentic-systems"
PAGE = "react"
PAGE_PATH = f"{TOPIC}/{PAGE}.md"

VALID_PAGE = """\
---
type: concept
topic: agentic-systems
created: 2026-07-03
updated: 2026-07-03
confidence: high
sources: [yao2022react]
status: active
tags: [reasoning, acting]
---

# ReAct

Reasoning and acting, interleaved.
"""

# Missing every required core field except type/topic -> fails validate_frontmatter.
INVALID_PAGE = """\
---
type: concept
topic: agentic-systems
---

# Broken page
"""


# ---------------------------------------------------------------------------
# Deferred operation call + envelope readers (single signature site)
# ---------------------------------------------------------------------------


def _write_page(vault: Path, **kwargs):
    """Call the operation under test with a real store bound to ``vault``.

    Deferred import keeps collection green until the paired implementation lands.
    """
    from knotica.core.operations.write_page import write_page
    from knotica.store.local import LocalFSStore

    return write_page(store=LocalFSStore(vault), vault_root=vault, **kwargs)


def _error_code(result) -> object:
    if isinstance(result, dict):
        return result.get("error", {}).get("code")
    return None


def _warnings_text(result) -> str:
    warnings = result.get("warnings", []) if isinstance(result, dict) else []
    return repr(warnings)


def _head_files(vault: Path) -> set[str]:
    """The set of paths changed by the newest commit."""
    out = run_git(vault, "show", "--name-only", "--format=", "HEAD")
    return {line.strip() for line in out.splitlines() if line.strip()}


def _read(vault: Path, relpath: str) -> str:
    return (vault / relpath).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_valid_page_is_written_in_exactly_one_commit(template_vault):
    before = git_commit_count(template_vault)

    _write_page(
        template_vault, topic=TOPIC, page=PAGE, content=VALID_PAGE, summary="Add ReAct page"
    )

    assert git_commit_count(template_vault) == before + 1
    assert (template_vault / PAGE_PATH).exists()
    assert _read(template_vault, PAGE_PATH) == VALID_PAGE


def test_valid_page_leaves_a_clean_working_tree(template_vault):
    _write_page(
        template_vault, topic=TOPIC, page=PAGE, content=VALID_PAGE, summary="Add ReAct page"
    )

    assert git_status_porcelain(template_vault) == ""


def test_write_records_the_frozen_commit_and_log_grammar(template_vault):
    _write_page(
        template_vault, topic=TOPIC, page=PAGE, content=VALID_PAGE, summary="Add ReAct page"
    )

    subject = run_git(template_vault, "log", "-1", "--format=%s").strip()
    parsed = parse_knotica_commit(subject)
    assert parsed is not None, f"commit subject not in knotica grammar: {subject!r}"
    assert parsed["op"] == "write_page"
    assert parsed["topic"] == TOPIC
    assert parsed["title"] == "Add ReAct page"

    entries = parse_log_entries(_read(template_vault, "log.md"))
    newest = entries[-1]
    assert newest.op == "write_page"
    assert newest.topic == TOPIC
    assert newest.title == "Add ReAct page"
    assert PAGE_PATH in newest.pages


# ---------------------------------------------------------------------------
# 2. Index upsert -- catalog line maintained atomically, siblings untouched
# ---------------------------------------------------------------------------


def test_index_entry_upserts_this_pages_catalog_line(template_vault):
    entry = "reasoning-and-acting agents that interleave thought and tool use"

    _write_page(
        template_vault,
        topic=TOPIC,
        page=PAGE,
        content=VALID_PAGE,
        summary="Add ReAct page",
        index_entry=entry,
    )

    index = _read(template_vault, "index.md")
    assert entry in index
    assert f"[[{TOPIC}/{PAGE}]]" in index


def test_index_upsert_leaves_every_other_catalog_line_untouched(template_vault):
    before_lines = {line for line in _read(template_vault, "index.md").splitlines() if line.strip()}

    _write_page(
        template_vault,
        topic=TOPIC,
        page=PAGE,
        content=VALID_PAGE,
        summary="Add ReAct page",
        index_entry="a new catalog line for react",
    )

    after_lines = {line for line in _read(template_vault, "index.md").splitlines() if line.strip()}
    # Every pre-existing catalog line survives verbatim; only additions occur.
    assert before_lines <= after_lines


def test_index_upsert_is_part_of_the_same_single_commit(template_vault):
    before = git_commit_count(template_vault)

    _write_page(
        template_vault,
        topic=TOPIC,
        page=PAGE,
        content=VALID_PAGE,
        summary="Add ReAct page",
        index_entry="a new catalog line for react",
    )

    # Page + index.md + log.md are one atomic commit, not three.
    assert git_commit_count(template_vault) == before + 1
    assert _head_files(template_vault) == {PAGE_PATH, "index.md", "log.md"}


def test_index_entry_updates_an_existing_pages_line_without_adding_a_duplicate(template_vault):
    _write_page(
        template_vault,
        topic=TOPIC,
        page=PAGE,
        content=VALID_PAGE,
        summary="Add ReAct page",
        index_entry="first description of react",
    )

    updated_body = VALID_PAGE.replace("interleaved.", "interleaved (revised).")
    _write_page(
        template_vault,
        topic=TOPIC,
        page=PAGE,
        content=updated_body,
        summary="Revise ReAct page",
        index_entry="second, revised description of react",
    )

    index = _read(template_vault, "index.md")
    assert "second, revised description of react" in index
    assert "first description of react" not in index
    # Upsert, not append: exactly one catalog line references this page.
    assert index.count(f"[[{TOPIC}/{PAGE}]]") == 1


# ---------------------------------------------------------------------------
# 3. Reserved-name refusal -- bookkeeping files are never write_page targets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reserved", ["index.md", "log.md", "SCHEMA.md"])
def test_reserved_page_name_is_refused_with_reserved_name(template_vault, reserved):
    before = git_commit_count(template_vault)

    result = _write_page(
        template_vault, topic=TOPIC, page=reserved, content=VALID_PAGE, summary="should not commit"
    )

    assert _error_code(result) == "RESERVED_NAME"
    assert git_commit_count(template_vault) == before
    assert git_status_porcelain(template_vault) == ""


# ---------------------------------------------------------------------------
# 4. Invalid frontmatter -- fail fast, no commit
# ---------------------------------------------------------------------------


def test_invalid_frontmatter_is_refused_and_makes_no_commit(template_vault):
    before = git_commit_count(template_vault)

    result = _write_page(
        template_vault, topic=TOPIC, page=PAGE, content=INVALID_PAGE, summary="broken page"
    )

    assert _error_code(result) == "INVALID_FRONTMATTER"
    # The actionable message names at least one of the missing core fields.
    message = result["error"]["message"]
    assert any(field in message for field in ("confidence", "status", "created", "tags"))
    assert git_commit_count(template_vault) == before
    assert not (template_vault / PAGE_PATH).exists()


# ---------------------------------------------------------------------------
# 5. Idempotency by result-state -- identical re-write is a no-op
# ---------------------------------------------------------------------------


def test_identical_rewrite_makes_no_new_commit(template_vault):
    _write_page(
        template_vault,
        topic=TOPIC,
        page=PAGE,
        content=VALID_PAGE,
        summary="Add ReAct page",
        index_entry="a stable description",
    )
    after_first = git_commit_count(template_vault)
    head_after_first = git_head_sha(template_vault)

    result = _write_page(
        template_vault,
        topic=TOPIC,
        page=PAGE,
        content=VALID_PAGE,
        summary="Add ReAct page",
        index_entry="a stable description",
    )

    assert git_commit_count(template_vault) == after_first
    assert git_head_sha(template_vault) == head_after_first
    assert result["changed"] is False


# ---------------------------------------------------------------------------
# 6. Secret scrub -- loud redaction of real keys; false positives survive
# ---------------------------------------------------------------------------

_REAL_KEY = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"  # a GitHub-token-shaped secret

_REAL_KEY_PAGE = f"""\
---
type: concept
topic: agentic-systems
created: 2026-07-03
updated: 2026-07-03
confidence: high
sources: [yao2022react]
status: active
tags: [reasoning]
---

# Config leak

Someone pasted a token: {_REAL_KEY} into the notes.
"""

# Legitimate research strings the conservative scrub must NOT flag: an arXiv id,
# a hex digest, a git commit sha, and a base64-looking figure blob.
_FALSE_POSITIVE_STRINGS = (
    "arXiv:2210.03629",
    "fb5866613dd38425bd7c0b3e99f6e8663182489640d56ffae79da85e31bbec70",
    "b477ba0e1f738a4cdd4cd0d4dce13926d",
    "iVBORw0KGgoAAAANSUhEUgAAAAUA",
)

_FALSE_POSITIVE_PAGE = f"""\
---
type: paper
topic: agentic-systems
created: 2026-07-03
updated: 2026-07-03
confidence: high
sources: [yao2022react]
status: active
tags: [reasoning]
---

# ReAct paper

Published as {_FALSE_POSITIVE_STRINGS[0]}; sha256 {_FALSE_POSITIVE_STRINGS[1]};
introduced at commit {_FALSE_POSITIVE_STRINGS[2]}; figure blob {_FALSE_POSITIVE_STRINGS[3]}.
"""


def test_real_key_is_redacted_in_committed_content(template_vault):
    _write_page(
        template_vault, topic=TOPIC, page=PAGE, content=_REAL_KEY_PAGE, summary="Add config note"
    )

    stored = _read(template_vault, PAGE_PATH)
    assert _REAL_KEY not in stored, "raw secret reached the committed page"
    assert "[REDACTED:" in stored


def test_real_key_redaction_is_surfaced_as_a_warning(template_vault):
    result = _write_page(
        template_vault, topic=TOPIC, page=PAGE, content=_REAL_KEY_PAGE, summary="Add config note"
    )

    # The write still succeeds (scrub is a warning, never an error) but the
    # redaction is loud: SECRET_SCRUBBED rides on the success result.
    assert _error_code(result) is None
    assert "SECRET_SCRUBBED" in _warnings_text(result)


def test_false_positive_corpus_is_committed_verbatim(template_vault):
    result = _write_page(
        template_vault,
        topic=TOPIC,
        page=PAGE,
        content=_FALSE_POSITIVE_PAGE,
        summary="Add ReAct paper",
    )

    stored = _read(template_vault, PAGE_PATH)
    for legitimate in _FALSE_POSITIVE_STRINGS:
        assert legitimate in stored, (
            f"conservative scrub corrupted legitimate content: {legitimate}"
        )
    assert "[REDACTED:" not in stored
    assert "SECRET_SCRUBBED" not in _warnings_text(result)


# ---------------------------------------------------------------------------
# 7. Candidate-scoped writes -- an open ingest's worktree branch, never the
#    canonical default branch
# ---------------------------------------------------------------------------

CANDIDATE_PAGE = "candidate-page"
CANDIDATE_PAGE_PATH = f"{TOPIC}/{CANDIDATE_PAGE}.md"


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
        gap_id="gap-candidate-write-page",
        topic=TOPIC,
        qa_id="golden-candidate-write-page",
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


def _worktree_dir(vault: Path, candidate: str) -> Path:
    """Locate the worktree checkout backing an open candidate handle."""
    entry = next(wt for wt in VaultVcs(vault).list_worktrees() if wt.get("branch") == candidate)
    return Path(entry["path"])


def test_write_page_with_an_open_candidate_lands_on_the_worktree_branch(template_vault):
    candidate = _open_candidate(template_vault)
    vcs = VaultVcs(template_vault)
    wip_tip_before = vcs.ref_sha(candidate)

    _write_page(
        template_vault,
        topic=TOPIC,
        page=CANDIDATE_PAGE,
        content=VALID_PAGE,
        summary="Add candidate-scoped page",
        candidate=candidate,
    )

    assert vcs.ref_sha(candidate) != wip_tip_before, (
        "the write must land as a new commit on the candidate's worktree branch"
    )
    worktree = _worktree_dir(template_vault, candidate)
    assert (worktree / CANDIDATE_PAGE_PATH).exists(), (
        "the page must be written into the worktree's checkout, not the canonical vault"
    )


def test_write_page_with_an_open_candidate_leaves_the_canonical_vault_untouched(template_vault):
    candidate = _open_candidate(template_vault)
    vcs = VaultVcs(template_vault)
    canonical_head_before = vcs.head_sha()
    canonical_branch_before = vcs.current_branch()

    _write_page(
        template_vault,
        topic=TOPIC,
        page=CANDIDATE_PAGE,
        content=VALID_PAGE,
        summary="Add candidate-scoped page",
        candidate=candidate,
    )

    assert vcs.head_sha() == canonical_head_before, "the canonical default-branch ref must not move"
    assert vcs.current_branch() == canonical_branch_before
    assert git_status_porcelain(template_vault) == "", "the canonical working tree must stay clean"
    assert not (template_vault / CANDIDATE_PAGE_PATH).exists(), (
        "a candidate-scoped page must never appear in the canonical working tree"
    )


def test_write_page_with_an_unopened_candidate_handle_fails_and_points_to_open(template_vault):
    unopened = f"loop/wip/{TOPIC}/source-deadbeef"

    result = _write_page(
        template_vault,
        topic=TOPIC,
        page=CANDIDATE_PAGE,
        content=VALID_PAGE,
        summary="should not commit",
        candidate=unopened,
    )

    assert _error_code(result) == "SUGGESTION_NOT_FOUND"
    fix = result["error"]["fix"]
    assert "source_ingest_open" in fix, (
        f"the fix text must direct the caller to open first: {fix!r}"
    )
    assert not (template_vault / CANDIDATE_PAGE_PATH).exists()

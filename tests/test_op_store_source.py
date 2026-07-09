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

Operations are config-agnostic: they take an already-resolved ``store`` and
``vault_root`` and return a result envelope rather than raising. The production
``operations`` package is imported inside each test (deferred), so collection
succeeds while Step-25 code is still in flight.
"""

from pathlib import Path

from knotica.core.records import body_sha256, parse_log_entries, parse_source_document
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

    Deferred import keeps collection green until the Step-25 operation lands.
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

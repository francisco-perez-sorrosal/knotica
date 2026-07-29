"""Standing guard: a private notes/ tree must never move the eval-scored legs.

``core.lint._check_orphans`` builds its inbound-link set from *every* page in
the vault (``_vault_link_map`` walks the whole tree), while
``evals.harness._compose_scalar`` lints a *topic-scoped* view. A wikilink from
anywhere -- including a personal note that lives outside the scored corpus by
design -- currently counts as an inbound edge, so it can silently de-orphan a
content page. That drops the ``lint_violations`` leg the eval scalar reads,
which reads as a real quality improvement even though nothing about the KB
content changed. Under a ratcheting baseline policy, that would permanently
inflate the high-water mark against a number the vault never earned.

This module pins the vector directly, in both directions:

- a note-sourced inbound link must NOT suppress a ``PAGE_ORPHANED`` finding
  (the bug this module exists to close -- red until the source-family filter
  lands);
- a page-sourced inbound link must still suppress it (unchanged, the existing
  guarantee any fix must not break);
- a source-sourced inbound link is pinned as it behaves today, since
  ``sources/`` is a distinct, legitimately-scored family and its treatment is
  not part of this fix.

It also pins the narrowed proxy for "the eval composite scalar is unaffected
by a populated notes/ tree": the two scalar-adjacent legs that are directly
importable without pulling in the (locally absent) LLM dependency --
``lint_vault``'s violation set and the harness's content-page count. The full
four-leg ``run_eval``/``_compose_scalar`` round-trip is not exercised here;
that is a real, tracked coverage gap this module's docstring does not paper
over, not a claim of complete protection.

Intended as the standing home for future score-isolation regressions, not a
one-off -- add here, don't grow a copy elsewhere.
"""

import shutil
from pathlib import Path

from knotica.core.lint import LintCheck, Violation, lint_vault
from knotica.evals.harness import _count_content_pages
from knotica.store import LocalFSStore

#: The template vault's only topic -- reused across every test in this module.
TOPIC = "agentic-systems"
#: An existing, indexed content page every fixture can safely append a link to.
MEMORY_PAGE = f"{TOPIC}/agent-memory.md"
#: Vault-relative stem for the page this module deliberately orphans.
ORPHAN_STEM = "score-isolation-orphan"
ORPHAN_PAGE = f"{TOPIC}/{ORPHAN_STEM}.md"


def lint(vault: Path, topic: str) -> list[Violation]:
    return lint_vault(LocalFSStore(vault), topic)


def checks(violations: list[Violation]) -> set[LintCheck]:
    return {v.check for v in violations}


def write(vault: Path, relpath: str, text: str) -> None:
    path = vault / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def read(vault: Path, relpath: str) -> str:
    return (vault / relpath).read_text()


def plant_unlinked_page(vault: Path) -> None:
    """Write a genuine, real orphan: no page in the template links to it.

    Mirrors ``tests/test_lint.py``'s own ``lonely.md`` fixture shape --
    ``sources: [wang2024awm]`` cites the one source the template already
    stores, so this page carries no incidental ``CITATION_UNRESOLVED``.
    """
    write(
        vault,
        ORPHAN_PAGE,
        "---\n"
        f"type: concept\ntopic: {TOPIC}\ncreated: 2026-07-03\n"
        "updated: 2026-07-03\nconfidence: medium\nsources: [wang2024awm]\n"
        "status: active\ntags: [demo]\n---\n\n# Score isolation orphan\n",
    )


# ---------------------------------------------------------------------------
# The bug: a note-sourced inbound link must not suppress the orphan finding.
# ---------------------------------------------------------------------------


def test_note_sourced_inbound_link_does_not_suppress_orphan_report(
    template_vault: Path, tmp_path: Path
) -> None:
    # Vault X: a real, verified orphan -- nothing links to it yet.
    plant_unlinked_page(template_vault)
    violations_before_notes = lint(template_vault, TOPIC)
    pages_before_notes = _count_content_pages(LocalFSStore(template_vault), TOPIC)
    assert LintCheck.PAGE_ORPHANED in checks(violations_before_notes), (
        "fixture sanity: the planted page must be a genuine orphan before notes/ exists"
    )

    # Vault Y: an exact clone of X, plus a private note whose only content is
    # a wikilink back to the "orphaned" page -- a raw write, no operation.
    vault_with_notes = tmp_path / "vault-with-notes"
    shutil.copytree(template_vault, vault_with_notes)
    write(
        vault_with_notes,
        f"notes/{TOPIC}/reflection.md",
        f"A private reflection that happens to reference [[{TOPIC}/{ORPHAN_STEM}]].\n",
    )

    violations_with_notes = lint(vault_with_notes, TOPIC)
    pages_with_notes = _count_content_pages(LocalFSStore(vault_with_notes), TOPIC)

    # The eval-scored legs must be byte-identical whether or not notes/ exists.
    assert violations_with_notes == violations_before_notes
    assert pages_with_notes == pages_before_notes
    # Stated directly, not just implied by the list equality above: the note's
    # backlink must not have de-orphaned the page.
    assert LintCheck.PAGE_ORPHANED in checks(violations_with_notes)


# ---------------------------------------------------------------------------
# The regression guard: a page-sourced inbound link still suppresses it.
# ---------------------------------------------------------------------------


def test_page_sourced_inbound_link_still_suppresses_orphan_report(template_vault: Path) -> None:
    plant_unlinked_page(template_vault)

    # Link to the orphan from an ordinary content page in the same topic --
    # a bare wikilink resolves same-directory, exactly like a real backlink.
    write(
        template_vault,
        MEMORY_PAGE,
        read(template_vault, MEMORY_PAGE) + f"\n\nSee [[{ORPHAN_STEM}]].\n",
    )

    violations = lint(template_vault, TOPIC)
    assert LintCheck.PAGE_ORPHANED not in {v.check for v in violations if v.path == ORPHAN_PAGE}


# ---------------------------------------------------------------------------
# sources/ is a distinct family -- pin today's behavior, do not assume it.
# ---------------------------------------------------------------------------


def test_source_sourced_inbound_link_suppresses_orphan_report_as_today(
    template_vault: Path,
) -> None:
    plant_unlinked_page(template_vault)

    # A stored source citing the "orphaned" page -- a full vault-path link,
    # since sources/<topic>/ is a different directory than the page it cites.
    write(
        template_vault,
        f"sources/{TOPIC}/score-isolation-synthetic-source.md",
        f"A stored source that cites [[{TOPIC}/{ORPHAN_STEM}]].\n",
    )

    violations = lint(template_vault, TOPIC)
    assert LintCheck.PAGE_ORPHANED not in {v.check for v in violations if v.path == ORPHAN_PAGE}

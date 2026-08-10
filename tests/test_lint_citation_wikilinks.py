"""A wikilink to a page is a page link, whatever the page is named.

Page slugs and citation keys share a shape. ``simon1955-behavioral-model-of-
rational-choice`` is an ordinary page name and also matches the citation-key
pattern exactly, so scanning a page's raw body read every author-year *page
link* as a citation and demanded a stored source under the linked page's own
name.

Two Simon paper-pages that cross-reference each other produced two permanent
``citation-unresolved`` violations, clearable only by deleting legitimate
cross-references, with remediation text advising the creation of a source keyed
to a page name. Links to concept pages escaped only because their slugs carry
no four-digit year -- a coincidence of naming, not a rule, which is why the
fault was mistaken for something to do with the target's ``type``.
"""

from __future__ import annotations

from pathlib import Path

from knotica.core.lint import LintCheck, lint_vault
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"


def _page(
    vault: Path, slug: str, body: str, *, sources: list[str] | None = None, title: str = "A Paper"
) -> None:
    """Write one ``type: paper`` page.

    ``title`` is deliberately prose, and the H1 mirrors it rather than the slug:
    a real page's heading is its human title, and a slug-shaped heading would
    itself be scanned as an inline citation key, masking what these tests are
    about.
    """
    declared = "\n".join(f"  - {key}" for key in (sources or []))
    front = [
        "---",
        f"title: {title}",
        "type: paper",
        "created: 2026-08-08",
        "updated: 2026-08-08",
        "tags: []",
        "sources:" if sources else "sources: []",
    ]
    if sources:
        front.append(declared)
    front.append("---")
    path = vault / TOPIC / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(front) + f"\n\n# {title}\n\n{body}\n", encoding="utf-8")


def _source(vault: Path, key: str) -> None:
    path = vault / "sources" / TOPIC / f"{key}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {key}\n\nstored source body\n", encoding="utf-8")


def _citation_violations(vault: Path) -> list:
    return [
        v
        for v in lint_vault(LocalFSStore(vault), TOPIC)
        if v.check == LintCheck.CITATION_UNRESOLVED
    ]


def test_two_paper_pages_citing_each_other_produce_no_citation_violations(
    template_vault: Path,
) -> None:
    """The reported pair, reproduced: both link forms, both directions."""
    _source(template_vault, "simon1955bmrc-distillation")
    _source(template_vault, "simon1979rdm-distillation")
    _page(
        template_vault,
        "simon1955-behavioral-model-of-rational-choice",
        "Extended in [[simon1979-rational-decision-making-in-business-organizations|the 1979 lecture]].",
        sources=["simon1955bmrc-distillation"],
    )
    _page(
        template_vault,
        "simon1979-rational-decision-making-in-business-organizations",
        "Builds on [[agentic-systems/simon1955-behavioral-model-of-rational-choice|the 1955 QJE paper]].",
        sources=["simon1979rdm-distillation"],
    )

    assert _citation_violations(template_vault) == []


def test_the_bare_and_topic_qualified_link_forms_are_treated_alike(
    template_vault: Path,
) -> None:
    """Normalising between the two forms left the count unchanged at 2 -- because
    the link form was never what mattered; the target *string* was."""
    _source(template_vault, "wang2024awm-distillation")
    _page(template_vault, "wang2024-agent-workflow-memory", "seminal")
    _page(
        template_vault,
        "hu2025-memory-survey",
        "Bare: [[wang2024-agent-workflow-memory]]. "
        "Qualified: [[agentic-systems/wang2024-agent-workflow-memory|AWM]].",
        sources=["wang2024awm-distillation"],
    )

    assert _citation_violations(template_vault) == []


def test_a_genuinely_unresolved_citation_is_still_flagged(template_vault: Path) -> None:
    """The check must keep catching a page that outruns its evidence."""
    _page(
        template_vault,
        "hu2025-memory-survey",
        "Claims rest on zhao2031unstored, which was never stored.",
    )

    violations = _citation_violations(template_vault)

    assert len(violations) == 1
    assert "zhao2031unstored" in violations[0].message


def test_an_undeclared_frontmatter_source_is_still_flagged(template_vault: Path) -> None:
    """Masking wikilinks must not weaken the declared-``sources`` half."""
    _page(
        template_vault,
        "hu2025-memory-survey",
        "Body with no inline keys at all.",
        sources=["never-stored-key"],
    )

    violations = _citation_violations(template_vault)

    assert len(violations) == 1
    assert "never-stored-key" in violations[0].message


def test_an_inline_citation_outside_a_wikilink_is_still_scanned(
    template_vault: Path,
) -> None:
    """Only the inside of ``[[...]]`` is masked -- prose around it is not."""
    _source(template_vault, "wang2024awm")
    _page(template_vault, "wang2024-agent-workflow-memory", "seminal")
    _page(
        template_vault,
        "hu2025-memory-survey",
        "See [[wang2024-agent-workflow-memory]] and also wang2024awm and hu2099missing.",
    )

    messages = " ".join(v.message for v in _citation_violations(template_vault))

    assert "hu2099missing" in messages, "an unstored inline key in prose must still be caught"
    assert "wang2024-agent-workflow-memory" not in messages, "the link target must not be"

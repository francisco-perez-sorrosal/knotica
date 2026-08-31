"""Tests for `scripts/check_adr_health.py`, the ADR-metadata gate.

A gate that fails open is worse than no gate: it reports success over records it
never validated. Both defects this script exists to catch reached the repo
silently and were found by an audit rather than by a check, so the cases below
pin that the check actually rejects each of them.

The draft-exemption case is the designed subtlety. A draft legitimately points
at a finalized decision before finalize rewrites ids and adds the back-reference,
so reciprocity must not be demanded of drafts -- while YAML validity must be,
since a malformed draft breaks finalize itself.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_adr_health.py"

# A fabricated draft id for the fixtures below. Not a reference to any real
# draft -- the whole point is that it never resolves to a finalized record.
DRAFT_ID = "dec-draft-abc12345"  # id-citation-discipline:ignore
DRAFT_FILE = "20260804-1200-u-b-slug.md"

# A *valid* record by default. The fixture has to be healthy or every test that
# is about something else fails on the disconfirmation rule instead; the canaries
# below strip what they mean to test.
ADR = """\
---
id: {id}
title: {id} title
status: accepted
category: architectural
date: 2026-08-04
summary: {summary}
tags: [x]
made_by: agent
dissent: A one-line strongest objection.
{extra}---

## Context

Body.

## Decision

The decision.

## Considered Options

The options.

## Consequences

The consequences.

## Disconfirmation

- **Falsifier.** Something measurable.
- **Steelmanned runner-up.** The next-best option.
- **Reversal trigger.** The signal to revisit.
"""


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    """Load the checker as a module -- `scripts/` is not an importable package."""
    spec = importlib.util.spec_from_file_location("adr_health", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def corpus(
    checker: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., int]:
    """Build a throwaway decisions tree and run the checker over it."""
    decisions = tmp_path / "decisions"
    (decisions / "drafts").mkdir(parents=True)
    monkeypatch.setattr(checker, "DECISIONS_DIR", decisions)
    # The index and affected_files checks read outside DECISIONS_DIR, so the
    # throwaway tree has to redirect those roots too or every test inherits the
    # committed corpus's index and the repo's real file layout.
    monkeypatch.setattr(checker, "INDEX_PATH", decisions / "DECISIONS_INDEX.md")
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)

    def run(
        finalized: dict[str, str],
        drafts: dict[str, str] | None = None,
        index: str | None = None,
    ) -> int:
        for name, body in finalized.items():
            (decisions / name).write_text(body)
        for name, body in (drafts or {}).items():
            (decisions / "drafts" / name).write_text(body)
        # Default to an index that agrees with the records, so a test that is not
        # about index freshness is not testing index freshness by accident.
        if index is None:
            rows = "".join(
                f"| {identifier} | t | accepted | architectural | 2026-01-01 | tags | s |\n"
                for identifier in sorted(_ids(finalized))
            )
            index = f"# Decisions Index\n\n| ID | Title | Status |\n|--|--|--|\n{rows}"
        (decisions / "DECISIONS_INDEX.md").write_text(index)
        return int(checker.main())

    return run


def _ids(finalized: dict[str, str]) -> list[str]:
    return re.findall(r"^id: (dec-\S+)$", "\n".join(finalized.values()), re.MULTILINE)


def adr(identifier: str, summary: str = "a summary", **fields: str) -> str:
    extra = "".join(f"{key}: {value}\n" for key, value in fields.items())
    return ADR.format(id=identifier, summary=summary, extra=extra)


def test_passes_on_the_committed_decision_corpus(checker: ModuleType) -> None:
    assert checker.main() == 0


def test_accepts_a_reciprocal_pair(corpus: Callable[..., int]) -> None:
    exit_code = corpus(
        {
            "001-a.md": adr("dec-001", re_affirmed_by="[dec-002]"),
            "002-b.md": adr("dec-002", re_affirms="dec-001"),
        }
    )

    assert exit_code == 0


def test_rejects_frontmatter_that_strict_yaml_cannot_parse(
    corpus: Callable[..., int], capsys
) -> None:
    # An unquoted scalar containing a colon-space reads as a nested mapping.
    exit_code = corpus({"001-a.md": adr("dec-001", summary="uses source: curate_example here")})

    assert exit_code == 1
    assert "not valid YAML" in capsys.readouterr().err


def test_rejects_a_one_directional_re_affirms(corpus: Callable[..., int], capsys) -> None:
    exit_code = corpus(
        {"001-a.md": adr("dec-001"), "002-b.md": adr("dec-002", re_affirms="dec-001")}
    )

    assert exit_code == 1
    assert "does not list it back" in capsys.readouterr().err


def test_rejects_a_back_reference_with_no_forward_pointer(
    corpus: Callable[..., int], capsys
) -> None:
    exit_code = corpus(
        {"001-a.md": adr("dec-001", re_affirmed_by="[dec-002]"), "002-b.md": adr("dec-002")}
    )

    assert exit_code == 1
    assert "does not re_affirm it" in capsys.readouterr().err


def test_rejects_a_re_affirms_pointing_at_a_decision_that_does_not_exist(
    corpus: Callable[..., int], capsys
) -> None:
    exit_code = corpus({"001-a.md": adr("dec-001", re_affirms="dec-999")})

    assert exit_code == 1
    assert "does not exist" in capsys.readouterr().err


def test_rejects_a_finalized_record_that_points_at_a_draft_id(
    corpus: Callable[..., int], capsys
) -> None:
    # Finalize rewrites draft ids as it promotes them, so one surviving on a
    # finalized record either escaped that rewrite or names a draft that was
    # abandoned -- dec-062 carried exactly the latter.
    exit_code = corpus({"001-a.md": adr("dec-001", supersedes=DRAFT_ID)})

    assert exit_code == 1
    assert "a draft id" in capsys.readouterr().err


def test_a_draft_pointing_at_a_finalized_decision_is_not_a_reciprocity_failure(
    corpus: Callable[..., int],
) -> None:
    # Finalize rewrites the id and adds the back-reference; demanding it earlier
    # would fail the gate for an in-flight draft doing exactly the right thing.
    exit_code = corpus(
        {"001-a.md": adr("dec-001")},
        {DRAFT_FILE: adr(DRAFT_ID, re_affirms="dec-001")},
    )

    assert exit_code == 0


def test_a_draft_with_invalid_yaml_still_fails(corpus: Callable[..., int], capsys) -> None:
    # Drafts are exempt from reciprocity, not from parseability -- finalize
    # itself reads them.
    exit_code = corpus(
        {"001-a.md": adr("dec-001")},
        {DRAFT_FILE: adr(DRAFT_ID, summary="breaks: here")},
    )

    assert exit_code == 1
    assert "not valid YAML" in capsys.readouterr().err


def test_rejects_a_one_directional_supersedes(corpus: Callable[..., int], capsys) -> None:
    # The same defect the re_affirms pair was gated against, on the field that
    # answers "which decision replaced this one?". It went unchecked because
    # CROSS_REFERENCE_FIELDS was consumed only by the draft-id scan.
    exit_code = corpus(
        {"001-a.md": adr("dec-001"), "002-b.md": adr("dec-002", supersedes="dec-001")}
    )

    assert exit_code == 1
    assert "does not list it back in superseded_by" in capsys.readouterr().err


def test_rejects_an_architectural_record_with_no_disconfirmation_section(
    corpus: Callable[..., int], capsys
) -> None:
    # dec-021 shipped architectural with no Disconfirmation and nothing objected.
    body = adr("dec-001").replace("## Disconfirmation", "## Something Else")

    exit_code = corpus({"001-a.md": body})

    assert exit_code == 1
    assert "no `## Disconfirmation` section" in capsys.readouterr().err


def test_rejects_an_architectural_record_with_an_empty_dissent(
    corpus: Callable[..., int], capsys
) -> None:
    body = adr("dec-001").replace("dissent: A one-line strongest objection.", "dissent: ")

    exit_code = corpus({"001-a.md": body})

    assert exit_code == 1
    assert "`dissent:` is missing or empty" in capsys.readouterr().err


def test_a_non_architectural_record_needs_neither(corpus: Callable[..., int]) -> None:
    # The rule is scoped to `architectural` by the ADR conventions; applying it to
    # every category would demand a falsifier for a config tweak.
    body = (
        adr("dec-001")
        .replace("category: architectural", "category: implementation")
        .replace("dissent: A one-line strongest objection.\n", "")
        .replace("## Disconfirmation", "## Something Else")
    )

    assert corpus({"001-a.md": body}) == 0


def test_rejects_affected_files_that_do_not_resolve(corpus: Callable[..., int], capsys) -> None:
    # Six records named src/knotica/mcp/, a path dec-009 renamed away the same day.
    exit_code = corpus({"001-a.md": adr("dec-001", affected_files="[src/knotica/ghost/]")})

    assert exit_code == 1
    assert "which is not on disk" in capsys.readouterr().err


def test_rejects_an_index_whose_status_disagrees_with_the_record(
    corpus: Callable[..., int], capsys
) -> None:
    stale = "# Decisions Index\n\n| ID | Title | Status |\n|--|--|--|\n" + (
        "| dec-001 | t | superseded | architectural | 2026-01-01 | tags | s |\n"
    )

    exit_code = corpus({"001-a.md": adr("dec-001")}, index=stale)

    assert exit_code == 1
    assert "DECISIONS_INDEX.md says status `superseded`" in capsys.readouterr().err


def test_rejects_an_index_missing_a_decision(corpus: Callable[..., int], capsys) -> None:
    empty = "# Decisions Index\n\n| ID | Title | Status |\n|--|--|--|\n"

    exit_code = corpus({"001-a.md": adr("dec-001")}, index=empty)

    assert exit_code == 1
    assert "absent from DECISIONS_INDEX.md" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Check 7 -- the four universal body sections
# ---------------------------------------------------------------------------


def test_rejects_a_record_missing_its_consequences(
    corpus: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    # The dec-109 defect: a gate-behaviour change shipped with no consequences
    # record, so a future reader weighing reversal had nothing to weigh.
    body = adr("dec-001").replace("## Consequences\n\nThe consequences.\n\n", "")

    assert corpus({"001-a.md": body}) == 1
    assert "no `## Consequences` section" in capsys.readouterr().err


def test_the_grandfather_forgives_exactly_the_section_each_record_lacks(
    corpus: Callable[..., int], capsys: pytest.CaptureFixture[str], checker: ModuleType
) -> None:
    # dec-014 predates the check and never recorded its options -- forgiven. The
    # forgiveness is per-section: the same record missing `## Decision` still
    # fails, and an id outside the closed set gets no forgiveness at all.
    without_options = adr("dec-014").replace("## Considered Options\n\nThe options.\n\n", "")
    assert corpus({"014-a.md": without_options}) == 0

    also_without_decision = without_options.replace("## Decision\n\nThe decision.\n\n", "")
    assert corpus({"014-a.md": also_without_decision}) == 1
    assert "no `## Decision` section" in capsys.readouterr().err


def test_a_draft_is_not_gated_on_body_sections(corpus: Callable[..., int]) -> None:
    # A draft is in-flight; sections are demanded where the record is permanent.
    # (Frontmatter validity is still demanded of drafts -- see the module header.)
    draft = adr("dec-draft-abc12345").replace(
        "## Consequences\n\nThe consequences.\n\n", ""
    )  # id-citation-discipline:ignore

    assert corpus({}, drafts={DRAFT_FILE: draft}) == 0

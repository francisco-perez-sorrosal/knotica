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
{extra}---

## Context

Body.
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

    def run(finalized: dict[str, str], drafts: dict[str, str] | None = None) -> int:
        for name, body in finalized.items():
            (decisions / name).write_text(body)
        for name, body in (drafts or {}).items():
            (decisions / "drafts" / name).write_text(body)
        return int(checker.main())

    return run


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

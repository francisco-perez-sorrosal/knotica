#!/usr/bin/env python3
"""Gate the ADR-metadata defects that reached this repo silently.

Every one was found by an audit rather than by a check, which is the point of
this script -- an audit runs when someone remembers to run it. The first two
were caught by a sentinel pass; the last four by an architecture-record
reconciliation (td-044) that found this gate was narrower than the claims
resting on it.

1. **Frontmatter must parse as strict YAML.** Two ADRs carried unquoted scalars
   containing a colon-space (`... use source: curate_example ...`), which YAML
   reads as a nested mapping and rejects. The breakage was latent: this repo's
   own index generator uses a tolerant line-regex parser, so the index looked
   fine while every strict consumer -- Praxion's `finalize_adrs.py`, sentinel's
   decision-log checks, the verifier's `affected_reqs` cross-reference -- would
   have failed on those files.

2. **`re_affirms` and `re_affirmed_by` must be reciprocal.** Eight pointers were
   one-directional, so "what later decisions rest on this one?" -- the query the
   metadata exists to answer -- returned nothing from the target's side.

3. **`supersedes` / `superseded_by` must be reciprocal**, and `retired_by` must
   resolve. `CROSS_REFERENCE_FIELDS` was consumed only by the draft-id scan, so
   the reciprocity rule applied to `re_affirms` and to nothing else -- while
   "which decision replaced this one?" is the question the supersession fields
   exist to answer.

4. **An `architectural` ADR carries `dissent:` and `## Disconfirmation`.** The
   conventions require both at that category. dec-021 shipped with neither.

5. **`affected_files` entries resolve on disk.** Six records named
   `src/knotica/mcp/`, a path dec-009 renamed away on the same date; dec-026
   named a `discovery/exa.py` that was never written.

6. **`DECISIONS_INDEX.md` lists every finalized ADR at its real status.** The
   index is generated and says so, but nothing stopped a hand edit -- and one
   happened, leaving three rows with an unescaped pipe inside a Summary cell.

Checks 3-6 verify the property a reader depends on, not byte-equality with a
fresh generation: a check that fails on cosmetic diffs gets muted, and a muted
check is the state this script exists to leave behind.

Reciprocity is checked among **finalized** ADRs only. A draft legitimately
points at a finalized decision before finalize rewrites the ids and adds the
back-reference, so demanding reciprocity of in-flight drafts would fail the gate
for doing the right thing. Frontmatter validity is checked for drafts too, since
a malformed draft breaks finalize itself.

Exit 0 when healthy, 1 on any finding.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DECISIONS_DIR = REPO_ROOT / ".ai-state" / "decisions"
INDEX_PATH = DECISIONS_DIR / "DECISIONS_INDEX.md"
FINALIZED_GLOB = "[0-9][0-9][0-9]-*.md"
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---", re.DOTALL)

#: Every frontmatter field that names another decision.
CROSS_REFERENCE_FIELDS = (
    "supersedes",
    "superseded_by",
    "re_affirms",
    "re_affirmed_by",
    "retired_by",
)

#: Prefix of a provisional draft id. Split so this line is a rule, not a citation.
DRAFT_ID_PREFIX = "dec-" + "draft-"


def _as_list(value: Any) -> list[str]:
    """Both fields appear as a bare scalar and as a list across this corpus."""
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)] if value else []


def _load(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Return (frontmatter, error). Exactly one is None."""
    match = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    if match is None:
        return None, "no YAML frontmatter block"
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        detail = str(exc).split("\n")[0]
        return None, f"frontmatter is not valid YAML ({detail})"
    if not isinstance(parsed, dict):
        return None, "frontmatter is not a mapping"
    return parsed, None


#: Pairs of frontmatter fields that must point at each other.
RECIPROCAL_PAIRS = (("supersedes", "superseded_by"), ("superseded_by", "supersedes"))


def _check_supersession(
    identifier: str, frontmatter: dict[str, Any], finalized: dict[str, dict[str, Any]]
) -> list[str]:
    """`supersedes` and `superseded_by` resolve and point back at each other.

    Previously unchecked: ``CROSS_REFERENCE_FIELDS`` was consumed only by the
    draft-id scan, so a one-directional supersession pointer -- the same defect
    the ``re_affirms`` pair was gated against -- passed silently. "Which decision
    replaced this one?" is the question the field exists to answer, and a
    one-directional pointer answers it from one side only.
    """
    failures = []
    for field, mirror in RECIPROCAL_PAIRS:
        for target in _as_list(frontmatter.get(field)):
            if target not in finalized:
                failures.append(f"{identifier}: {field} `{target}`, which does not exist")
            elif identifier not in _as_list(finalized[target].get(mirror)):
                failures.append(
                    f"{identifier}: {field} `{target}`, but {target} does not list it back "
                    f"in {mirror}"
                )
    for target in _as_list(frontmatter.get("retired_by")):
        if target not in finalized:
            failures.append(f"{identifier}: retired_by `{target}`, which does not exist")
    return failures


def _check_disconfirmation(identifier: str, frontmatter: dict[str, Any]) -> list[str]:
    """An `architectural` ADR carries `dissent:` and a `## Disconfirmation` section.

    The ADR conventions make both mandatory at that category, and nothing enforced
    it: dec-021 shipped architectural with neither, and the gap was found by an
    audit rather than by a check. The body section is the load-bearing half -- a
    one-line `dissent:` is cheap to satisfy, while naming a falsifier, a
    steelmanned runner-up, and a reversal trigger is the work that makes a
    decision reviewable later.
    """
    if frontmatter.get("category") != "architectural":
        return []
    failures = []
    if not str(frontmatter.get("dissent") or "").strip():
        failures.append(
            f"{identifier}: category is architectural but `dissent:` is missing or empty"
        )
    path = frontmatter.get("__path")
    if isinstance(path, Path) and "## Disconfirmation" not in path.read_text(encoding="utf-8"):
        failures.append(
            f"{identifier}: category is architectural but the body has no `## Disconfirmation` "
            f"section (falsifier / steelmanned runner-up / reversal trigger)"
        )
    return failures


def _check_affected_files(identifier: str, frontmatter: dict[str, Any]) -> list[str]:
    """Every `affected_files` entry resolves on disk.

    Two classes of stale pointer reached the corpus unnoticed: six records named
    ``src/knotica/mcp/``, a path renamed away by dec-009 on the same date, and
    dec-026 named a ``discovery/exa.py`` that was never written. Both are
    bookkeeping rather than wrong decisions, but they make the field unusable for
    the "what did this touch?" query it exists to serve.
    """
    failures = []
    for entry in _as_list(frontmatter.get("affected_files")):
        candidate = entry.strip()
        if not candidate:
            continue
        resolved = (
            any(REPO_ROOT.glob(candidate)) if "*" in candidate else (REPO_ROOT / candidate).exists()
        )
        if not resolved:
            failures.append(
                f"{identifier}: affected_files names `{candidate}`, which is not on disk"
            )
    return failures


def _check_index_freshness(finalized: dict[str, dict[str, Any]]) -> list[str]:
    """`DECISIONS_INDEX.md` has one row per finalized ADR, with a matching status.

    The index is generated by ``scripts/regenerate_adr_index.py`` and says so at
    the top, but nothing stopped a hand edit -- and one happened, leaving three
    rows with an unescaped pipe inside a Summary cell that split them into extra
    columns in any renderer. This checks the property that matters to a reader
    (every decision is listed, at its real status) rather than byte-equality with
    a fresh generation, which would fail on cosmetic diffs and get muted.
    """
    if not INDEX_PATH.exists():
        return [f"{INDEX_PATH.name} is missing; regenerate with scripts/regenerate_adr_index.py"]
    text = INDEX_PATH.read_text(encoding="utf-8")
    rows = dict(re.findall(r"^\|\s*(dec-\d+)\s*\|[^|]*\|\s*([a-z-]+)\s*\|", text, re.MULTILINE))
    failures = []
    for identifier, frontmatter in sorted(finalized.items()):
        if identifier not in rows:
            failures.append(f"{identifier}: absent from DECISIONS_INDEX.md — regenerate the index")
        elif rows[identifier] != frontmatter.get("status"):
            failures.append(
                f"{identifier}: DECISIONS_INDEX.md says status `{rows[identifier]}`, the record "
                f"says `{frontmatter.get('status')}` — regenerate the index"
            )
    for identifier in sorted(set(rows) - set(finalized)):
        failures.append(
            f"{identifier}: in DECISIONS_INDEX.md but has no record — regenerate the index"
        )
    return failures


def main() -> int:
    failures: list[str] = []
    finalized: dict[str, dict[str, Any]] = {}

    drafts = sorted((DECISIONS_DIR / "drafts").glob("*.md"))
    for path in sorted(DECISIONS_DIR.glob(FINALIZED_GLOB)) + drafts:
        frontmatter, error = _load(path)
        if error is not None:
            failures.append(f"{path.name}: {error}")
            continue
        identifier = frontmatter.get("id")
        if not isinstance(identifier, str):
            failures.append(f"{path.name}: missing or non-string `id`")
        elif path.parent == DECISIONS_DIR:
            # The Disconfirmation check reads the body, so carry the path along.
            frontmatter["__path"] = path
            finalized[identifier] = frontmatter

    for identifier, frontmatter in sorted(finalized.items()):
        # A finalized record must not point at a draft id. Finalize rewrites
        # those to dec-NNN as it promotes them -- so one surviving here either
        # escaped that rewrite, or names a draft that was abandoned and will
        # never finalize. Either way the pointer can never resolve.
        for field in CROSS_REFERENCE_FIELDS:
            for value in _as_list(frontmatter.get(field)):
                if value.startswith(DRAFT_ID_PREFIX):
                    failures.append(
                        f"{identifier}: {field} names `{value}`, a draft id — a finalized record "
                        f"cannot reference one, since it resolves only if that draft finalizes"
                    )

        for target in _as_list(frontmatter.get("re_affirms")):
            if target not in finalized:
                failures.append(f"{identifier}: re_affirms `{target}`, which does not exist")
            elif identifier not in _as_list(finalized[target].get("re_affirmed_by")):
                failures.append(
                    f"{identifier}: re_affirms `{target}`, but {target} does not list it back "
                    f"in re_affirmed_by"
                )
        for source in _as_list(frontmatter.get("re_affirmed_by")):
            if source not in finalized:
                failures.append(f"{identifier}: re_affirmed_by `{source}`, which does not exist")
            elif identifier not in _as_list(finalized[source].get("re_affirms")):
                failures.append(
                    f"{identifier}: claims re_affirmed_by `{source}`, but {source} does not "
                    f"re_affirm it"
                )

        failures.extend(_check_supersession(identifier, frontmatter, finalized))
        failures.extend(_check_disconfirmation(identifier, frontmatter))
        failures.extend(_check_affected_files(identifier, frontmatter))

    failures.extend(_check_index_freshness(finalized))

    if failures:
        print(f"ADR health check FAILED ({len(failures)} finding(s)):", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(f"ADR health OK — {len(finalized)} finalized + {len(drafts)} draft record(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

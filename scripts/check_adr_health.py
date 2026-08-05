#!/usr/bin/env python3
"""Gate the two ADR-metadata defects that reached this repo silently.

Both were found by a sentinel audit rather than by any check, which is the
point of this script -- an audit runs when someone remembers to run it.

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

DECISIONS_DIR = Path(__file__).resolve().parents[1] / ".ai-state" / "decisions"
FINALIZED_GLOB = "[0-9][0-9][0-9]-*.md"
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---", re.DOTALL)


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
            finalized[identifier] = frontmatter

    for identifier, frontmatter in sorted(finalized.items()):
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

    if failures:
        print(f"ADR health check FAILED ({len(failures)} finding(s)):", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(f"ADR health OK — {len(finalized)} finalized + {len(drafts)} draft record(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

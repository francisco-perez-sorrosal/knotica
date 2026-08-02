"""Resolve real anchors against real rewrites -- no synthetic perturbation, no curve.

`measure_orphan_rate.py` had to synthesise rewrites and `measure_rewrite_severity.py`
had to interpolate a curve. With a vault clone in hand neither approximation is
needed: every (before, after) page pair is real, so anchors sampled from the
`before` text can be resolved against the `after` text by the shipped resolver.
This is the measurement both scripts were proxies for.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from measure_orphan_rate import (  # noqa: E402
    COMPLETE_ORPHAN_THRESHOLD,
    GUESS_THRESHOLD,
    HARD_ORPHAN_STATUSES,
    QUEUE_MEMBER_STATUSES,
    AnchorRecord,
    build_quotes,
    enclosing_heading,
    resolve_anchor,
)
from measure_rewrite_severity import git, is_content_page  # noqa: E402

MECHANICAL_PREFIXES = ("knotica(okf):", "fix(pages):")
REPO = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/vault-clone")


def events() -> list[tuple[str, str, str, str]]:
    """(subject, path, before_text, after_text) for every modified content page."""
    out = git(REPO, "log", "--first-parent", "--format=%H%x00%s", "--name-status", "--no-renames")
    sha = subject = ""
    found: list[tuple[str, str, str, str]] = []
    for line in out.splitlines():
        if "\x00" in line:
            sha, subject = line.split("\x00", 1)
            continue
        if not line or line[0] != "M":
            continue
        path = line.split("\t", 1)[1].strip() if "\t" in line else ""
        if not is_content_page(path):
            continue
        found.append(
            (
                subject,
                path,
                git(REPO, "show", f"{sha}~1:{path}"),
                git(REPO, "show", f"{sha}:{path}"),
            )
        )
    return found


def resolve_all(pairs: list[tuple[str, str, str, str]]) -> list[str]:
    statuses: list[str] = []
    for _subject, path, before, after in pairs:
        for _shape, span in build_quotes(before, cap=40, rng=None):
            anchor = AnchorRecord(
                page=path,
                heading=enclosing_heading(before, span[0]),
                fidelity="span",
                pinned_at="0" * 40,
                quote=before[span[0] : span[1]],
                start=span[0],
            )
            projection = resolve_anchor(
                before,
                after,
                anchor,
                guess_threshold=GUESS_THRESHOLD,
                complete_orphan_threshold=COMPLETE_ORPHAN_THRESHOLD,
            )
            statuses.append(
                f"{projection.status}@{projection.fidelity}"
                if projection.status == "orphaned"
                else projection.status
            )
    return statuses


def summarise(label: str, statuses: list[str]) -> None:
    if not statuses:
        print(f"\n{label}: no anchors")
        return
    total = len(statuses)
    orphan = sum(1 for s in statuses if s.split("@")[0] in HARD_ORPHAN_STATUSES)
    queue = sum(1 for s in statuses if s.split("@")[0] in QUEUE_MEMBER_STATUSES)
    print(f"\n{label}  (n={total} anchor resolutions)")
    for name, count in Counter(statuses).most_common():
        print(f"    {name:<22}{count:>5}{count / total:>8.1%}")
    print(f"    {'-> HARD ORPHAN':<22}{orphan:>5}{orphan / total:>8.1%}")
    print(f"    {'-> drift queue':<22}{queue:>5}{queue / total:>8.1%}")


def main() -> int:
    pairs = events()
    genuine = [p for p in pairs if not p[0].startswith(MECHANICAL_PREFIXES)]
    okf = [p for p in pairs if p[0].startswith("knotica(okf):")]
    reflow = [p for p in pairs if p[0].startswith("fix(pages):")]

    print(f"{len(pairs)} real page-rewrite events from the vault clone")
    print(f"  genuine knowledge rewrites : {len(genuine)}")
    print(f"  OKF repair migration       : {len(okf)}")
    print(f"  hard-wrap reflow migration : {len(reflow)}")

    summarise("GENUINE KNOWLEDGE REWRITES", resolve_all(genuine))
    summarise("OKF REPAIR MIGRATION", resolve_all(okf))
    summarise("HARD-WRAP REFLOW MIGRATION", resolve_all(reflow))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Measure the severity distribution of real page rewrites, from git history alone.

This is the free prior question that gates the notes overlay's billed spikes.
`scripts/measure_orphan_rate.py` established that the residual hard-orphan rate
is governed almost entirely by *how far a rewrite moves a page*, with a cliff
between page similarity 0.91 and 0.71:

    page similarity   1.000  0.997  0.964  0.907  0.713  0.403
    hard-orphan rate   0.0%   0.6%   2.5%  15.5%  83.9% 100.0%

What that measurement could not supply is where real rewrites actually fall on
that curve. This script reads it straight out of a vault's git history: for
every commit that *modified* an existing content page, it computes the page's
before/after similarity, then folds the observed distribution through the curve
above to produce an expected hard-orphan rate per rewrite event.

**Read-only and clone-only.** It runs `git log` / `git show` and nothing else --
no checkout, no fetch, no write, no lock. It refuses outright to run against any
vault named in `~/.config/knotica/config.toml`, because a loop service watches
the live vault and a content commit there costs billed eval spend. Clone first:

    git clone --no-hardlinks <live-vault> /tmp/vault-clone
    uv run --frozen python scripts/measure_rewrite_severity.py /tmp/vault-clone

Nothing here calls an LLM or an eval. The cost is disk and a few seconds.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knotica.core.records import parse_commit_subject  # noqa: E402
from knotica.core.vault_layout import RESERVED_TOP_LEVEL_NAMES, family_of  # noqa: E402

#: Measured (page similarity, hard-orphan rate) points from
#: `measure_orphan_rate.py --replicates`, means over 6 independent draws.
#: `section-restructure` is deliberately excluded: its elevated rate comes from
#: renaming headings (which removes the resolution ladder's structural
#: fallback), not from lexical distance, so including it would double-count an
#: effect this script reports separately.
ORPHAN_CURVE: tuple[tuple[float, float], ...] = (
    (0.403, 1.000),
    (0.713, 0.839),
    (0.907, 0.155),
    (0.964, 0.025),
    (0.997, 0.006),
    (1.000, 0.000),
)

#: Multiplier applied to a rewrite that also renames or removes headings.
#: Measured: 27.7% hard-orphan against 15.5% for the same 20% prose
#: perturbation without the heading change.
HEADING_CHANGE_PENALTY = 27.7 / 15.5

#: `dec-058` accepted this band as the price of declining block IDs.
DEC_058_ESTIMATE = (0.08, 0.20)

SEVERITY_BANDS: tuple[tuple[str, float], ...] = (
    ("untouched-ish  >=0.99", 0.99),
    ("light      0.95-0.99", 0.95),
    ("moderate   0.90-0.95", 0.90),
    ("substantial 0.75-0.90", 0.75),
    ("heavy      0.50-0.75", 0.50),
    ("near-total    <0.50", 0.0),
)


@dataclass(frozen=True)
class Rewrite:
    """One (commit, page) pair in which an existing content page changed."""

    sha: str
    op: str
    path: str
    similarity: float
    headings_changed: bool


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def configured_vault_paths() -> set[Path]:
    """Every vault path named in the user's knotica config -- all forbidden."""
    config = Path.home() / ".config" / "knotica" / "config.toml"
    if not config.is_file():
        return set()
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    vaults = data.get("vaults", {})
    paths: set[Path] = set()
    if isinstance(vaults, dict):
        for entry in vaults.values():
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                paths.add(Path(entry["path"]).expanduser().resolve())
    return paths


def refuse_live_vault(target: Path) -> None:
    """Abort unless `target` is demonstrably not a configured (live) vault."""
    forbidden = configured_vault_paths()
    if target in forbidden:
        raise SystemExit(
            f"refusing: {target} is a vault configured in ~/.config/knotica/config.toml.\n"
            "A loop service watches the live vault and a content commit there costs billed\n"
            "eval spend. Clone it first:\n"
            f"  git clone --no-hardlinks {target} /tmp/vault-clone"
        )


def is_content_page(path: str) -> bool:
    """Ordinary KB content pages only -- what notes predominantly anchor to.

    Excludes the `source` and `note` families via `family_of`, dot-prefixed
    trees (`.knotica/`), and the reserved bookkeeping files by *basename* --
    `family_of` reports `page` for both `index.md` and a per-topic `SCHEMA.md`,
    and neither is knowledge a note would anchor to.
    """
    if not path.endswith(".md") or path.startswith(".") or "/." in path:
        return False
    if PurePosixPath(path).name in RESERVED_TOP_LEVEL_NAMES:
        return False
    return family_of(path) == "page"


def headings_of(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.lstrip().startswith("#")]


def collect(repo: Path, limit: int | None) -> list[Rewrite]:
    """Every (commit, page) pair in HEAD's ancestry where a page was modified."""
    log_args = ["log", "--first-parent", "--format=%H%x00%s", "--name-status", "--no-renames"]
    if limit is not None:
        log_args.insert(1, f"--max-count={limit}")

    rewrites: list[Rewrite] = []
    sha = ""
    op = ""
    for line in git(repo, *log_args).splitlines():
        if "\x00" in line:
            sha, subject = line.split("\x00", 1)
            parsed = parse_commit_subject(subject)
            op = parsed.op if parsed is not None else "(non-knotica)"
            continue
        if not line or not line[0] == "M":
            continue
        path = line.split("\t", 1)[1].strip() if "\t" in line else ""
        if not is_content_page(path):
            continue
        try:
            before = git(repo, "show", f"{sha}~1:{path}")
            after = git(repo, "show", f"{sha}:{path}")
        except RuntimeError:
            continue
        rewrites.append(
            Rewrite(
                sha=sha[:12],
                op=op,
                path=path,
                similarity=SequenceMatcher(None, before, after, autojunk=False).ratio(),
                headings_changed=headings_of(before) != headings_of(after),
            )
        )
    return rewrites


def expected_orphan_rate(similarity: float) -> float:
    """Interpolate the measured orphan curve at one page similarity."""
    points = ORPHAN_CURVE
    if similarity <= points[0][0]:
        return points[0][1]
    if similarity >= points[-1][0]:
        return points[-1][1]
    index = bisect_left([x for x, _ in points], similarity)
    (x0, y0), (x1, y1) = points[index - 1], points[index]
    return y0 + (y1 - y0) * (similarity - x0) / (x1 - x0)


def band_of(similarity: float) -> str:
    for name, floor in SEVERITY_BANDS:
        if similarity >= floor:
            return name
    return SEVERITY_BANDS[-1][0]


def report(rewrites: list[Rewrite]) -> None:
    total = len(rewrites)
    print(f"\n{total} page-rewrite events (commit x modified content page)\n")

    print(f"{'severity band':<24}{'events':>9}{'share':>9}{'headings changed':>19}")
    print("-" * 61)
    counts = Counter(band_of(r.similarity) for r in rewrites)
    for name, _ in SEVERITY_BANDS:
        events = counts[name]
        if not events:
            continue
        renamed = sum(1 for r in rewrites if band_of(r.similarity) == name and r.headings_changed)
        print(f"{name:<24}{events:>9}{events / total:>8.1%}{renamed / events:>18.1%}")

    print(f"\n{'by operation':<24}{'events':>9}{'median similarity':>20}")
    print("-" * 53)
    for op, events in Counter(r.op for r in rewrites).most_common():
        sims = sorted(r.similarity for r in rewrites if r.op == op)
        print(f"{op:<24}{events:>9}{sims[len(sims) // 2]:>20.3f}")

    base = [expected_orphan_rate(r.similarity) for r in rewrites]
    adjusted = [
        min(1.0, rate * HEADING_CHANGE_PENALTY) if r.headings_changed else rate
        for rate, r in zip(base, rewrites, strict=True)
    ]
    plain = sum(base) / total
    with_headings = sum(adjusted) / total

    print("\n" + "=" * 61)
    print("EXPECTED RESIDUAL HARD-ORPHAN RATE")
    print("=" * 61)
    print("Observed severity distribution folded through the measured curve.")
    print("This is the number dec-058 estimated but never measured.\n")
    print(f"  lexical distance only          {plain:>7.1%}")
    print(f"  incl. heading-change penalty   {with_headings:>7.1%}")
    low, high = DEC_058_ESTIMATE
    print(f"\n  dec-058's accepted band        {low:>7.1%} - {high:.1%}")
    if with_headings < low:
        verdict = "BELOW the accepted band -- 3a/3b lose their justification."
    elif with_headings <= high:
        verdict = "WITHIN the accepted band -- dec-058's bet holds as written."
    else:
        verdict = "ABOVE the accepted band -- 3a/3b target a real, measured cost."
    print(f"\n  verdict: {verdict}")
    print("\nA rewrite event queues (anchors on that page) x this rate, so")
    print(
        f"dec-058 trigger (b) breaches above {1 / with_headings:.1f} anchors on a rewritten page."
    )


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__)
        print("usage: measure_rewrite_severity.py <clone-path> [--limit=N]")
        return 2

    # Refuse before touching the target at all -- not even a stat on a live vault.
    target = Path(args[0]).expanduser().resolve()
    refuse_live_vault(target)
    if not (target / ".git").exists():
        raise SystemExit(f"refusing: {target} is not a git repository")

    limit = next((int(a.split("=", 1)[1]) for a in sys.argv[1:] if a.startswith("--limit=")), None)
    print(f"reading {target} (read-only; git log/show only)")
    rewrites = collect(target, limit)
    if not rewrites:
        print("\nNo modified content pages found in this history.")
        print("If the vault is young, every page may still be an addition rather than a rewrite.")
        return 0
    report(rewrites)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

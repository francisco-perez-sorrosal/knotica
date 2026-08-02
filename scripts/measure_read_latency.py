"""Measure read-time anchor-resolution cost on a seeded, isolated vault.

`dec-058` accepted per-read resolution on the premise that it is cheap, and named
the measurement that would refute it: *"read-time resolution measured on a
realistic vault costs enough to be user-visible"*. That is the one falsifier
Phase 3 left open, and it is the whole justification for a persisted projection
index. This script answers it.

**No live vault can be reached.** This script takes no vault argument and never
opens an existing one -- it builds a throwaway vault under `tempfile.mkdtemp()`,
measures against it, and removes it. Contact with `~/dev/data/knotica` is
structurally impossible rather than merely guarded against, which is a stronger
guarantee than `measure_rewrite_severity.py`'s config-file refusal.

Three surfaces are timed, because they have genuinely different cost shapes:

- ``read_note``   -- the O(1) single-note read; the control.
- ``list_notes``  -- resolves every anchor in a topic once.
- *drift open*    -- what the MCP `notes read action=drift` path actually costs:
  ``list_notes`` for the members, then ``reconcile_notes`` (which calls
  ``list_notes`` a **second** time internally), then one historical resolution
  per queue member. Cost is O(topic), not O(page): the transitions are computed
  for every queue member regardless of the page the caller asked for.

Wall-clock alone would not say *what* to fix, so each measurement is decomposed
into git-subprocess time (counted by wrapping ``VaultVcs._run``) and everything
else, which is dominated by ``difflib.SequenceMatcher`` inside ``resolve_anchor``.
A cost that is process-spawn-bound and one that is comparison-bound call for
different remedies, and only one of them is an index.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knotica.core.notes.anchor import (  # noqa: E402
    AnchorRecord,
    NoteDocument,
    serialize_note,
)
from knotica.core.notes.reconcile import reconcile_notes  # noqa: E402
from knotica.core.notes.store import list_notes, read_note  # noqa: E402
from knotica.core.notes_config import (  # noqa: E402
    DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    DEFAULT_GUESS_THRESHOLD,
)
from knotica.core.vcs import VaultVcs  # noqa: E402
from knotica.store import LocalFSStore  # noqa: E402

TOPIC = "agentic-systems"

#: Phase 3 measured 15.3% drift-queue membership on ordinary knowledge rewrites
#: (`STEP1_ORPHAN_RATE.md` § "Measured on the real vault"). A seed that orphans
#: every anchor would overstate the drift-queue cost by ~6x, so the realistic
#: mix is the default and the pessimistic one is opt-in.
REALISTIC_QUEUE_FRACTION = 0.15

#: Vault-template KB content pages run 1.7-2.5KB; 2KB is the middle of that.
#: Paragraph count and sentence length are chosen to land there.
PARAGRAPHS_PER_PAGE = 8
SENTENCES_PER_PARAGRAPH = 3

#: What "user-visible" means, for the report's verdict. 200ms is the usual
#: interaction-latency bar for something that should feel immediate; 1s is where
#: a caller starts to experience it as waiting.
SNAPPY_BUDGET_SECONDS = 0.200
VISIBLE_BUDGET_SECONDS = 1.000


@dataclass
class GitCounter:
    """Accumulates git subprocess calls and their wall time."""

    calls: int = 0
    seconds: float = 0.0

    def reset(self) -> None:
        self.calls = 0
        self.seconds = 0.0


@dataclass
class Timing:
    """One timed surface, decomposed into git-subprocess and in-process cost."""

    label: str
    seconds: float
    git_calls: int
    git_seconds: float

    @property
    def cpu_seconds(self) -> float:
        return self.seconds - self.git_seconds

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "seconds": round(self.seconds, 4),
            "git_calls": self.git_calls,
            "git_seconds": round(self.git_seconds, 4),
            "cpu_seconds": round(self.cpu_seconds, 4),
        }


@dataclass
class Scenario:
    """One point in the sweep."""

    notes: int
    anchors_per_note: int
    queue_fraction: float
    timings: list[Timing] = field(default_factory=list)

    @property
    def anchors(self) -> int:
        return self.notes * self.anchors_per_note


def instrument_git(counter: GitCounter) -> None:
    """Wrap ``VaultVcs._run`` so every git subprocess is counted and timed.

    Patching the single chokepoint rather than ``subprocess.run`` globally keeps
    the count honest: it attributes exactly the git calls the vault layer makes,
    and nothing the harness itself runs while seeding.
    """
    original = VaultVcs._run

    def counted(self: VaultVcs, *args: object, **kwargs: object) -> object:
        start = time.perf_counter()
        try:
            return original(self, *args, **kwargs)  # type: ignore[arg-type]
        finally:
            counter.seconds += time.perf_counter() - start
            counter.calls += 1

    VaultVcs._run = counted  # type: ignore[method-assign, assignment]


def run_git(root: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")


def page_text(index: int, *, rewritten_paragraphs: frozenset[int] = frozenset()) -> str:
    """A KB content page of realistic size and shape.

    Paragraphs in ``rewritten_paragraphs`` are replaced with different prose, so
    an anchor quoting one of them stops matching verbatim and becomes a
    drift-queue member. Everything else is byte-identical across revisions, so
    those anchors resolve ``exact`` and are correctly skipped by the queue.
    """
    lines = [
        "---",
        "type: page",
        f"title: Seeded page {index}",
        "---",
        "",
        f"# Seeded page {index}",
        "",
    ]
    for paragraph in range(PARAGRAPHS_PER_PAGE):
        lines.append(f"## Section {paragraph}")
        lines.append("")
        if paragraph in rewritten_paragraphs:
            sentences = [
                f"Revised claim {paragraph}.{sentence} on page {index} states a "
                f"materially different position than the text it replaced, using "
                f"substantially altered vocabulary throughout the passage."
                for sentence in range(SENTENCES_PER_PARAGRAPH)
            ]
        else:
            sentences = [
                f"Original claim {paragraph}.{sentence} on page {index} records a "
                f"stable finding that the surrounding revision does not disturb, "
                f"phrased in the vocabulary the anchor pinned."
                for sentence in range(SENTENCES_PER_PARAGRAPH)
            ]
        lines.append(" ".join(sentences))
        lines.append("")
    return "\n".join(lines)


def anchored_quote(page_body: str, paragraph: int) -> str:
    """The first sentence of ``paragraph`` -- a whole-sentence anchor quote.

    Whole-sentence is the shape `dec-062`'s geometry fix made representative:
    Phase 3 measured hard-orphan rate flat across quote shapes on KB pages, so
    the shape axis is retired and one shape suffices.
    """
    marker = f"## Section {paragraph}\n\n"
    start = page_body.index(marker) + len(marker)
    end = page_body.index(".", start) + 1
    return page_body[start:end]


def seed_vault(root: Path, scenario: Scenario) -> None:
    """Build a vault whose anchors land in the requested status mix.

    Three commits, which is the minimum that makes the drift queue meaningful:
    pages at v1, then the notes, then a rewrite. ``path_commit_shas(page,
    limit=2)`` needs two commits touching a page before it will report a
    transition, and only rewritten pages get them -- untouched pages carry one
    commit and hold no queue members, which is exactly the real shape.
    """
    pages = max(1, scenario.notes // 4)
    (root / TOPIC).mkdir(parents=True, exist_ok=True)
    (root / "notes" / TOPIC).mkdir(parents=True, exist_ok=True)

    for page in range(pages):
        (root / TOPIC / f"page-{page}.md").write_text(page_text(page), encoding="utf-8")
    run_git(root, "init", "-q")
    run_git(root, "config", "user.name", "knotica-measure")
    run_git(root, "config", "user.email", "measure@knotica.invalid")
    run_git(root, "config", "commit.gpgsign", "false")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "vault: seed pages")
    pinned_at = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()

    # Which (page, paragraph) pairs get rewritten -- the queue members. Chosen by
    # a deterministic stride rather than a random sample so a re-run is
    # comparable, and so the fraction is exact rather than approximate.
    total_anchors = scenario.anchors
    queue_target = round(total_anchors * scenario.queue_fraction)
    stride = total_anchors / queue_target if queue_target else float("inf")

    rewritten: dict[int, set[int]] = {}
    anchor_slots: list[tuple[int, int]] = []
    for slot in range(total_anchors):
        page = slot % pages
        paragraph = (slot // pages) % PARAGRAPHS_PER_PAGE
        anchor_slots.append((page, paragraph))
        if queue_target and slot < queue_target * stride and int(slot % stride) == 0:
            rewritten.setdefault(page, set()).add(paragraph)

    for note_index in range(scenario.notes):
        anchors = []
        for anchor_index in range(scenario.anchors_per_note):
            page, paragraph = anchor_slots[note_index * scenario.anchors_per_note + anchor_index]
            body = page_text(page)
            anchors.append(
                AnchorRecord(
                    page=f"{TOPIC}/page-{page}.md",
                    heading=f"Section {paragraph}",
                    fidelity="span",
                    pinned_at=pinned_at,
                    quote=anchored_quote(body, paragraph),
                    start=body.index(anchored_quote(body, paragraph)),
                )
            )
        note_id = f"20260101-{note_index:06d}-seeded-note"
        document = NoteDocument(
            id=note_id,
            topic=TOPIC,
            intent="reflection",
            created="2026-01-01T09:00:00Z",
            updated="2026-01-01T09:00:00Z",
            status="active",
            tags=(),
            body=f"Seeded note {note_index}.",
            anchors=tuple(anchors),
        )
        (root / "notes" / TOPIC / f"{note_id}.md").write_text(
            serialize_note(document), encoding="utf-8"
        )
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "notes: seed notes")

    for page, paragraphs in rewritten.items():
        (root / TOPIC / f"page-{page}.md").write_text(
            page_text(page, rewritten_paragraphs=frozenset(paragraphs)), encoding="utf-8"
        )
    if rewritten:
        run_git(root, "add", "-A")
        run_git(root, "commit", "-q", "-m", "pages: rewrite anchored passages")


def time_surface(label: str, counter: GitCounter, operation: object) -> Timing:
    counter.reset()
    start = time.perf_counter()
    operation()  # type: ignore[operator]
    elapsed = time.perf_counter() - start
    return Timing(
        label=label, seconds=elapsed, git_calls=counter.calls, git_seconds=counter.seconds
    )


def measure(scenario: Scenario, counter: GitCounter) -> Scenario:
    root = Path(tempfile.mkdtemp(prefix="knotica-latency-"))
    try:
        seed_vault(root, scenario)
        store = LocalFSStore(root)
        vcs = VaultVcs(root)
        guess = DEFAULT_GUESS_THRESHOLD
        orphan = DEFAULT_COMPLETE_ORPHAN_THRESHOLD

        listing = list_notes(
            store, vcs, TOPIC, guess_threshold=guess, complete_orphan_threshold=orphan
        )
        statuses = [
            projection.status
            for note in listing.notes
            for _anchor, projection in note.resolved_anchors
        ]
        queue_members = sum(
            1 for status in statuses if status in {"fuzzy", "orphaned", "anchor-invalid"}
        )
        first_note_id = listing.notes[0].document.id if listing.notes else ""

        scenario.timings.append(
            time_surface(
                "read_note",
                counter,
                lambda: read_note(
                    store,
                    vcs,
                    TOPIC,
                    first_note_id,
                    guess_threshold=guess,
                    complete_orphan_threshold=orphan,
                ),
            )
        )
        scenario.timings.append(
            time_surface(
                "list_notes",
                counter,
                lambda: list_notes(
                    store, vcs, TOPIC, guess_threshold=guess, complete_orphan_threshold=orphan
                ),
            )
        )
        scenario.timings.append(
            time_surface(
                "drift_open",
                counter,
                lambda: (
                    list_notes(
                        store, vcs, TOPIC, guess_threshold=guess, complete_orphan_threshold=orphan
                    ),
                    reconcile_notes(
                        store, vcs, TOPIC, guess_threshold=guess, complete_orphan_threshold=orphan
                    ),
                ),
            )
        )
        scenario.measured_queue_members = queue_members  # type: ignore[attr-defined]
        scenario.measured_anchors = len(statuses)  # type: ignore[attr-defined]
        return scenario
    finally:
        shutil.rmtree(root, ignore_errors=True)


def report(scenarios: list[Scenario]) -> None:
    print()
    print("Read-time resolution cost -- seeded isolated vault, never the live one")
    print()
    header = (
        f"{'notes':>6} {'anch/note':>10} {'anchors':>8} {'queue':>7} "
        f"{'surface':<12} {'wall(s)':>9} {'git(s)':>8} {'cpu(s)':>8} {'git calls':>10}"
    )
    print(header)
    print("-" * len(header))
    for scenario in scenarios:
        queue = getattr(scenario, "measured_queue_members", 0)
        for index, timing in enumerate(scenario.timings):
            prefix = (
                f"{scenario.notes:>6} {scenario.anchors_per_note:>10} "
                f"{scenario.anchors:>8} {queue:>7} "
                if index == 0
                else " " * 35
            )
            print(
                f"{prefix}{timing.label:<12} {timing.seconds:>9.3f} "
                f"{timing.git_seconds:>8.3f} {timing.cpu_seconds:>8.3f} {timing.git_calls:>10}"
            )
    print()

    drift = [
        (scenario, timing)
        for scenario in scenarios
        for timing in scenario.timings
        if timing.label == "drift_open"
    ]
    print("Verdict -- drift-queue open against the interaction budgets")
    for scenario, timing in drift:
        verdict = (
            "snappy"
            if timing.seconds < SNAPPY_BUDGET_SECONDS
            else "perceptible"
            if timing.seconds < VISIBLE_BUDGET_SECONDS
            else "USER-VISIBLE"
        )
        share = timing.git_seconds / timing.seconds if timing.seconds else 0.0
        print(
            f"  {scenario.notes:>4} notes x {scenario.anchors_per_note} anchors "
            f"(queue {scenario.queue_fraction:.0%}): {timing.seconds:>7.3f}s  "
            f"{verdict:<12} git={share:.0%} of wall"
        )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--notes",
        type=int,
        nargs="+",
        default=[10, 25, 50, 100, 200],
        help="note counts to sweep",
    )
    parser.add_argument(
        "--anchors-per-note", type=int, nargs="+", default=[1, 3], help="anchors per note"
    )
    parser.add_argument(
        "--queue-fraction",
        type=float,
        nargs="+",
        default=[REALISTIC_QUEUE_FRACTION],
        help="fraction of anchors that are drift-queue members",
    )
    parser.add_argument("--json", type=Path, default=None, help="also write raw results here")
    args = parser.parse_args()

    counter = GitCounter()
    instrument_git(counter)

    scenarios: list[Scenario] = []
    for queue_fraction in args.queue_fraction:
        for anchors_per_note in args.anchors_per_note:
            for notes in args.notes:
                scenario = Scenario(
                    notes=notes,
                    anchors_per_note=anchors_per_note,
                    queue_fraction=queue_fraction,
                )
                print(
                    f"measuring {notes} notes x {anchors_per_note} anchors "
                    f"(queue {queue_fraction:.0%}) ...",
                    file=sys.stderr,
                )
                scenarios.append(measure(scenario, counter))

    report(scenarios)

    if args.json:
        args.json.write_text(
            json.dumps(
                [
                    {
                        "notes": scenario.notes,
                        "anchors_per_note": scenario.anchors_per_note,
                        "anchors": scenario.anchors,
                        "queue_fraction": scenario.queue_fraction,
                        "measured_queue_members": getattr(scenario, "measured_queue_members", 0),
                        "timings": [timing.as_dict() for timing in scenario.timings],
                    }
                    for scenario in scenarios
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

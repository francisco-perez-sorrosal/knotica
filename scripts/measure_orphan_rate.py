"""Residual orphan-rate measurement for the notes anchor recovery ladder.

Measures, on this project's own KB corpus and at the shipped thresholds, the
distribution of anchor projection outcomes across a matrix of quote shapes and
page-rewrite classes -- the number `dec-058` assumed (8-20% hard orphans) but
never measured, re-measured after the `dec-062` geometry fix.

Design notes, stated because they bound what the result means:

* **No vault is touched.** `resolve_anchor` is a pure function of
  (historical_text, head_text, anchor, two thresholds). Driving it directly on
  real page text is strictly more precise than round-tripping through a seeded
  vault, and it makes contact with the live vault structurally impossible.
  Queue membership is likewise a pure predicate over status, lifted verbatim
  from `reconcile._QUEUE_MEMBER_STATUSES`.

* **Rewrites are synthesised at controlled lexical distance.** A real loop
  rewrite is LLM-authored and costs billed spend. The resolver, however, is
  *purely lexical* -- `difflib.SequenceMatcher` plus page-rarest-word seeding --
  so it cannot distinguish a semantic paraphrase from a synthetic perturbation
  at equal lexical distance. Lexical distance is therefore the only property of
  a rewrite the measurement needs to reproduce faithfully. Substitution words
  are drawn from the corpus's own vocabulary so page word-frequency statistics
  (which drive rarest-word seeding) stay realistic.

* **The mix is not asserted.** Results are reported per rewrite class. Any
  aggregate depends on how often each class occurs in real loop output, which
  is unmeasured; the report inverts the question instead.
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass
from statistics import mean
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]

from knotica.core.notes.anchor import AnchorRecord  # noqa: E402
from knotica.core.notes.resolve import Projection, resolve_anchor  # noqa: E402

GUESS_THRESHOLD = 0.75
COMPLETE_ORPHAN_THRESHOLD = 0.35

# Lifted from reconcile._QUEUE_MEMBER_STATUSES -- the statuses that put an
# anchor in the drift review queue. Note that `fuzzy` is a member: an
# auto-placed anchor is still reported as having moved.
QUEUE_MEMBER_STATUSES = frozenset({"fuzzy", "orphaned", "anchor-invalid"})

# Statuses needing a human decision. Narrower than queue membership: `fuzzy`
# already carries a placement, so it is a notification, not a task.
HARD_ORPHAN_STATUSES = frozenset({"orphaned"})

SEED = 20260731

#: Per-page anchor cap. Keeps the one raw source page from supplying 95% of the
#: sample; see `build_quotes`.
QUOTES_PER_PAGE_CAP = 48

#: The raw source document. Real, and anchorable, but 54KB of unedited source
#: text is not what notes predominantly point at, so headline figures exclude it.
SOURCE_PAGE = "wang2024awm.md"

#: Independent perturbation draws used by ``--replicates``.
REPLICATE_DRAWS = 6
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
HEADING_RE = re.compile(r"^#{1,6}[ \t]+", re.MULTILINE)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Outcome:
    page: str
    shape: str
    anchor: int
    rewrite: str
    status: str
    fidelity: str | None
    score: float | None
    measured: bool

    @property
    def bucket(self) -> str:
        """The reporting bucket: status, split by fidelity where it matters."""
        if self.status == "orphaned" and self.fidelity in {"section", "page"}:
            return f"orphaned@{self.fidelity}"
        return self.status


def load_pages(root: Path) -> dict[str, str]:
    """The corpus: every KB content page in the vault template."""
    topic_dir = root / "vault-template" / "agentic-systems"
    pages = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(topic_dir.glob("*.md"))
        if path.name != "SCHEMA.md"
    }
    source = root / "vault-template" / "sources" / "agentic-systems" / "wang2024awm.md"
    pages[source.name] = source.read_text(encoding="utf-8")
    return pages


def prose_spans(text: str) -> list[tuple[int, int]]:
    """Spans of body prose -- what a client could plausibly quote.

    Excludes the frontmatter block, heading lines, and the template's own
    demo-sample callout (a banner, not knowledge). Bullet bodies are included:
    a claim bullet is the single most likely thing a reader highlights.
    """
    body_start = 0
    frontmatter = FRONTMATTER_RE.match(text)
    if frontmatter is not None:
        body_start = frontmatter.end()

    spans: list[tuple[int, int]] = []
    for match in re.finditer(
        r"^(?![#>\s]).+(?:\n(?![#>\s\n]).+)*", text[body_start:], re.MULTILINE
    ):
        start, end = match.start() + body_start, match.end() + body_start
        if end - start >= 60:
            spans.append((start, end))
    return spans


def sentence_spans_within(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Sentence spans inside one prose block, as absolute page offsets."""
    block = text[start:end]
    spans: list[tuple[int, int]] = []
    cursor = 0
    for piece in SENTENCE_SPLIT_RE.split(block):
        if not piece:
            continue
        offset = block.index(piece, cursor)
        spans.append((start + offset, start + offset + len(piece)))
        cursor = offset + len(piece)
    return spans


def build_quotes(
    text: str, cap: int | None = None, rng: random.Random | None = None
) -> list[tuple[str, tuple[int, int]]]:
    """(shape, span) pairs -- the four shapes `dec-062` proved decisive.

    ``cap`` stratifies the sample: without it the 54KB raw source page supplies
    18x more anchors than every curated KB page combined and silently becomes
    the entire measurement. Sampling is stratified *within* shape so capping
    cannot skew the shape mix, which is the axis under study.
    """
    quotes: list[tuple[str, tuple[int, int]]] = []
    for block_start, block_end in prose_spans(text):
        sentences = sentence_spans_within(text, block_start, block_end)
        for index, (s_start, s_end) in enumerate(sentences):
            if s_end - s_start < 40:
                continue
            quotes.append(("whole-sentence", (s_start, s_end)))

            words = list(WORD_RE.finditer(text, s_start, s_end))
            if len(words) >= 8:
                clause_words = words[2 : 2 + max(4, len(words) // 2)]
                quotes.append(("sub-clause", (clause_words[0].start(), clause_words[-1].end())))

            if index + 1 < len(sentences):
                quotes.append(("two-sentence", (s_start, sentences[index + 1][1])))
            if index + 2 < len(sentences):
                quotes.append(("three-sentence", (s_start, sentences[index + 2][1])))

    if cap is None or rng is None or len(quotes) <= cap:
        return quotes

    by_shape: dict[str, list[tuple[str, tuple[int, int]]]] = {}
    for entry in quotes:
        by_shape.setdefault(entry[0], []).append(entry)
    per_shape = max(1, cap // len(by_shape))
    sampled: list[tuple[str, tuple[int, int]]] = []
    for shape in sorted(by_shape):
        pool = by_shape[shape]
        sampled.extend(pool if len(pool) <= per_shape else rng.sample(pool, per_shape))
    return sampled


def corpus_vocabulary(pages: dict[str, str]) -> list[str]:
    """Substitution pool: real words from the corpus, so frequency stays sane."""
    counter: Counter[str] = Counter()
    for text in pages.values():
        counter.update(WORD_RE.findall(text))
    return sorted(word for word, count in counter.items() if count >= 2 and len(word) > 3)


def perturb(text: str, probability: float, vocabulary: list[str], rng: random.Random) -> str:
    """Substitute `probability` of body words with other real corpus words.

    Frontmatter and heading lines are left alone: the loop rewrites prose, and
    an anchor never points into frontmatter.
    """
    if probability <= 0:
        return text
    body_start = 0
    frontmatter = FRONTMATTER_RE.match(text)
    if frontmatter is not None:
        body_start = frontmatter.end()

    head, body = text[:body_start], text[body_start:]
    heading_lines = {match.start() for match in HEADING_RE.finditer(body)}

    def line_is_heading(offset: int) -> bool:
        line_start = body.rfind("\n", 0, offset) + 1
        return line_start in heading_lines

    pieces: list[str] = []
    cursor = 0
    for match in WORD_RE.finditer(body):
        if line_is_heading(match.start()) or rng.random() >= probability:
            continue
        pieces.append(body[cursor : match.start()])
        pieces.append(rng.choice(vocabulary))
        cursor = match.end()
    pieces.append(body[cursor:])
    return head + "".join(pieces)


def reorder_sentences(text: str, rng: random.Random) -> str:
    """Shuffle sentence order inside each prose block -- paraphrase restructure."""
    out = text
    for start, end in reversed(prose_spans(text)):
        block = text[start:end]
        sentences = [piece for piece in SENTENCE_SPLIT_RE.split(block) if piece]
        if len(sentences) < 2:
            continue
        shuffled = sentences[:]
        rng.shuffle(shuffled)
        out = out[:start] + " ".join(shuffled) + out[end:]
    return out


def rename_headings(text: str) -> str:
    return re.sub(r"^(#{1,6}[ \t]+)(.*)$", r"\1On \2, revised", text, flags=re.MULTILINE)


def insert_paragraph(text: str) -> str:
    frontmatter = FRONTMATTER_RE.match(text)
    at = frontmatter.end() if frontmatter is not None else 0
    added = (
        "\nThis section was expanded during a maintenance pass to record an "
        "additional consideration that the earlier revision omitted entirely.\n"
    )
    return text[:at] + added + text[at:]


def delete_block_containing(text: str, span: tuple[int, int]) -> str:
    for start, end in prose_spans(text):
        if start <= span[0] and span[1] <= end:
            return text[:start] + text[end:]
    return text


def rewrite_variants(text: str, vocabulary: list[str], rng: random.Random) -> dict[str, str]:
    """Page-level rewrite classes, ordered by increasing lexical distance."""
    return {
        "untouched": text,
        "insertion-elsewhere": insert_paragraph(text),
        "typo": perturb(text, 0.01, vocabulary, rng),
        "light-copyedit": perturb(text, 0.05, vocabulary, rng),
        "moderate-rewording": perturb(text, 0.20, vocabulary, rng),
        "heavy-paraphrase": reorder_sentences(perturb(text, 0.50, vocabulary, rng), rng),
        "total-rewrite": reorder_sentences(perturb(text, 1.0, vocabulary, rng), rng),
        "section-restructure": rename_headings(perturb(text, 0.20, vocabulary, rng)),
    }


def enclosing_heading(text: str, offset: int) -> str:
    heading = ""
    for match in re.finditer(r"^#{1,6}[ \t]+(.*)$", text, re.MULTILINE):
        if match.start() > offset:
            break
        heading = match.group(1).strip()
    return heading


def measure(root: Path) -> list[Outcome]:
    pages = load_pages(root)
    vocabulary = corpus_vocabulary(pages)
    rng = random.Random(SEED)
    outcomes: list[Outcome] = []

    for page_name, text in pages.items():
        variants = rewrite_variants(text, vocabulary, rng)
        for index, (shape, span) in enumerate(build_quotes(text, cap=QUOTES_PER_PAGE_CAP, rng=rng)):
            quote = text[span[0] : span[1]]
            anchor = AnchorRecord(
                page=f"agentic-systems/{page_name}",
                heading=enclosing_heading(text, span[0]),
                fidelity="span",
                pinned_at="0" * 40,
                quote=quote,
                start=span[0],
            )
            variants_for_anchor = dict(variants)
            variants_for_anchor["paragraph-deletion"] = delete_block_containing(text, span)
            for rewrite_name, head_text in variants_for_anchor.items():
                projection: Projection = resolve_anchor(
                    text,
                    head_text,
                    anchor,
                    guess_threshold=GUESS_THRESHOLD,
                    complete_orphan_threshold=COMPLETE_ORPHAN_THRESHOLD,
                )
                outcomes.append(
                    Outcome(
                        page=page_name,
                        shape=shape,
                        anchor=index,
                        rewrite=rewrite_name,
                        status=projection.status,
                        fidelity=projection.fidelity,
                        score=projection.score,
                        measured=projection.score_measured,
                    )
                )
    return outcomes


BUCKET_ORDER = [
    "exact",
    "shifted",
    "fuzzy",
    "orphaned@section",
    "orphaned@page",
    "orphaned",
    "anchor-invalid",
    "unanchored",
]


def rate(subset: list[Outcome], statuses: frozenset[str]) -> float:
    if not subset:
        return 0.0
    return sum(1 for outcome in subset if outcome.status in statuses) / len(subset)


def table(title: str, key, outcomes: list[Outcome]) -> None:
    groups: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        groups.setdefault(key(outcome), []).append(outcome)

    buckets = [b for b in BUCKET_ORDER if any(o.bucket == b for o in outcomes)]
    header = f"{title:<22}" + "".join(f"{b:>18}" for b in buckets)
    header += f"{'queue%':>9}{'orphan%':>9}{'n':>7}"
    print("\n" + header)
    print("-" * len(header))
    for name, subset in groups.items():
        counts = Counter(o.bucket for o in subset)
        row = f"{name:<22}"
        for bucket in buckets:
            share = counts[bucket] / len(subset)
            row += f"{counts[bucket]:>10} {share:>6.1%}"
        row += f"{rate(subset, QUEUE_MEMBER_STATUSES):>8.1%}"
        row += f"{rate(subset, HARD_ORPHAN_STATUSES):>9.1%}"
        row += f"{len(subset):>7}"
        print(row)


def replicate_report() -> None:
    """Re-run the KB-page matrix over independent rewrite draws.

    A single run applies one perturbation per (page, class), so the anchors
    sharing a page also share one rewrite realization and are not independent:
    the effective n per class is nearer the page count than the anchor count.
    This reports the spread across draws so a real effect is distinguishable
    from a lucky draw.
    """
    pages = {name: text for name, text in load_pages(ROOT).items() if name != SOURCE_PAGE}
    vocabulary = corpus_vocabulary(load_pages(ROOT))

    draws: list[dict[str, list[str]]] = []
    for index in range(REPLICATE_DRAWS):
        rng = random.Random(1000 + index)
        statuses: dict[str, list[str]] = {}
        for page_name, text in pages.items():
            variants = rewrite_variants(text, vocabulary, rng)
            for shape, span in build_quotes(text, cap=QUOTES_PER_PAGE_CAP, rng=rng):
                anchor = AnchorRecord(
                    page=f"agentic-systems/{page_name}",
                    heading=enclosing_heading(text, span[0]),
                    fidelity="span",
                    pinned_at="0" * 40,
                    quote=text[span[0] : span[1]],
                    start=span[0],
                )
                per_anchor = dict(variants)
                per_anchor["paragraph-deletion"] = delete_block_containing(text, span)
                for name, head_text in per_anchor.items():
                    projection = resolve_anchor(
                        text,
                        head_text,
                        anchor,
                        guess_threshold=GUESS_THRESHOLD,
                        complete_orphan_threshold=COMPLETE_ORPHAN_THRESHOLD,
                    )
                    statuses.setdefault(name, []).append(projection.status)
        draws.append(statuses)

    print(
        f"\n{len(pages)} KB pages, {len(draws[0]['untouched'])} anchors, "
        f"{REPLICATE_DRAWS} independent rewrite draws"
    )
    for title, wanted in (
        ("HARD-ORPHAN rate (needs a human decision)", HARD_ORPHAN_STATUSES),
        ("DRIFT-QUEUE rate (fuzzy + orphaned + anchor-invalid)", QUEUE_MEMBER_STATUSES),
    ):
        print(f"\n{title}")
        print(f"{'rewrite class':<22}{'mean':>9}{'min':>9}{'max':>9}")
        print("-" * 49)
        for name in draws[0]:
            rates = [
                sum(1 for status in draw[name] if status in wanted) / len(draw[name])
                for draw in draws
            ]
            print(f"{name:<22}{mean(rates):>8.1%}{min(rates):>9.1%}{max(rates):>9.1%}")


def main() -> int:
    if "--replicates" in sys.argv:
        replicate_report()
        return 0

    outcomes = measure(ROOT)

    control = [o for o in outcomes if o.rewrite == "untouched"]
    bad = [o for o in control if o.status != "exact"]
    invalid = [o for o in outcomes if o.status == "anchor-invalid"]
    print(
        f"corpus: {len({o.page for o in outcomes})} pages, "
        f"{len({(o.page, o.anchor) for o in outcomes})} distinct anchors, "
        f"{len(outcomes)} resolutions"
    )
    print(f"self-check | untouched-page anchors not resolving `exact`: {len(bad)} (must be 0)")
    print(f"self-check | anchor-invalid outcomes: {len(invalid)} (must be 0)")

    table("by rewrite class", lambda o: o.rewrite, outcomes)
    table("by quote shape", lambda o: o.shape, outcomes)
    table("by page", lambda o: o.page, outcomes)

    kb = [o for o in outcomes if o.page != SOURCE_PAGE]
    table("KB pages only", lambda o: o.rewrite, kb)

    print("\n" + "=" * 74)
    print("dec-058 reversal trigger (b): >1 queue item per rewrite event per topic")
    print("=" * 74)
    print("A rewrite event queues (anchors on that page) x (queue rate), so the")
    print("trigger breaches only above a threshold anchor density:\n")
    print(f"{'rewrite class':<24}{'queue rate':>12}{'anchors/page to breach':>26}")
    print("-" * 62)
    for name in dict.fromkeys(o.rewrite for o in outcomes):
        subset = [o for o in outcomes if o.rewrite == name]
        queue = rate(subset, QUEUE_MEMBER_STATUSES)
        need = "never" if queue == 0 else f"{1 / queue:.1f}"
        print(f"{name:<24}{queue:>11.1%}{need:>26}")

    dump = Path(__file__).with_suffix(".json")
    dump.write_text(
        json.dumps([outcome.__dict__ for outcome in outcomes], indent=1), encoding="utf-8"
    )
    print(f"\nraw outcomes -> {dump}")
    return 1 if bad or invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())

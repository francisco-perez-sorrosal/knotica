"""Ripgrep-backed ``SearchBackend`` with BM25 ranking and a pure-Python fallback.

``RipgrepBackend`` shells out to ``rg`` for fast candidate selection when
ripgrep is on ``PATH`` and falls back to a pure-Python file walk when it is
not. **The protocol hides which one ran** -- the engines only decide *which
files might match* (whitespace-split terms, OR'd, case-insensitive literal
substrings); term counting, snippet extraction, and BM25 scoring happen in one
shared Python pass over the candidate files, so callers see identical results
either way.

Ranking is Okapi BM25 (k1=1.2, b=0.75, Lucene's non-negative idf variant)
computed live per query -- no index, no cache. Term frequencies saturate via
``k1`` (the 50th occurrence adds almost nothing), document length normalizes
via ``b`` (a term hit in a short page outweighs the same hit in a huge stored
source), and ubiquitous terms are discounted by inverse document frequency (a
term present in every file contributes ~0, so question glue cannot dominate).
Document length and the corpus average use file byte size -- a stat-only proxy
that avoids reading unmatched files.

Performance note on the fallback: the Python walk reads every markdown file
in scope on each call -- O(total vault bytes) per search, roughly one to two
orders of magnitude slower than ripgrep's parallel scan. Acceptable at MVP
vault scale (hundreds of pages); installing ripgrep is the fix for large
vaults.

Scope rules (identical in both engines): only ``*.md`` files; dot-folders and
dot-files (``.knotica/``, ``.git/``, ``.obsidian/``, ...) are skipped; stored
sources under ``sources/<topic>/`` ARE searched and marked ``kind="source"``.
Ripgrep runs with ``--no-config --no-ignore`` so user ripgrep configs and
vault ``.gitignore`` files cannot make its file set diverge from the fallback's
walk, and with an explicit ``--glob '!**/.*'`` so hidden files and dot-folders
are excluded regardless of ripgrep's version-dependent hidden-path defaults --
matching the fallback's own dot-skipping walk exactly.

Read-only boundary: this module never writes, locks, or touches git.
"""

import math
import os
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePath, PurePosixPath

from knotica.search import (
    DEFAULT_PAGE_SIZE,
    ResultKind,
    SearchPage,
    SearchResult,
    clamp_limit,
    paginate,
    resolve_offset,
)

#: Snippets are decision material ("do I read_page this?"), not payloads.
SNIPPET_MAX_CHARS = 200

_SOURCES_DIR = "sources"
_MARKDOWN_SUFFIX = ".md"

#: Ripgrep exclusion glob for every hidden path -- dot-files at any depth and
#: anything under a dot-folder. Made explicit (not left to ripgrep's default
#: hidden-path handling, which ``--no-ignore`` disables on ripgrep 15+) so the
#: rg file set matches the pure-Python fallback's dot-skipping walk exactly.
_HIDDEN_EXCLUDE_GLOB = "!**/.*"

#: rg exit codes that mean the scan itself succeeded (0 = matches, 1 = none).
_RG_OK_EXIT_CODES = frozenset({0, 1})

#: BM25 term-frequency saturation: how fast repeated occurrences stop adding
#: score. The IR-standard default; raise only if long documents should keep
#: earning score from repetition.
_BM25_K1 = 1.2

#: BM25 length normalization: 0 = none (raw-count behavior, which let the
#: largest stored sources dominate every ranking), 1 = full. The IR-standard
#: default balances the two.
_BM25_B = 0.75

#: Scores are rounded for stable, readable envelopes; ranking ties introduced
#: by rounding fall back to the deterministic path-ascending tie-break. Four
#: decimals keeps genuinely distinct scores distinct even on tiny corpora,
#: where near-ubiquitous terms make every idf (and thus every score) small.
_SCORE_PRECISION = 4


class _DocMatch:
    """Per-candidate accumulation: per-term occurrence counts + snippet + length."""

    __slots__ = ("rel_path", "term_counts", "snippet", "byte_length")

    def __init__(
        self, rel_path: str, term_counts: dict[str, int], snippet: str, byte_length: int
    ) -> None:
        self.rel_path = rel_path
        self.term_counts = term_counts
        self.snippet = snippet
        self.byte_length = byte_length


class RipgrepBackend:
    """Full-text vault search implementing the ``SearchBackend`` protocol.

    Args:
        root: The vault root directory (e.g. ``LocalFSStore.root``). Resolved
            at construction; must already exist and be a directory.

    Raises:
        NotADirectoryError: If ``root`` does not exist or is not a directory.
    """

    def __init__(self, root: str | PurePath) -> None:
        resolved = Path(root).resolve()
        if not resolved.is_dir():
            raise NotADirectoryError(f"Vault root is not an existing directory: {root}")
        self._root = resolved
        self._rg_path = shutil.which("rg")

    def search(
        self,
        query: str,
        *,
        topic: str = "",
        cursor: str = "",
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> SearchPage:
        """Search the vault and return one page of pointer results.

        See :class:`~knotica.search.SearchBackend` for the full contract.
        The cursor is resolved (and validated) before any scanning happens,
        so a bad token fails fast without paying the scan cost.
        """
        offset = resolve_offset(cursor, query)
        page_size = clamp_limit(limit)
        terms = query.split()
        scan_dirs = self._scope_dirs(topic)
        if not terms or not scan_dirs:
            return paginate((), query, offset, page_size)
        if self._rg_path is not None:
            candidates = self._candidates_ripgrep(terms, scan_dirs)
        else:
            candidates = list(_walk_markdown_files(scan_dirs))
        doc_count, average_bytes = _corpus_stats(scan_dirs)
        matches = _collect_matches(candidates, terms, self._root)
        results = _score_bm25(matches, terms, doc_count, average_bytes)
        ranked = sorted(results, key=lambda result: (-result.score, result.path))
        return paginate(ranked, query, offset, page_size)

    def _scope_dirs(self, topic: str) -> list[Path]:
        """Return the directories one search scans, given a topic scope.

        Empty topic scans the whole vault root (which covers ``sources/``
        too). A named topic scans the topic directory *and* its stored
        sources (``sources/<topic>/``) -- topic scope filters by the result's
        ``topic`` attribute, and sources carry the topic. A topic with no
        existing directory scans nothing (zero results; the adapter owns the
        ``TOPIC_NOT_FOUND`` decision, since it knows the valid topic list).
        """
        if not topic:
            return [self._root]
        candidate = PurePath(topic)
        if candidate.is_absolute() or len(candidate.parts) != 1 or topic.startswith("."):
            raise ValueError(f"Topic must be a bare, non-hidden directory name, got: {topic!r}")
        scoped = [self._root / topic, self._root / _SOURCES_DIR / topic]
        return [directory for directory in scoped if directory.is_dir()]

    def _candidates_ripgrep(self, terms: list[str], scan_dirs: list[Path]) -> list[Path]:
        """List candidate files with ``rg --files-with-matches``.

        Candidate selection only -- counting and scoring happen in the shared
        Python pass, so both engines produce identical envelopes.
        """
        command = [
            self._rg_path or "rg",
            "--files-with-matches",
            "--no-config",
            "--no-ignore",
            "--ignore-case",
            "--fixed-strings",
            "--glob",
            f"*{_MARKDOWN_SUFFIX}",
            "--glob",
            _HIDDEN_EXCLUDE_GLOB,
        ]
        for term in terms:
            command.extend(["-e", term])
        command.append("--")
        command.extend(str(directory) for directory in scan_dirs)
        completed = subprocess.run(command, capture_output=True, encoding="utf-8", check=False)
        if completed.returncode not in _RG_OK_EXIT_CODES:
            raise RuntimeError(
                f"ripgrep failed (exit {completed.returncode}): {completed.stderr.strip()}"
            )
        return [Path(line) for line in completed.stdout.splitlines() if line]


def _walk_markdown_files(scan_dirs: Iterable[Path]) -> Iterator[Path]:
    """Yield every non-hidden ``*.md`` file under the scan dirs, skipping dot-folders."""
    for scan_dir in scan_dirs:
        for dirpath, dirnames, filenames in os.walk(scan_dir):
            dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
            for filename in sorted(filenames):
                if filename.endswith(_MARKDOWN_SUFFIX) and not filename.startswith("."):
                    yield Path(dirpath) / filename


def _corpus_stats(scan_dirs: Iterable[Path]) -> tuple[int, float]:
    """Document count and average byte length over every markdown file in scope.

    Stat-only (no file reads): byte size is the BM25 document-length proxy, so
    the corpus average costs one ``stat`` per file rather than a full read.
    """
    doc_count = 0
    total_bytes = 0
    for file_path in _walk_markdown_files(scan_dirs):
        doc_count += 1
        total_bytes += file_path.stat().st_size
    average_bytes = (total_bytes / doc_count) if doc_count else 0.0
    return doc_count, max(average_bytes, 1.0)


def _collect_matches(candidates: Iterable[Path], terms: list[str], root: Path) -> list[_DocMatch]:
    """Read each candidate once: per-term occurrence counts, snippet, byte length.

    A candidate where no term occurs (possible only through engine-specific
    case-folding edge cases) is dropped, keeping the two engines' result sets
    identical by construction.
    """
    lowered_terms = [term.lower() for term in terms]
    matches: list[_DocMatch] = []
    for file_path in candidates:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        lowered_content = content.lower()
        term_counts = {
            term: count
            for term, lowered in zip(terms, lowered_terms, strict=True)
            if (count := lowered_content.count(lowered))
        }
        if not term_counts:
            continue
        rel_path = file_path.relative_to(root).as_posix()
        snippet = _first_matching_snippet(content, lowered_terms)
        matches.append(_DocMatch(rel_path, term_counts, snippet, len(content.encode("utf-8"))))
    return matches


def _score_bm25(
    matches: list[_DocMatch], terms: list[str], doc_count: int, average_bytes: float
) -> list[SearchResult]:
    """Fold per-document term counts into BM25-scored pointer results."""
    document_frequency = {
        term: sum(1 for match in matches if term in match.term_counts) for term in terms
    }
    return [
        _build_result(match, _bm25(match, document_frequency, doc_count, average_bytes))
        for match in matches
    ]


def _bm25(
    match: _DocMatch, document_frequency: dict[str, int], doc_count: int, average_bytes: float
) -> float:
    """Okapi BM25 with Lucene's non-negative idf, over byte-length documents."""
    length_norm = 1 - _BM25_B + _BM25_B * (match.byte_length / average_bytes)
    score = 0.0
    for term, term_frequency in match.term_counts.items():
        frequency = document_frequency[term]
        idf = math.log(1 + (doc_count - frequency + 0.5) / (frequency + 0.5))
        score += idf * (term_frequency * (_BM25_K1 + 1) / (term_frequency + _BM25_K1 * length_norm))
    return round(score, _SCORE_PRECISION)


def _first_matching_snippet(content: str, lowered_terms: list[str]) -> str:
    """The first line where any term occurs, stripped and truncated."""
    for line in content.splitlines():
        lowered_line = line.lower()
        if any(term in lowered_line for term in lowered_terms):
            return _make_snippet(line)
    return ""


def _build_result(match: _DocMatch, score: float) -> SearchResult:
    """Assemble a pointer result, deriving topic and kind from the path shape."""
    topic, kind = _classify(match.rel_path)
    return SearchResult(
        topic=topic, path=match.rel_path, snippet=match.snippet, score=score, kind=kind
    )


def _classify(rel_path: str) -> tuple[str, ResultKind]:
    """Derive ``(topic, kind)`` from a vault-relative path.

    ``sources/<topic>/...`` is a source of that topic; a vault-root file
    (``index.md``, ``START_HERE.md``, ...) is a page with no topic; anything
    else is a page of its first path segment.
    """
    parts = PurePosixPath(rel_path).parts
    if parts[0] == _SOURCES_DIR:
        return (parts[1] if len(parts) >= 3 else "", "source")
    if len(parts) == 1:
        return ("", "page")
    return (parts[0], "page")


def _make_snippet(line: str) -> str:
    """Strip and truncate a matching line into a bounded snippet."""
    stripped = line.strip()
    if len(stripped) <= SNIPPET_MAX_CHARS:
        return stripped
    return stripped[:SNIPPET_MAX_CHARS] + "…"

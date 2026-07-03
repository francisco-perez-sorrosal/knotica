"""Ripgrep-backed ``SearchBackend`` with a pure-Python scanning fallback.

``RipgrepBackend`` shells out to ``rg --json`` when ripgrep is on ``PATH`` and
falls back to a pure-Python line scan when it is not. **The protocol hides
which one ran** -- both engines apply the same match semantics (whitespace-
split terms, OR'd, case-insensitive literals, non-overlapping occurrence
counts per line) and feed the same ranking and envelope code, so callers see
identical results either way.

Performance note on the fallback: the Python scan reads every markdown file
in scope on each call -- O(total vault bytes) per search, roughly one to two
orders of magnitude slower than ripgrep's parallel scan. Acceptable at MVP
vault scale (hundreds of pages); installing ripgrep is the fix for large
vaults.

Scope rules (identical in both engines): only ``*.md`` files; dot-folders and
dot-files (``.knotica/``, ``.git/``, ``.obsidian/``, ...) are skipped; stored
sources under ``sources/<topic>/`` ARE searched and marked ``kind="source"``.
Ripgrep runs with ``--no-config --no-ignore`` so user ripgrep configs and
vault ``.gitignore`` files cannot make its file set diverge from the
fallback's walk (hidden-path skipping is ripgrep's default and is preserved).

Read-only boundary: this module never writes, locks, or touches git.
"""

import json
import os
import re
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

#: rg exit codes that mean the scan itself succeeded (0 = matches, 1 = none).
_RG_OK_EXIT_CODES = frozenset({0, 1})


class _Hit:
    """Mutable per-file accumulator: occurrence count + first matching line."""

    __slots__ = ("count", "snippet")

    def __init__(self) -> None:
        self.count = 0
        self.snippet = ""


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
            results = self._scan_ripgrep(terms, scan_dirs)
        else:
            results = self._scan_python(terms, scan_dirs)
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

    def _scan_ripgrep(self, terms: list[str], scan_dirs: list[Path]) -> list[SearchResult]:
        """Scan with ``rg --json`` and aggregate match events per file."""
        command = [
            self._rg_path or "rg",
            "--json",
            "--no-config",
            "--no-ignore",
            "--ignore-case",
            "--fixed-strings",
            "--glob",
            f"*{_MARKDOWN_SUFFIX}",
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
        return self._collect_rg_hits(completed.stdout)

    def _collect_rg_hits(self, rg_json_stream: str) -> list[SearchResult]:
        """Fold rg's JSON-lines match events into one result per file."""
        hits: dict[str, _Hit] = {}
        for line in rg_json_stream.splitlines():
            event = json.loads(line)
            if event.get("type") != "match":
                continue
            data = event["data"]
            rel_path = Path(data["path"]["text"]).relative_to(self._root).as_posix()
            hit = hits.setdefault(rel_path, _Hit())
            hit.count += len(data["submatches"])
            if not hit.snippet:
                hit.snippet = _make_snippet(data["lines"]["text"])
        return [_build_result(rel_path, hit) for rel_path, hit in hits.items()]

    def _scan_python(self, terms: list[str], scan_dirs: list[Path]) -> list[SearchResult]:
        """Pure-Python fallback scan mirroring the ripgrep match semantics."""
        pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
        results: list[SearchResult] = []
        for file_path in _walk_markdown_files(scan_dirs):
            hit = _count_line_matches(file_path, pattern)
            if hit.count:
                rel_path = file_path.relative_to(self._root).as_posix()
                results.append(_build_result(rel_path, hit))
        return results


def _walk_markdown_files(scan_dirs: Iterable[Path]) -> Iterator[Path]:
    """Yield every non-hidden ``*.md`` file under the scan dirs, skipping dot-folders."""
    for scan_dir in scan_dirs:
        for dirpath, dirnames, filenames in os.walk(scan_dir):
            dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
            for filename in sorted(filenames):
                if filename.endswith(_MARKDOWN_SUFFIX) and not filename.startswith("."):
                    yield Path(dirpath) / filename


def _count_line_matches(file_path: Path, pattern: re.Pattern[str]) -> _Hit:
    """Count non-overlapping matches per line; keep the first matching line."""
    hit = _Hit()
    content = file_path.read_text(encoding="utf-8", errors="replace")
    for line in content.splitlines():
        line_matches = sum(1 for _ in pattern.finditer(line))
        if line_matches:
            hit.count += line_matches
            if not hit.snippet:
                hit.snippet = _make_snippet(line)
    return hit


def _build_result(rel_path: str, hit: _Hit) -> SearchResult:
    """Assemble a pointer result, deriving topic and kind from the path shape."""
    topic, kind = _classify(rel_path)
    return SearchResult(topic=topic, path=rel_path, snippet=hit.snippet, score=hit.count, kind=kind)


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

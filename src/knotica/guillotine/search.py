"""Lexical claim search and context extraction over the vault."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath

_SOURCES_DIR = "sources"
_MARKDOWN_SUFFIX = ".md"
_REPORTS_DIR = "reports/guillotine"
_CONTEXT_LINES = 2

#: Directories skipped during vault walks (dot-folders and guillotine output).
_DEFAULT_EXCLUDES = frozenset({".git", ".obsidian", ".knotica"})


@dataclass(frozen=True, slots=True)
class CandidateHit:
    """A line-range match before classification."""

    path: str
    line_start: int
    line_end: int
    text: str
    is_source: bool


def normalize_claim(claim: str) -> str:
    """Lower-case, collapse whitespace, strip trailing punctuation."""
    collapsed = " ".join(claim.split()).lower()
    return collapsed.rstrip(".,;:!?")


def claim_slug(claim: str, *, max_words: int = 8) -> str:
    """Stable filesystem slug from claim text."""
    words = re.sub(r"[^a-z0-9]+", "-", normalize_claim(claim)).strip("-").split("-")
    trimmed = [word for word in words if word][:max_words]
    return "-".join(trimmed) or "claim"


def expand_search_terms(claim: str) -> list[str]:
    """Deterministic keyword expansion for fuzzy lexical search."""
    normalized = normalize_claim(claim)
    terms: list[str] = []
    if normalized:
        terms.append(normalized)
    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if len(token) > 2]
    if len(tokens) >= 3:
        terms.append(" ".join(tokens[:4]))
        terms.append(" ".join(tokens[-4:]))
    if len(tokens) >= 2:
        for index in range(len(tokens) - 1):
            pair = f"{tokens[index]} {tokens[index + 1]}"
            if pair not in terms:
                terms.append(pair)
    # Common hyphenation variants for compound adjectives.
    variants: list[str] = []
    for term in terms:
        variants.append(term)
        variants.append(term.replace("-", " "))
        variants.append(term.replace("open source", "open-source"))
        variants.append(term.replace("open-source", "open source"))
    deduped: list[str] = []
    seen: set[str] = set()
    for term in variants:
        cleaned = term.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return deduped


def resolve_search_scope(
    vault_root: Path,
    topic: str,
    *,
    include_sources: bool = True,
    include_reports: bool = False,
) -> list[Path]:
    """Return directories to scan for a guillotine run."""
    if not topic:
        raise ValueError("topic is required for guillotine search scope")
    candidate = PurePath(topic)
    if candidate.is_absolute() or len(candidate.parts) != 1 or topic.startswith("."):
        raise ValueError(f"Topic must be a bare, non-hidden directory name, got: {topic!r}")
    scoped: list[Path] = [vault_root / topic]
    if include_sources:
        source_dir = vault_root / _SOURCES_DIR / topic
        if source_dir.is_dir():
            scoped.append(source_dir)
    existing = [directory for directory in scoped if directory.is_dir()]
    if not existing:
        raise FileNotFoundError(f"No topic directory named '{topic}' at the vault root.")
    if include_reports:
        report_dir = vault_root / _REPORTS_DIR
        if report_dir.is_dir():
            existing.append(report_dir)
    return existing


def find_candidate_mentions(
    claim: str,
    vault_root: Path,
    scan_dirs: list[Path],
    *,
    max_results: int = 50,
    include_reports: bool = False,
) -> list[CandidateHit]:
    """Search markdown files for exact and expanded-term matches."""
    terms = expand_search_terms(claim)
    if not terms:
        return []
    pattern = re.compile(
        "|".join(re.escape(term) for term in sorted(terms, key=len, reverse=True)), re.I
    )
    hits: list[CandidateHit] = []
    for file_path in _walk_markdown_files(scan_dirs, include_reports=include_reports):
        rel_path = file_path.relative_to(vault_root).as_posix()
        if not include_reports and rel_path.startswith(_REPORTS_DIR):
            continue
        file_hits = _scan_file(file_path, rel_path, pattern)
        hits.extend(file_hits)
        if len(hits) >= max_results:
            return hits[:max_results]
    return hits


def extract_context_windows(
    hits: Iterable[CandidateHit],
    vault_root: Path,
    *,
    context_lines: int = _CONTEXT_LINES,
) -> list[CandidateHit]:
    """Merge overlapping hits and widen each to a context window."""
    by_path: dict[str, list[CandidateHit]] = {}
    for hit in hits:
        by_path.setdefault(hit.path, []).append(hit)
    merged: list[CandidateHit] = []
    for path, path_hits in sorted(by_path.items()):
        lines = (vault_root / path).read_text(encoding="utf-8", errors="replace").splitlines()
        ranges = _merge_line_ranges([(hit.line_start, hit.line_end) for hit in path_hits])
        is_source = path_hits[0].is_source
        for start, end in ranges:
            window_start = max(1, start - context_lines)
            window_end = min(len(lines), end + context_lines)
            text = "\n".join(lines[window_start - 1 : window_end])
            merged.append(
                CandidateHit(
                    path=path,
                    line_start=window_start,
                    line_end=window_end,
                    text=text,
                    is_source=is_source,
                )
            )
    return merged


def _scan_file(file_path: Path, rel_path: str, pattern: re.Pattern[str]) -> list[CandidateHit]:
    content = file_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    is_source = _is_source_path(rel_path)
    hits: list[CandidateHit] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if pattern.search(line):
            start = index + 1
            end = index + 1
            # Extend multi-line passages when consecutive lines match or continue a sentence.
            while end < len(lines) and (
                pattern.search(lines[end])
                or (lines[end - 1].rstrip().endswith((",", ";")) and lines[end].strip())
            ):
                end += 1
            text = "\n".join(lines[start - 1 : end])
            hits.append(
                CandidateHit(
                    path=rel_path, line_start=start, line_end=end, text=text, is_source=is_source
                )
            )
            index = end
            continue
        index += 1
    return hits


def _merge_line_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    sorted_ranges = sorted(ranges)
    merged: list[tuple[int, int]] = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + _CONTEXT_LINES + 1:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _walk_markdown_files(scan_dirs: list[Path], *, include_reports: bool) -> Iterator[Path]:
    for scan_dir in scan_dirs:
        for dirpath, dirnames, filenames in os.walk(scan_dir):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if not name.startswith(".") and name not in _DEFAULT_EXCLUDES
            )
            for filename in sorted(filenames):
                if not filename.endswith(_MARKDOWN_SUFFIX) or filename.startswith("."):
                    continue
                yield Path(dirpath) / filename


def _is_source_path(rel_path: str) -> bool:
    parts = PurePosixPath(rel_path).parts
    return bool(parts) and parts[0] == _SOURCES_DIR

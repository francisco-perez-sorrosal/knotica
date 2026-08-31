#!/usr/bin/env python3
"""ID citation discipline checker (inbound isolation) -- knotica port.

Scans code files (Python, TS, JS, etc.) for references to ephemeral pipeline
identifiers -- REQ-*, AC-*, Step N, PM-N, dec-draft-<hash>, citations of any
.ai-work/-lifetime document (WIP.md, SYSTEMS_PLAN.md, INTERFACE_DESIGN.md,
LEARNINGS*.md, ...), and bare section citations (`§2.4`). Those identifiers
live in documents that are deleted with .ai-work/ (or, for dec-draft-<hash>,
are rewritten to dec-NNN by ADR finalize) -- so citations left behind in code
or tests dangle the moment the pipeline that minted them completes.

The section-citation pattern earns its place from td-062: ~200 `§N.N` pointers
accumulated across dashboard/src, and by the end *two different* documents
named INTERFACE_DESIGN.md, with independent numbering, were cited from the
same file -- so a reader could not tell which numbering a comment meant. A
section citation of a published standard (WCAG, RFC) is legitimate and takes
the escape hatch below.

Ported from praxion's scripts/check_id_citation_discipline.py, adapted to
knotica's repo layout. Key asymmetry (do not "simplify" this away): the ADR
finalize walk scope (.ai-state/**, every markdown file under docs/, and
in-flight .ai-work/*/LEARNINGS.md et al.) is *allowed* -- expected, even -- to
cite dec-draft-<hash> ids, because finalize rewrites them to dec-NNN in place.
Code and tests must never carry them at all, since nothing ever rewrites a
citation baked into a .py/.ts/... file.

Escape hatch: add ``id-citation-discipline:ignore`` on the same line as an
intentional reference (comment syntax varies by language -- the check only
requires the literal substring to be present on the line).

Baseline: BASELINE_EXEMPT_PATHS lists files with pre-existing violations
discovered when this gate was introduced (td-014). This is a decontamination
backlog, not a permanent exemption: entries are removed as files are cleaned
up, never added to -- and touching a baselined file forces its cleanup, since
the pre-commit scan does not consult this list for files in the commit. It
stood at 21 files when the gate landed; `src/knotica/discovery/service.py`
left it on 2026-08-05 and `tests/support/dispatch.py` on 2026-08-10, the latter
when a stale docstring citing a pipeline step number was rewritten.

The remaining count is deliberately NOT restated here. The previous version of
this paragraph asserted that the count is never restated and then restated it
in the same sentence, which is precisely how it went stale -- read the number
off the list. See the notes column on td-014 in .ai-state/TECH_DEBT_RESOLVED.md.

Exempt paths (pipeline/ADR-finalize/docs state):
  .ai-work/, .ai-state/, docs/
Exempt paths (shipped vault content, not code):
  vault-template/
Exempt paths (test fixtures/data):
  **/tests/fixtures/**, **/testdata/**

Exit codes: 0 clean, 1 violations found, 2 script error.

Usage:
    python3 scripts/check_id_citation_discipline.py
    python3 scripts/check_id_citation_discipline.py --files FILE [FILE ...]
    python3 scripts/check_id_citation_discipline.py --repo-root PATH
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

CODE_EXTENSIONS = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".rb",
        ".sh",
        ".swift",
        ".cs",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
    }
)

EXEMPT_PATH_PREFIXES = (
    # Nested git worktrees are full repo copies; scanning them reports another
    # branch's violations against this one.
    ".claude/",
    ".ai-work/",
    ".ai-state/",
    "docs/",
    # Shipped vault content (scaffolded into every new vault) -- not this
    # repo's code, even where a stray .py/.jsonl example might land here.
    "vault-template/",
)

EXEMPT_FILENAMES = frozenset(
    {
        "CHANGELOG.md",
        "ROADMAP.md",
    }
)

# Specific files exempted because they describe the forbidden patterns as
# part of their own documentation (this detector script) or as fixture data
# for the detector's own test. Without this, the detector would flag its own
# pattern strings (e.g. "REQ-NN" in a docstring example) and block every
# commit that touches it.
EXEMPT_EXACT_PATHS = frozenset(
    {
        "scripts/check_id_citation_discipline.py",
        "tests/test_check_id_citation_discipline.py",
    }
)

# Decontamination backlog (td-014): files with pre-existing citations
# discovered by a full-repo scan when this gate was introduced. NOT a
# permanent exemption -- see the module docstring. Do not add new entries
# here; a new violation anywhere else is a real regression and should be
# fixed inline, not baselined.
BASELINE_EXEMPT_PATHS = frozenset(
    {
        "src/knotica/discovery/youcom.py",
        "tests/discovery/test_openalex.py",
        "tests/discovery/test_records.py",
        "tests/test_arena_race_characterization.py",
        "tests/test_best_effort_characterization.py",
        "tests/test_branch_namespaces_characterization.py",
        "tests/test_decision_envelope.py",
        "tests/test_gap_classifier.py",
        "tests/test_gapfill.py",
        "tests/test_gapfill_discovery_default.py",
        "tests/test_gapfill_integration.py",
        "tests/test_loop_gapfill_hook.py",
        "tests/test_loop_runner_factory_characterization.py",
        "tests/test_mcp_prompts.py",
        "tests/test_mcp_read.py",
        "tests/test_mcp_resources.py",
        "tests/test_op_create_topic.py",
        "tests/test_records_gap.py",
        "tests/test_transaction.py",
    }
)

EXCLUDED_PATH_FRAGMENTS = (
    "/tests/fixtures/",
    "/testdata/",
    "/test_fixtures/",
    "/__pycache__/",
    "/.git/",
    # Vendored dependency trees -- never scan third-party library code.
    "/.venv/",
    "/venv/",
    "/node_modules/",
    "/.tox/",
    "/dist/",
    "/build/",
    "/.cache/",
    "/htmlcov/",
    "/.mypy_cache/",
    "/.pytest_cache/",
    "/.ruff_cache/",
    "/site-packages/",
    "/tmp/",
)

IGNORE_MARKER = "id-citation-discipline:ignore"

PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "req-id",
        re.compile(r"\bREQ-[A-Z0-9][A-Z0-9\-]*\b"),
        "REQ identifier (e.g., REQ-CFG-01) -- describe behavior inline; "
        "REQ->test mapping belongs in .ai-work/<slug>/traceability.yml",
    ),
    (
        "ac-id",
        re.compile(r"\bAC-\d+\b"),
        "AC identifier (e.g., AC-14) -- acceptance criteria live only in "
        "ephemeral SYSTEMS_PLAN.md; describe behavior inline",
    ),
    (
        "step-ref",
        re.compile(r"\bStep \d+[a-z]?\b"),
        "Step reference (e.g., Step 31) -- pipeline-local, deleted with "
        ".ai-work/; remove or rephrase without the step number",
    ),
    (
        "pm-id",
        re.compile(r"\bPM-\d+\b"),
        "PM identifier -- pipeline-local milestone reference; describe behavior inline instead",
    ),
    (
        "dec-draft-id",
        re.compile(r"\bdec-draft-[0-9a-f]{6,}\b"),
        "dec-draft-<hash> ADR fragment id -- finalize rewrites this to "
        "dec-NNN only inside .ai-state/**, docs/**, and .ai-work/; a code or "
        "test citation is never rewritten and goes stale. Cite the decision "
        "by describing it, or wait for finalize and cite dec-NNN.",
    ),
    (
        "ephemeral-doc-ref",
        re.compile(
            r"\b(?:WIP|SYSTEMS_PLAN|IMPLEMENTATION_PLAN|INTERFACE_DESIGN|TRANSACTIONS_DESIGN"
            r"|RESEARCH_FINDINGS|TASK_BRIEF|CONTEXT_REVIEW|IDEA_PROPOSAL|SPEC_DELTA"
            r"|PRE_REFACTOR_PLAN|VERIFICATION_REPORT|REWORK_MANIFEST|TEST_RESULTS"
            r"|TEST_BASELINE|LEARNINGS)(?:_[A-Za-z0-9_-]+)?\.md\b"
        ),
        "reference to an ephemeral pipeline document (anything under "
        ".ai-work/<slug>/ -- WIP.md, SYSTEMS_PLAN.md, INTERFACE_DESIGN.md, "
        "LEARNINGS*.md, ...) -- deleted at pipeline cleanup; describe the "
        "behavior or decision inline, or cite a finalized dec-NNN instead",
    ),
    (
        "section-citation",
        # A section symbol followed by a section number is a pointer into a
        # document, and in code that document is almost always the pipeline's
        # own ephemeral design doc (td-062: ~200 such pointers accumulated,
        # two different INTERFACE_DESIGN.md files with clashing numbering).
        # A genuine standard citation (WCAG 2.2 s2.5.8, RFC ...) takes the
        # escape hatch.
        re.compile(r"§\s*\d"),
        "section citation (e.g. §2.4) -- section numbers point into an "
        "ephemeral design document that is deleted with .ai-work/, and two "
        "such documents can share a filename with clashing numbering. State "
        "the constraint itself, or cite a finalized dec-NNN. A citation of a "
        "published standard takes the escape hatch.",
    ),
)


_SHEBANG_INTERPRETERS = ("bash", "sh", "zsh", "dash", "ksh")
_SHEBANG_PATTERNS = tuple(re.compile(rf"\b{shell}\b") for shell in _SHEBANG_INTERPRETERS)


def is_bash_shebang(path: Path) -> bool:
    """Return True if `path`'s first line is a bash/sh-family shebang.

    Extensionless executable scripts escape the extension-based corpus
    selection. Shebang detection brings them back into scope so id-citation
    violations in bash scripts are not silently skipped on commit.
    """
    try:
        with path.open("rb") as f:
            first_line = f.readline(256)
    except OSError:
        return False
    try:
        text = first_line.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return False
    if not text.startswith("#!"):
        return False
    return any(pattern.search(text) for pattern in _SHEBANG_PATTERNS)


# A section symbol is a dangling pointer only when the document it points into
# is ephemeral. A line naming a durable document -- or a published standard --
# cites something a reader can still open years from now, so `DESIGN.md § 3`
# and `WCAG 2.2 §2.5.8` are exactly the citations this project wants.
DURABLE_CITATION_TARGETS = re.compile(
    r"\b(?:DESIGN|CLAUDE|README|CONTRIBUTING|CHANGELOG|ROADMAP|TEST_TOPOLOGY"
    r"|TECH_DEBT_LEDGER|TECH_DEBT_RESOLVED|DECISIONS_INDEX)\.md\b"
    r"|\bdocs/|\bdec-\d{3}\b"
    r"|\b(?:WCAG|RFC|PEP|ISO|ECMA|ARIA|Unicode)\b"
)


def cites_durable_document(line: str) -> bool:
    """Return True if this line's section citation points at a durable target."""
    return DURABLE_CITATION_TARGETS.search(line) is not None


def is_excluded_path(path: Path) -> bool:
    path_str = str(path).replace("\\", "/")
    return any(fragment in path_str for fragment in EXCLUDED_PATH_FRAGMENTS)


def is_exempt_by_path(rel_path: Path) -> bool:
    rel_str = str(rel_path).replace("\\", "/")
    if rel_str in EXEMPT_EXACT_PATHS:
        return True
    if rel_str in EXEMPT_FILENAMES:
        return True
    for prefix in EXEMPT_PATH_PREFIXES:
        if rel_str == prefix.rstrip("/") or rel_str.startswith(prefix):
            return True
    return False


def is_baseline_exempt(rel_path: Path) -> bool:
    """Decontamination-backlog exemption -- see BASELINE_EXEMPT_PATHS."""
    return str(rel_path).replace("\\", "/") in BASELINE_EXEMPT_PATHS


def iter_code_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for ext in CODE_EXTENSIONS:
        for path in repo_root.rglob(f"*{ext}"):
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(repo_root)
            except ValueError:
                continue
            if is_exempt_by_path(rel) or is_baseline_exempt(rel) or is_excluded_path(path):
                continue
            files.append(path)

    # Second pass: extensionless executable shell scripts identified by shebang.
    # Heuristic: only executable files are scanned in full-repo mode to keep
    # false positives down (most extensionless text files are not scripts).
    seen = {f.resolve() for f in files}
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix:
            continue
        if path.resolve() in seen:
            continue
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            continue
        if is_exempt_by_path(rel) or is_baseline_exempt(rel) or is_excluded_path(path):
            continue
        if not os.access(path, os.X_OK):
            continue
        if not is_bash_shebang(path):
            continue
        files.append(path)
    return files


def scan_file(path: Path) -> list[tuple[int, str, str, str]]:
    findings: list[tuple[int, str, str, str]] = []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return findings

    for line_no, line in enumerate(content.splitlines(), start=1):
        if IGNORE_MARKER in line:
            continue
        for name, pattern, description in PATTERNS:
            if not pattern.search(line):
                continue
            if name == "section-citation" and cites_durable_document(line):
                continue
            findings.append((line_no, name, description, line.rstrip()))
            break  # one pattern report per line keeps output readable
    return findings


def filter_files(explicit_files: list[Path], repo_root: Path) -> list[Path]:
    out: list[Path] = []
    for candidate in explicit_files:
        abs_path = candidate if candidate.is_absolute() else (repo_root / candidate).resolve()
        if not abs_path.is_file():
            continue
        # Accept recognized code extensions OR extensionless files with a
        # bash/sh-family shebang. Explicit user-passed paths (e.g., from
        # pre-commit's staged-files list) override the executable-bit
        # heuristic used in full-repo scans.
        if abs_path.suffix not in CODE_EXTENSIONS and not is_bash_shebang(abs_path):
            continue
        try:
            rel = abs_path.relative_to(repo_root)
        except ValueError:
            continue
        if is_exempt_by_path(rel) or is_baseline_exempt(rel) or is_excluded_path(abs_path):
            continue
        out.append(abs_path)
    return out


def format_findings(files: list[Path], repo_root: Path) -> tuple[int, list[str]]:
    lines: list[str] = []
    total = 0
    for path in sorted(files):
        findings = scan_file(path)
        if not findings:
            continue
        try:
            display = path.relative_to(repo_root)
        except ValueError:
            display = path
        lines.append("")
        lines.append(f"{display}:")
        for line_no, name, description, text in findings:
            snippet = text.strip()
            if len(snippet) > 120:
                snippet = snippet[:117] + "..."
            lines.append(f"  [{name}] line {line_no}: {description}")
            lines.append(f"    > {snippet}")
            total += 1
    return total, lines


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: current working directory)",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        type=Path,
        default=None,
        help="Explicit file list (e.g., from pre-commit). Filtered to code surfaces.",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()

    if args.files:
        files = filter_files(list(args.files), repo_root)
    else:
        files = iter_code_files(repo_root)

    total, detail_lines = format_findings(files, repo_root)

    if total == 0:
        print(f"scanned {len(files)} code file(s); 0 id-citation violations.")
        return 0

    print("\n".join(detail_lines))
    print(f"\nscanned {len(files)} code file(s); {total} violation(s).")
    print("")
    print("Escape hatch:   add `id-citation-discipline:ignore` on the same line")
    print("                when the reference is truly intentional.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

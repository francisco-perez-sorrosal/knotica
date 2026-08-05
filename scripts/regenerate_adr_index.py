#!/usr/bin/env python3
"""Regenerate `.ai-state/decisions/DECISIONS_INDEX.md` from ADR frontmatter.

The index has always declared itself auto-generated and named this script as the
way to rebuild it, but the script did not exist -- which is why five accepted
decisions sat unfinalized in `drafts/` with nothing to promote them. It reads
every finalized `<NNN>-<slug>.md`, sorts by id, and renders one row per record.

Only finalized records are indexed. Drafts under `drafts/` are deliberately
skipped: they carry provisional `dec-draft-<hash>` ids that are rewritten at
finalize, so indexing them would publish an identifier guaranteed to change.

Idempotent -- running it twice produces the same file. Exit 0 on success, 1 if a
record is missing a field the table needs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DECISIONS_DIR = Path(__file__).resolve().parents[1] / ".ai-state" / "decisions"
INDEX_PATH = DECISIONS_DIR / "DECISIONS_INDEX.md"
FINALIZED_GLOB = "[0-9][0-9][0-9]-*.md"
REQUIRED_FIELDS = ("id", "title", "status", "category", "date", "tags", "summary")

HEADER = """# Decisions Index

Auto-generated from ADR frontmatter. Do not edit manually.
Regenerate: `python scripts/regenerate_adr_index.py`

| ID | Title | Status | Category | Date | Tags | Summary |
|----|-------|--------|----------|------|------|---------|
"""

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Read the leading YAML block as flat `key: value` pairs.

    Deliberately not a YAML parser: ADR frontmatter is a flat scalar map by
    convention, and a real parser would invite nested shapes the table cannot
    render. Continuation lines (a wrapped `summary`, a block list) are folded
    into the preceding key so a hand-wrapped field still reads correctly.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}
    fields: dict[str, str] = {}
    key: str | None = None
    for line in match.group(1).split("\n"):
        header = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if header:
            key = header.group(1)
            fields[key] = header.group(2).strip()
        elif key and line.strip():
            fields[key] = f"{fields[key]} {line.strip()}".strip()
    return {key: _unquote(value) for key, value in fields.items()}


def _unquote(value: str) -> str:
    """Strip the surrounding quotes YAML requires around some scalars.

    A `summary:` or `dissent:` containing a colon-space *must* be quoted or
    `yaml.safe_load` rejects the whole block. Without this, the quotes leak
    into the rendered table as literal characters -- so the fix that makes a
    record machine-readable would corrupt its own index row.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\") if value[0] == '"' else inner
    return value


def _render_tags(raw: str) -> str:
    """Flow list (`[a, b]`) or block list, rendered as the table's `a, b`."""
    return ", ".join(part.strip() for part in raw.strip("[]").split(",") if part.strip())


def _cell(value: str) -> str:
    """Escape the one character that would break a Markdown table row."""
    return value.replace("|", "\\|").strip()


def _sort_key(record: dict[str, str]) -> tuple[int, str]:
    """Numeric where the id is `dec-NNN`, so dec-9 sorts before dec-10."""
    numeric = re.match(r"^dec-(\d+)$", record["id"])
    return (int(numeric.group(1)) if numeric else 1 << 30, record["id"])


def main() -> int:
    records: list[dict[str, str]] = []
    problems: list[str] = []
    for path in sorted(DECISIONS_DIR.glob(FINALIZED_GLOB)):
        fields = _parse_frontmatter(path.read_text(encoding="utf-8"))
        missing = [field for field in REQUIRED_FIELDS if not fields.get(field)]
        if missing:
            problems.append(f"{path.name}: missing {', '.join(missing)}")
            continue
        records.append(fields)

    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    records.sort(key=_sort_key)
    rows = "".join(
        "| {id} | {title} | {status} | {category} | {date} | {tags} | {summary} |\n".format(
            id=_cell(r["id"]),
            title=_cell(r["title"]),
            status=_cell(r["status"]),
            category=_cell(r["category"]),
            date=_cell(r["date"]),
            tags=_cell(_render_tags(r["tags"])),
            summary=_cell(r["summary"]),
        )
        for r in records
    )
    INDEX_PATH.write_text(HEADER + rows, encoding="utf-8")
    print(f"wrote {INDEX_PATH.relative_to(DECISIONS_DIR.parents[1])} — {len(records)} decisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

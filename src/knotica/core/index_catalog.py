"""Root ``index.md`` catalog maintenance helpers."""

from __future__ import annotations

INDEX_PATH = "index.md"
REPORTS_SECTION = "Reports"


def upsert_index_bullet(
    index_text: str,
    *,
    vault_path: str,
    index_entry: str,
    section: str,
) -> str:
    """Replace or insert one catalog bullet keyed by full-path wikilink.

    Bullets use ``- [[topic/.../page]] — <index_entry>``. Existing bullets for
    the same wikilink are replaced in place; new bullets append under
    ``### <section>`` when present, otherwise at end of file.
    """
    wikilink = f"[[{vault_path.removesuffix('.md')}]]"
    new_bullet = f"- {wikilink} — {index_entry}"
    lines = index_text.splitlines()
    block = _find_bullet_block(lines, wikilink)
    if block is not None:
        start, end = block
        updated = lines[:start] + [new_bullet] + lines[end:]
    else:
        insert_at = _section_insert_point(lines, section)
        updated = lines[:insert_at] + [new_bullet] + lines[insert_at:]
    return "\n".join(updated).rstrip("\n") + "\n"


def _find_bullet_block(lines: list[str], wikilink: str) -> tuple[int, int] | None:
    """Return the ``[start, end)`` line span of the bullet for ``wikilink``, if present."""
    for index, line in enumerate(lines):
        if line.lstrip().startswith("- ") and wikilink in line:
            end = index + 1
            while end < len(lines) and _is_continuation(lines[end]):
                end += 1
            return index, end
    return None


def _is_continuation(line: str) -> bool:
    """Whether ``line`` continues the preceding bullet (indented, not a new bullet/heading)."""
    stripped = line.lstrip()
    return bool(stripped) and not stripped.startswith("- ") and not stripped.startswith("#")


def _section_insert_point(lines: list[str], section: str) -> int:
    """Line index to insert a new bullet: after the section's content, or end of file."""
    header = f"### {section}"
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == header)
    except StopIteration:
        return len(lines)
    end = start + 1
    while end < len(lines) and not lines[end].lstrip().startswith("#"):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end

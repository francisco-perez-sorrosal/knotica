"""Reflow hard-wrapped PDF extraction text into readable Markdown paragraphs."""

from __future__ import annotations

import re

_HEADER_RE = re.compile(r"^#{1,6}\s")
_LIST_ITEM_RE = re.compile(r"^(\s*)([-*+•●◦‣]|\d+\.)\s+")
_BLOCKQUOTE_RE = re.compile(r"^>\s?")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})\s*$")
_FIGURE_OR_TABLE_CAPTION_RE = re.compile(r"^\[(Figure|Table)\b", re.I)
_SENTENCE_END_RE = re.compile(r"[.!?][\"')\]]*$")
_MATH_OR_DISPLAY_RE = re.compile(r"^\s{8,}\S")
_SPECIAL_BULLET_RE = re.compile(r"^[❶-❿]")
_CIRCLED_BULLET_SPLIT_RE = re.compile(r"\.\s+([❷-❿])")
_QUESTION_SPLIT_RE = re.compile(r"\.\s+(Question [❷-❿])")
_STANDALONE_LABELS = frozenset({"Key Questions", "Contributions", "Outline of the Survey"})
_RUNIN_HEADING_RE = re.compile(
    r"^(?P<title>[A-Z][^.!?\n]{5,90}?)\s+"
    r"(?P<rest>(?:Given|The contributions|The remainder|This survey|We present|To address)\b.*)$"
)

_ANAPHORIC_OR_CONTINUATION = frozenset(
    {
        "these",
        "this",
        "that",
        "it",
        "they",
        "their",
        "such",
        "also",
        "however",
        "while",
        "although",
        "because",
        "since",
        "when",
        "where",
        "as",
        "in",
        "for",
        "with",
        "within",
        "together",
        "some",
        "other",
        "another",
        "therefore",
        "specifically",
        "following",
        "from",
        "to",
    }
)


def reflow_pdf_markdown(text: str) -> str:
    """Join PDF column wraps into paragraphs while preserving Markdown structure."""
    if not text.strip():
        return text

    blocks = _split_preserving_blank_runs(text)
    reflowed_blocks: list[str] = []
    for block in blocks:
        if block == "\n":
            reflowed_blocks.append(block)
            continue
        reflowed_blocks.append(_reflow_block(block))

    body = "".join(reflowed_blocks)
    body = _structure_prose(body)
    if body and not body.endswith("\n"):
        body += "\n"
    return body


def _structure_prose(text: str) -> str:
    """Turn reflowed prose into paragraph blocks with headings and sensible breaks."""
    text = _promote_standalone_labels(text)
    text = _split_special_bullets(text)
    text = _split_inline_enumerations(text)
    text = _split_question_paragraphs(text)
    text = _split_therefore_paragraphs(text)
    lines: list[str] = []
    for line in text.splitlines():
        promoted = _promote_runin_heading_line(line)
        if promoted is not None:
            lines.extend(promoted)
        else:
            lines.append(line)
    return _normalize_paragraph_spacing("\n".join(lines))


def _promote_standalone_labels(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in _STANDALONE_LABELS:
            lines.append(f"### {stripped}")
        else:
            lines.append(line)
    return "\n".join(lines)


def _promote_runin_heading_line(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or _SPECIAL_BULLET_RE.match(stripped):
        return None
    match = _RUNIN_HEADING_RE.match(stripped)
    if match is None:
        return None
    title = match.group("title").strip()
    rest = match.group("rest").strip()
    if len(title.split()) > 10 or any(char in title for char in ":;(.,"):
        return None
    return [f"### {title}", "", rest]


def _split_special_bullets(text: str) -> str:
    """Break enumerated circled-number items onto separate blocks when clearly list-shaped."""
    text = re.sub(r": ([❶-❿])", r":\n\n\1", text)
    return re.sub(r"\? ([❷-❿])", r"?\n\n\1", text)


def _split_inline_enumerations(text: str) -> str:
    return _CIRCLED_BULLET_SPLIT_RE.sub(r".\n\n\1", text)


def _split_question_paragraphs(text: str) -> str:
    return _QUESTION_SPLIT_RE.sub(r".\n\n\1", text)


def _split_therefore_paragraphs(text: str) -> str:
    return re.sub(r"\s+(Therefore,)", r"\n\n\1", text)


def _normalize_paragraph_spacing(text: str) -> str:
    lines = text.splitlines()
    normalized: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if normalized and normalized[-1] != "":
                normalized.append("")
            continue
        if normalized and normalized[-1] != "":
            prev = normalized[-1].strip()
            if _needs_blank_line_before(prev, stripped):
                normalized.append("")
        normalized.append(line.rstrip())
    return "\n".join(normalized).rstrip("\n") + "\n"


def _needs_blank_line_before(previous: str, current: str) -> bool:
    if previous.startswith("#") or current.startswith("#"):
        return True
    if _SPECIAL_BULLET_RE.match(current) or _SPECIAL_BULLET_RE.match(previous):
        return True
    if _is_structural_line(previous) or _is_structural_line(current):
        return True
    if _SENTENCE_END_RE.search(previous):
        return True
    return False


def _split_preserving_blank_runs(text: str) -> list[str]:
    """Split on blank lines while keeping newline characters as separate tokens."""
    parts: list[str] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.strip() == "":
            if current:
                parts.append("".join(current))
                current = []
            parts.append("\n")
        else:
            current.append(line)
    if current:
        parts.append("".join(current))
    return parts


def _reflow_block(block: str) -> str:
    lines = block.splitlines()
    elements: list[str] = []
    paragraph: list[str] = []
    in_fence = False

    def flush_paragraph() -> None:
        if not paragraph:
            return
        elements.append(_join_wrapped_lines(paragraph))
        paragraph.clear()

    for line in lines:
        stripped_fence = line.lstrip()
        if stripped_fence.startswith("```"):
            flush_paragraph()
            in_fence = not in_fence
            elements.append(line.rstrip())
            continue
        if in_fence:
            elements.append(line.rstrip())
            continue

        if _is_structural_line(line, treat_circled_as_structural=False):
            flush_paragraph()
            elements.append(line.rstrip())
            continue

        if paragraph and _should_join(paragraph[-1], line):
            paragraph.append(line.strip())
            continue

        flush_paragraph()
        paragraph.append(line.strip())

    flush_paragraph()
    if not elements:
        return block
    return "\n\n".join(elements) + "\n"


def _is_structural_line(line: str, *, treat_circled_as_structural: bool = True) -> bool:
    stripped = line.lstrip()
    if _HEADER_RE.match(stripped):
        return True
    if _LIST_ITEM_RE.match(line):
        return True
    if _BLOCKQUOTE_RE.match(stripped):
        return True
    if _HR_RE.match(stripped):
        return True
    if _FIGURE_OR_TABLE_CAPTION_RE.match(stripped):
        return True
    if _MATH_OR_DISPLAY_RE.match(line):
        return True
    if treat_circled_as_structural and _SPECIAL_BULLET_RE.match(stripped):
        return True
    return False


def _should_join(previous: str, current: str) -> bool:
    prev = previous.rstrip()
    curr = current.strip()
    if not prev or not curr:
        return False

    if _SPECIAL_BULLET_RE.match(curr):
        return False

    if _is_structural_line(current, treat_circled_as_structural=False) or _is_structural_line(
        previous, treat_circled_as_structural=False
    ):
        return False

    if _LIST_ITEM_RE.match(current):
        return False

    if current.startswith(("   ", "\t")) and not _HEADER_RE.match(curr):
        return True

    if prev.endswith("-"):
        return curr[0].islower() or curr[0].isdigit()

    if prev.endswith(":") and _LIST_ITEM_RE.match(curr):
        return False

    if _SENTENCE_END_RE.search(prev):
        first_word = _first_word(curr)
        if first_word in _ANAPHORIC_OR_CONTINUATION:
            return True
        if curr[0].isupper():
            return False

    if curr[0].islower() or curr.startswith(("(", "[", ",", ";", "❶", "❷", "❸", "❹", "❺")):
        return True

    if not _SENTENCE_END_RE.search(prev):
        return True

    if prev.endswith((",", ";")):
        return True

    return False


def _first_word(text: str) -> str:
    match = re.match(r"^[\"'“‘(\[]*([A-Za-z]+)", text)
    return match.group(1).lower() if match else ""


def _join_wrapped_lines(lines: list[str]) -> str:
    merged = lines[0]
    for line in lines[1:]:
        if merged.endswith("-"):
            merged = merged + line.lstrip()
        else:
            merged = f"{merged.rstrip()} {line.lstrip()}"
    merged = re.sub(r"  +", " ", merged)
    return merged.strip()

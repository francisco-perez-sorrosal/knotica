"""Heading slug generation for wikilink -> Markdown anchor conversion."""

from __future__ import annotations

import re
import unicodedata

_PUNCT_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def slugify_heading(text: str) -> str:
    """GitHub-like heading slug: lowercase, punctuation stripped, hyphens."""
    normalized = unicodedata.normalize("NFKD", text.strip())
    without_punct = _PUNCT_RE.sub("", normalized)
    collapsed = _SPACE_RE.sub("-", without_punct).strip("-")
    return collapsed.lower()

"""Distinguish "this page was replaced" from "your passage was reworded".

Phase 3 measured that **one wholesale supersession supplied 85% of all observed
orphaning** -- a page whose content was replaced outright, at page similarity
0.161 with every heading gone. Every anchor into it orphaned, correctly. But the
review surface could not tell that case apart from an ordinary rewrite, so it
offered the same affordance for both: a best-guess span on a page that no longer
discusses the subject, presented as though the passage had merely moved.

The two populations are far apart, which is why this needs no calibration:

===========================  ==================
event class                  page similarity
===========================  ==================
ordinary knowledge rewrites  0.885 - 0.997
wholesale supersession       0.161
===========================  ==================

**This is a classifier for an event, not a gate on placement.** It never decides
where an anchor lands -- :func:`~knotica.core.notes.resolve.resolve_anchor` owns
that and is untouched. It only labels *why* an anchor orphaned, so the review
item can say the true thing. Keeping it out of the resolver also keeps the two
modules a mis-scored anchor can silently damage (``scoring``, ``candidates``)
out of this change entirely.

Both signals must fire. Similarity alone would misread a ``section-restructure``
-- Phase 3 measured those at 0.885 with headings renamed -- and heading loss
alone would misread any large edit that happens to retitle. Requiring both makes
a false positive need a page that was *both* rewritten past recognition *and*
completely restructured, which is the definition of the event.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

__all__ = ["SUPERSESSION_SIMILARITY_CEILING", "is_superseded"]

#: Page similarity at or above which a change is a rewrite, never a replacement.
#: Sits in the wide empty band between the two measured populations (0.161 vs
#: 0.885), so it is a separator rather than a tuned parameter -- an order of
#: magnitude of headroom on both sides.
SUPERSESSION_SIMILARITY_CEILING = 0.35

_ATX_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)


def is_superseded(historical_text: str, head_text: str | None) -> bool:
    """Whether the page was replaced wholesale rather than edited.

    ``head_text`` is ``None`` when the page is gone from the vault entirely.
    That is a deletion, not a supersession: nothing replaced the page, so there
    is no replacing content to point a reader at, and claiming otherwise would
    invent a successor that does not exist.
    """
    if head_text is None or not historical_text.strip() or not head_text.strip():
        return False
    if SequenceMatcher(None, historical_text, head_text).ratio() >= (
        SUPERSESSION_SIMILARITY_CEILING
    ):
        return False
    return not (_headings(historical_text) & _headings(head_text))


def _headings(text: str) -> frozenset[str]:
    """The page's ATX heading texts, casefolded for comparison."""
    return frozenset(match.casefold() for match in _ATX_HEADING.findall(text) if match)

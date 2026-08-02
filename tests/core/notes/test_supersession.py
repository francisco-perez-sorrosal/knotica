"""Behavioral contract of the supersession classifier.

Phase 3 measured one wholesale supersession supplying 85% of all observed
orphaning, at page similarity 0.161 with every heading replaced, against
ordinary knowledge rewrites at 0.885-0.997. The classifier separates those two
populations so the review surface can say which one happened.

It classifies an *event*; it never decides where an anchor lands.
"""

from knotica.core.notes.supersession import is_superseded

ORIGINAL = (
    "# Agent memory\n\n"
    "## Persistence\n\n"
    "The model has no persistent notion of the goal it is optimizing for.\n\n"
    "## Consequences\n\n"
    "Long-horizon tasks degrade as context is lost between episodes.\n"
)


def test_a_page_replaced_wholesale_is_superseded():
    replacement = (
        "# Retrieval benchmarks\n\n"
        "## Corpus construction\n\n"
        "Documents are sampled from a fixed snapshot and deduplicated by hash.\n\n"
        "## Scoring\n\n"
        "Relevance is graded by pooled human judgement over the top k results.\n"
    )

    assert is_superseded(ORIGINAL, replacement)


def test_an_ordinary_reword_is_not_superseded():
    reworded = ORIGINAL.replace(
        "The model has no persistent notion",
        "The model retains no persistent notion",
    )

    assert not is_superseded(ORIGINAL, reworded)


def test_a_section_restructure_is_not_superseded():
    """Headings renamed but prose intact -- Phase 3 measured these at 0.885 similarity.

    Similarity alone would misread this; requiring heading disjointness *and*
    low similarity is what keeps a restructure out of the superseded bucket.
    """
    restructured = ORIGINAL.replace("## Persistence", "## Goal persistence").replace(
        "## Consequences", "## Downstream effects"
    )

    assert not is_superseded(ORIGINAL, restructured)


def test_a_deleted_page_is_not_superseded():
    """Deletion is not supersession: nothing replaced the page.

    Claiming otherwise would invent a successor that does not exist.
    """
    assert not is_superseded(ORIGINAL, None)


def test_an_emptied_page_is_not_superseded():
    assert not is_superseded(ORIGINAL, "   \n")

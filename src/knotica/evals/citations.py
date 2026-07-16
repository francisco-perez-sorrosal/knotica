"""Deterministic citation-integrity scoring for the eval harness -- pure, no LLM.

The Phase-2 scalar's citation-validity leg is *integrity only*: of the citations
a baseline answer makes, what fraction resolve to a source stored under the
vault's ``sources/<topic>/`` directory. It reuses the resolution rule of the
constitution's source-citation lint check in :mod:`knotica.core.lint` (the
``CITATION_UNRESOLVED`` finding): a citation key ``<key>`` resolves iff
``sources/<topic>/<key>.md`` exists in the store. Judge-based *faithfulness*
(does the resolved source actually support the claim?) is a deliberately
deferred extension, so this module has no judge/LLM dependency and makes no
network call.

The single public entry point, :func:`integrity`, is a pure read over the
:class:`~knotica.store.VaultStore` protocol: no git, no locking, no mutation. Its
prediction argument is **duck-typed** on the :class:`CitingPrediction` protocol
-- ``integrity`` reads only ``prediction.citations``, so it stays decoupled from
the concrete runner ``Prediction`` (a not-yet-built sibling that would pull an
LLM dependency in). A ``dspy.Prediction`` or the runner's ``Prediction`` both
satisfy the seam structurally, and the scorer passes its ``prediction`` straight
through.

**Empty-citations contract.** A prediction that cites nothing scores ``1.0``,
mirroring the lint check it reuses: a page (or answer) that cites nothing raises
*no* unresolved-citation violation -- it makes no claim the vault cannot back, so
its citation integrity is vacuously perfect. The dominant answer-quality leg of
the per-example score independently penalizes an uncited answer, so this vacuous
reading does not reward citation-dropping.
"""

from collections.abc import Sequence
from typing import Protocol

from knotica.store import PathOutsideVaultError, VaultStore

__all__ = [
    "CitingPrediction",
    "integrity",
]

#: The reserved directory every stored source lives under, addressed as
#: ``sources/<topic>/<citation-key>.md`` (root constitution, reserved names).
#: Mirrors the resolution rule of ``core.lint``'s source-citation check; kept as
#: a private module constant per the codebase convention (``core.lint`` and
#: ``core.page`` each hold their own), rather than importing a private symbol.
_SOURCES_DIR = "sources"

#: Score for a prediction that makes no citations -- vacuously perfect, mirroring
#: the lint check (no citations means no unresolved-citation violation).
_EMPTY_CITATIONS_SCORE = 1.0


class CitingPrediction(Protocol):
    """The one attribute :func:`integrity` reads: a baseline answer's citation keys.

    A structural seam so citation scoring never imports the concrete runner
    ``Prediction`` -- any object exposing ``citations`` (the runner's
    ``Prediction``, a ``dspy.Prediction``, a test stand-in) satisfies it.
    """

    citations: Sequence[str]


def integrity(store: VaultStore, topic: str, prediction: CitingPrediction) -> float:
    """Fraction of ``prediction.citations`` that resolve to a stored source, in ``[0, 1]``.

    A citation key ``<key>`` resolves iff ``sources/<topic>/<key>.md`` exists in
    ``store`` -- the same resolution rule the source-citation lint check uses,
    applied here to an answer's citation list rather than a page's declared
    sources. A key whose path escapes the vault counts as unresolved (never a
    raised error), keeping the function total over arbitrary citation strings.

    Returns ``1.0`` for an empty citation list (the vacuous empty-citations
    contract -- see the module docstring), and otherwise ``resolved / total``.
    """
    citations = prediction.citations
    if not citations:
        return _EMPTY_CITATIONS_SCORE
    resolved = sum(1 for key in citations if _source_exists(store, topic, key))
    return resolved / len(citations)


def _source_exists(store: VaultStore, topic: str, key: str) -> bool:
    """Whether ``sources/<topic>/<key>.md`` exists; an escaping path reads as absent."""
    source_path = f"{_SOURCES_DIR}/{topic}/{key}.md"
    try:
        return store.exists(source_path)
    except (PathOutsideVaultError, ValueError):
        return False

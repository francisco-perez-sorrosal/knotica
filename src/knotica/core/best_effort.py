"""Shared failure-isolation primitive for the loop's best-effort side-effects.

Several loop-side actions are *best-effort*: a discovery drain, a post-merge
trainset grower, housekeeping branch prunes, a quarantine-diff artifact write,
and the regression classifier. Each runs *after* (or *alongside*) a load-bearing
action that has already committed, so a failure in one of them must never crash
the cycle or roll back the action it followed. Historically each site spelled
this out by hand with its own ``try/except`` -- five independently-written
wrappers with subtly different post-swallow behaviour (silent for most, a named
loop-state trace for the classifier, level-split logging for the grower).

:func:`best_effort` collapses that pattern to one context manager. The caught
type stays a parameter (defaulting to :class:`Exception`, never
:class:`BaseException` -- ``KeyboardInterrupt``/``SystemExit`` always propagate)
and any per-site reaction (log, record a trace, nothing) is expressed as an
``on_error`` callback, so a site that swallows *silently* stays silent and a
site that logs keeps its exact log. The yielded :class:`BestEffortAttempt`
carries a ``failed`` flag for the one caller that must branch on whether the
guarded block was swallowed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager


class BestEffortAttempt:
    """Records whether the guarded block swallowed a failure.

    ``failed`` is ``False`` while the block runs and stays ``False`` when it
    completes cleanly; it flips to ``True`` (with ``error`` set) only when
    :func:`best_effort` swallowed a matching exception. Callers that continue
    past the block only on success -- e.g. code that uses values computed inside
    the block -- inspect ``failed`` to decide whether to fall back.
    """

    __slots__ = ("error", "failed")

    def __init__(self) -> None:
        self.failed: bool = False
        self.error: BaseException | None = None


@contextmanager
def best_effort(
    *,
    swallow: type[BaseException] | tuple[type[BaseException], ...] = Exception,
    on_error: Callable[[BaseException], None] | None = None,
) -> Iterator[BestEffortAttempt]:
    """Run a block whose failure must never fail the load-bearing action around it.

    A matching exception (``swallow``, default :class:`Exception`) is caught and
    swallowed; ``on_error`` -- when given -- is invoked with the caught exception
    from inside the ``except`` block (so ``logging``'s ``exc_info=True`` still
    resolves the active exception). Anything not matching ``swallow`` -- notably
    ``KeyboardInterrupt``/``SystemExit`` -- propagates unchanged.
    """
    attempt = BestEffortAttempt()
    try:
        yield attempt
    except swallow as exc:
        attempt.failed = True
        attempt.error = exc
        if on_error is not None:
            on_error(exc)

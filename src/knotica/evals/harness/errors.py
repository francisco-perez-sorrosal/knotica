"""The harness's refusal grammar -- every way ``run_eval`` declines to produce a scalar.

Declared once, in the package's leaf module, because three of the four refusals are
raised from a different stage than the one that catches them: the live-vault guard
fires before the clone, the spend ceiling after the run, and the instrument-failure
rejection between scoring and persistence. Keeping them here is what lets those
stages be separate modules without importing each other.

Every variant carries the house ``NOT_CONFIGURED`` envelope -- the eval cannot
proceed for this topic in this state -- and the variants are told apart by their
concrete type, not by the code. This is the same convention the golden-set errors
follow.
"""

from knotica.core.errors import ErrorCode, KnoticaError

__all__ = [
    "EvalHarnessError",
    "EvalRunError",
    "LiveVaultTargetError",
    "SpendCeilingExceededError",
]


class EvalHarnessError(KnoticaError):
    """The eval harness refused to run or could not produce a trustworthy scalar.

    Carries the house error envelope (``NOT_CONFIGURED`` -- the eval cannot
    proceed for this topic in this state), so an adapter renders a clean,
    actionable message rather than a stack trace. The concrete subclass names
    the specific refusal; the code is shared, discriminated by type -- the same
    convention the golden-set errors follow.
    """


class LiveVaultTargetError(EvalHarnessError):
    """The eval's write target resolved to the live source vault, not a clone.

    A safety backstop: the evaluator must only ever write a throwaway clone, so
    if the clone root and the source root resolve to the same real path the run
    is refused before any write.
    """

    def __init__(self, source_root: str) -> None:
        super().__init__(
            ErrorCode.NOT_CONFIGURED,
            (
                "The eval write target resolved to the live source vault "
                f"({source_root}) instead of a throwaway clone, so the run was "
                "refused to protect the live wiki."
            ),
            fix=(
                "This is an internal safety guard that should never trip; if it "
                "does, the clone step failed -- re-run, and report it if it persists."
            ),
        )


class SpendCeilingExceededError(EvalHarnessError):
    """A per-run token or USD spend ceiling was crossed; the record is not committed."""

    def __init__(self, topic: str, reason: str) -> None:
        super().__init__(
            ErrorCode.NOT_CONFIGURED,
            f"The eval run for topic '{topic}' exceeded its per-run spend ceiling: {reason}.",
            fix=(
                "Raise the ceiling (`--max-total-tokens` / `--max-usd`) if the run is "
                "legitimately large, or investigate a cache-keying regression (a "
                "warm-cache re-run should show a high judge cache hit-rate)."
            ),
        )
        self.topic = topic


class EvalRunError(EvalHarnessError):
    """The run completed but its scalar cannot be trusted (failures or no examples).

    Instrument failures -- a malformed runner response or an unparseable judge
    score -- surface as failure-scored examples; a scalar averaged over silently
    zeroed instrument failures is not trustworthy, so the harness aborts rather
    than emit a misleading record. Also raised when the golden set loaded zero
    examples.
    """

    def __init__(self, topic: str, reason: str) -> None:
        super().__init__(
            ErrorCode.NOT_CONFIGURED,
            f"The eval run for topic '{topic}' cannot produce a trustworthy scalar: {reason}.",
            fix=(
                "Re-run; if instrument failures persist, inspect the worker/judge "
                "model snapshot or the topic's `query.md` prompt -- a persistent "
                "malformed response or unparseable judge score is a broken instrument."
            ),
        )
        self.topic = topic

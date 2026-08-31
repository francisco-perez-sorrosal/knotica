"""Exact per-call token accounting and the post-run spend ceilings it feeds.

One :class:`_UsageAccountingClient` proxy wraps the injected LLM client and is
passed to *both* the baseline runner and the judge, so every billed call in a run
totals through a single accumulator. That total is the only input to the two hard
ceilings enforced here, and to the manifest's ``token_usage`` / ``cost_usd``.

The proxy and the ceilings live together because the ceiling check is meaningless
without the accumulator's exact semantics: both caches sit *above* the proxy, so a
warm hit makes no ``complete`` call and is correctly not billed -- which is what
lets a warm re-run pass a ceiling a cold run breached while still reproducing the
scalar's per-item token measure bit-for-bit.
"""

import threading
from collections.abc import Mapping

from knotica.evals.config import JUDGE_SNAPSHOT, WORKER_SNAPSHOT, HarnessConfig
from knotica.evals.harness.errors import SpendCeilingExceededError
from knotica.evals.llm import Completion, LLMClient, Message

#: Packaged per-model prices in USD per million tokens as ``(input, output)``,
#: keyed on the pinned snapshot ids. Used only to enforce the USD spend ceiling
#: and record ``cost_usd`` in the manifest. Pricing arguably belongs beside the
#: ceilings in ``evals.config``; it lives here because ``config`` ships no price
#: table. An overridden snapshot absent from this map contributes ``0`` USD --
#: the exact token ceiling remains the hard guard.
_MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    JUDGE_SNAPSHOT: (5.0, 25.0),
    WORKER_SNAPSHOT: (3.0, 15.0),
}


class _UsageAccountingClient:
    """An :class:`~knotica.evals.llm.LLMClient` proxy that totals exact token usage.

    Wraps the injected client and delegates :meth:`complete` unchanged, while
    accumulating each response's exact input/output tokens per model snapshot.
    The harness passes one proxy instance to *both* the baseline runner and the
    judge, so every billed call -- worker synthesis and judge sampling -- is
    accounted through a single accumulator. Both consumers cache *above* this
    proxy, so a warm runner- or judge-cache hit makes no ``complete`` call and is
    correctly not counted -- the replayed usage still feeds the scalar's ``T``, but
    the billed total (and thus the ceiling and ``cost_usd``) sees only fresh calls.
    That total drives the per-run spend ceilings and the manifest's ``cost_usd``;
    the proxy never sees or stores the API key (it holds no key of its own).
    """

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        # snapshot -> [input_tokens, output_tokens], accumulated across calls.
        # Lock-guarded: a multi-threaded dspy.Evaluate accounts through one proxy.
        self._by_snapshot: dict[str, list[int]] = {}
        self._usage_lock = threading.Lock()

    @property
    def auth_mode(self) -> str | None:
        """The wrapped client's resolved auth mode (``"oauth"`` / ``"api_key"``), or ``None``.

        Delegates to the real :class:`~knotica.evals.llm.AnthropicClient`, which
        records the mode (never the credential) for the run manifest. An injected
        fake exposes no auth mode, so the mode is ``None`` on a zero-network test
        run -- honest: no real credential was resolved.
        """
        return getattr(self._inner, "auth_mode", None)

    def complete(
        self,
        *,
        snapshot: str,
        system: str,
        messages: list[Message],
        temperature: float = 0.0,
        max_tokens: int,
        json_schema: dict[str, object] | None = None,
    ) -> Completion:
        """Delegate the call and accumulate its exact per-snapshot token usage.

        ``json_schema`` is forwarded verbatim so the proxy stays transparent to the
        structured-outputs contract: the baseline runner passes its answer/citations
        schema, the judge passes none, and either way the wrapped client's request
        shape is unchanged by the proxy.
        """
        completion = self._inner.complete(
            snapshot=snapshot,
            system=system,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_schema=json_schema,
        )
        with self._usage_lock:
            totals = self._by_snapshot.setdefault(snapshot, [0, 0])
            totals[0] += completion.usage.input_tokens
            totals[1] += completion.usage.output_tokens
        return completion

    @property
    def total_tokens(self) -> int:
        """Total input+output tokens across every accounted call."""
        return sum(inp + out for inp, out in self._by_snapshot.values())

    def cost_usd(self, pricing: Mapping[str, tuple[float, float]]) -> float:
        """Total spend in USD from the accumulated per-snapshot usage and ``pricing``.

        An unpriced snapshot (an override absent from ``pricing``) contributes
        ``0`` -- the exact token total remains the hard ceiling regardless.
        """
        total = 0.0
        for snapshot, (inp, out) in self._by_snapshot.items():
            rates = pricing.get(snapshot)
            if rates is None:
                continue
            input_rate, output_rate = rates
            total += (inp / 1_000_000) * input_rate + (out / 1_000_000) * output_rate
        return total

    def usage_summary(self) -> dict[str, dict[str, int]]:
        """Per-snapshot ``{input_tokens, output_tokens}`` for the manifest."""
        return {
            snapshot: {"input_tokens": inp, "output_tokens": out}
            for snapshot, (inp, out) in self._by_snapshot.items()
        }


def _enforce_spend_ceilings(
    topic: str, client: _UsageAccountingClient, config: HarnessConfig
) -> None:
    """Hard-abort if the run's total token or USD spend crossed its ceiling.

    ``dspy.Evaluate`` runs the whole devset in one batch, so this is a post-run
    check: it cannot un-spend, but it refuses to commit a record for an
    over-budget run and surfaces the overage instead of a silent surprise bill.
    """
    total_tokens = client.total_tokens
    if total_tokens > config.max_total_tokens:
        raise SpendCeilingExceededError(
            topic,
            f"{total_tokens} tokens exceeds the {config.max_total_tokens}-token ceiling",
        )
    cost = client.cost_usd(_MODEL_PRICING_USD_PER_MTOK)
    if cost > config.max_usd:
        raise SpendCeilingExceededError(
            topic, f"${cost:.2f} exceeds the ${config.max_usd:.2f} ceiling"
        )

"""Behavioral spec for the judge-response cache.

The cache exists so that a frozen-corpus re-run is near-deterministic and cheap:
when a judge input tuple has been seen before, the stored value is returned
without re-calling the (expensive, mildly non-deterministic) model. These tests
pin that contract and the two properties that make it safe to persist:

- **Round-trip.** A first lookup of a key is a miss -- it computes and stores the
  value; a repeat lookup of the same key is a hit -- it returns the stored value
  without recomputing. The hit/miss counters advance accordingly (the harness
  reports the hit-rate so a silent cache failure is visible).
- **Key stability across process boundaries.** A fresh cache instance over the
  same storage root reuses a persisted entry, and logically-identical inputs hit
  the same entry even when their dict was built in a different insertion order.
  This pins a canonical serialization: an unstable key would silently produce
  zero hits and an unbounded model bill on every re-run.
- **Key sensitivity.** Changing any of ``snapshot`` / ``prompt_hash`` / ``inputs``
  produces a distinct entry, so a rotated model pin or an edited judge prompt can
  never collide with -- and reuse -- a stale score.
- **Corruption tolerance.** A truncated or garbage entry file is treated as a
  miss and self-heals; the cache never propagates a read error as an exception.
- **No secret material.** A credential present in the environment during a
  store/load cycle never lands in the cache's bytes -- the cache persists only
  the key tuple and its value, never anything sourced from ``os.environ``.

Zero network throughout: the cache never touches the LLM, and an autouse guard
replaces ``socket.socket`` so any accidental network attempt fails loudly.

Written concurrently with the cache implementation (disjoint files). The store
mechanics (on-disk layout, JSON encoding) are the implementer's call; the tests
address them only through the public seam. That seam is pinned below to the most
natural reading of the design (see ``# PINNED INTERFACE``); a mismatch surfaces
as a loud ``AttributeError`` / ``TypeError`` at the integration checkpoint, not a
silent wrong value -- reconcile there.
"""

import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from knotica.evals.cache import ResponseCache

#: A synthetic, structurally-plausible sentinel used to prove a credential in the
#: environment never lands in a cache file. NOT a real credential.
SENTINEL_KEY = "sk-ant-api03-SENTINEL-do-not-leak-0000000000"

#: A baseline judge key. ``inputs`` bundles the per-example judge arguments
#: (question / candidate / reference) that the harness hashes into one key.
BASE_SNAPSHOT = "claude-opus-judge-snapshot"
BASE_PROMPT_HASH = "judge-prompt-hash-v1"
BASE_INPUTS: dict[str, str] = {
    "question": "What is X?",
    "candidate": "X is Y.",
    "reference": "X is Y.",
}

#: The same content as ``BASE_INPUTS`` but with the keys inserted in a different
#: order -- the fixture behind the canonical-serialization test. The module-level
#: guard below keeps that test from going vacuous if the literals ever converge.
INPUTS_REORDERED: dict[str, str] = {
    "reference": "X is Y.",
    "candidate": "X is Y.",
    "question": "What is X?",
}
assert list(BASE_INPUTS) != list(INPUTS_REORDERED), (
    "the reordered-inputs fixture must differ in insertion order from the "
    "baseline, or the canonical-serialization test proves nothing"
)


@pytest.fixture(autouse=True)
def _scrub_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from the absent-key state.

    A real ``ANTHROPIC_API_KEY`` exported on the dev machine must never leak into
    these tests. The one test that needs a key present sets a sentinel explicitly.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any socket creation in this module fail loudly.

    The cache is a pure on-disk store -- it must never reach the network. Replacing
    ``socket.socket`` turns any accidental network attempt into a hard failure
    rather than a silent success.
    """

    def _blocked(*args: object, **kwargs: object) -> object:
        raise RuntimeError("network access is forbidden in the judge-cache test suite")

    monkeypatch.setattr(socket, "socket", _blocked)


@pytest.fixture
def cache_root(tmp_path: Path) -> Path:
    """A fresh, isolated storage root per test -- no cross-test state can leak."""
    root = tmp_path / "judge-cache"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# PINNED INTERFACE (documented negotiable -- see module docstring)
#
# The key is the triad (snapshot, prompt_hash, inputs) named verbatim in the
# design. `get_or_compute` is the get-or-memoize seam: it runs the zero-arg
# `compute` callback only on a miss and returns the stored value on a hit -- the
# only shape under which the "no logic in tests" rule and the "miss invokes the
# callback exactly once" contract can both hold. `hits` / `misses` are the
# counters the harness reads for the manifest hit-rate. If any of these names
# diverge, this section plus the two recorder helpers are the single
# reconciliation point.
# ---------------------------------------------------------------------------


def _key(**overrides: Any) -> dict[str, Any]:
    """The baseline key kwargs, with any component overridden for a variation."""
    key: dict[str, Any] = {
        "snapshot": BASE_SNAPSHOT,
        "prompt_hash": BASE_PROMPT_HASH,
        "inputs": dict(BASE_INPUTS),
    }
    key.update(overrides)
    return key


class _Recorder:
    """A zero-arg compute callback that records how many times it was invoked.

    The invocation count is the behavioral proof of a miss: the cache runs it only
    when it has no stored value for the key.
    """

    def __init__(self, value: float) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.value


def _never_called() -> float:
    """A compute callback that fails if run -- passed when only a hit is valid."""
    raise AssertionError("the compute/miss-path callback must not run on a cache hit")


def _cache_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _corrupt_every_file(root: Path) -> int:
    """Overwrite every file under ``root`` with non-JSON garbage; return the count."""
    files = _cache_files(root)
    for path in files:
        path.write_bytes(b"\x00 not valid json at all }{ \xff")
    return len(files)


def _all_bytes_under(root: Path) -> bytes:
    return b"".join(path.read_bytes() for path in _cache_files(root))


# ---------------------------------------------------------------------------
# Round-trip: a stored entry is reused without recomputing
# ---------------------------------------------------------------------------


def test_a_stored_entry_is_returned_on_a_repeat_call_without_recomputing(
    cache_root: Path,
) -> None:
    cache = ResponseCache(cache_root)
    recorder = _Recorder(0.75)

    first = cache.get_or_compute(**_key(), compute=recorder)
    second = cache.get_or_compute(**_key(), compute=_never_called)

    assert first == 0.75, "the miss path computes and returns the value"
    assert second == 0.75, "the hit path returns the same stored value"
    assert recorder.calls == 1, (
        "the value is computed once on the miss and reused on the hit -- the "
        f"miss-path callback ran {recorder.calls} times"
    )


def test_hit_and_miss_counters_advance_with_each_lookup(cache_root: Path) -> None:
    cache = ResponseCache(cache_root)

    cache.get_or_compute(**_key(), compute=_Recorder(0.5))
    assert (cache.misses, cache.hits) == (1, 0), "the first lookup of a key is a miss"

    cache.get_or_compute(**_key(), compute=_never_called)
    assert (cache.misses, cache.hits) == (1, 1), (
        "the second lookup of the same key is a hit -- counters must reflect the "
        "hit-rate the harness reports"
    )


# ---------------------------------------------------------------------------
# Key stability across process boundaries (canonical serialization)
# ---------------------------------------------------------------------------


def test_a_fresh_cache_over_the_same_root_reuses_a_persisted_entry(
    cache_root: Path,
) -> None:
    ResponseCache(cache_root).get_or_compute(**_key(), compute=_Recorder(0.42))

    reopened = ResponseCache(cache_root)
    value = reopened.get_or_compute(**_key(), compute=_never_called)

    assert value == 0.42, (
        "a persisted entry is reused by a fresh cache instance over the same root "
        "-- the on-disk backing survives across process boundaries"
    )


def test_inputs_differing_only_in_key_order_resolve_to_the_same_entry(
    cache_root: Path,
) -> None:
    ResponseCache(cache_root).get_or_compute(
        snapshot=BASE_SNAPSHOT,
        prompt_hash=BASE_PROMPT_HASH,
        inputs=BASE_INPUTS,
        compute=_Recorder(0.9),
    )

    reopened = ResponseCache(cache_root)
    value = reopened.get_or_compute(
        snapshot=BASE_SNAPSHOT,
        prompt_hash=BASE_PROMPT_HASH,
        inputs=INPUTS_REORDERED,
        compute=_never_called,
    )

    assert value == 0.9, (
        "inputs with identical content but a different dict insertion order must "
        "hash to the same key -- the serialization is canonical, not dict-order "
        "dependent (an unstable key means zero hits and an unbounded bill)"
    )


# ---------------------------------------------------------------------------
# Key sensitivity: each component participates in the key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"snapshot": "a-rotated-judge-snapshot"}, id="snapshot"),
        pytest.param({"prompt_hash": "an-edited-prompt-hash"}, id="prompt_hash"),
        pytest.param(
            {"inputs": {"question": "What is X?", "candidate": "X is Y.", "reference": "X is Z."}},
            id="inputs",
        ),
    ],
)
def test_changing_any_key_component_produces_a_distinct_cache_entry(
    cache_root: Path, overrides: Mapping[str, Any]
) -> None:
    cache = ResponseCache(cache_root)
    cache.get_or_compute(**_key(), compute=_Recorder(0.1))

    variant = _Recorder(0.99)
    value = cache.get_or_compute(**_key(**overrides), compute=variant)

    assert variant.calls == 1, (
        "a key differing in one component must miss and recompute, never collide "
        f"with the baseline entry (varied: {dict(overrides)})"
    )
    assert value == 0.99, (
        "the varied lookup returns its own freshly-computed value, not the baseline's cached value"
    )


# ---------------------------------------------------------------------------
# Corruption tolerance: a garbage entry file is a miss, then self-heals
# ---------------------------------------------------------------------------


def test_a_corrupted_entry_file_is_treated_as_a_miss_and_self_heals(
    cache_root: Path,
) -> None:
    ResponseCache(cache_root).get_or_compute(**_key(), compute=_Recorder(0.3))

    corrupted = _corrupt_every_file(cache_root)
    assert corrupted >= 1, (
        "the store must have written at least one file, else corruption is untested"
    )

    healed = ResponseCache(cache_root)
    recompute = _Recorder(0.6)
    value = healed.get_or_compute(**_key(), compute=recompute)

    assert recompute.calls == 1, (
        "a corrupt/garbage entry file is treated as a miss and recomputed, never "
        "raised as an exception"
    )
    assert value == 0.6, "the recomputed value is returned after corruption"

    settled = healed.get_or_compute(**_key(), compute=_never_called)
    assert settled == 0.6, (
        "the miss re-stored a valid entry, so the next lookup hits (self-healing)"
    )


# ---------------------------------------------------------------------------
# No secret material: a credential in the environment never reaches the bytes
# ---------------------------------------------------------------------------


def test_the_api_key_never_appears_in_the_cache_directory_bytes(
    cache_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL_KEY)

    ResponseCache(cache_root).get_or_compute(**_key(), compute=_Recorder(0.5))
    # A load cycle with the sentinel key still present in the environment. A
    # recorder (not `_never_called`) keeps this test independent of persistence:
    # the byte-level leak check is the sole behavior under test here.
    ResponseCache(cache_root).get_or_compute(**_key(), compute=_Recorder(0.5))

    files = _cache_files(cache_root)
    assert files, "the store must have written at least one file, else the leak check is vacuous"
    assert SENTINEL_KEY.encode() not in _all_bytes_under(cache_root), (
        "a sentinel ANTHROPIC_API_KEY present in the environment during store and "
        "load must never be persisted into any cache file -- the cache stores only "
        "the (snapshot, prompt_hash, inputs) key and its value, never the credential"
    )

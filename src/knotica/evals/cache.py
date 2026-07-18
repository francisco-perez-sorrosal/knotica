"""Content-addressed response cache for the eval harness.

A frozen-corpus re-run must reproduce the same scalar bit-for-bit and must not
re-pay for LLM work it already did. This cache is what makes both true: it keys
a computed value on the triple ``(snapshot, prompt_hash, inputs)`` and, on a
warm hit, returns the stored value without invoking the (expensive, billed)
compute callback again.

Design constraints, each load-bearing:

* **Deterministic, collision-resistant keys.** :func:`cache_key` is a small pure
  function that canonically serializes the three components (sorted keys, compact
  UTF-8 JSON) and takes their ``sha256`` digest, so identical logical inputs
  yield an identical key across process restarts and dict orderings -- an
  unstable key would silently miss on every warm re-run (100% cache miss =
  surprise API spend). Labeling the components before hashing prevents the
  field-boundary ambiguity a raw string concatenation would allow (``"a"+"bc"``
  vs ``"ab"+"c"``).
* **Stdlib only, no vault coupling.** Uses ``hashlib`` / ``json`` / ``pathlib`` /
  ``tempfile`` / ``os`` only. The on-disk backing lives under a **constructor-
  supplied** ``storage_root`` (the harness decides where; this module hardcodes
  no path and reads no config). Cache files are runtime state, not vault content,
  so writes go through a local temp-file-plus-atomic-``os.replace`` -- never the
  vault's single-writer transaction path.
* **Self-healing.** A corrupted or unreadable on-disk entry is treated as a
  MISS, never a crash; the next store for that key overwrites it atomically.
* **No secrets.** Entries carry only the supplied inputs and the computed value.
  Nothing is read from the process environment here.

Two consumers share one per-run instance. The LLM-as-judge stores a per-example
median score (a bounded float) keyed on ``(judge_snapshot, judge_prompt_hash,
question, candidate, reference)``; the baseline runner stores its synthesis
completion (a ``{text, usage}`` dict) keyed on ``(worker_snapshot,
synthesis_prompt_hash, user_message)``. The two keyspaces never collide (distinct
snapshots and prompt hashes), so a single cache serves both. The cache itself is
value-agnostic: it stores any JSON-serializable value, so the stored shape is the
caller's choice. An optional per-lookup ``namespace`` lets that one shared cache
report each consumer's hit-rate separately (see :meth:`ResponseCache.stats_for`).
"""

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CacheStats", "ResponseCache", "cache_key"]

#: A JSON-serializable value -- what the cache stores. The judge stores a bounded
#: float score; the cache accepts any JSON value so the shape is the caller's.
JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

#: Suffix for a committed cache entry file (``<key>.json``).
_ENTRY_SUFFIX = ".json"
#: Suffix for the same-directory temp file swapped into place atomically.
_TEMP_SUFFIX = ".tmp"

#: Sentinel distinguishing "absent from cache" from a legitimately stored value
#: (which may itself be ``None`` / ``0`` / ``""`` -- none of which are this object).
_MISS: object = object()


def cache_key(snapshot: str, prompt_hash: str, inputs: object) -> str:
    """Derive a stable, collision-resistant hex key from the three components.

    Pure function. Canonically serializes ``{snapshot, prompt_hash, inputs}``
    (sorted keys, compact separators, UTF-8) and returns the ``sha256`` hex
    digest. Identical logical inputs always produce the same key -- across
    processes and dict orderings -- and the labeled wrapping means distinct
    components can never alias through field-boundary ambiguity.

    ``inputs`` may be any JSON-serializable structure (the judge passes the
    ``(question, candidate, reference)`` tuple).
    """
    canonical = json.dumps(
        {"snapshot": snapshot, "prompt_hash": prompt_hash, "inputs": inputs},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CacheStats:
    """One consumer's hit/miss counts, with the derived hit-rate.

    Returned by :meth:`ResponseCache.stats_for` so the harness can record a
    per-consumer cache breakdown in the run manifest: the runner's synthesis cache
    and the judge's score cache report their own hit-rates even though they share a
    single cache instance.
    """

    hits: int
    misses: int

    @property
    def hit_rate(self) -> float:
        """Fraction of this consumer's lookups served from the cache, or ``0.0`` if none."""
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class ResponseCache:
    """A content-addressed cache with an optional on-disk backing.

    An in-memory dict is the fast layer; when ``storage_root`` is supplied,
    entries also persist as one ``<key>.json`` file per key so a later process
    (a warm-cache re-run of the same generation) reuses them. Absent a
    ``storage_root`` the cache is memory-only.

    Args:
        storage_root: Directory for the on-disk backing. The caller owns this
            path -- the cache creates it lazily on first store and hardcodes no
            location. ``None`` keeps the cache purely in memory.
    """

    def __init__(self, storage_root: str | Path | None = None) -> None:
        self._storage_root = Path(storage_root) if storage_root is not None else None
        self._memory: dict[str, JsonValue] = {}
        self._hits = 0
        self._misses = 0
        # namespace -> [hits, misses]: per-consumer counters for a shared cache.
        self._namespace_stats: dict[str, list[int]] = {}
        # Thread safety (multi-threaded dspy.Evaluate shares one cache): the
        # stats lock guards counters/memory bookkeeping; per-key locks make a
        # concurrent miss on the SAME key compute exactly once (the second
        # thread waits, then reads the first thread's stored value).
        self._stats_lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._key_locks_guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._key_locks_guard:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    @property
    def hits(self) -> int:
        """How many lookups were served from the cache (memory or disk)."""
        return self._hits

    @property
    def misses(self) -> int:
        """How many lookups had to invoke the compute callback."""
        return self._misses

    @property
    def hit_rate(self) -> float:
        """Fraction of lookups served from the cache, or ``0.0`` if none yet."""
        total = self._hits + self._misses
        return self._hits / total if total else 0.0

    def get_or_compute(
        self,
        *,
        snapshot: str,
        prompt_hash: str,
        inputs: object,
        compute: Callable[[], JsonValue],
        namespace: str | None = None,
    ) -> JsonValue:
        """Return the cached value for the key, or compute, store, and return it.

        On a hit (memory or disk), ``compute`` is never called -- the whole point,
        so a warm re-run pays nothing. On a miss, ``compute`` is called exactly
        once and its result is stored in memory and (if configured) on disk.

        ``namespace`` optionally tags the lookup for :meth:`stats_for`, so one
        shared cache can report each consumer's hit-rate separately. It never
        participates in the key -- two consumers with colliding keys would still
        share the value; only the per-namespace counters are segmented. Aggregate
        counters advance regardless, so an unlabeled lookup is still counted.
        """
        key = cache_key(snapshot, prompt_hash, inputs)
        with self._lock_for(key):
            cached = self._lookup(key)
            if cached is not _MISS:
                with self._stats_lock:
                    self._hits += 1
                    self._record_namespace(namespace, hit=True)
                return cached
            with self._stats_lock:
                self._misses += 1
                self._record_namespace(namespace, hit=False)
            value = compute()
            self._memory[key] = value
            self._write_disk(key, value)
            return value

    def _record_namespace(self, namespace: str | None, *, hit: bool) -> None:
        """Advance the per-namespace hit/miss counter when a namespace is supplied."""
        if namespace is None:
            return
        counts = self._namespace_stats.setdefault(namespace, [0, 0])
        if hit:
            counts[0] += 1
        else:
            counts[1] += 1

    def stats_for(self, namespace: str) -> CacheStats:
        """Return the :class:`CacheStats` recorded under ``namespace`` (zeros if unseen)."""
        hits, misses = self._namespace_stats.get(namespace, (0, 0))
        return CacheStats(hits=hits, misses=misses)

    def _lookup(self, key: str) -> JsonValue | object:
        """Return the stored value for ``key``, or :data:`_MISS`.

        Checks memory first, then the on-disk backing; a disk hit is promoted
        into memory so subsequent lookups are served without re-reading the file.
        """
        if key in self._memory:
            return self._memory[key]
        value = self._read_disk(key)
        if value is not _MISS:
            self._memory[key] = value  # type: ignore[assignment]
        return value

    def _entry_path(self, key: str) -> Path | None:
        """The on-disk file for ``key`` (``<root>/<key>.json``), or ``None`` if memory-only."""
        if self._storage_root is None:
            return None
        return self._storage_root / f"{key}{_ENTRY_SUFFIX}"

    def _read_disk(self, key: str) -> JsonValue | object:
        """Load ``key``'s on-disk entry, or :data:`_MISS` if absent/corrupted.

        A missing file, an OS-level read error, or malformed JSON all resolve to
        a MISS -- the entry self-heals when the value is recomputed and stored.
        """
        entry_path = self._entry_path(key)
        if entry_path is None:
            return _MISS
        try:
            return json.loads(entry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _MISS

    def _write_disk(self, key: str, value: JsonValue) -> None:
        """Persist ``value`` for ``key`` via a same-directory temp file + atomic swap.

        No-op when the cache is memory-only. Mirrors the vault store's atomicity
        (temp in the target dir, flush + fsync, ``os.replace``) but stays off the
        single-writer transaction path -- cache files are runtime state, not vault
        content. On any failure the temp file is removed and the entry is left as
        it was.
        """
        entry_path = self._entry_path(key)
        if entry_path is None:
            return
        entry_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(dir=entry_path.parent, suffix=_TEMP_SUFFIX)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
                json.dump(value, temp_file, ensure_ascii=False, sort_keys=True)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_name, entry_path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

"""Smart in-memory caching: LRU eviction + per-entry TTL + memory bound.

One :class:`SmartCache` instance backs all tools. Entries carry their own
TTL (searches expire quickly, currency rates hourly, wiki summaries
daily), eviction is LRU once the entry-count or approximate memory limit
is reached, and :meth:`get_or_compute` serializes concurrent identical
requests so a duplicated in-flight query is computed only once.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import OrderedDict
from typing import Callable, Optional

from utils import (
    CACHE_MAX_ENTRIES,
    CACHE_MAX_MEMORY_MB,
    CACHE_TTL_SECONDS,
    STATS,
    get_logger,
)

logger = get_logger("cache")


class _Entry:
    __slots__ = ("value", "expires_at", "size")

    def __init__(self, value: str, expires_at: float, size: int) -> None:
        self.value = value
        self.expires_at = expires_at
        self.size = size


def _sizeof(value: str) -> int:
    """Approximate memory footprint of a cached string in bytes."""
    try:
        return sys.getsizeof(value)
    except TypeError:  # pragma: no cover - defensive
        return len(value) * 2


class SmartCache:
    """Thread-safe LRU cache with per-entry TTL and a memory budget.

    Designed for a long-running single process on constrained hardware:
    the cache can never grow past ``max_entries`` items or ``max_memory``
    bytes of stored strings, whichever is hit first.
    """

    def __init__(
        self,
        default_ttl: int = CACHE_TTL_SECONDS,
        max_entries: int = CACHE_MAX_ENTRIES,
        max_memory_mb: int = CACHE_MAX_MEMORY_MB,
    ) -> None:
        self._default_ttl = default_ttl
        self._max_entries = max(1, max_entries)
        self._max_memory = max(1, max_memory_mb) * 1024 * 1024
        self._store: OrderedDict[str, _Entry] = OrderedDict()
        self._memory_used = 0
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._hits = 0
        self._misses = 0

    # -- core operations ---------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if now >= entry.expires_at:
                self._remove_locked(key)
                self._misses += 1
                return None
            self._store.move_to_end(key)  # LRU touch
            self._hits += 1
            return entry.value

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        if value is None:
            return
        ttl = ttl if ttl is not None else self._default_ttl
        size = _sizeof(value)
        if size > self._max_memory:
            logger.warning("Value for key '%s' exceeds cache memory budget, not cached", key)
            return
        with self._lock:
            if key in self._store:
                self._remove_locked(key)
            self._store[key] = _Entry(value, time.time() + ttl, size)
            self._memory_used += size
            self._evict_locked()

    def get_or_compute(
        self,
        key: str,
        factory: Callable[[], str],
        ttl: Optional[int] = None,
        cache_predicate: Optional[Callable[[str], bool]] = None,
    ) -> tuple[str, bool]:
        """Return ``(value, was_cache_hit)``, computing and storing on miss.

        Identical concurrent requests share one per-key lock, so the same
        expensive search is never executed twice at the same time: the
        second caller blocks briefly and then reads the cached result.
        ``cache_predicate`` decides whether a computed value is worth
        caching (error messages should not be).
        """
        value = self.get(key)
        if value is not None:
            STATS.record_cache_hit()
            return value, True

        key_lock = self._lock_for(key)
        with key_lock:
            value = self.get(key)  # may have been computed while waiting
            if value is not None:
                STATS.record_cache_hit()
                return value, True
            value = factory()
            if value and (cache_predicate is None or cache_predicate(value)):
                self.set(key, value, ttl)
            return value, False
        # key locks are intentionally kept for reuse; the pool is bounded
        # by the number of distinct keys seen between clear() calls and is
        # trimmed in _evict_locked when it grows large.

    def clear(self) -> int:
        """Empty the cache entirely. Returns the number of removed entries."""
        with self._lock:
            count = len(self._store)
            self._store.clear()
            self._key_locks.clear()
            self._memory_used = 0
            return count

    def purge_expired(self) -> int:
        """Drop expired entries. Returns the number removed."""
        now = time.time()
        with self._lock:
            expired = [k for k, e in self._store.items() if now >= e.expires_at]
            for key in expired:
                self._remove_locked(key)
            return len(expired)

    def stats(self) -> dict:
        with self._lock:
            return {
                "entries": len(self._store),
                "memory_bytes": self._memory_used,
                "hits": self._hits,
                "misses": self._misses,
            }

    # -- internals ----------------------------------------------------------

    def _lock_for(self, key: str) -> threading.Lock:
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def _remove_locked(self, key: str) -> None:
        entry = self._store.pop(key, None)
        if entry is not None:
            self._memory_used -= entry.size

    def _evict_locked(self) -> None:
        while self._store and (
            len(self._store) > self._max_entries or self._memory_used > self._max_memory
        ):
            _, entry = self._store.popitem(last=False)  # oldest (LRU) first
            self._memory_used -= entry.size
        # Keep the key-lock pool from growing unbounded on high-cardinality
        # workloads: once it far exceeds the entry budget, drop locks for
        # keys that are no longer cached (safe: callers of an in-flight
        # computation already hold their own reference to the lock object).
        if len(self._key_locks) > self._max_entries * 4:
            live = set(self._store)
            self._key_locks = {k: v for k, v in self._key_locks.items() if k in live}


cache = SmartCache()

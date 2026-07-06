"""SmartCache: TTL expiry, LRU eviction, memory bound, dedup, clearing."""

import threading
import time

from cache import SmartCache


def test_set_get_roundtrip():
    c = SmartCache(default_ttl=60, max_entries=10, max_memory_mb=1)
    c.set("k", "value")
    assert c.get("k") == "value"


def test_miss_returns_none():
    c = SmartCache(default_ttl=60, max_entries=10, max_memory_mb=1)
    assert c.get("absent") is None


def test_ttl_expiry():
    c = SmartCache(default_ttl=60, max_entries=10, max_memory_mb=1)
    c.set("k", "value", ttl=0)  # expires immediately
    time.sleep(0.01)
    assert c.get("k") is None


def test_per_entry_ttl_overrides_default():
    c = SmartCache(default_ttl=0, max_entries=10, max_memory_mb=1)
    c.set("k", "value", ttl=60)
    assert c.get("k") == "value"


def test_lru_eviction_order():
    c = SmartCache(default_ttl=60, max_entries=3, max_memory_mb=1)
    c.set("a", "1")
    c.set("b", "2")
    c.set("c", "3")
    c.get("a")  # touch "a" so "b" becomes the oldest
    c.set("d", "4")  # evicts "b"
    assert c.get("a") == "1"
    assert c.get("b") is None
    assert c.get("c") == "3"
    assert c.get("d") == "4"


def test_memory_bound_evicts():
    c = SmartCache(default_ttl=60, max_entries=1000, max_memory_mb=1)
    big = "x" * 300_000  # ~300KB values
    for i in range(10):  # ~3MB total, must stay under 1MB
        c.set(f"k{i}", big)
    stats = c.stats()
    assert stats["memory_bytes"] <= 1024 * 1024
    assert stats["entries"] < 10


def test_oversized_value_not_cached():
    c = SmartCache(default_ttl=60, max_entries=10, max_memory_mb=1)
    c.set("huge", "x" * (2 * 1024 * 1024))
    assert c.get("huge") is None


def test_clear():
    c = SmartCache(default_ttl=60, max_entries=10, max_memory_mb=1)
    c.set("a", "1")
    c.set("b", "2")
    removed = c.clear()
    assert removed == 2
    assert c.get("a") is None
    assert c.stats()["entries"] == 0
    assert c.stats()["memory_bytes"] == 0


def test_purge_expired():
    c = SmartCache(default_ttl=60, max_entries=10, max_memory_mb=1)
    c.set("stale", "1", ttl=0)
    c.set("fresh", "2", ttl=60)
    time.sleep(0.01)
    assert c.purge_expired() == 1
    assert c.get("fresh") == "2"


def test_get_or_compute_caches_success():
    c = SmartCache(default_ttl=60, max_entries=10, max_memory_mb=1)
    calls = []

    def factory():
        calls.append(1)
        return "computed"

    v1, hit1 = c.get_or_compute("k", factory)
    v2, hit2 = c.get_or_compute("k", factory)
    assert (v1, hit1) == ("computed", False)
    assert (v2, hit2) == ("computed", True)
    assert len(calls) == 1


def test_get_or_compute_respects_predicate():
    c = SmartCache(default_ttl=60, max_entries=10, max_memory_mb=1)
    v, hit = c.get_or_compute("k", lambda: "Error: boom",
                              cache_predicate=lambda r: not r.startswith("Error"))
    assert v == "Error: boom" and hit is False
    assert c.get("k") is None  # errors are not cached


def test_get_or_compute_deduplicates_concurrent_calls():
    c = SmartCache(default_ttl=60, max_entries=10, max_memory_mb=1)
    calls = []
    barrier = threading.Barrier(4)
    results = []

    def factory():
        calls.append(1)
        time.sleep(0.05)
        return "expensive"

    def worker():
        barrier.wait()
        value, _ = c.get_or_compute("same-key", factory)
        results.append(value)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == ["expensive"] * 4
    assert len(calls) == 1  # computed exactly once despite 4 concurrent callers


def test_memory_accounting_on_overwrite():
    c = SmartCache(default_ttl=60, max_entries=10, max_memory_mb=1)
    c.set("k", "x" * 1000)
    first = c.stats()["memory_bytes"]
    c.set("k", "y" * 1000)
    second = c.stats()["memory_bytes"]
    assert abs(first - second) < 200  # replaced, not double-counted
    assert c.stats()["entries"] == 1

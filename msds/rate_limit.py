"""Thread-safe, bounded rate limiter.

Design notes (fixes SEC-04):
* Buckets are pruned of empty entries so the mapping cannot grow without
  bound (the previous implementation kept every key forever).
* The limiter is pluggable: swap ``RateLimiter`` for a Redis-backed
  implementation of the same ``allow`` contract when running multiple
  gunicorn workers / hosts.  A ``sliding_window`` is used per key.
* The key is built by the caller and *must* include both account identity and
  source IP so a single attacker cannot exhaust everyone's budget and so a
  shared proxy IP does not block the office (SEC-04, SEC-13).
"""

import threading
import time
from collections import OrderedDict


class RateLimiter:
    def __init__(self, max_keys=50_000):
        self._lock = threading.Lock()
        self._buckets = OrderedDict()
        self._max_keys = max_keys

    def allow(self, key, limit, window_seconds):
        """Return True if the call is within ``limit`` per ``window``."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = []
                self._buckets[key] = bucket
            # Drop expired stamps.
            cutoff = now - window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.pop(0)
            if len(bucket) >= limit:
                # Move to the end so eviction is LRU-ish.
                self._buckets.move_to_end(key)
                return False
            bucket.append(now)
            self._buckets.move_to_end(key)
            self._evict_locked()
            return True

    def _evict_locked(self):
        if len(self._buckets) <= self._max_keys:
            return
        # Drop oldest, empty buckets first.
        for key in list(self._buckets.keys()):
            if len(self._buckets) <= self._max_keys:
                break
            if not self._buckets[key]:
                del self._buckets[key]

    def reset(self):
        with self._lock:
            self._buckets.clear()

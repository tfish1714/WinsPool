"""services/rate_limit_service.py -- in-process sliding-window rate limiting.

State lives in this process's memory: it resets on restart and is not shared
across instances. That is deliberate and correct for this app because
production is pinned to a single Cloud Run instance (deploy/deploy.ps1
--max-instances=1, also required by the draft room's in-process WebSocket
state). This is abuse protection, not a correctness guarantee.
"""
import math
import os
import time
from typing import Callable, Optional


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float, clock: Callable[[], float] = time.time):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._buckets: dict[str, list[float]] = {}

    def _evict_idle(self, cutoff: float) -> None:
        for key in [k for k, ts in self._buckets.items() if not ts or ts[-1] < cutoff]:
            del self._buckets[key]

    def check(self, key: str) -> tuple[bool, int]:
        """Record and allow the request, or refuse it.

        Returns (allowed, retry_after_seconds). Refused requests are NOT
        recorded, so hammering a blocked endpoint does not extend the block.
        """
        now = self._clock()
        cutoff = now - self.window_seconds
        self._evict_idle(cutoff)
        bucket = [t for t in self._buckets.get(key, []) if t > cutoff]
        if len(bucket) >= self.max_requests:
            self._buckets[key] = bucket
            retry = math.ceil(bucket[0] + self.window_seconds - now)
            return False, max(1, retry)
        bucket.append(now)
        self._buckets[key] = bucket
        return True, 0

    def reset(self) -> None:
        self._buckets.clear()


_registry: dict[str, RateLimiter] = {}


def get_limiter(name: str, max_requests: int, window_seconds: float = 60.0) -> RateLimiter:
    if name not in _registry:
        _registry[name] = RateLimiter(max_requests, window_seconds)
    return _registry[name]


def reset_all() -> None:
    for limiter in _registry.values():
        limiter.reset()


def client_ip(request, trusted_hops: Optional[int] = None) -> str:
    """Best-effort client address behind Cloud Run's proxy.

    Takes the X-Forwarded-For entry `trusted_hops` positions from the RIGHT
    (default env TRUSTED_PROXY_HOPS, 1): entries further left are supplied by
    the client and can be forged to dodge a per-IP limit. Falls back to the
    socket peer when the header is missing or shorter than the hop count.
    """
    if trusted_hops is None:
        try:
            trusted_hops = max(1, int(os.environ.get("TRUSTED_PROXY_HOPS", "1")))
        except ValueError:
            trusted_hops = 1
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= trusted_hops:
            return parts[-trusted_hops]
    return request.client.host if request.client else "unknown"

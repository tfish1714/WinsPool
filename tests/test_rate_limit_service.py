"""services/rate_limit_service.py -- sliding-window per-key limiter and the
proxy-aware client IP helper."""
from types import SimpleNamespace

import pytest

from services.rate_limit_service import RateLimiter, client_ip, get_limiter, reset_all


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_allows_up_to_max_then_blocks():
    clock = _Clock()
    lim = RateLimiter(3, 60, clock=clock)
    assert [lim.check("ip")[0] for _ in range(3)] == [True, True, True]
    allowed, retry = lim.check("ip")
    assert allowed is False and retry >= 1


def test_retry_after_counts_down_from_oldest_request():
    clock = _Clock()
    lim = RateLimiter(1, 60, clock=clock)
    lim.check("ip")
    clock.now += 45
    allowed, retry = lim.check("ip")
    assert allowed is False and retry == 15


def test_window_slides_and_allows_again():
    clock = _Clock()
    lim = RateLimiter(2, 60, clock=clock)
    lim.check("ip"); lim.check("ip")
    assert lim.check("ip")[0] is False
    clock.now += 61
    assert lim.check("ip")[0] is True


def test_keys_are_independent():
    lim = RateLimiter(1, 60, clock=_Clock())
    assert lim.check("a")[0] is True
    assert lim.check("b")[0] is True
    assert lim.check("a")[0] is False


def test_blocked_requests_are_not_recorded():
    clock = _Clock()
    lim = RateLimiter(1, 60, clock=clock)
    lim.check("ip")
    for _ in range(10):
        lim.check("ip")
    clock.now += 61
    assert lim.check("ip")[0] is True   # not extended by the blocked attempts


def test_idle_keys_are_evicted():
    clock = _Clock()
    lim = RateLimiter(5, 60, clock=clock)
    for i in range(50):
        lim.check(f"ip{i}")
    clock.now += 120
    lim.check("fresh")
    assert len(lim._buckets) == 1


def _req(xff=None, host="9.9.9.9"):
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))


def test_client_ip_uses_rightmost_hop_by_default():
    assert client_ip(_req("6.6.6.6, 1.2.3.4")) == "1.2.3.4"


def test_client_ip_ignores_spoofed_left_entries():
    assert client_ip(_req("evil-1, evil-2, 1.2.3.4")) == "1.2.3.4"


def test_client_ip_respects_trusted_hops():
    assert client_ip(_req("1.1.1.1, 2.2.2.2, 3.3.3.3"), trusted_hops=2) == "2.2.2.2"


def test_client_ip_falls_back_when_header_short_or_missing():
    assert client_ip(_req(None)) == "9.9.9.9"
    assert client_ip(_req("1.1.1.1"), trusted_hops=3) == "9.9.9.9"


def test_client_ip_no_client_object():
    req = SimpleNamespace(headers={}, client=None)
    assert client_ip(req) == "unknown"


def test_registry_reuses_named_limiter_and_reset_all_clears():
    a = get_limiter("t-registry", 1)
    b = get_limiter("t-registry", 99)
    assert a is b
    a.check("ip")
    assert a.check("ip")[0] is False
    reset_all()
    assert a.check("ip")[0] is True

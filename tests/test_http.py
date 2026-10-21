import time

import httpx

from pmcal.http import ApiClient, RateLimiter


def test_rate_limiter_spaces_calls():
    limiter = RateLimiter(rate=50)
    start = time.monotonic()
    for _ in range(6):
        limiter.wait()
    # Six calls at 50/s need at least five 20 ms gaps.
    assert time.monotonic() - start >= 0.09


def test_get_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": True})

    client = ApiClient("https://example.test", rate=1000)
    client._client = httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    assert client.get("/x") == {"ok": True}
    assert calls["n"] == 2


def test_get_returns_none_on_404():
    client = ApiClient("https://example.test", rate=1000)
    client._client = httpx.Client(
        base_url="https://example.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(404)),
    )
    assert client.get("/missing") is None

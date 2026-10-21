"""A small HTTP client with a shared rate limit and retries.

Both APIs are public but rate-limited, and the fetchers make tens of thousands of
requests, so every request goes through one throttled, retrying client.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logging.getLogger(__name__)

USER_AGENT = (
    "pmcal/0.1 (calibration research; github.com/hrishi-tailor/prediction-market-calibration)"
)


class RateLimiter:
    """Allows at most `rate` calls per second across threads."""

    def __init__(self, rate: float) -> None:
        self._interval = 1.0 / rate
        self._lock = threading.Lock()
        self._next = time.monotonic()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            time.sleep(delay)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


class ApiClient:
    def __init__(self, base_url: str, rate: float, timeout: float = 30.0) -> None:
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout, headers={"User-Agent": USER_AGENT}
        )
        self._limiter = RateLimiter(rate)

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential_jitter(initial=1, max=60),
        stop=stop_after_attempt(8),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GETs `path` and returns the decoded JSON, or None on a 404."""
        self._limiter.wait()
        response = self._client.get(path, params=params)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

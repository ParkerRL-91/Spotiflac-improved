"""
Retry and rate-limit handling for the flaky network calls spotdl makes.

Two pieces:

- :func:`retry_call` - run a callable with exponential backoff + jitter,
  honouring ``Retry-After`` when a rate-limit response carries one.
- :class:`AdaptiveRateLimiter` - a process-wide gate that slows everything
  down when 429s start appearing and speeds back up when they stop. Over a
  run of thousands of tracks this is the difference between getting
  soft-banned and finishing.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable, Optional, Tuple, Type, TypeVar

logger = logging.getLogger("spotiflac.retry")

T = TypeVar("T")

__all__ = ["retry_call", "AdaptiveRateLimiter", "is_rate_limit_error"]


def _retry_after_seconds(exc: BaseException) -> Optional[float]:
    """Extract a ``Retry-After`` hint from common HTTP exception shapes."""
    # spotipy.SpotifyException carries .headers; requests carries .response.
    headers = getattr(exc, "headers", None)
    if headers is None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
    if headers:
        value = headers.get("Retry-After") or headers.get("retry-after")
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def is_rate_limit_error(exc: BaseException) -> bool:
    """Best-effort detection of a rate-limit / throttling error."""
    status = getattr(exc, "http_status", None) or getattr(exc, "status", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return "rate" in text and "limit" in text or "429" in text or "too many" in text


def retry_call(
    func: Callable[[], T],
    *,
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: float = 0.3,
    retry_on: Tuple[Type[BaseException], ...] = (Exception,),
    rate_limiter: "Optional[AdaptiveRateLimiter]" = None,
    description: str = "operation",
    should_cancel: Optional[Callable[[], bool]] = None,
) -> T:
    """
    Call ``func`` with exponential backoff.

    ### Arguments
    - max_attempts: total tries before giving up.
    - base_delay / max_delay: backoff bounds in seconds.
    - jitter: fraction of the delay to randomise (avoids thundering herd).
    - retry_on: exception types that trigger a retry; anything else re-raises.
    - rate_limiter: optional shared limiter notified on 429s.
    - should_cancel: optional predicate; if it returns True we stop retrying.
    """
    attempt = 0
    while True:
        attempt += 1
        if rate_limiter is not None:
            rate_limiter.acquire()
        try:
            result = func()
            if rate_limiter is not None:
                rate_limiter.report_success()
            return result
        except retry_on as exc:  # noqa: PERF203 - clarity over micro-perf
            rate_limited = is_rate_limit_error(exc)
            if rate_limited and rate_limiter is not None:
                rate_limiter.report_rate_limited()

            if should_cancel is not None and should_cancel():
                logger.info("Cancelled during %s; not retrying", description)
                raise

            if attempt >= max_attempts:
                logger.warning(
                    "%s failed after %d attempts: %s", description, attempt, exc
                )
                raise

            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            hint = _retry_after_seconds(exc)
            if hint is not None:
                delay = max(delay, hint)
            delay += random.uniform(0, delay * jitter)

            logger.info(
                "%s failed (attempt %d/%d)%s: %s - retrying in %.1fs",
                description,
                attempt,
                max_attempts,
                " [rate-limited]" if rate_limited else "",
                exc,
                delay,
            )
            _interruptible_sleep(delay, should_cancel)


def _interruptible_sleep(
    seconds: float, should_cancel: Optional[Callable[[], bool]]
) -> None:
    """Sleep in small slices so cancellation is responsive during backoff."""
    if should_cancel is None:
        time.sleep(seconds)
        return
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if should_cancel():
            return
        time.sleep(min(0.25, deadline - time.monotonic()))


class AdaptiveRateLimiter:
    """
    Process-wide adaptive throttle.

    Starts with no delay. Each rate-limit report increases a cooldown applied
    before subsequent acquisitions (additive-increase); sustained success
    decays it back toward zero (multiplicative-decrease). This keeps a big run
    just under the provider's limit instead of repeatedly slamming into it.
    """

    def __init__(
        self,
        *,
        step: float = 1.0,
        max_delay: float = 30.0,
        decay: float = 0.9,
        decay_after_successes: int = 10,
    ):
        self._step = step
        self._max_delay = max_delay
        self._decay = decay
        self._decay_after = decay_after_successes
        self._delay = 0.0
        self._successes = 0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            delay = self._delay
        if delay > 0:
            time.sleep(delay)

    def report_rate_limited(self) -> None:
        with self._lock:
            self._successes = 0
            self._delay = min(self._max_delay, self._delay + self._step)
            logger.debug("Rate limiter cooldown raised to %.1fs", self._delay)

    def report_success(self) -> None:
        with self._lock:
            if self._delay <= 0:
                return
            self._successes += 1
            if self._successes >= self._decay_after:
                self._successes = 0
                self._delay = max(0.0, self._delay * self._decay)
                if self._delay < 0.05:
                    self._delay = 0.0

    @property
    def current_delay(self) -> float:
        with self._lock:
            return self._delay

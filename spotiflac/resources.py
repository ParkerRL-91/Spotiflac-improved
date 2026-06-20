"""
Resource control for long runs: bounded concurrency and a memory watchdog.

spotdl already limits download concurrency with a semaphore sized by the
``threads`` setting; here we (a) pick a sane default thread count for the host
and (b) run a lightweight background watchdog that logs memory growth and
nudges the garbage collector, so a multi-hour run doesn't quietly balloon.
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("spotiflac.resources")

__all__ = ["safe_thread_count", "MemoryWatchdog", "current_rss_mb"]


def safe_thread_count(requested: Optional[int] = None) -> int:
    """
    Choose a conservative concurrency level.

    Too many concurrent yt-dlp + ffmpeg processes is a common cause of
    instability on long runs (fd exhaustion, memory spikes, rate-limit
    bans). We cap the default well below the core count.
    """
    cpu = os.cpu_count() or 4
    if requested and requested > 0:
        return max(1, min(requested, cpu * 2))
    return max(1, min(4, cpu))


def current_rss_mb() -> Optional[float]:
    """Resident set size in MiB, or None if it can't be determined."""
    try:
        import psutil  # type: ignore

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        pass
    # Fallback: Linux /proc.
    try:
        with open(f"/proc/{os.getpid()}/statm", "r", encoding="utf-8") as handle:
            pages = int(handle.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except Exception:
        return None


class MemoryWatchdog:
    """
    Background thread that samples RSS and reacts to growth.

    - Logs memory at a steady cadence so a leak is visible in the job log.
    - Forces a ``gc.collect()`` when RSS climbs past a soft threshold.
    - Invokes ``on_pressure`` (if given) when a hard threshold is crossed so
      the orchestrator can, e.g., flush state and shrink batch size.
    """

    def __init__(
        self,
        *,
        interval: float = 30.0,
        soft_limit_mb: float = 1024.0,
        hard_limit_mb: float = 2048.0,
        on_pressure: Optional[Callable[[float], None]] = None,
    ):
        self._interval = interval
        self._soft = soft_limit_mb
        self._hard = hard_limit_mb
        self._on_pressure = on_pressure
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if current_rss_mb() is None:
            logger.debug("Memory watchdog disabled (RSS unavailable on host)")
            return
        self._thread = threading.Thread(
            target=self._run, name="spotiflac-memwatch", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        peak = 0.0
        while not self._stop.wait(self._interval):
            rss = current_rss_mb()
            if rss is None:
                continue
            peak = max(peak, rss)
            logger.debug("Memory: %.0f MiB (peak %.0f MiB)", rss, peak)
            if rss >= self._soft:
                collected = gc.collect()
                logger.info(
                    "Memory %.0f MiB over soft limit; gc collected %d objects",
                    rss,
                    collected,
                )
            if rss >= self._hard and self._on_pressure is not None:
                logger.warning("Memory %.0f MiB over hard limit", rss)
                try:
                    self._on_pressure(rss)
                except Exception:  # pragma: no cover - defensive
                    logger.exception("Memory pressure handler failed")

    def __enter__(self) -> "MemoryWatchdog":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

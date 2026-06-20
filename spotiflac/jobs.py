"""
The download orchestrator.

:class:`DownloadJob` runs a spotdl download with the stability machinery
layered on top:

- resumes from a :class:`~spotiflac.checkpoint.Checkpoint`, skipping tracks
  already completed on a previous run;
- processes tracks in bounded batches so memory stays flat and progress is
  checkpointed frequently;
- retries individual tracks that fail, with exponential backoff, up to a
  per-track attempt budget;
- runs a memory watchdog and an adaptive rate limiter for the whole run;
- streams structured progress events to a callback (the UI/WebSocket layer);
- can be paused/cancelled cleanly at a batch boundary.

:class:`JobManager` owns the one-time Spotify client init and tracks jobs by id.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from spotiflac.checkpoint import Checkpoint, TrackStatus
from spotiflac.logging_setup import (
    attach_job_logger,
    detach_job_logger,
    write_failure_report,
)
from spotiflac.paths import job_id_for
from spotiflac.resources import MemoryWatchdog, safe_thread_count
from spotiflac.retry import AdaptiveRateLimiter, retry_call

logger = logging.getLogger("spotiflac.jobs")

__all__ = ["JobState", "JobConfig", "DownloadJob", "JobManager"]


class JobState(str, Enum):
    QUEUED = "queued"
    SEARCHING = "searching"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobConfig:
    """User-facing knobs for a job; ``settings`` flows through to spotdl."""

    query: List[str]
    output: str = "{artists} - {title}.{output-ext}"
    settings: Dict[str, Any] = field(default_factory=dict)
    batch_size: int = 50
    max_track_attempts: int = 4
    backoff_base: float = 2.0
    backoff_max: float = 120.0


EventCallback = Callable[[Dict[str, Any]], None]


class DownloadJob:
    """A single resumable download job, run on its own thread."""

    def __init__(
        self,
        config: JobConfig,
        rate_limiter: AdaptiveRateLimiter,
        on_event: Optional[EventCallback] = None,
    ):
        self.config = config
        self.id = job_id_for(config.query, config.output)
        self.uid = uuid.uuid4().hex[:8]  # distinguishes re-runs in the UI
        self.state = JobState.QUEUED
        self.error: Optional[str] = None
        self._on_event = on_event
        self._rate_limiter = rate_limiter
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._log_handler = None
        self.checkpoint = Checkpoint.load_or_create(
            self.id, config.query, config.output
        )

    # ----------------------------------------------------------- control API
    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_guarded, name=f"spotiflac-job-{self.id}", daemon=True
        )
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.clear()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def should_cancel(self) -> bool:
        return self._cancel.is_set()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # ----------------------------------------------------------------- events
    def status(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "uid": self.uid,
            "state": self.state.value,
            "error": self.error,
            "query": self.config.query,
            "output": self.config.output,
            "counts": self.checkpoint.counts(),
            "rate_limit_delay": round(self._rate_limiter.current_delay, 2),
        }

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self._on_event is None:
            return
        event = {"type": event_type, "job": self.status()}
        event.update(payload)
        try:
            self._on_event(event)
        except Exception:  # pragma: no cover - never let UI errors kill a run
            logger.exception("Event callback failed")

    def _set_state(self, state: JobState) -> None:
        self.state = state
        self._emit("state")

    # -------------------------------------------------------------- execution
    def _run_guarded(self) -> None:
        self._log_handler = attach_job_logger(self.id)
        watchdog = MemoryWatchdog(on_pressure=self._on_memory_pressure)
        watchdog.start()
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001 - top-level safety net
            logger.exception("Job %s crashed", self.id)
            self.error = f"{exc.__class__.__name__}: {exc}"
            self._set_state(JobState.FAILED)
        finally:
            watchdog.stop()
            try:
                self.checkpoint.save()
                report = write_failure_report(self.checkpoint)
                if report is not None:
                    logger.info("Failure report written to %s", report)
            finally:
                detach_job_logger(self._log_handler)

    def _on_memory_pressure(self, rss_mb: float) -> None:
        # Shrink batch size and flush state when memory gets tight.
        self.config.batch_size = max(10, self.config.batch_size // 2)
        self.checkpoint.save()
        logger.warning(
            "Reducing batch size to %d under memory pressure (%.0f MiB)",
            self.config.batch_size,
            rss_mb,
        )

    def _run(self) -> None:
        from spotdl.download.downloader import Downloader
        from spotdl.download.progress_handler import ProgressHandler
        from spotdl.utils.search import parse_query

        settings = dict(self.config.settings)
        settings.setdefault("output", self.config.output)
        settings["simple_tui"] = True
        settings["threads"] = safe_thread_count(settings.get("threads"))

        # --- search phase ---------------------------------------------------
        self._set_state(JobState.SEARCHING)
        logger.info("Searching %d quer%s", len(self.config.query),
                    "y" if len(self.config.query) == 1 else "ies")
        songs = retry_call(
            lambda: parse_query(
                query=self.config.query,
                threads=settings["threads"],
                use_ytm_data=settings.get("ytm_data", False),
                playlist_numbering=settings.get("playlist_numbering", False),
                album_type=settings.get("album_type", "album"),
                playlist_retain_track_cover=settings.get(
                    "playlist_retain_track_cover", False
                ),
            ),
            max_attempts=self.config.max_track_attempts,
            base_delay=self.config.backoff_base,
            max_delay=self.config.backoff_max,
            rate_limiter=self._rate_limiter,
            description="search",
            should_cancel=self.should_cancel,
        )

        # Register every discovered song and figure out what's left to do.
        for song in songs:
            self.checkpoint.register(song.url, song.display_name)
        self.checkpoint.save()

        pending = [s for s in songs if not self.checkpoint.is_done(s.url)]
        already = len(songs) - len(pending)
        if already:
            logger.info("Resuming: %d of %d already done", already, len(songs))
        self._emit("searched", total=len(songs), pending=len(pending))

        if self._cancel.is_set():
            self._set_state(JobState.CANCELLED)
            return

        # --- build a single downloader and reuse it across batches ----------
        downloader = Downloader(settings=settings)
        downloader.progress_handler = ProgressHandler(
            simple_tui=True, web_ui=True, update_callback=self._progress_callback
        )

        self._set_state(JobState.DOWNLOADING)
        self._download_with_retries(downloader, pending)

        if self._cancel.is_set():
            self._set_state(JobState.CANCELLED)
            return

        counts = self.checkpoint.counts()
        if counts["failed"] > 0:
            logger.warning("Finished with %d failed tracks", counts["failed"])
        else:
            logger.info("Job complete: %d tracks", counts["total"])
        self._set_state(JobState.COMPLETED)

    def _download_with_retries(self, downloader, songs: List[Any]) -> None:
        """Download ``songs`` in batches, retrying failures with backoff."""
        by_url = {s.url: s for s in songs}
        attempt = 0
        remaining = list(songs)

        while remaining and not self._cancel.is_set():
            attempt += 1
            if attempt > 1:
                # Backoff between whole-batch retry passes.
                delay = min(
                    self.config.backoff_max,
                    self.config.backoff_base * (2 ** (attempt - 2)),
                )
                logger.info(
                    "Retry pass %d for %d track(s) in %.0fs",
                    attempt,
                    len(remaining),
                    delay,
                )
                self._sleep_interruptible(delay)
                if self._cancel.is_set():
                    break

            failed_this_pass: List[Any] = []
            for batch in _chunks(remaining, self.config.batch_size):
                self._wait_if_paused()
                if self._cancel.is_set():
                    return

                results = downloader.download_songs(batch)

                for song, path in results:
                    rec = self.checkpoint.register(song.url, song.display_name)
                    rec.attempts = attempt
                    if path is not None:
                        self.checkpoint.mark(
                            song.url,
                            TrackStatus.COMPLETED,
                            name=song.display_name,
                            path=str(path),
                        )
                    else:
                        if attempt >= self.config.max_track_attempts:
                            self.checkpoint.mark(
                                song.url,
                                TrackStatus.FAILED,
                                name=song.display_name,
                                error=_last_error_for(downloader, song.url),
                            )
                        else:
                            self.checkpoint.mark(
                                song.url,
                                TrackStatus.PENDING,
                                name=song.display_name,
                            )
                            failed_this_pass.append(by_url[song.url])

                # Checkpoint + emit after every batch; this is the durable point.
                self.checkpoint.save()
                self._emit("progress")

                # Drop spotdl's per-run errors and the (class-level) progress
                # tracker map so they don't accumulate across a long run.
                downloader.errors.clear()
                downloader.progress_handler.progress_tracker.songs.clear()

            remaining = failed_this_pass

    # ---------------------------------------------------------------- helpers
    def _progress_callback(self, tracker, message: str) -> None:
        """Bridge spotdl's SongTracker updates to UI events."""
        self._emit(
            "track",
            track={
                "url": tracker.song.url,
                "name": tracker.song_name,
                "progress": tracker.progress,
                "message": message,
                "path": tracker.path,
            },
        )

    def _wait_if_paused(self) -> None:
        if self._pause.is_set():
            self._set_state(JobState.PAUSED)
            while self._pause.is_set() and not self._cancel.is_set():
                time.sleep(0.2)
            if not self._cancel.is_set():
                self._set_state(JobState.DOWNLOADING)

    def _sleep_interruptible(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._cancel.is_set():
            time.sleep(min(0.25, deadline - time.monotonic()))


def _last_error_for(downloader, url: str) -> Optional[str]:
    """Pull the most relevant error string spotdl recorded for ``url``."""
    for err in reversed(downloader.errors):
        if err.startswith(url):
            return err
    return downloader.errors[-1] if downloader.errors else "Download failed"


def _chunks(items: List[Any], size: int):
    for i in range(0, len(items), max(1, size)):
        yield items[i : i + size]


class JobManager:
    """Owns Spotify client init and the registry of live jobs."""

    def __init__(self) -> None:
        self._jobs: Dict[str, DownloadJob] = {}
        self._lock = threading.Lock()
        self._rate_limiter = AdaptiveRateLimiter()
        self._spotify_ready = False

    def ensure_spotify(
        self,
        client_id: str,
        client_secret: str,
        *,
        user_auth: bool = False,
        cache_path: Optional[str] = None,
        no_cache: bool = False,
        headless: bool = True,
        use_official_api: bool = False,
    ) -> None:
        """Initialise the global Spotify client exactly once."""
        from spotdl.utils.spotify import SpotifyClient, SpotifyError

        if self._spotify_ready:
            return
        try:
            SpotifyClient.init(
                client_id=client_id,
                client_secret=client_secret,
                user_auth=user_auth,
                cache_path=cache_path,
                no_cache=no_cache,
                headless=headless,
                use_official_api=use_official_api,
            )
        except SpotifyError:
            # Already initialised elsewhere - that's fine.
            pass
        self._spotify_ready = True

    def create_job(
        self, config: JobConfig, on_event: Optional[EventCallback] = None
    ) -> DownloadJob:
        job = DownloadJob(config, self._rate_limiter, on_event)
        with self._lock:
            self._jobs[job.uid] = job
        job.start()
        return job

    def get(self, uid: str) -> Optional[DownloadJob]:
        with self._lock:
            return self._jobs.get(uid)

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [job.status() for job in self._jobs.values()]

    def cancel(self, uid: str) -> bool:
        job = self.get(uid)
        if job is None:
            return False
        job.cancel()
        return True

"""
Durable, resumable per-job state.

A :class:`Checkpoint` records the outcome of every track in a job so that a
run which is interrupted - by a crash, a quit, or the user pausing - can be
resumed without re-downloading what already succeeded. Writes are atomic
(write-to-temp then ``os.replace``) so a crash mid-write can never corrupt the
file, which matters precisely during the long runs this is meant to protect.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from spotiflac.paths import get_jobs_dir

__all__ = ["TrackStatus", "TrackRecord", "Checkpoint"]


class TrackStatus(str, Enum):
    """Lifecycle state of a single track within a job."""

    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"  # already on disk / in archive
    FAILED = "failed"


@dataclass
class TrackRecord:
    """Per-track outcome persisted in the checkpoint."""

    url: str
    name: str = ""
    status: TrackStatus = TrackStatus.PENDING
    path: Optional[str] = None
    attempts: int = 0
    error: Optional[str] = None
    updated_at: float = field(default_factory=time.time)


class Checkpoint:
    """
    Resumable state for a single download job.

    Thread-safe: the orchestrator updates records from worker threads while the
    UI may read a snapshot concurrently.
    """

    def __init__(self, job_id: str, query: List[str], output: str):
        self.job_id = job_id
        self.query = query
        self.output = output
        self.created_at = time.time()
        self.tracks: Dict[str, TrackRecord] = {}
        self._lock = threading.RLock()
        self._path = get_jobs_dir() / f"{job_id}.json"

    # ------------------------------------------------------------------ load
    @classmethod
    def load_or_create(
        cls, job_id: str, query: List[str], output: str
    ) -> "Checkpoint":
        """Load an existing checkpoint for ``job_id`` or create a fresh one."""
        cp = cls(job_id, query, output)
        if cp._path.exists():
            try:
                cp._read_from_disk()
            except (json.JSONDecodeError, OSError, KeyError, ValueError):
                # A corrupt checkpoint should never block a run; start clean
                # but keep a copy for debugging.
                try:
                    cp._path.rename(cp._path.with_suffix(".corrupt.json"))
                except OSError:
                    pass
        return cp

    def _read_from_disk(self) -> None:
        with open(self._path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        with self._lock:
            self.created_at = data.get("created_at", self.created_at)
            self.query = data.get("query", self.query)
            self.output = data.get("output", self.output)
            self.tracks = {
                url: TrackRecord(
                    url=rec["url"],
                    name=rec.get("name", ""),
                    status=TrackStatus(rec.get("status", "pending")),
                    path=rec.get("path"),
                    attempts=rec.get("attempts", 0),
                    error=rec.get("error"),
                    updated_at=rec.get("updated_at", time.time()),
                )
                for url, rec in data.get("tracks", {}).items()
            }

    # ----------------------------------------------------------------- mutate
    def register(self, url: str, name: str = "") -> TrackRecord:
        """Ensure a record exists for ``url`` and return it."""
        with self._lock:
            rec = self.tracks.get(url)
            if rec is None:
                rec = TrackRecord(url=url, name=name)
                self.tracks[url] = rec
            elif name and not rec.name:
                rec.name = name
            return rec

    def mark(
        self,
        url: str,
        status: TrackStatus,
        *,
        name: str = "",
        path: Optional[str] = None,
        error: Optional[str] = None,
        bump_attempt: bool = False,
    ) -> None:
        """Update a track's outcome."""
        with self._lock:
            rec = self.register(url, name)
            rec.status = status
            if path is not None:
                rec.path = path
            if error is not None:
                rec.error = error
            if bump_attempt:
                rec.attempts += 1
            rec.updated_at = time.time()

    # ------------------------------------------------------------------ query
    def is_done(self, url: str) -> bool:
        """True if the track succeeded or was deliberately skipped."""
        with self._lock:
            rec = self.tracks.get(url)
            return rec is not None and rec.status in (
                TrackStatus.COMPLETED,
                TrackStatus.SKIPPED,
            )

    def pending_urls(self) -> List[str]:
        with self._lock:
            return [
                url
                for url, rec in self.tracks.items()
                if rec.status in (TrackStatus.PENDING, TrackStatus.FAILED)
            ]

    def counts(self) -> Dict[str, int]:
        with self._lock:
            out = {s.value: 0 for s in TrackStatus}
            for rec in self.tracks.values():
                out[rec.status.value] += 1
            out["total"] = len(self.tracks)
            return out

    def failed_records(self) -> List[TrackRecord]:
        with self._lock:
            return [
                rec
                for rec in self.tracks.values()
                if rec.status == TrackStatus.FAILED
            ]

    def snapshot(self) -> dict:
        """Return a JSON-serialisable copy of the full state."""
        with self._lock:
            return {
                "job_id": self.job_id,
                "query": self.query,
                "output": self.output,
                "created_at": self.created_at,
                "counts": self.counts(),
                "tracks": {url: asdict(rec) for url, rec in self.tracks.items()},
            }

    # ------------------------------------------------------------------- save
    def save(self) -> None:
        """Atomically persist the checkpoint to disk."""
        with self._lock:
            snapshot = self.snapshot()
        # Coerce enums for json.
        for rec in snapshot["tracks"].values():
            rec["status"] = (
                rec["status"].value
                if isinstance(rec["status"], TrackStatus)
                else rec["status"]
            )
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)  # atomic on POSIX and Windows
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    @property
    def path(self) -> Path:
        return self._path

    def delete(self) -> None:
        """Remove the on-disk checkpoint (e.g. after a fully clean finish)."""
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass

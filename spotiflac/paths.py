"""
Filesystem locations for Spotiflac runtime state.

Everything Spotiflac persists (checkpoints, logs, reports) lives under a
single user-level directory so that long runs can be resumed across app
restarts and crashes. We reuse spotdl's platformdirs-based config dir as the
parent when available, falling back to ``~/.spotiflac``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

__all__ = [
    "get_state_dir",
    "get_jobs_dir",
    "get_logs_dir",
    "job_id_for",
]


def _base_dir() -> Path:
    """Return the parent directory for all Spotiflac state."""
    try:
        # Reuse spotdl's config directory so everything lives together.
        from spotdl.utils.config import get_spotdl_path

        return Path(get_spotdl_path()) / "spotiflac"
    except Exception:  # pragma: no cover - spotdl always available in practice
        return Path.home() / ".spotiflac"


def get_state_dir() -> Path:
    """Root directory for Spotiflac runtime state (created on demand)."""
    path = _base_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_jobs_dir() -> Path:
    """Directory holding one checkpoint file per job."""
    path = get_state_dir() / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_logs_dir() -> Path:
    """Directory holding per-job log files and failure reports."""
    path = get_state_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_id_for(query: Iterable[str], output: str) -> str:
    """
    Deterministically derive a job id from its inputs.

    The same query + output always maps to the same id, which is what lets a
    re-run pick up an existing checkpoint and resume instead of starting over.
    """
    payload = json.dumps(
        {"query": sorted(query), "output": output}, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

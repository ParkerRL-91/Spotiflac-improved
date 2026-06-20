"""
Per-job logging and the failed-tracks report.

Each job gets its own rotating log file under the Spotiflac logs directory so
that, after a 3000-track overnight run, you can actually find what went wrong.
When a job finishes, :func:`write_failure_report` emits both a machine-readable
JSON report and a human-readable text summary of everything that failed.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from spotiflac.checkpoint import Checkpoint
from spotiflac.paths import get_logs_dir

__all__ = ["attach_job_logger", "detach_job_logger", "write_failure_report"]


def attach_job_logger(job_id: str, level: int = logging.INFO) -> RotatingFileHandler:
    """
    Attach a per-job rotating file handler to the spotdl + spotiflac loggers.

    Returns the handler so it can be detached when the job ends. Rotation caps
    a single job's log at a few MiB even if it's extremely chatty.
    """
    log_path = get_logs_dir() / f"{job_id}.log"
    handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler._spotiflac_job_id = job_id  # type: ignore[attr-defined]

    for name in ("spotdl", "spotiflac"):
        logger = logging.getLogger(name)
        if logger.level > level or logger.level == logging.NOTSET:
            logger.setLevel(level)
        logger.addHandler(handler)
    return handler


def detach_job_logger(handler: Optional[RotatingFileHandler]) -> None:
    """Remove a previously attached per-job handler."""
    if handler is None:
        return
    for name in ("spotdl", "spotiflac"):
        logging.getLogger(name).removeHandler(handler)
    try:
        handler.close()
    except Exception:  # pragma: no cover - defensive
        pass


def write_failure_report(checkpoint: Checkpoint) -> Optional[Path]:
    """
    Write JSON + text reports of failed tracks for ``checkpoint``.

    Returns the path to the text report, or None if nothing failed.
    """
    failed = checkpoint.failed_records()
    counts = checkpoint.counts()
    logs_dir = get_logs_dir()

    summary = {
        "job_id": checkpoint.job_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "query": checkpoint.query,
        "output": checkpoint.output,
        "counts": counts,
        "failed": [
            {
                "url": rec.url,
                "name": rec.name,
                "attempts": rec.attempts,
                "error": rec.error,
            }
            for rec in failed
        ],
    }
    json_path = logs_dir / f"{checkpoint.job_id}.report.json"
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)

    if not failed:
        return None

    text_path = logs_dir / f"{checkpoint.job_id}.failed.txt"
    with open(text_path, "w", encoding="utf-8") as handle:
        handle.write(
            f"Spotiflac failed-tracks report - {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        handle.write(
            f"{counts['completed']} completed, {counts['skipped']} skipped, "
            f"{counts['failed']} failed of {counts['total']} total\n\n"
        )
        for rec in failed:
            handle.write(f"- {rec.name or rec.url}\n")
            handle.write(f"    url:      {rec.url}\n")
            handle.write(f"    attempts: {rec.attempts}\n")
            handle.write(f"    error:    {rec.error}\n\n")
    return text_path

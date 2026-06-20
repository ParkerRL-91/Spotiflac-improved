"""
Spotiflac - a stability + UI layer on top of spotdl.

This package wraps spotdl's :class:`~spotdl.download.downloader.Downloader`
with the pieces that make long, unattended runs survivable:

- :mod:`spotiflac.checkpoint` - durable, resumable per-job state.
- :mod:`spotiflac.retry` - exponential backoff with jitter and rate-limit
  awareness for the flaky network calls spotdl makes.
- :mod:`spotiflac.resources` - bounded concurrency and a memory watchdog so
  multi-hour runs don't grow without bound.
- :mod:`spotiflac.logging_setup` - per-job logs and a failed-tracks report.
- :mod:`spotiflac.jobs` - the orchestrator that ties it all together and
  emits progress events the UI can render.
- :mod:`spotiflac.profiles` - search Spotify profiles and list their public
  playlists.

The upstream spotdl package is left untouched so the fork stays easy to
rebase on new releases; everything here lives alongside it.
"""

from spotiflac._version import __version__

__all__ = ["__version__"]

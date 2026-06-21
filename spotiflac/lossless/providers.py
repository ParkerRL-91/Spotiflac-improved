"""
Lossless audio providers.

A provider takes a :class:`~spotiflac.lossless.matcher.Target` (a track we
found on Spotify) and returns a downloaded file. The Tidal provider uses your
own Tidal account via `streamrip <https://github.com/nathom/streamrip>`_:

- **search** goes through streamrip's Tidal client, which returns the raw Tidal
  track objects (including ISRC + duration) so we can match by ISRC first;
- **download** shells out to the stable ``rip id tidal track <id>`` CLI, so we
  don't couple to streamrip's internal download API across versions.

streamrip is imported lazily so this module loads even where it isn't installed
(e.g. the frozen build without the lossless extra, or unit tests).
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Set

from spotiflac.lossless.matcher import Target, best_match

logger = logging.getLogger("spotiflac.lossless")

__all__ = [
    "LosslessProvider",
    "TidalProvider",
    "ProviderNotConfigured",
    "get_provider",
]

_AUDIO_EXTS = {".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".alac"}


class ProviderNotConfigured(RuntimeError):
    """Raised when a provider needs credentials/config it doesn't have yet."""


class LosslessProvider(ABC):
    """Resolve a Spotify track to a downloaded audio file on another service."""

    name: str = "base"

    @abstractmethod
    def is_configured(self) -> bool:
        """True if the provider has the credentials it needs to run."""

    @abstractmethod
    def fetch(self, target: Target, out_dir: Path) -> Path:
        """Find ``target`` on the service and download it into ``out_dir``."""


def _default_streamrip_config() -> Path:
    try:
        from platformdirs import user_config_dir

        return Path(user_config_dir("streamrip")) / "config.toml"
    except Exception:
        return Path.home() / ".config" / "streamrip" / "config.toml"


class TidalProvider(LosslessProvider):
    """Tidal FLAC via the user's own account (streamrip)."""

    name = "tidal"

    def __init__(
        self,
        *,
        config_path: Optional[str] = None,
        quality: int = 3,
        match_threshold: float = 0.8,
        search_limit: int = 20,
        rip_executable: Optional[str] = None,
    ):
        self.config_path = Path(config_path) if config_path else _default_streamrip_config()
        self.quality = quality  # streamrip: 3 = highest (hi-res FLAC) for Tidal
        self.match_threshold = match_threshold
        self.search_limit = search_limit
        # The streamrip CLI. Override with SPOTIFLAC_RIP for packaged builds
        # where the `rip` console script isn't on PATH.
        self.rip = rip_executable or os.environ.get("SPOTIFLAC_RIP", "rip")
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client = None
        self._lock = threading.Lock()

    # ----------------------------------------------------------- configuration
    def is_configured(self) -> bool:
        """True if streamrip holds Tidal tokens (i.e. the account is linked)."""
        if not self.config_path.exists():
            return False
        try:
            from streamrip.config import Config

            cfg = Config(str(self.config_path))
            token = getattr(cfg.session.tidal, "access_token", "")
            return bool(token)
        except Exception:
            # Fall back to a cheap text probe if the schema shifts.
            try:
                text = self.config_path.read_text(encoding="utf-8")
            except OSError:
                return False
            import re

            match = re.search(r'access_token\s*=\s*"([^"]*)"', text)
            return bool(match and match.group(1))

    # ------------------------------------------------------------------- search
    def _ensure_client(self):
        if self._client is not None:
            return self._client
        from streamrip.client.tidal import TidalClient
        from streamrip.config import Config

        if not self.config_path.exists():
            raise ProviderNotConfigured(
                "Tidal isn't linked yet. Connect your Tidal account first."
            )
        self._loop = asyncio.new_event_loop()
        cfg = Config(str(self.config_path))
        client = TidalClient(cfg)
        self._loop.run_until_complete(client.login())
        self._client = client
        return client

    def _run(self, coro):
        assert self._loop is not None
        return self._loop.run_until_complete(coro)

    @staticmethod
    def _normalize_item(item: Dict) -> Dict:
        artists: List[str] = []
        if item.get("artists"):
            artists = [a.get("name") for a in item["artists"] if a.get("name")]
        elif isinstance(item.get("artist"), dict):
            if item["artist"].get("name"):
                artists = [item["artist"]["name"]]
        elif item.get("artist"):
            artists = [str(item["artist"])]
        return {
            "id": str(item.get("id")),
            "title": item.get("title") or item.get("name"),
            "artist": ", ".join(artists),
            "artists": artists,
            "duration": item.get("duration"),
            "isrc": item.get("isrc"),
        }

    def search_candidates(self, target: Target) -> List[Dict]:
        """Search Tidal for ``target`` and return normalized candidate dicts."""
        with self._lock:
            client = self._ensure_client()
            query = f"{target.artist_str} {target.title}".strip()
            pages = self._run(
                client.search("track", query, limit=self.search_limit)
            )
        items: List[Dict] = []
        for page in pages or []:
            items.extend(page.get("items", []) or [])
        return [self._normalize_item(it) for it in items]

    # ----------------------------------------------------------------- download
    def _download_command(self, tidal_id: str, out_dir: Path) -> List[str]:
        return [
            self.rip,
            "--config-path",
            str(self.config_path),
            "-f",
            str(out_dir),
            "-ndb",  # ignore streamrip's own dedup db; our checkpoint is authoritative
            "-q",
            str(self.quality),
            "-c",
            "FLAC",
            "--no-progress",
            "id",
            "tidal",
            "track",
            str(tidal_id),
        ]

    def _download_id(self, tidal_id: str, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = self._download_command(tidal_id, out_dir)
        logger.debug("rip download: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=900
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"streamrip failed (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
            )

    def fetch(self, target: Target, out_dir: Path) -> Path:
        if not self.is_configured():
            raise ProviderNotConfigured("Tidal account is not linked.")

        candidates = self.search_candidates(target)
        if not candidates:
            raise LookupError(f"No Tidal results for {target.artist_str} - {target.title}")

        match = best_match(target, candidates, threshold=self.match_threshold)
        if match is None:
            raise LookupError(
                f"No confident Tidal match for {target.artist_str} - {target.title}"
            )
        logger.info(
            "Matched '%s - %s' -> Tidal %s (score %.2f%s)",
            target.artist_str,
            target.title,
            match["id"],
            match.get("_match_score", 0.0),
            ", ISRC" if target.isrc and match.get("isrc") else "",
        )

        before = _snapshot_audio(out_dir)
        self._download_id(match["id"], out_dir)
        new_file = _newest_new_audio(out_dir, before)
        if new_file is None:
            raise RuntimeError("streamrip reported success but no audio file appeared")
        return new_file


def _snapshot_audio(directory: Path) -> Set[Path]:
    if not directory.exists():
        return set()
    return {p for p in directory.rglob("*") if p.suffix.lower() in _AUDIO_EXTS}


def _newest_new_audio(directory: Path, before: Set[Path]) -> Optional[Path]:
    after = _snapshot_audio(directory)
    new = after - before
    if not new:
        return None
    return max(new, key=lambda p: p.stat().st_mtime)


def get_provider(name: str, settings: Optional[Dict] = None) -> LosslessProvider:
    """Factory: return a configured provider by name."""
    settings = settings or {}
    if name == "tidal":
        return TidalProvider(
            config_path=settings.get("streamrip_config"),
            quality=int(settings.get("tidal_quality", 3)),
            match_threshold=float(settings.get("match_threshold", 0.8)),
        )
    raise ValueError(f"Unknown lossless provider: {name}")

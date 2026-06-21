"""
Link a Tidal account, reusing streamrip's proven device-flow login.

We deliberately don't reimplement Tidal's OAuth device flow. Instead we trigger
streamrip's own interactive auth in a subprocess (it prints a
``https://link.tidal.com/...`` URL, waits for the user to approve it in a
browser, then writes the tokens to streamrip's config). We capture that URL and
surface it to the UI, and report "linked" once the tokens land in the config.

This keeps us on streamrip's battle-tested auth path and version-stable.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("spotiflac.lossless.tidal_auth")

__all__ = ["tidal_is_linked", "TidalLinker", "extract_login_url"]

# streamrip prints "Go to https://link.tidal.com/XXXX to log into Tidal".
_URL_RE = re.compile(r"https?://(?:link\.)?tidal\.com/\S+", re.IGNORECASE)
_ANY_TIDAL_URL = re.compile(r"https?://\S*tidal\S*", re.IGNORECASE)


def tidal_is_linked(config_path: Path) -> bool:
    """True if streamrip's config holds a non-empty Tidal access token."""
    if not config_path.exists():
        return False
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return False
    match = re.search(r'access_token\s*=\s*"([^"]*)"', text)
    return bool(match and match.group(1))


def extract_login_url(line: str) -> Optional[str]:
    """Pull a Tidal login URL out of a line of streamrip output, if present."""
    match = _URL_RE.search(line) or _ANY_TIDAL_URL.search(line)
    return match.group(0).rstrip(".,)") if match else None


class TidalLinker:
    """
    Drives a single Tidal account-linking attempt in the background.

    Lifecycle: ``idle`` -> ``awaiting_user`` (URL available) -> ``linked`` /
    ``error`` / ``timeout``.
    """

    def __init__(
        self,
        config_path: Path,
        *,
        rip_executable: str = "rip",
        trigger_query: str = "test",
    ):
        self.config_path = config_path
        self.rip = rip_executable
        self.trigger_query = trigger_query
        self.state = "idle"
        self.url: Optional[str] = None
        self.message: str = ""
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def status(self) -> Dict[str, Optional[str]]:
        # If tokens already exist, we're linked regardless of any live attempt.
        if tidal_is_linked(self.config_path):
            self.state = "linked"
        return {"state": self.state, "url": self.url, "message": self.message}

    def start(self) -> Dict[str, Optional[str]]:
        """Begin linking. Returns immediately; poll :meth:`status` for the URL."""
        with self._lock:
            if tidal_is_linked(self.config_path):
                self.state = "linked"
                return self.status()
            if self.state == "awaiting_user" and self._proc and self._proc.poll() is None:
                return self.status()  # a link attempt is already running

            self.url = None
            self.message = ""
            self.state = "starting"
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            # `rip search tidal ...` forces a login when no tokens exist; the
            # subsequent (throwaway) search just exits cleanly afterwards.
            cmd = [
                self.rip,
                "--config-path",
                str(self.config_path),
                "search",
                "--first",
                "tidal",
                "track",
                self.trigger_query,
            ]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except FileNotFoundError as exc:
                self.state = "error"
                self.message = (
                    "streamrip ('rip') not found. Install the lossless extra."
                )
                logger.error("Cannot start Tidal login: %s", exc)
                return self.status()

            self._thread = threading.Thread(
                target=self._pump_output, name="tidal-linker", daemon=True
            )
            self._thread.start()
        return self.status()

    def _pump_output(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            logger.debug("[rip] %s", line.rstrip())
            if self.url is None:
                found = extract_login_url(line)
                if found:
                    self.url = found
                    self.state = "awaiting_user"
        self._proc.wait()
        if tidal_is_linked(self.config_path):
            self.state = "linked"
            self.message = "Tidal account linked."
        elif self.state != "error":
            self.state = "timeout"
            self.message = "Login was not completed in time. Try again."

    def cancel(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
            if self.state not in ("linked",):
                self.state = "idle"

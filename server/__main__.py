"""
Entry point for the Spotiflac sidecar.

Run directly (``python -m server``) during development, or as the frozen
binary the Electron app spawns in production. Honours ``SPOTIFLAC_HOST`` /
``SPOTIFLAC_PORT`` and prints a single ``SPOTIFLAC_READY <port>`` line on
stdout once bound, which the Electron main process waits for.
"""

from __future__ import annotations

import logging
import os
import socket
import sys

import uvicorn


def _pick_port(preferred: int) -> int:
    """Return ``preferred`` if free, else an OS-assigned free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("SPOTIFLAC_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    host = os.environ.get("SPOTIFLAC_HOST", "127.0.0.1")
    port = _pick_port(int(os.environ.get("SPOTIFLAC_PORT", "8765")))

    # Announce the bound port for the parent process before blocking.
    print(f"SPOTIFLAC_READY {port}", flush=True)

    uvicorn.run(
        "server.app:app",
        host=host,
        port=port,
        log_level=os.environ.get("SPOTIFLAC_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    sys.exit(main())

"""PyInstaller entry point for a bundled streamrip ('rip') CLI.

The Tidal backend shells out to `rip`; in a packaged app it won't be on PATH,
so we freeze it alongside the sidecar and point SPOTIFLAC_RIP at it.
"""

from streamrip.rip.cli import rip

if __name__ == "__main__":
    rip()

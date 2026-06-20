"""
The Spotiflac sidecar HTTP/WebSocket API.

This is a thin, local-only FastAPI app the Electron front-end drives. It owns a
single :class:`~spotiflac.jobs.JobManager`, exposes REST endpoints to create
and control download jobs and to look up profiles, and pushes live progress
events to every connected WebSocket client.

Job events originate on worker threads; :class:`ConnectionManager` marshals
them onto the event loop with ``call_soon_threadsafe`` so broadcasting stays
thread-safe.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from server.models import CredentialsRequest, JobRequest, ProfileResponse
from spotiflac import __version__
from spotiflac.jobs import JobConfig, JobManager
from spotiflac.profiles import get_profile

logger = logging.getLogger("spotiflac.server")

app = FastAPI(title="Spotiflac", version=__version__)
manager = JobManager()


class ConnectionManager:
    """Tracks WebSocket clients and fans out job events to all of them."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def _send_to_all(self, message: Dict[str, Any]) -> None:
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_threadsafe(self, message: Dict[str, Any]) -> None:
        """Schedule a broadcast from any thread."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._send_to_all(message))
        )


connections = ConnectionManager()


def _default_credentials() -> Dict[str, Any]:
    """Pull credentials from spotdl's config, falling back to its defaults."""
    try:
        from spotdl.utils.config import SPOTIFY_OPTIONS, get_config

        try:
            cfg = get_config()
        except Exception:
            cfg = {}
        return {
            "client_id": cfg.get("client_id", SPOTIFY_OPTIONS["client_id"]),
            "client_secret": cfg.get(
                "client_secret", SPOTIFY_OPTIONS["client_secret"]
            ),
            "user_auth": cfg.get("user_auth", False),
            "use_official_api": cfg.get("use_official_api", False),
            "cache_path": cfg.get("cache_path"),
        }
    except Exception:
        return {
            "client_id": "5f573c9620494bae87890c0f08a60293",
            "client_secret": "212476d9b0f3472eaa762d90b19b0ba8",
            "user_auth": False,
            "use_official_api": False,
            "cache_path": None,
        }


@app.on_event("startup")
async def _startup() -> None:
    connections.bind_loop(asyncio.get_event_loop())
    # Initialise Spotify with whatever credentials are configured so the app is
    # usable immediately; the user can override via /api/credentials.
    creds = _default_credentials()
    try:
        manager.ensure_spotify(
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            user_auth=creds["user_auth"],
            use_official_api=creds["use_official_api"],
            cache_path=creds["cache_path"],
        )
    except Exception:
        logger.exception("Could not auto-initialise Spotify client")


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "version": __version__, "jobs": len(manager.list_jobs())}


@app.post("/api/credentials")
async def set_credentials(req: CredentialsRequest) -> Dict[str, Any]:
    defaults = _default_credentials()
    try:
        manager.ensure_spotify(
            client_id=req.client_id or defaults["client_id"],
            client_secret=req.client_secret or defaults["client_secret"],
            user_auth=req.user_auth,
            use_official_api=req.use_official_api,
            cache_path=req.cache_path or defaults["cache_path"],
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


def _build_settings(req: JobRequest) -> Dict[str, Any]:
    settings = dict(req.settings)
    settings["format"] = req.format
    if req.bitrate:
        settings["bitrate"] = req.bitrate
    if req.threads:
        settings["threads"] = req.threads
    if req.audio_providers:
        settings["audio_providers"] = req.audio_providers
    if req.lyrics_providers:
        settings["lyrics_providers"] = req.lyrics_providers
    if req.overwrite:
        settings["overwrite"] = req.overwrite
    if req.output:
        settings["output"] = req.output
    return settings


@app.post("/api/jobs")
async def create_job(req: JobRequest) -> Dict[str, Any]:
    settings = _build_settings(req)
    config = JobConfig(
        query=req.query,
        output=req.output or settings.get("output", "{artists} - {title}.{output-ext}"),
        settings=settings,
        batch_size=req.batch_size,
        max_track_attempts=req.max_track_attempts,
    )
    job = manager.create_job(config, on_event=connections.broadcast_threadsafe)
    return job.status()


@app.get("/api/jobs")
async def list_jobs() -> List[Dict[str, Any]]:
    return manager.list_jobs()


@app.get("/api/jobs/{uid}")
async def get_job(uid: str) -> Dict[str, Any]:
    job = manager.get(uid)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.status()


@app.post("/api/jobs/{uid}/cancel")
async def cancel_job(uid: str) -> Dict[str, Any]:
    if not manager.cancel(uid):
        raise HTTPException(status_code=404, detail="job not found")
    return {"status": "cancelling"}


@app.post("/api/jobs/{uid}/pause")
async def pause_job(uid: str) -> Dict[str, Any]:
    job = manager.get(uid)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    job.pause()
    return {"status": "pausing"}


@app.post("/api/jobs/{uid}/resume")
async def resume_job(uid: str) -> Dict[str, Any]:
    job = manager.get(uid)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    job.resume()
    return {"status": "resuming"}


@app.get("/api/profile", response_model=ProfileResponse)
async def profile(user: str) -> ProfileResponse:
    """Resolve a Spotify profile and list its public playlists."""
    try:
        data = await asyncio.to_thread(get_profile, user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ProfileResponse(**data)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await connections.connect(ws)
    # Send a snapshot of current jobs so a freshly-opened UI is in sync.
    await ws.send_json({"type": "snapshot", "jobs": manager.list_jobs()})
    try:
        while True:
            # We don't expect inbound messages, but keep the socket alive.
            await ws.receive_text()
    except WebSocketDisconnect:
        connections.disconnect(ws)
    except Exception:
        connections.disconnect(ws)


# Serve the built UI when present (packaged app / web fallback). Mounted last so
# it doesn't shadow the API routes above.
_RENDERER = Path(__file__).resolve().parent.parent / "app" / "renderer"
if _RENDERER.is_dir():
    app.mount("/", StaticFiles(directory=str(_RENDERER), html=True), name="ui")


@app.exception_handler(404)
async def _not_found(_request, _exc):  # pragma: no cover - cosmetic
    return JSONResponse(status_code=404, content={"detail": "not found"})

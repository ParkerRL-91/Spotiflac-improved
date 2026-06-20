"""Pydantic request/response models for the sidecar API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CredentialsRequest(BaseModel):
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    user_auth: bool = False
    use_official_api: bool = False
    cache_path: Optional[str] = None


class JobRequest(BaseModel):
    query: List[str] = Field(..., min_length=1)
    output: Optional[str] = None
    # Common spotdl knobs surfaced directly; anything else goes in `settings`.
    format: str = "mp3"
    bitrate: Optional[str] = None
    threads: Optional[int] = None
    audio_providers: Optional[List[str]] = None
    lyrics_providers: Optional[List[str]] = None
    overwrite: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)
    batch_size: int = 50
    max_track_attempts: int = 4


class ProfileResponse(BaseModel):
    id: str
    display_name: str
    url: str
    followers: Optional[int] = None
    image: Optional[str] = None
    playlists: List[Dict[str, Any]] = Field(default_factory=list)

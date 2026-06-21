"""
Discovery: turn a Spotify query into match targets.

We reuse spotdl's existing ``parse_query`` (which already reads playlists,
albums, artists and tracks, and populates each :class:`~spotdl.types.song.Song`
with its ISRC, duration and artists) so we don't reimplement Spotify access or
depend on the deprecated recommendation/audio-feature endpoints. This module
just adapts a ``Song`` into a :class:`~spotiflac.lossless.matcher.Target`.
"""

from __future__ import annotations

from typing import Any, List

from spotiflac.lossless.matcher import Target

__all__ = ["song_to_target", "songs_to_targets"]


def song_to_target(song: Any) -> Target:
    """Adapt a spotdl ``Song`` (or any object with the same fields) to a Target."""
    artists = getattr(song, "artists", None) or (
        [song.artist] if getattr(song, "artist", None) else []
    )
    return Target(
        title=getattr(song, "name", "") or "",
        artists=artists,
        duration=getattr(song, "duration", None),
        isrc=getattr(song, "isrc", None),
        album=getattr(song, "album_name", None),
    )


def songs_to_targets(songs: List[Any]) -> List[Target]:
    return [song_to_target(s) for s in songs]

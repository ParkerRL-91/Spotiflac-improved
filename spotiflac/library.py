"""
"Download my whole Spotify library" support.

spotdl already understands special query tokens that expand to everything in
the authenticated user's account. We just bundle the ones a "grab everything"
button should use:

- ``saved``                  - Liked Songs
- ``all-user-saved-albums``  - every saved album
- ``all-user-playlists``     - every playlist you own or follow

All of these require Spotify **user authentication** (OAuth with
``user-library-read`` + ``playlist-read-private``), so a library job must be run
with ``user_auth=True``.
"""

from __future__ import annotations

from typing import List

__all__ = ["LIBRARY_TOKENS", "library_query", "requires_user_auth"]

# Order matters only cosmetically; spotdl de-duplicates across them.
LIBRARY_TOKENS = {
    "liked": "saved",
    "albums": "all-user-saved-albums",
    "playlists": "all-user-playlists",
    "followed_artists": "all-user-followed-artists",
}


def library_query(
    *,
    liked: bool = True,
    albums: bool = True,
    playlists: bool = True,
    followed_artists: bool = False,
) -> List[str]:
    """Build the spotdl query token list for the requested library sections."""
    selected = []
    if liked:
        selected.append(LIBRARY_TOKENS["liked"])
    if albums:
        selected.append(LIBRARY_TOKENS["albums"])
    if playlists:
        selected.append(LIBRARY_TOKENS["playlists"])
    if followed_artists:
        selected.append(LIBRARY_TOKENS["followed_artists"])
    return selected


def requires_user_auth(query: List[str]) -> bool:
    """True if any token in ``query`` needs Spotify user authentication."""
    tokens = set(LIBRARY_TOKENS.values())
    return any(q in tokens for q in query)

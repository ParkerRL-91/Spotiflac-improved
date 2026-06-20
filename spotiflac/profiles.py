"""
Profile lookup: resolve a Spotify user and list their public playlists.

Note on "searching" profiles: the Spotify Web API deliberately exposes **no**
free-text user search endpoint, so you cannot type a display name and get a
list of people the way you can for tracks. What you *can* do is resolve a user
by their username, profile URL, or URI and then enumerate their public
playlists - which is what this module does, and what the UI surfaces.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from spotiflac.retry import AdaptiveRateLimiter, retry_call

logger = logging.getLogger("spotiflac.profiles")

__all__ = ["parse_user_id", "get_profile", "get_user_playlists"]

_USER_URL_RE = re.compile(r"(?:open\.spotify\.com/user/|spotify:user:)([^/?#\s]+)")


def parse_user_id(value: str) -> str:
    """
    Extract a Spotify user id from a URL, URI, or bare username.

    Accepts ``https://open.spotify.com/user/abc``, ``spotify:user:abc`` or
    just ``abc``. Usernames are URL-decoded so names with spaces work.
    """
    value = value.strip()
    match = _USER_URL_RE.search(value)
    if match:
        return match.group(1)
    return value


def _client():
    from spotdl.utils.spotify import SpotifyClient

    return SpotifyClient()


def get_profile(
    user: str, *, rate_limiter: Optional[AdaptiveRateLimiter] = None
) -> Dict[str, Any]:
    """
    Resolve a profile and return its metadata plus public playlists.

    Raises ``LookupError`` if the user can't be found, or ``RuntimeError`` if
    the active Spotify client doesn't support profile lookups (the free client
    does not - the official Web API is required).
    """
    user_id = parse_user_id(user)
    client = _client()

    if not hasattr(client, "user"):
        raise RuntimeError(
            "Profile lookup requires the official Spotify Web API. "
            "Restart Spotiflac with official-API credentials enabled."
        )

    try:
        profile = retry_call(
            lambda: client.user(user_id),
            description=f"profile {user_id}",
            rate_limiter=rate_limiter,
        )
    except Exception as exc:  # noqa: BLE001
        raise LookupError(f"Could not find Spotify user '{user_id}': {exc}") from exc

    if not profile:
        raise LookupError(f"Could not find Spotify user '{user_id}'")

    playlists = get_user_playlists(user_id, rate_limiter=rate_limiter)
    images = profile.get("images") or []
    return {
        "id": profile.get("id", user_id),
        "display_name": profile.get("display_name") or user_id,
        "url": (profile.get("external_urls") or {}).get(
            "spotify", f"https://open.spotify.com/user/{user_id}"
        ),
        "followers": (profile.get("followers") or {}).get("total"),
        "image": images[0]["url"] if images else None,
        "playlists": playlists,
    }


def get_user_playlists(
    user_id: str,
    *,
    limit: int = 50,
    rate_limiter: Optional[AdaptiveRateLimiter] = None,
) -> List[Dict[str, Any]]:
    """Return all public playlists owned/followed by ``user_id`` (paginated)."""
    client = _client()
    if not hasattr(client, "user_playlists"):
        return []

    playlists: List[Dict[str, Any]] = []
    offset = 0
    while True:
        page = retry_call(
            lambda off=offset: client.user_playlists(
                user_id, limit=limit, offset=off
            ),
            description=f"playlists {user_id}@{offset}",
            rate_limiter=rate_limiter,
        )
        if not page:
            break
        items = page.get("items", []) or []
        for item in items:
            if not item:
                continue
            images = item.get("images") or []
            playlists.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "url": (item.get("external_urls") or {}).get("spotify"),
                    "tracks": (item.get("tracks") or {}).get("total"),
                    "image": images[0]["url"] if images else None,
                    "owner": (item.get("owner") or {}).get("display_name"),
                }
            )
        if page.get("next") and len(items) == limit:
            offset += limit
        else:
            break
    return playlists

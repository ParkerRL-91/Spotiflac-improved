"""
Lossless backend: source track lists from Spotify, fetch audio elsewhere.

The fragile part of the original pipeline was depending on Spotify's Web API +
an unofficial client for *everything*. Here we keep Spotify only as the
**discovery** layer (read a playlist's tracks, including their ISRC codes —
those endpoints still work) and pull the actual audio from a pluggable
provider:

- :class:`~spotiflac.lossless.providers.TidalProvider` - real FLAC from the
  user's own Tidal account, via streamrip. Search goes through streamrip's
  Tidal client (so we get ISRC + duration to match on); download uses the
  stable ``rip`` CLI.
- account linking is handled by :mod:`spotiflac.lossless.tidal_auth`.

Matching from a Spotify track to a candidate on another service is ISRC-first
(exact) with a metadata/duration fallback — see :mod:`spotiflac.lossless.matcher`.
"""

__all__ = ["matcher", "discovery", "providers"]

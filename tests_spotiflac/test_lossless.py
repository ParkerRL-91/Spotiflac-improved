"""
Tests for the lossless (Tidal) backend: matching, discovery adaptation,
library query building, Tidal login-URL extraction, and provider plumbing.

No network, no Tidal account, no streamrip auth required - the I/O is isolated
and the matching/parsing logic is pure.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --------------------------------------------------------------------- matcher
def test_isrc_match_short_circuits_to_perfect_score():
    from spotiflac.lossless.matcher import Target, score_candidate

    target = Target(title="Whatever", artists=["X"], duration=200, isrc="GBAYE0601498")
    # Title/artist deliberately wrong; ISRC equal (with dashes/case noise).
    cand = {"title": "totally different", "artist": "nope", "isrc": "gb-aye-06-01498"}
    assert score_candidate(target, cand) == 1.0


def test_metadata_match_without_isrc():
    from spotiflac.lossless.matcher import Target, best_match

    target = Target(title="Get Lucky", artists=["Daft Punk"], duration=248)
    candidates = [
        {"title": "Get Lucky", "artist": "Daft Punk", "duration": 249, "id": "1"},
        {"title": "Get Lucky (Live)", "artist": "Someone Else", "duration": 600, "id": "2"},
    ]
    match = best_match(target, candidates, threshold=0.8)
    assert match is not None and match["id"] == "1"
    assert match["_match_score"] >= 0.8


def test_duration_penalty_rejects_wrong_length():
    from spotiflac.lossless.matcher import Target, best_match

    target = Target(title="Song", artists=["Band"], duration=180)
    # Same title/artist but a 10-minute version -> should fall below threshold.
    candidates = [{"title": "Song", "artist": "Band", "duration": 600, "id": "x"}]
    assert best_match(target, candidates, threshold=0.85) is None


def test_normalize_strips_noise():
    from spotiflac.lossless.matcher import normalize

    assert normalize("Song (Remastered 2011) [feat. Someone]") == "song"
    assert normalize("Hello - Radio Edit") == "hello"


# ------------------------------------------------------------------- discovery
class _FakeSong:
    def __init__(self):
        self.name = "Get Lucky"
        self.artists = ["Daft Punk", "Pharrell"]
        self.artist = "Daft Punk"
        self.duration = 248
        self.isrc = "GBAYE0601498"
        self.album_name = "Random Access Memories"


def test_song_to_target():
    from spotiflac.lossless.discovery import song_to_target

    t = song_to_target(_FakeSong())
    assert t.title == "Get Lucky"
    assert t.artists == ["Daft Punk", "Pharrell"]
    assert t.isrc == "GBAYE0601498"
    assert t.duration == 248


# --------------------------------------------------------------------- library
def test_library_query_selection():
    from spotiflac.library import library_query, requires_user_auth

    assert library_query() == ["saved", "all-user-saved-albums", "all-user-playlists"]
    assert library_query(albums=False, playlists=False) == ["saved"]
    assert library_query(followed_artists=True)[-1] == "all-user-followed-artists"
    assert requires_user_auth(["saved"]) is True
    assert requires_user_auth(["https://open.spotify.com/track/x"]) is False


# ------------------------------------------------------------------ tidal auth
def test_extract_login_url():
    from spotiflac.lossless.tidal_auth import extract_login_url

    line = "Go to https://link.tidal.com/AB12CD to log into Tidal within 5 minutes."
    assert extract_login_url(line) == "https://link.tidal.com/AB12CD"
    assert extract_login_url("nothing here") is None


def test_tidal_is_linked(tmp_path):
    from spotiflac.lossless.tidal_auth import tidal_is_linked

    cfg = tmp_path / "config.toml"
    assert tidal_is_linked(cfg) is False
    cfg.write_text('[tidal]\naccess_token = ""\n', encoding="utf-8")
    assert tidal_is_linked(cfg) is False
    cfg.write_text('[tidal]\naccess_token = "abc123"\n', encoding="utf-8")
    assert tidal_is_linked(cfg) is True


# -------------------------------------------------------------------- provider
def test_provider_normalizes_tidal_items():
    from spotiflac.lossless.providers import TidalProvider

    item = {
        "id": 12345,
        "title": "Get Lucky",
        "duration": 248,
        "isrc": "GBAYE0601498",
        "artists": [{"name": "Daft Punk"}, {"name": "Pharrell Williams"}],
    }
    norm = TidalProvider._normalize_item(item)
    assert norm["id"] == "12345"
    assert norm["artist"] == "Daft Punk, Pharrell Williams"
    assert norm["isrc"] == "GBAYE0601498"
    assert norm["duration"] == 248


def test_provider_download_command_shape(tmp_path):
    from spotiflac.lossless.providers import TidalProvider

    p = TidalProvider(config_path=str(tmp_path / "c.toml"), quality=3)
    cmd = p._download_command("999", tmp_path / "out")
    assert cmd[0] == "rip"
    assert "id" in cmd and "tidal" in cmd and "track" in cmd and "999" in cmd
    assert "FLAC" in cmd and "-c" in cmd
    # quality flag present
    assert "-q" in cmd and "3" in cmd


def test_get_provider_unknown_raises():
    from spotiflac.lossless.providers import get_provider

    with pytest.raises(ValueError):
        get_provider("napster", {})


def test_fetch_uses_match_then_downloads(tmp_path, monkeypatch):
    """End-to-end provider flow with search + download stubbed out."""
    from spotiflac.lossless.matcher import Target
    from spotiflac.lossless.providers import TidalProvider

    p = TidalProvider(config_path=str(tmp_path / "c.toml"))
    monkeypatch.setattr(p, "is_configured", lambda: True)
    monkeypatch.setattr(
        p,
        "search_candidates",
        lambda target: [
            {"id": "777", "title": "Song", "artist": "Band", "isrc": "AAAAA0000001"}
        ],
    )

    produced = tmp_path / "out" / "Band - Song.flac"

    def fake_download(tidal_id, out_dir):
        assert tidal_id == "777"
        produced.parent.mkdir(parents=True, exist_ok=True)
        produced.write_bytes(b"\x00fLaC")

    monkeypatch.setattr(p, "_download_id", fake_download)

    target = Target(title="Song", artists=["Band"], isrc="AAAAA0000001")
    result = p.fetch(target, tmp_path / "out")
    assert result == produced

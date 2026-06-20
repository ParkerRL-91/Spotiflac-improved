"""
Tests for the Spotiflac stability layer.

These exercise checkpointing, retry/backoff, the adaptive rate limiter, and
resource helpers without needing spotdl's network stack or ffmpeg.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Redirect all Spotiflac state into a temp dir for each test."""
    import spotiflac.paths as paths

    monkeypatch.setattr(paths, "_base_dir", lambda: tmp_path / "spotiflac")
    yield


# ----------------------------------------------------------------- checkpoint
def test_checkpoint_roundtrip_and_resume():
    from spotiflac.checkpoint import Checkpoint, TrackStatus

    cp = Checkpoint.load_or_create("job1", ["q"], "out/{title}")
    cp.register("u1", "Song One")
    cp.mark("u1", TrackStatus.COMPLETED, path="/music/one.mp3")
    cp.register("u2", "Song Two")
    cp.mark("u2", TrackStatus.FAILED, error="boom", bump_attempt=True)
    cp.save()

    reloaded = Checkpoint.load_or_create("job1", ["q"], "out/{title}")
    assert reloaded.is_done("u1") is True
    assert reloaded.is_done("u2") is False  # failed -> still pending work
    assert reloaded.pending_urls() == ["u2"]
    counts = reloaded.counts()
    assert counts["completed"] == 1
    assert counts["failed"] == 1
    assert counts["total"] == 2
    assert reloaded.tracks["u2"].attempts == 1


def test_checkpoint_atomic_write_survives_garbage(tmp_path):
    from spotiflac.checkpoint import Checkpoint

    cp = Checkpoint.load_or_create("job2", ["q"], "out")
    cp.register("u1", "x")
    cp.save()
    # Corrupt the file; loader should recover by starting fresh, not crash.
    cp.path.write_text("{not valid json", encoding="utf-8")
    recovered = Checkpoint.load_or_create("job2", ["q"], "out")
    assert recovered.counts()["total"] == 0
    assert cp.path.with_suffix(".corrupt.json").exists()


# ---------------------------------------------------------------------- retry
def test_retry_succeeds_after_transient_failures():
    from spotiflac.retry import retry_call

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("temporary")
        return "ok"

    result = retry_call(flaky, max_attempts=5, base_delay=0.001, jitter=0)
    assert result == "ok"
    assert calls["n"] == 3


def test_retry_gives_up_and_raises():
    from spotiflac.retry import retry_call

    def always_fails():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        retry_call(always_fails, max_attempts=3, base_delay=0.001, jitter=0)


def test_retry_respects_cancellation():
    from spotiflac.retry import retry_call

    def fails():
        raise ConnectionError("x")

    with pytest.raises(ConnectionError):
        retry_call(
            fails,
            max_attempts=10,
            base_delay=0.001,
            jitter=0,
            should_cancel=lambda: True,
        )


def test_rate_limit_detection():
    from spotiflac.retry import is_rate_limit_error

    class FakeResp:
        status_code = 429

    class FakeExc(Exception):
        response = FakeResp()

    assert is_rate_limit_error(FakeExc("too many requests")) is True
    assert is_rate_limit_error(ValueError("ordinary")) is False


def test_adaptive_rate_limiter_increase_and_decay():
    from spotiflac.retry import AdaptiveRateLimiter

    rl = AdaptiveRateLimiter(step=1.0, max_delay=5.0, decay=0.5, decay_after_successes=2)
    assert rl.current_delay == 0.0
    rl.report_rate_limited()
    rl.report_rate_limited()
    assert rl.current_delay == 2.0
    for _ in range(2):
        rl.report_success()
    assert rl.current_delay == 1.0  # decayed by 0.5 after 2 successes


# ------------------------------------------------------------------ resources
def test_safe_thread_count_bounds():
    from spotiflac.resources import safe_thread_count

    assert safe_thread_count(0) >= 1
    assert safe_thread_count(1000) <= (os.cpu_count() or 4) * 2
    assert safe_thread_count(None) >= 1


# ------------------------------------------------------------------- profiles
def test_parse_user_id_variants():
    from spotiflac.profiles import parse_user_id

    assert parse_user_id("https://open.spotify.com/user/spotify") == "spotify"
    assert parse_user_id("spotify:user:abc123") == "abc123"
    assert parse_user_id("  plainname  ") == "plainname"
    assert (
        parse_user_id("https://open.spotify.com/user/wizzler?si=xyz") == "wizzler"
    )

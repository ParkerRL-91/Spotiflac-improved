"""
Match a Spotify track to a candidate on another service.

Strategy, in order of trust:

1. **ISRC** - the International Standard Recording Code uniquely identifies a
   recording across services. If both sides report the same ISRC it's the same
   recording, full stop. This is what makes cross-service matching reliable
   instead of the fuzzy guesswork YouTube matching requires.
2. **Metadata similarity** - when an ISRC is missing on either side, fall back
   to fuzzy title + artist similarity, gated by duration proximity.

Everything here is pure and unit-testable: it operates on plain dicts, no I/O.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

try:
    from rapidfuzz import fuzz

    def _ratio(a: str, b: str) -> float:
        return fuzz.token_sort_ratio(a, b) / 100.0

except Exception:  # pragma: no cover - rapidfuzz ships with spotdl
    from difflib import SequenceMatcher

    def _ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()


__all__ = ["Target", "normalize", "score_candidate", "best_match"]

# Common noise we strip before comparing titles.
_NOISE = re.compile(
    r"\b(feat\.?|featuring|ft\.?|with|remaster(ed)?|remastered\s*\d{2,4}|"
    r"radio edit|single version|album version|bonus track|explicit|clean)\b",
    re.IGNORECASE,
)
_BRACKETS = re.compile(r"[\(\[\{].*?[\)\]\}]")
_NONALNUM = re.compile(r"[^a-z0-9\s]")


def normalize(text: Optional[str]) -> str:
    """Lower-case, drop bracketed asides and noise words, collapse whitespace."""
    if not text:
        return ""
    text = text.lower()
    text = _BRACKETS.sub(" ", text)
    text = _NOISE.sub(" ", text)
    text = _NONALNUM.sub(" ", text)
    return " ".join(text.split())


class Target:
    """A normalized representation of the track we're trying to find."""

    def __init__(
        self,
        *,
        title: str,
        artists: Sequence[str],
        duration: Optional[int] = None,
        isrc: Optional[str] = None,
        album: Optional[str] = None,
    ):
        self.title = title
        self.artists = list(artists)
        self.duration = duration  # seconds
        self.isrc = (isrc or "").upper().replace("-", "") or None
        self.album = album

    @property
    def artist_str(self) -> str:
        return ", ".join(self.artists)


def _candidate_isrc(cand: Dict) -> Optional[str]:
    raw = cand.get("isrc")
    if not raw:
        return None
    return str(raw).upper().replace("-", "")


def _candidate_duration(cand: Dict) -> Optional[int]:
    for key in ("duration", "duration_s", "length"):
        if cand.get(key) is not None:
            return int(cand[key])
    if cand.get("duration_ms") is not None:
        return int(cand["duration_ms"] / 1000)
    return None


def score_candidate(target: Target, cand: Dict) -> float:
    """
    Return a confidence score in [0, 1] that ``cand`` is ``target``.

    A matching ISRC short-circuits to 1.0. Otherwise we blend title and artist
    similarity and apply a duration penalty when both durations are known.
    """
    cand_isrc = _candidate_isrc(cand)
    if target.isrc and cand_isrc and target.isrc == cand_isrc:
        return 1.0

    title_sim = _ratio(normalize(target.title), normalize(cand.get("title", "")))

    cand_artist = cand.get("artist") or ", ".join(cand.get("artists", []) or [])
    artist_sim = _ratio(normalize(target.artist_str), normalize(cand_artist))

    score = 0.65 * title_sim + 0.35 * artist_sim

    cand_dur = _candidate_duration(cand)
    if target.duration and cand_dur:
        diff = abs(target.duration - cand_dur)
        if diff > 15:
            # Penalise increasingly for drift beyond ~15s; >40s is almost surely
            # a different recording (live/extended/wrong match).
            score *= max(0.0, 1.0 - (diff - 15) / 60.0)

    return round(score, 4)


def best_match(
    target: Target, candidates: List[Dict], *, threshold: float = 0.8
) -> Optional[Dict]:
    """
    Return the highest-scoring candidate at or above ``threshold``, else None.

    The chosen candidate is annotated with ``_match_score`` for logging.
    """
    best: Optional[Dict] = None
    best_score = 0.0
    for cand in candidates:
        score = score_candidate(target, cand)
        if score > best_score:
            best_score = score
            best = cand
    if best is not None and best_score >= threshold:
        best = dict(best)
        best["_match_score"] = best_score
        return best
    return None

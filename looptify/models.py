"""The data interface between the Windows adapters and the pure logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Snapshot:
    """One observation of Spotify's playback state, as reported by SMTC.

    `position` and `duration` are seconds. `last_updated` is the timezone-aware
    UTC timestamp SMTC attached to this data, which may be several seconds old —
    see `logic.extrapolate_position`.
    """

    title: str
    artist: str
    album: str
    track_number: int
    is_playing: bool
    position: float
    duration: float
    last_updated: datetime
    can_seek: bool

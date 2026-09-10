"""The data passed from the Windows adapters to the pure logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Snapshot:
    """One observation of Spotify's playback state.

    Seconds throughout. `last_updated` is when SMTC stamped this, often
    seconds ago — see `logic.extrapolate_position`.
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

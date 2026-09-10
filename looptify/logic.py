"""Pure decision logic. Imports nothing platform-specific, by design.

Everything here is a pure function of its arguments, which is what lets the
whole brain be tested in CI on a machine with no Spotify and no Windows.
"""

from __future__ import annotations

from datetime import datetime

from looptify.models import Snapshot


def extrapolate_position(snap: Snapshot, now: datetime) -> float:
    """Estimate Spotify's true playback position right now, in seconds.

    SMTC does not tick. It pushes an update only on events, measured at roughly
    every 4.5 seconds, so `snap.position` can be badly stale. While playing, the
    real position is the reported one plus the time since it was stamped. This
    was measured accurate to about 20ms across a full 4.5s gap.
    """
    if not snap.is_playing:
        return snap.position

    drift = (now - snap.last_updated).total_seconds()
    if drift < 0:  # clock skew; never rewind
        drift = 0.0

    estimated = snap.position + drift
    if snap.duration > 0:
        return min(estimated, snap.duration)
    return estimated


def is_ad(snap: Snapshot, markers: tuple[str, ...]) -> bool:
    """True when this item matches a configured ad marker.

    An empty marker tuple always returns False. That is the safety property:
    ad detection ships off, and is enabled only after a real ad's SMTC fields
    have been captured. Guessing here risks muting real music, which fails
    silently and is miserable to diagnose.
    """
    if not markers:
        return False

    haystack = f"{snap.title}\n{snap.artist}\n{snap.album}".lower()
    return any(m.lower() in haystack for m in markers if m)

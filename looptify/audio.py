"""Adapter for muting Spotify's audio sessions.

Measured on 2026-09-10: Spotify runs TWO audio sessions simultaneously, only
one of which is active. Muting just the first match silently fails, so every
matching session is muted.
"""

from __future__ import annotations

from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

_PROCESS_NAME = "spotify.exe"


def set_spotify_muted(muted: bool) -> int:
    """Mute or unmute every Spotify audio session. Returns the count changed.

    Returns 0 when Spotify has no audio sessions, which is normal when it is
    closed or has been idle long enough for Windows to drop the session.
    """
    changed = 0
    for session in AudioUtilities.GetAllSessions():
        process = session.Process
        if process is None or process.name().lower() != _PROCESS_NAME:
            continue
        try:
            volume = session._ctl.QueryInterface(ISimpleAudioVolume)
            volume.SetMute(1 if muted else 0, None)
            changed += 1
        except OSError:
            # A session can disappear between enumeration and use.
            continue
    return changed

"""Mutes Spotify. It runs several audio sessions, so mute them all."""

from __future__ import annotations

from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

_PROCESS_NAME = "spotify.exe"


def set_spotify_muted(muted: bool) -> int:
    """Mute or unmute every Spotify session. Returns how many changed.

    0 is normal when Spotify is closed or has been idle.
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

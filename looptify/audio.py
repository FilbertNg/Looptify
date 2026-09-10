"""Mutes Spotify. It runs several audio sessions, so mute them all."""

from __future__ import annotations

import comtypes
import psutil
from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow, ERole
from pycaw.pycaw import ISimpleAudioVolume

_PROCESS_NAME = "spotify.exe"


def _session_manager() -> IAudioSessionManager2:
    """Get the default playback device's audio session manager.

    pycaw's `AudioUtilities.GetAllSessions()` reaches this same object, but it
    goes through `CreateDevice()`, which first reads the device's entire
    property store — around 200 properties — to build a description nothing
    here looks at. That cost ~200ms per call, and this runs on the asyncio
    thread, so every ad boundary stalled the poll loop for longer than a poll
    interval. Asking the endpoint directly costs ~5ms.
    """
    enumerator = comtypes.CoCreateInstance(
        CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
    )
    endpoint = enumerator.GetDefaultAudioEndpoint(
        EDataFlow.eRender.value, ERole.eMultimedia.value
    )
    return endpoint.Activate(
        IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None
    ).QueryInterface(IAudioSessionManager2)


def set_spotify_muted(muted: bool) -> int:
    """Mute or unmute every Spotify session. Returns how many changed.

    0 is normal when Spotify is closed or has been idle.
    """
    try:
        sessions = _session_manager().GetSessionEnumerator()
    except OSError:
        # No playback device at all, so there is nothing to mute.
        return 0

    changed = 0
    for index in range(sessions.GetCount()):
        control = sessions.GetSession(index)
        if control is None:
            continue
        try:
            control2 = control.QueryInterface(IAudioSessionControl2)
            pid = control2.GetProcessId()
            # 0 is the system-sounds session, which owns no process.
            if pid == 0 or psutil.Process(pid).name().lower() != _PROCESS_NAME:
                continue
            control2.QueryInterface(ISimpleAudioVolume).SetMute(1 if muted else 0, None)
            changed += 1
        except (OSError, psutil.Error):
            # A session, or its process, can vanish between enumeration and use.
            continue
    return changed

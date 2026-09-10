"""Adapter over Windows System Media Transport Controls.

Verified against winsdk 1.0.0b10 and the Microsoft Store build of Spotify
(app id 'SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify') on 2026-09-10.
"""

from __future__ import annotations

import time

from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSession as Session,
)
from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
)
from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)

from looptify.models import Snapshot

# Media properties come from an async call that is far heavier than reading the
# timeline, so they are refreshed on this cadence rather than every poll.
MEDIA_REFRESH_SECONDS = 1.0


class SpotifyMonitor:
    """Finds Spotify's SMTC session and reads playback state from it."""

    def __init__(self) -> None:
        self._manager: SessionManager | None = None
        self._session: Session | None = None
        self._media: tuple[str, str, str] = ("", "", "")
        self._media_fetched_at: float = float("-inf")
        self._media_duration: float = -1.0

    async def connect(self) -> bool:
        """Locate Spotify's session. Returns False if Spotify is not running."""
        self._manager = await SessionManager.request_async()
        return await self._refresh_session()

    async def _refresh_session(self) -> bool:
        if self._manager is None:
            return False
        for session in self._manager.get_sessions():
            app_id = session.source_app_user_model_id or ""
            if "spotify" in app_id.lower():
                if session is not self._session:
                    # New session: force a media-properties refresh.
                    self._media_duration = -1.0
                    self._media_fetched_at = float("-inf")
                self._session = session
                return True
        self._session = None
        return False

    async def snapshot(self) -> Snapshot | None:
        """Read current playback state, or None if Spotify is unavailable."""
        if self._session is None and not await self._refresh_session():
            return None

        session = self._session
        assert session is not None
        try:
            timeline = session.get_timeline_properties()
            playback = session.get_playback_info()
        except OSError:
            # Spotify closed or the session went stale; re-scan next poll.
            self._session = None
            return None

        duration = timeline.end_time.total_seconds()
        position = timeline.position.total_seconds()
        is_playing = playback.playback_status == PlaybackStatus.PLAYING

        # Refresh title/artist/album on a slower cadence, or immediately when
        # the duration changes, which reliably signals a new track.
        now_mono = time.monotonic()
        stale = now_mono - self._media_fetched_at >= MEDIA_REFRESH_SECONDS
        if stale or duration != self._media_duration:
            self._media = await self._read_media_properties(session)
            self._media_fetched_at = now_mono
            self._media_duration = duration

        title, artist, album = self._media
        return Snapshot(
            title=title,
            artist=artist,
            album=album,
            is_playing=is_playing,
            position=position,
            duration=duration,
            last_updated=timeline.last_updated_time,
            can_seek=bool(playback.controls.is_playback_position_enabled),
        )

    @staticmethod
    async def _read_media_properties(session: Session) -> tuple[str, str, str]:
        try:
            info = await session.try_get_media_properties_async()
        except OSError:
            return ("", "", "")
        return (
            info.title or "",
            info.artist or "",
            info.album_title or "",
        )

    async def loop_now(self) -> str:
        """Restart the current track. Returns which method was used.

        Seeking to zero is preferred: it is exact, and it does not depend on
        Spotify's rule that previous-track restarts rather than goes back when
        position is past ~3 seconds. Skip-previous is the fallback.
        """
        if self._session is None:
            return "unavailable"

        session = self._session
        try:
            if session.get_playback_info().controls.is_playback_position_enabled:
                if await session.try_change_playback_position_async(0):
                    return "seek"
            if await session.try_skip_previous_async():
                return "skip_previous"
        except OSError:
            self._session = None
            return "error"
        return "failed"

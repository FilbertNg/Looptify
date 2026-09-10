"""Reads Spotify's playback state from Windows media controls (SMTC)."""

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

# Reading media properties is much heavier than the timeline, so do it rarely.
MEDIA_REFRESH_SECONDS = 1.0


class SpotifyMonitor:
    """Finds Spotify's SMTC session and reads playback state from it."""

    def __init__(self) -> None:
        self._manager: SessionManager | None = None
        self._session: Session | None = None
        self._media: tuple[str, str, str, int] = ("", "", "", 0)
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

        # Documented as non-null, but WinRT returns null between tracks.
        if timeline is None or playback is None:
            self._session = None
            return None

        controls = playback.controls
        end_time = timeline.end_time
        position_delta = timeline.position
        last_updated = timeline.last_updated_time
        if end_time is None or position_delta is None or last_updated is None:
            return None

        duration = end_time.total_seconds()
        position = position_delta.total_seconds()
        is_playing = playback.playback_status == PlaybackStatus.PLAYING

        # A changed duration means a new track, so refresh metadata now.
        now_mono = time.monotonic()
        stale = now_mono - self._media_fetched_at >= MEDIA_REFRESH_SECONDS
        if stale or duration != self._media_duration:
            self._media = await self._read_media_properties(session)
            self._media_fetched_at = now_mono
            self._media_duration = duration

        title, artist, album, track_number = self._media
        return Snapshot(
            title=title,
            artist=artist,
            album=album,
            track_number=track_number,
            is_playing=is_playing,
            position=position,
            duration=duration,
            last_updated=last_updated,
            can_seek=bool(controls is not None and controls.is_playback_position_enabled),
        )

    @staticmethod
    async def _read_media_properties(session: Session) -> tuple[str, str, str, int]:
        try:
            info = await session.try_get_media_properties_async()
        except OSError:
            return ("", "", "", 0)
        if info is None:
            return ("", "", "", 0)
        return (
            info.title or "",
            info.artist or "",
            info.album_title or "",
            info.track_number or 0,
        )

    async def loop_now(self) -> str:
        """Restart the current track. Returns which method worked.

        Prefers seeking to 0:00 — exact, and doesn't rely on Spotify's
        "previous restarts if past ~3s" behaviour.
        """
        if self._session is None:
            return "unavailable"

        session = self._session
        try:
            playback = session.get_playback_info()
            controls = playback.controls if playback is not None else None
            if controls is not None and controls.is_playback_position_enabled:
                if await session.try_change_playback_position_async(0):
                    return "seek"
            if await session.try_skip_previous_async():
                return "skip_previous"
        except OSError:
            self._session = None
            return "error"
        return "failed"

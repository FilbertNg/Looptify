"""Pure decision logic — no Windows APIs here, so it's testable without Spotify."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from looptify.config import Config
from looptify.models import Snapshot


def extrapolate_position(snap: Snapshot, now: datetime) -> float:
    """Estimate where playback actually is, in seconds.

    SMTC only pushes updates on events (~every 4.5s on Spotify), so the
    reported position is usually stale. Add the time since it was stamped.
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


def is_ad(snap: Snapshot, cfg: Config) -> bool:
    """True if this looks like an ad. Either rule alone is enough.

    By artist, matched exactly (catches Spotify's own ads), or by shape:
    no album, no track number, and short (catches third-party advertisers,
    whose names we can't know in advance). See "Ad muting" in the README.
    """
    artist = snap.artist.strip().lower()
    if artist:
        for marker in cfg.ad_markers:
            if marker.strip() and artist == marker.strip().lower():
                return True

    if cfg.detect_ads_by_structure:
        # Duration must be non-zero: Spotify reports 0 between tracks.
        if (
            not snap.album.strip()
            and snap.track_number == 0
            and 0 < snap.duration <= cfg.ad_max_duration_seconds
        ):
            return True

    return False


@dataclass(frozen=True)
class LooperState:
    """What we remember between polls."""

    armed: bool = False
    # Set when a loop fires, cleared once playback is seen back near the
    # start. Stops one pass through the lead window firing repeatedly.
    awaiting_restart: bool = False
    last_fire_monotonic: float | None = None


@dataclass(frozen=True)
class Decision:
    """What to do about one snapshot, plus the state to carry forward."""

    est_position: float
    remaining: float
    fire_loop: bool
    should_mute: bool
    state: LooperState


def evaluate(
    snap: Snapshot,
    state: LooperState,
    cfg: Config,
    now_utc: datetime,
    now_monotonic: float,
) -> Decision:
    """Decide what to do about one snapshot, and return the next state.

    Takes both clocks: wall-clock to compare against SMTC's timestamps,
    monotonic for the cooldown so changing the system clock can't break it.
    """
    est = extrapolate_position(snap, now_utc)
    remaining = snap.duration - est if snap.duration > 0 else float("inf")
    ad = is_ad(snap, cfg)
    drift = (now_utc - snap.last_updated).total_seconds()

    next_state = state
    if state.awaiting_restart and est < cfg.restart_threshold_seconds:
        next_state = replace(next_state, awaiting_restart=False)

    cooled_down = (
        state.last_fire_monotonic is None
        or now_monotonic - state.last_fire_monotonic >= cfg.cooldown_seconds
    )

    fire = (
        state.armed
        and snap.is_playing
        and not ad
        and snap.duration > 0
        and drift <= cfg.max_drift_seconds
        and remaining <= cfg.lead_seconds
        and not next_state.awaiting_restart
        and cooled_down
    )

    if fire:
        next_state = replace(
            next_state, awaiting_restart=True, last_fire_monotonic=now_monotonic
        )

    return Decision(
        est_position=est,
        remaining=remaining,
        fire_loop=fire,
        should_mute=ad,
        state=next_state,
    )

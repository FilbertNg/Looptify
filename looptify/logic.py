"""Pure decision logic. Imports nothing platform-specific, by design.

Everything here is a pure function of its arguments, which is what lets the
whole brain be tested in CI on a machine with no Spotify and no Windows.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from looptify.config import Config
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


def is_ad(snap: Snapshot, cfg: Config) -> bool:
    """True when this item looks like an ad rather than a track.

    Two independent rules, both built from real captured ads rather than
    guessed. Either one is enough.

    **Named artists** (`ad_markers`). Spotify's own house ads report
    `artist='Spotify'`. Matching is on the artist, exactly, and both choices
    matter. Titles are campaign- and language-specific — two ads captured in a
    single break were 'Dengarkan musik tanpa iklan.' and 'Nikmati musik tanpa
    iklan.', which is Indonesian — so a title can never be a reliable marker.
    And exact matching rather than substring is what stops legitimate releases
    like *Spotify Singles* (1,099 tracks) or *Spotify Sessions* (Dua Lipa, Sia,
    Twenty One Pilots) from being muted as ads.

    **Structure** (`detect_ads_by_structure`). A named-artist list can only
    ever catch Spotify's own ads; a third-party advertiser reports its own
    brand as the artist, and no list can enumerate every advertiser. But ads
    are structurally distinct from catalogue tracks regardless of who made
    them: every real Spotify track belongs to a release, so it carries an
    album and a track number, while ads carry neither and run short.

        ad:    album=''            track_number=0  duration=15-30s
        track: album='mosi mosi?'  track_number=1  duration=163.75s

    All three conditions must hold, which keeps an untagged local file from
    being muted unless it is also under `ad_max_duration_seconds`. The duration
    must be non-zero because Spotify briefly reports zero at a track boundary.
    """
    artist = snap.artist.strip().lower()
    if artist:
        for marker in cfg.ad_markers:
            if marker.strip() and artist == marker.strip().lower():
                return True

    if cfg.detect_ads_by_structure:
        if (
            not snap.album.strip()
            and snap.track_number == 0
            and 0 < snap.duration <= cfg.ad_max_duration_seconds
        ):
            return True

    return False


@dataclass(frozen=True)
class LooperState:
    """What Looptify remembers between polls.

    `awaiting_restart` is set when a loop fires and cleared only once playback
    is observed back near the start. Together with the cooldown it is what stops
    a single pass through the lead window from firing repeatedly.
    """

    armed: bool = False
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
    """Decide what to do about one snapshot. Pure; returns the next state.

    `now_monotonic` is a monotonic clock reading (time.monotonic()), used for
    the cooldown so that a system clock change cannot break it. `now_utc` is
    wall-clock UTC, needed because SMTC timestamps are wall-clock.
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

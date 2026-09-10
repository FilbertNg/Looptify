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
    ad = is_ad(snap, cfg.ad_markers)
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

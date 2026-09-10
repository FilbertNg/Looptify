from datetime import datetime, timedelta, timezone

from looptify.logic import extrapolate_position
from looptify.models import Snapshot

T0 = datetime(2026, 9, 10, 9, 42, 40, tzinfo=timezone.utc)


def snap(**kw) -> Snapshot:
    """A playing track at 79.4s of 170.6s, stamped at T0."""
    base = dict(
        title="oh yeah?",
        artist="Steve Lacy",
        album="Oh yeah?",
        is_playing=True,
        position=79.409,
        duration=170.584,
        last_updated=T0,
        can_seek=True,
    )
    base.update(kw)
    return Snapshot(**base)


def test_fresh_data_returns_reported_position():
    assert extrapolate_position(snap(), T0) == 79.409


def test_advances_by_elapsed_time_while_playing():
    got = extrapolate_position(snap(), T0 + timedelta(seconds=1.067))
    assert abs(got - 80.476) < 1e-6


def test_matches_measured_45_second_gap():
    # Measured on 2026-09-10: 128.995 stamped, 4.489s stale, actual 133.504.
    got = extrapolate_position(
        snap(position=128.995, last_updated=T0),
        T0 + timedelta(seconds=4.489),
    )
    assert abs(got - 133.504) < 0.05


def test_paused_playback_does_not_advance():
    got = extrapolate_position(snap(is_playing=False), T0 + timedelta(seconds=30))
    assert got == 79.409


def test_never_exceeds_duration():
    got = extrapolate_position(snap(), T0 + timedelta(seconds=999))
    assert got == 170.584


def test_clock_skew_backwards_does_not_rewind():
    got = extrapolate_position(snap(), T0 - timedelta(seconds=5))
    assert got == 79.409


def test_unknown_duration_still_extrapolates():
    got = extrapolate_position(snap(duration=0.0), T0 + timedelta(seconds=2))
    assert abs(got - 81.409) < 1e-6

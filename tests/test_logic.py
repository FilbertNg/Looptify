from datetime import datetime, timedelta, timezone

import pytest

from looptify.config import Config
from looptify.logic import (
    Decision,
    LooperState,
    evaluate,
    extrapolate_position,
    is_ad,
)
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


def test_no_markers_means_detection_is_off():
    # The safety property: an empty marker list must never match anything,
    # including a track that looks exactly like an ad.
    assert is_ad(snap(title="Advertisement", artist=""), ()) is False


def test_matches_marker_in_title_case_insensitively():
    assert is_ad(snap(title="Advertisement"), ("advertisement",)) is True


def test_matches_marker_in_artist():
    assert is_ad(snap(artist="Spotify"), ("spotify",)) is True


def test_matches_marker_in_album():
    assert is_ad(snap(album="Spotify Advert"), ("advert",)) is True


def test_real_track_does_not_match():
    assert is_ad(snap(), ("advertisement", "spotify")) is False


def test_track_with_blank_metadata_does_not_match():
    # A local file with no tags must not be mistaken for an ad.
    assert is_ad(snap(title="", artist="", album=""), ("advertisement",)) is False


def test_empty_marker_string_is_ignored():
    # An empty string is a substring of everything; it must not match all.
    assert is_ad(snap(), ("",)) is False


CFG = Config()


def ev(state=None, now_offset=0.0, mono=100.0, cfg=CFG, **snap_kw) -> Decision:
    """Evaluate a snapshot `now_offset` seconds after it was stamped."""
    return evaluate(
        snap(**snap_kw),
        state if state is not None else LooperState(armed=True),
        cfg,
        T0 + timedelta(seconds=now_offset),
        mono,
    )


def test_fires_inside_the_lead_window():
    # 170.584 duration, stamped at 169.0, so remaining is 1.584 -> 1.084.
    d = ev(position=169.0, now_offset=0.5)
    assert d.fire_loop is True
    assert d.remaining == pytest.approx(1.084, abs=1e-3)


def test_does_not_fire_outside_the_lead_window():
    assert ev(position=100.0).fire_loop is False


def test_does_not_fire_when_disarmed():
    d = ev(state=LooperState(armed=False), position=169.5)
    assert d.fire_loop is False


def test_does_not_fire_when_paused():
    d = ev(position=169.5, is_playing=False)
    assert d.fire_loop is False


def test_does_not_fire_on_unknown_duration():
    d = ev(position=169.5, duration=0.0)
    assert d.fire_loop is False
    assert d.remaining == float("inf")


def test_does_not_fire_on_an_ad():
    cfg = Config(ad_markers=("advertisement",))
    d = ev(cfg=cfg, position=169.5, title="Advertisement")
    assert d.fire_loop is False
    assert d.should_mute is True


def test_does_not_fire_when_data_is_too_stale():
    # 30s of drift means Spotify stopped reporting; refuse to guess.
    d = ev(position=169.5, now_offset=30.0)
    assert d.fire_loop is False


def test_firing_sets_awaiting_restart_and_stamps_the_time():
    d = ev(position=169.5, mono=500.0)
    assert d.fire_loop is True
    assert d.state.awaiting_restart is True
    assert d.state.last_fire_monotonic == 500.0
    assert d.state.armed is True


def test_does_not_fire_twice_while_awaiting_restart():
    first = ev(position=169.5, mono=500.0)
    # Still near the end, cooldown already elapsed, but no restart seen yet.
    second = evaluate(
        snap(position=169.8), first.state, CFG,
        T0 + timedelta(seconds=10), 510.0,
    )
    assert second.fire_loop is False
    assert second.state.awaiting_restart is True


def test_does_not_fire_again_within_cooldown_even_after_restart():
    fired = ev(position=169.5, mono=500.0)
    # Playback restarted, so awaiting_restart clears...
    restarted = evaluate(snap(position=0.5), fired.state, CFG, T0, 500.5)
    assert restarted.state.awaiting_restart is False
    # ...but back at the boundary only 2s later, the 3s cooldown still blocks.
    # This is the case where awaiting_restart alone would NOT have saved us.
    too_soon = evaluate(snap(position=169.5), restarted.state, CFG, T0, 502.0)
    assert too_soon.fire_loop is False


def test_rearms_after_restart_and_cooldown():
    first = ev(position=169.5, mono=500.0)
    restarted = evaluate(snap(position=1.0), first.state, CFG, T0, 505.0)
    assert restarted.state.awaiting_restart is False

    near_end_again = evaluate(snap(position=169.5), restarted.state, CFG, T0, 600.0)
    assert near_end_again.fire_loop is True


def test_mute_is_reported_even_when_disarmed():
    cfg = Config(ad_markers=("advertisement",))
    d = ev(state=LooperState(armed=False), cfg=cfg, title="Advertisement")
    assert d.should_mute is True
    assert d.fire_loop is False


def test_state_is_not_mutated_in_place():
    original = LooperState(armed=True)
    ev(state=original, position=169.5)
    assert original.awaiting_restart is False
    assert original.last_fire_monotonic is None

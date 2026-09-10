from datetime import datetime, timedelta, timezone

from looptify.logic import extrapolate_position, is_ad
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

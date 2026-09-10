from pathlib import Path

import pytest

from looptify.config import Config, load_config


def test_defaults_match_measured_behaviour():
    cfg = Config()
    assert cfg.lead_seconds == 1.5
    assert cfg.cooldown_seconds == 3.0
    assert cfg.restart_threshold_seconds == 5.0
    assert cfg.max_drift_seconds == 10.0
    assert cfg.poll_interval == 0.15
    assert cfg.hotkey == "ctrl+alt+l"
    assert cfg.start_armed is True
    assert cfg.show_notifications is True
    assert cfg.notification_seconds == 3.0
    assert cfg.ad_markers == ("Spotify",)
    assert cfg.detect_ads_by_structure is True
    assert cfg.ad_max_duration_seconds == 60.0
    assert cfg.log_tracks is False


def test_missing_file_returns_defaults(tmp_path: Path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_loads_values_from_toml(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        "lead_seconds = 2.5\n"
        'hotkey = "ctrl+shift+p"\n'
        'ad_markers = ["Advertisement", "Spotify"]\n',
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.lead_seconds == 2.5
    assert cfg.hotkey == "ctrl+shift+p"
    assert cfg.ad_markers == ("Advertisement", "Spotify")
    assert cfg.cooldown_seconds == 3.0  # untouched keys keep defaults


def test_unknown_keys_are_rejected(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text("nonsense_key = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="nonsense_key"):
        load_config(p)


def test_lead_must_be_positive(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text("lead_seconds = -1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="lead_seconds"):
        load_config(p)


def test_start_armed_can_be_turned_off(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text("start_armed = false\n", encoding="utf-8")
    assert load_config(p).start_armed is False


def test_ad_detection_can_be_turned_off_entirely(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        "ad_markers = []\ndetect_ads_by_structure = false\n", encoding="utf-8"
    )
    cfg = load_config(p)
    assert cfg.ad_markers == ()
    assert cfg.detect_ads_by_structure is False

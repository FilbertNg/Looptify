"""Configuration loading. Defaults live here and nowhere else."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path


@dataclass(frozen=True)
class Config:
    """Looptify settings. See config.toml for user-facing documentation."""

    lead_seconds: float = 1.5
    cooldown_seconds: float = 3.0
    restart_threshold_seconds: float = 5.0
    max_drift_seconds: float = 10.0
    poll_interval: float = 0.15
    hotkey: str = "ctrl+alt+l"
    quit_hotkey: str = "ctrl+alt+q"
    show_notifications: bool = True
    notification_seconds: float = 3.0
    ad_markers: tuple[str, ...] = ("Spotify",)
    detect_ads_by_structure: bool = True
    ad_max_duration_seconds: float = 60.0
    log_tracks: bool = False


_POSITIVE = (
    "lead_seconds",
    "cooldown_seconds",
    "restart_threshold_seconds",
    "max_drift_seconds",
    "poll_interval",
    "ad_max_duration_seconds",
    "notification_seconds",
)


def load_config(path: Path | None = None) -> Config:
    """Load config from TOML, falling back to defaults for absent keys.

    Raises ValueError on unknown keys or non-positive timing values, so a typo
    fails loudly at startup instead of silently doing nothing.
    """
    path = Path("config.toml") if path is None else path
    if not path.exists():
        return Config()

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    known = {f.name for f in fields(Config)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(
            f"Unknown key(s) in {path}: {', '.join(unknown)}. "
            f"Valid keys: {', '.join(sorted(known))}"
        )

    if "ad_markers" in raw:
        raw["ad_markers"] = tuple(raw["ad_markers"])

    for key in _POSITIVE:
        if key in raw and raw[key] <= 0:
            raise ValueError(f"{key} must be greater than 0, got {raw[key]}")

    return replace(Config(), **raw)

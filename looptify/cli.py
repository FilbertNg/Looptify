"""The poll loop, console display, and wiring."""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
import threading
import time

from looptify.audio import set_spotify_muted
from looptify.config import Config, load_config
from looptify.console import enable_unicode_output, on_console_close
from looptify.hotkey import HotkeyListener
from looptify.logic import Decision, LooperState, evaluate
from looptify.models import Snapshot
from looptify.smtc import SpotifyMonitor
from looptify.toast import ToastNotifier


# Ride out transient media-API hiccups, but don't spin forever on a real fault.
MAX_CONSECUTIVE_ERRORS = 20


def _format_time(seconds: float) -> str:
    if seconds < 0 or seconds == float("inf"):
        return "--:--"
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _status_line(
    state: LooperState, snap: Snapshot | None, decision: Decision | None
) -> str:
    armed = "ARMED " if state.armed else "IDLE  "
    if snap is None or decision is None:
        return f"[{armed}] waiting for Spotify..."

    track = f"{snap.artist} - {snap.title}".strip(" -")
    if len(track) > 42:
        track = track[:41] + "…"

    playing = "▶" if snap.is_playing else "⏸"
    remaining = decision.remaining
    countdown = (
        f"loop in {remaining:5.1f}s"
        if state.armed and remaining != float("inf")
        else " " * 13
    )
    return (
        f"[{armed}] {playing} {track:<42} "
        f"{_format_time(decision.est_position)}/{_format_time(snap.duration)} "
        f"{countdown}"
    )


def _describe_ad_detection(cfg: Config) -> str:
    """Say which ad rules are live, so the banner can't misreport them."""
    rules = []
    if cfg.ad_markers:
        rules.append(f"artist in {list(cfg.ad_markers)}")
    if cfg.detect_ads_by_structure:
        rules.append(f"no album/track and under {cfg.ad_max_duration_seconds:g}s")
    if not rules:
        return "OFF (no rules enabled in config.toml)"
    return "ON — " + " or ".join(rules)


async def run() -> int:
    """Run the looper until interrupted. Returns a process exit code."""
    enable_unicode_output()

    try:
        cfg = load_config()
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    state = LooperState(armed=cfg.start_armed)
    state_lock = threading.Lock()

    toaster = ToastNotifier(
        enabled=cfg.show_notifications, seconds=cfg.notification_seconds
    )
    toaster.start()

    def toggle() -> None:
        nonlocal state
        with state_lock:
            state = LooperState(
                armed=not state.armed,
                awaiting_restart=state.awaiting_restart,
                last_fire_monotonic=state.last_fire_monotonic,
            )
            armed_now = state.armed

        if armed_now:
            toaster.show(
                "Looptify Activated",
                "This song will loop before it ends.",
                positive=True,
            )
        else:
            toaster.show(
                "Looptify Deactivated",
                "Songs will play through normally.",
                positive=False,
            )

    try:
        listener = HotkeyListener({cfg.hotkey: toggle})
        listener.start()
    except (RuntimeError, ValueError) as exc:
        print(f"Hotkey error: {exc}", file=sys.stderr)
        toaster.stop()
        return 2

    monitor = SpotifyMonitor()
    await monitor.connect()

    # A previous run killed mid-ad would have left Spotify muted.
    set_spotify_muted(False)

    muted = False
    last_track: tuple[str, str] | None = None

    def emergency_cleanup() -> None:
        """Run when the console window is closed, where `finally` never fires."""
        if muted:
            set_spotify_muted(False)

    on_console_close(emergency_cleanup)

    print(
        f"Looptify — {cfg.hotkey} to arm/disarm. "
        f"Ctrl+C or close this window to quit."
    )
    print(f"Ad muting: {_describe_ad_detection(cfg)}")
    if not toaster.active and cfg.show_notifications:
        print("Notifications unavailable (tkinter could not start).")
    print()

    # Starting armed is silent otherwise, which is when state matters most.
    if state.armed:
        toaster.show(
            "Looptify Activated",
            "This song will loop before it ends.",
            positive=True,
        )

    consecutive_errors = 0

    try:
        while True:
            try:
                snap = await monitor.snapshot()

                if snap is None:
                    with state_lock:
                        current = state
                    sys.stdout.write(
                        "\r" + _status_line(current, None, None).ljust(110)
                    )
                    sys.stdout.flush()
                    await asyncio.sleep(cfg.poll_interval)
                    continue

                with state_lock:
                    current = state
                decision = evaluate(
                    snap,
                    current,
                    cfg,
                    dt.datetime.now(dt.timezone.utc),
                    time.monotonic(),
                )
                with state_lock:
                    # Preserve an arm/disarm that landed during evaluation.
                    state = LooperState(
                        armed=state.armed,
                        awaiting_restart=decision.state.awaiting_restart,
                        last_fire_monotonic=decision.state.last_fire_monotonic,
                    )
                    current = state

                if cfg.log_tracks:
                    track = (snap.title, snap.artist)
                    if track != last_track:
                        last_track = track
                        print(
                            f"\n[track] title={snap.title!r} "
                            f"artist={snap.artist!r} album={snap.album!r} "
                            f"track_number={snap.track_number} "
                            f"duration={snap.duration:.3f}"
                        )

                if decision.should_mute and not muted:
                    count = set_spotify_muted(True)
                    muted = True
                    print(
                        f"\n[mute] ad detected ({snap.artist!r}) — muted "
                        f"{count} session(s)"
                    )
                elif not decision.should_mute and muted:
                    count = set_spotify_muted(False)
                    muted = False
                    print(f"\n[mute] ad over — unmuted {count} session(s)")

                if decision.fire_loop:
                    method = await monitor.loop_now()
                    print(f"\n[loop] restarted via {method}")

                sys.stdout.write(
                    "\r" + _status_line(current, snap, decision).ljust(110)
                )
                sys.stdout.flush()
                consecutive_errors = 0

            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception as exc:
                # This runs unattended for hours; one bad frame shouldn't end it.
                consecutive_errors += 1
                print(
                    f"\n[warn] poll failed "
                    f"({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {exc!r}"
                )
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print("[error] too many consecutive failures — stopping.")
                    return 1

            await asyncio.sleep(cfg.poll_interval)

    except KeyboardInterrupt:
        return 0
    finally:
        if muted:
            set_spotify_muted(False)
        listener.stop()
        toaster.stop()
        print("\nStopped.")


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0

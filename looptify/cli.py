"""The poll loop and console display."""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
import threading
import time

from looptify.audio import set_spotify_muted
from looptify.config import load_config
from looptify.console import enable_unicode_output
from looptify.hotkey import HotkeyListener
from looptify.logic import Decision, LooperState, evaluate
from looptify.models import Snapshot
from looptify.smtc import SpotifyMonitor


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


async def run() -> int:
    """Run the looper until interrupted. Returns a process exit code."""
    enable_unicode_output()

    try:
        cfg = load_config()
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    state = LooperState(armed=False)
    state_lock = threading.Lock()

    def toggle() -> None:
        nonlocal state
        with state_lock:
            state = LooperState(
                armed=not state.armed,
                awaiting_restart=state.awaiting_restart,
                last_fire_monotonic=state.last_fire_monotonic,
            )

    try:
        listener = HotkeyListener(cfg.hotkey, toggle)
        listener.start()
    except (RuntimeError, ValueError) as exc:
        print(f"Hotkey error: {exc}", file=sys.stderr)
        return 2

    monitor = SpotifyMonitor()
    await monitor.connect()

    print(f"Looptify — press {cfg.hotkey} to arm/disarm, Ctrl+C to quit.")
    if not cfg.ad_markers:
        print("Ad muting is OFF (ad_markers is empty in config.toml).")
    print()

    muted = False
    last_track: tuple[str, str] | None = None

    try:
        while True:
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
                        f"\n[track] title={snap.title!r} artist={snap.artist!r} "
                        f"album={snap.album!r} duration={snap.duration:.3f}"
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

            sys.stdout.write("\r" + _status_line(current, snap, decision).ljust(110))
            sys.stdout.flush()
            await asyncio.sleep(cfg.poll_interval)

    except KeyboardInterrupt:
        return 0
    finally:
        if muted:
            set_spotify_muted(False)
        listener.stop()
        print("\nStopped.")


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0

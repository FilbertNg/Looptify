"""The poll loop, console display, and wiring."""

from __future__ import annotations

import asyncio
import datetime as dt
import random
import sys
import threading
import time
from concurrent.futures import Future

from looptify.audio import set_spotify_muted
from looptify.config import Config, load_config
from looptify.console import enable_unicode_output, on_console_close
from looptify.focus import reveal_spotify
from looptify.hotkey import HotkeyListener
from looptify.launcher import needs_relaunch, relaunch, spotify_pids
from looptify.logic import Decision, LooperState, evaluate
from looptify.models import Snapshot
from looptify.planner import (
    Plan,
    TrackKey,
    clear_press,
    fallback_reason,
    mark_pressed,
    prepare_skip,
    rechoose,
    should_rescue,
    skip_refusal,
    skip_verdict,
    skip_wait_reason,
)
from looptify.planner import step as plan_step
from looptify.playlist import MODE_BLURBS, MODE_LABELS, Mode, next_mode
from looptify.smtc import SpotifyMonitor
from looptify.toast import ToastNotifier
from looptify.tracklist import TracklistWorker


# Ride out transient media-API hiccups, but don't spin forever on a real fault.
MAX_CONSECUTIVE_ERRORS = 20


def _format_time(seconds: float) -> str:
    if seconds < 0 or seconds == float("inf"):
        return "--:--"
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _mode_suffix(mode: Mode, plan: Plan, ready_row: int | None) -> str:
    """The mode, and the row lined up next: '  Shuffle Loop → #212 ✓'."""
    if mode is Mode.LOOP:
        return ""
    label = MODE_LABELS[mode]
    if plan.target is None:
        return f"  {label}"
    mark = "✓" if ready_row == plan.target else "…"
    return f"  {label} → #{plan.target} {mark}"


def _status_line(
    state: LooperState,
    snap: Snapshot | None,
    decision: Decision | None,
    mode: Mode = Mode.LOOP,
    suffix: str = "",
) -> str:
    armed = "ARMED " if state.armed else "IDLE  "
    if snap is None or decision is None:
        return f"[{armed}] waiting for Spotify..."

    track = f"{snap.artist} - {snap.title}".strip(" -")
    if len(track) > 42:
        track = track[:41] + "…"

    playing = "▶" if snap.is_playing else "⏸"
    remaining = decision.remaining
    verb = "loop" if mode is Mode.LOOP else "next"
    countdown = (
        f"{verb} in {remaining:5.1f}s"
        if state.armed and remaining != float("inf")
        else " " * 13
    )
    return (
        f"[{armed}] {playing} {track:<42} "
        f"{_format_time(decision.est_position)}/{_format_time(snap.duration)} "
        f"{countdown}{suffix}"
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
    mode = cfg.mode
    skip_requested = False
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
            mode_now = mode

        if armed_now:
            toaster.show("Looptify Activated", MODE_BLURBS[mode_now], positive=True)
        else:
            toaster.show(
                "Looptify Deactivated",
                "Songs will play through normally.",
                positive=False,
            )

    def cycle_mode() -> None:
        nonlocal mode
        with state_lock:
            mode = next_mode(mode)
            mode_now = mode
        toaster.show(
            f"Mode: {MODE_LABELS[mode_now]}", MODE_BLURBS[mode_now], positive=True
        )

    def request_skip() -> None:
        nonlocal skip_requested
        with state_lock:
            skip_requested = True

    try:
        listener = HotkeyListener(
            {
                cfg.hotkey: toggle,
                cfg.mode_hotkey: cycle_mode,
                cfg.skip_hotkey: request_skip,
            }
        )
        listener.start()
    except (RuntimeError, ValueError) as exc:
        print(f"Hotkey error: {exc}", file=sys.stderr)
        toaster.stop()
        return 2

    monitor = SpotifyMonitor()
    await monitor.connect()
    worker = TracklistWorker()
    worker.start()

    # A previous run killed mid-ad would have left Spotify muted, and one
    # killed mid-press would have left its window hidden.
    set_spotify_muted(False)
    reveal_spotify()

    muted = False
    last_track: tuple[str, str] | None = None
    plan = Plan()
    rng = random.Random()
    flags_checked = False
    press: Future[bool] | None = None
    press_is_skip = False
    skipping: TrackKey | None = None  # the song a pending skip will leave
    crash_reported = False

    def emergency_cleanup() -> None:
        """Run when the console window is closed, where `finally` never fires."""
        if muted:
            set_spotify_muted(False)
        reveal_spotify()

    on_console_close(emergency_cleanup)

    print(
        f"Looptify — {cfg.hotkey} to arm/disarm, {cfg.mode_hotkey} to change "
        f"mode, {cfg.skip_hotkey} to skip. Ctrl+C or close this window to quit."
    )
    print(f"Mode: {MODE_LABELS[mode]}")
    print(f"Ad muting: {_describe_ad_detection(cfg)}")
    if not toaster.active and cfg.show_notifications:
        print("Notifications unavailable (tkinter could not start).")
    print()

    # Starting armed is silent otherwise, which is when state matters most.
    if state.armed:
        toaster.show("Looptify Activated", MODE_BLURBS[mode], positive=True)

    consecutive_errors = 0

    try:
        while True:
            try:
                with state_lock:
                    current = state
                    active_mode = mode
                    skip_now, skip_requested = skip_requested, False

                # Once per session, the first time a playlist mode is active.
                if active_mode is not Mode.LOOP and not flags_checked:
                    flags_checked = True
                    if await asyncio.to_thread(needs_relaunch):
                        verb = "Restarting" if spotify_pids() else "Starting"
                        toaster.show(
                            f"{verb} Spotify",
                            "Playlist modes need Spotify's page exposed.",
                            positive=True,
                        )
                        result = await asyncio.to_thread(relaunch)
                        print(f"\n[spotify] {result.message}")

                snap = await monitor.snapshot()

                if snap is None:
                    if skip_now:
                        toaster.show(
                            "Can't skip",
                            "Nothing is playing that can be skipped.",
                            positive=False,
                        )
                    sys.stdout.write(
                        "\r" + _status_line(current, None, None).ljust(110)
                    )
                    sys.stdout.flush()
                    await asyncio.sleep(cfg.poll_interval)
                    continue

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

                status = worker.status()
                if status.crashed is not None and not crash_reported:
                    crash_reported = True
                    print(
                        f"\n[warn] tracklist worker stopped ({status.crashed}) — "
                        f"playlist modes will loop instead"
                    )
                # Disarmed or crashed: plan nothing, so the worker sits idle.
                usable = current.armed and status.crashed is None
                effective = active_mode if usable else Mode.LOOP
                playing = None if decision.should_mute else (snap.title, snap.artist)

                plan, actions = plan_step(
                    plan,
                    mode=effective,
                    track=playing,
                    page=status.page,
                    located=status.located,
                    rng=rng,
                )
                for action in actions:
                    worker.send(action)

                if press is not None and press.done():
                    if not press.result():
                        if press_is_skip:
                            print("\n[skip] couldn't press the row")
                            toaster.show(
                                "Couldn't skip",
                                "Spotify didn't take the press. Try again.",
                                positive=False,
                            )
                        else:
                            print("\n[next] couldn't press the row — looping instead")
                            await monitor.loop_now()
                        plan, actions = clear_press(plan)
                        for action in actions:
                            worker.send(action)
                    press = None

                if should_rescue(plan, playing, decision.remaining):
                    print("\n[next] the pressed row never started — looping instead")
                    await monitor.loop_now()
                    plan, actions = clear_press(plan)
                    for action in actions:
                        worker.send(action)

                if decision.fire_loop and plan.pressed_row is not None:
                    pass  # a skip already pressed the next row; the rescue covers it
                elif decision.fire_loop:
                    if plan.target is not None and status.ready_row == plan.target:
                        press = worker.press()
                        press_is_skip = False
                        plan = mark_pressed(plan)
                        print(f"\n[next] playing row {plan.target}")
                    else:
                        reason = fallback_reason(plan, status.ready_row)
                        if reason is not None:
                            print(f"\n[next] {reason} — looping instead")
                        method = await monitor.loop_now()
                        print(f"\n[loop] restarted via {method}")
                        # The same song again: let the random modes draw anew.
                        plan, actions = rechoose(plan, rng)
                        for action in actions:
                            worker.send(action)

                if skip_now and skipping is None:
                    refusal = skip_refusal(current.armed, active_mode, plan)
                    if plan.pressed_row is not None:
                        print("\n[skip] already moving to the next song")
                    elif refusal is not None:
                        toaster.show("Can't skip", refusal, positive=False)
                    else:
                        skipping = plan.track

                if skipping is not None:
                    plan, actions = prepare_skip(plan, rng)
                    for action in actions:
                        worker.send(action)
                    verdict = skip_verdict(plan, skipping, status.ready_row)
                    if verdict == "press":
                        skipping = None
                        press = worker.press()
                        press_is_skip = True
                        plan = mark_pressed(plan)
                        print(f"\n[skip] playing row {plan.target}")
                    elif verdict == "drop":
                        skipping = None
                        print(
                            "\n[skip] cancelled — the song, mode or open page "
                            "changed first"
                        )
                    elif skip_now:
                        reason = skip_wait_reason(plan)
                        toaster.show("Finding the next song", reason, positive=True)
                        print(f"\n[skip] {reason}")

                suffix = _mode_suffix(effective, plan, status.ready_row)
                if skipping is not None:
                    suffix += " ⏭"

                sys.stdout.write(
                    "\r"
                    + _status_line(
                        current,
                        snap,
                        decision,
                        effective,
                        suffix,
                    ).ljust(110)
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
        worker.stop()
        reveal_spotify()
        listener.stop()
        toaster.stop()
        print("\nStopped.")


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0

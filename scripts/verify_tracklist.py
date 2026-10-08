"""Manual check: can Looptify read and drive Spotify's tracklist?

Needs Spotify started with the playlist-mode flags (see launcher.REQUIRED_FLAGS)
and a playlist or Liked Songs open in it, with something playing.

    python scripts/verify_tracklist.py              # page, current row
    python scripts/verify_tracklist.py 120          # ...and scroll row 120 into range
    python scripts/verify_tracklist.py 120 --play   # ...and press it
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Python puts this script's own directory on sys.path, not the repo root,
# so the package would not be importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import win32con  # noqa: E402
import win32gui  # noqa: E402

from looptify.console import enable_unicode_output  # noqa: E402
from looptify.focus import _has_region  # noqa: E402
from looptify.launcher import spotify_window  # noqa: E402
from looptify.planner import Locate, Prepare  # noqa: E402
from looptify.smtc import SpotifyMonitor  # noqa: E402
from looptify.tracklist import TracklistStatus, TracklistWorker  # noqa: E402

enable_unicode_output()


def wait_for(worker: TracklistWorker, ready, timeout: float) -> TracklistStatus:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = worker.status()
        if status.crashed or ready(status):
            return status
        time.sleep(0.1)
    return worker.status()


def _above(a: int, b: int) -> bool:
    """True if window a is above window b in the z-order."""
    window = win32gui.GetWindow(b, win32con.GW_HWNDPREV)
    while window:
        if window == a:
            return True
        window = win32gui.GetWindow(window, win32con.GW_HWNDPREV)
    return False


async def now_playing() -> tuple[str, str] | None:
    monitor = SpotifyMonitor()
    if not await monitor.connect():
        return None
    snap = await monitor.snapshot()
    return (snap.title, snap.artist) if snap else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("row", type=int, nargs="?")
    parser.add_argument("--play", action="store_true")
    args = parser.parse_args()

    worker = TracklistWorker()
    worker.start()
    try:
        status = wait_for(worker, lambda s: s.page is not None, 5)
        print(f"exposed={status.exposed} page={status.page} crashed={status.crashed}")
        if status.page is None:
            print("No tracklist. Is Spotify started with the flags, on a playlist page?")
            return

        track = asyncio.run(now_playing())
        print(f"now playing: {track}")
        if track:
            started = time.monotonic()
            worker.send(Locate(1, *track))
            status = wait_for(worker, lambda s: s.located and s.located[0] == 1, 120)
            print(
                f"current row: {status.located and status.located[1]} "
                f"({time.monotonic() - started:.1f}s)"
            )

        if args.row is None:
            return
        started = time.monotonic()
        worker.send(Prepare(args.row))
        status = wait_for(worker, lambda s: s.ready_row == args.row, 120)
        print(
            f"row {args.row} ready={status.ready_row == args.row} "
            f"({time.monotonic() - started:.1f}s)"
        )

        if args.play and status.ready_row == args.row:
            user_window = win32gui.GetForegroundWindow()
            pressed = worker.press().result(timeout=5)
            time.sleep(2.0)
            print(f"pressed={pressed} focus_reclaim_ms={worker.focus_reclaim_ms}")
            print(f"now playing: {asyncio.run(now_playing())}")
            spotify = spotify_window()
            print(
                f"spotify restored: hidden={_has_region(spotify)} "
                f"in front of your window={_above(spotify, user_window)} "
                f"focus back={win32gui.GetForegroundWindow() == user_window}"
            )
    finally:
        worker.stop()


main()

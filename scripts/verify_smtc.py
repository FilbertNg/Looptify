"""Manual check: does the adapter read Spotify correctly? Read-only."""

import asyncio
import datetime as dt
import sys
from pathlib import Path

# Python puts this script's own directory on sys.path, not the repo root,
# so the package would not be importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looptify.console import enable_unicode_output  # noqa: E402
from looptify.logic import extrapolate_position  # noqa: E402
from looptify.smtc import SpotifyMonitor  # noqa: E402

enable_unicode_output()


async def main() -> None:
    monitor = SpotifyMonitor()
    if not await monitor.connect():
        print("Spotify not found. Start it and play something.")
        return

    for _ in range(20):
        snap = await monitor.snapshot()
        if snap is None:
            print("no snapshot")
        else:
            now = dt.datetime.now(dt.timezone.utc)
            est = extrapolate_position(snap, now)
            stale = (now - snap.last_updated).total_seconds()
            print(
                f"{snap.artist} - {snap.title} | "
                f"pos={snap.position:7.2f} est={est:7.2f} "
                f"dur={snap.duration:7.2f} stale={stale:5.2f} "
                f"playing={snap.is_playing} seek={snap.can_seek}"
            )
        await asyncio.sleep(0.5)


asyncio.run(main())

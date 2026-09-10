"""Manual check: does muting still work, and is it fast enough to not stall the poll loop?

Reads the mute state back through pycaw's own enumeration rather than through
`audio.py`, so a broken implementation cannot vouch for itself.

Spotify must be running. Always leaves it unmuted, even on failure.
"""

import statistics
import sys
import time
from pathlib import Path

# Python puts this script's own directory on sys.path, not the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume  # noqa: E402

from looptify.audio import set_spotify_muted  # noqa: E402

# set_spotify_muted runs on the asyncio thread, so it blocks the poll loop for
# however long it takes. Anything near a poll interval (150ms) is too slow.
BUDGET_MS = 50.0


def read_mute_states() -> list[int]:
    """Independent read-back: what does Windows say Spotify's sessions are set to?"""
    states = []
    for session in AudioUtilities.GetAllSessions():
        process = session.Process
        if process is None or process.name().lower() != "spotify.exe":
            continue
        states.append(session._ctl.QueryInterface(ISimpleAudioVolume).GetMute())
    return states


def main() -> int:
    if not read_mute_states():
        print("No Spotify audio sessions. Start Spotify and play something.")
        return 2

    failures = []
    try:
        changed = set_spotify_muted(True)
        states = read_mute_states()
        print(f"mute:   changed {changed} session(s), read back {states}")
        if not states or not all(states):
            failures.append("mute did not take effect")

        changed = set_spotify_muted(False)
        states = read_mute_states()
        print(f"unmute: changed {changed} session(s), read back {states}")
        if any(states):
            failures.append("unmute did not take effect")

        timings = []
        for _ in range(10):
            start = time.perf_counter()
            set_spotify_muted(False)
            timings.append((time.perf_counter() - start) * 1000)
        timings.sort()
        median = statistics.median(timings)
        print(
            f"timing: median {median:.1f}ms  min {timings[0]:.1f}ms  "
            f"max {timings[-1]:.1f}ms  (budget {BUDGET_MS:.0f}ms)"
        )
        if median > BUDGET_MS:
            failures.append(
                f"too slow: {median:.1f}ms median blocks the poll loop for "
                f"{median / 150:.1f} poll intervals"
            )
    finally:
        set_spotify_muted(False)

    if failures:
        print("\nFAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Manual check: press Ctrl+Alt+L a few times, then Ctrl+C."""

import sys
import time
from pathlib import Path

# Python puts this script's own directory on sys.path, not the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looptify.console import enable_unicode_output  # noqa: E402
from looptify.hotkey import HotkeyListener  # noqa: E402

enable_unicode_output()

count = 0


def on_press() -> None:
    global count
    count += 1
    print(f"hotkey fired ({count})")


listener = HotkeyListener("ctrl+alt+l", on_press)
listener.start()
print("Listening. Press Ctrl+Alt+L (works even when this window is not focused).")
try:
    while True:
        time.sleep(0.2)
except KeyboardInterrupt:
    listener.stop()
    print(f"\nstopped after {count} press(es)")

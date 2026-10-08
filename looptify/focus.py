"""Hands keyboard focus back when Spotify takes it during a row press.

Pressing a row's play button makes Spotify activate itself, and nothing outside
Spotify can stop that. What does work is taking focus straight back: a
zero-distance SendInput makes Looptify the last-input process, which Windows
allows to set the foreground. Measured at 3-5 ms on 2026-10-08.
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections.abc import Collection
from ctypes import wintypes

import pywintypes
import win32gui
import win32process

from looptify.launcher import spotify_pids

WATCH_SECONDS = 1.5
# Don't hammer SetForegroundWindow if Windows refuses the first try.
_RETRY_SECONDS = 0.02
_INPUT_MOUSE = 0
_MOUSEEVENTF_MOVE = 0x0001


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", _MouseInput)]


def should_reclaim(
    *,
    original: int,
    original_pid: int,
    current: int,
    current_pid: int,
    spotify_pids: Collection[int],
    original_alive: bool,
) -> bool:
    """Reclaim only when Spotify took focus from a still-open, non-Spotify window.

    Focus moving anywhere else is the user switching apps; leave it be.
    """
    return (
        current != original
        and original_alive
        and original_pid not in spotify_pids
        and current_pid in spotify_pids
    )


def _pid(hwnd: int) -> int:
    return win32process.GetWindowThreadProcessId(hwnd)[1] if hwnd else 0


def _reclaim(hwnd: int) -> None:
    nudge = _Input(_INPUT_MOUSE, _MouseInput(0, 0, 0, _MOUSEEVENTF_MOVE, 0, 0))
    ctypes.windll.user32.SendInput(1, ctypes.byref(nudge), ctypes.sizeof(_Input))
    try:
        win32gui.SetForegroundWindow(hwnd)
    except pywintypes.error:
        pass  # retried on a later pass


class FocusGuard:
    """Watches the foreground for a moment after a press, and undoes a steal."""

    def __init__(self, seconds: float = WATCH_SECONDS) -> None:
        self._seconds = seconds
        self.last_reclaim_ms: float | None = None

    def protect(self) -> threading.Thread:
        """Start watching from now. Call just before pressing; returns at once."""
        original = win32gui.GetForegroundWindow()
        thread = threading.Thread(
            target=self._watch,
            args=(original, _pid(original), spotify_pids()),
            name="looptify-focus",
            daemon=True,
        )
        thread.start()
        return thread

    def _watch(self, original: int, original_pid: int, spotify: set[int]) -> None:
        end = time.perf_counter() + self._seconds
        stolen_at: float | None = None
        last_try = float("-inf")
        while (now := time.perf_counter()) < end:
            current = win32gui.GetForegroundWindow()
            if should_reclaim(
                original=original,
                original_pid=original_pid,
                current=current,
                current_pid=_pid(current),
                spotify_pids=spotify,
                original_alive=bool(original and win32gui.IsWindow(original)),
            ):
                if stolen_at is None:
                    stolen_at = now
                if now - last_try >= _RETRY_SECONDS:
                    last_try = now
                    _reclaim(original)
            elif stolen_at is not None and current == original:
                self.last_reclaim_ms = (now - stolen_at) * 1000
                stolen_at = None
            time.sleep(0.001)

"""Keeps a row press in the background: hides Spotify, and hands focus back.

Pressing a row's play button makes Spotify activate itself, and nothing outside
Spotify can stop that: disabling its windows and LockSetForegroundWindow both
fail, and another process's window can't be DWM-cloaked. Two things do work:

- Taking focus straight back. A zero-distance SendInput makes Looptify the
  last-input process, which Windows allows to set the foreground. Measured at
  4-12 ms on 2026-10-09.
- Cutting Spotify's window down, for the moment of the press, to the part
  already on screen, so being raised to the top shows nothing new. Without
  it, Spotify visibly flashes on screen; with it, the user saw nothing
  (2026-10-09). Covered entirely, the window is cut to nothing. An empty
  region regardless blacked out every part of Spotify showing around a
  smaller window in front, such as the Looptify console (2026-10-09).
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections.abc import Collection, Iterable, Iterator, Sequence
from contextlib import contextmanager
from ctypes import wintypes

import pywintypes
import win32con
import win32gui
import win32process

from looptify.launcher import spotify_pids, spotify_window

WATCH_SECONDS = 1.5
# The steal lands within ~20 ms of the press; keep Spotify hidden well past it.
HIDE_SECONDS = 0.8
# Don't hammer SetForegroundWindow if Windows refuses the first try.
_RETRY_SECONDS = 0.02
_INPUT_MOUSE = 0
_MOUSEEVENTF_MOVE = 0x0001
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_DWMWA_CLOAKED = 14
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_RGN_DIFF = 4

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_dwmapi = ctypes.windll.dwmapi
_user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]

Rect = tuple[int, int, int, int]  # left, top, right, bottom


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


def should_hide(*, spotify: int, foreground: int, minimized: bool) -> bool:
    """Hide Spotify for the press only when it's open behind the user's window.

    Minimized, nothing would show anyway. In front, the user is looking at it,
    and hiding it would cause the very flicker this prevents.
    """
    return bool(spotify) and not minimized and foreground != spotify


def restack_anchor(above: Sequence[tuple[int, bool]]) -> int | None:
    """The window to slot Spotify back under, from those above it (nearest first,
    as (hwnd, is_topmost)).

    Topmost windows are skipped: inserting after one would make Spotify
    topmost too.
    """
    for hwnd, topmost in above:
        if not topmost:
            return hwnd
    return None


def visible_part(
    window: Rect, frame: Rect, covers: Iterable[Rect]
) -> tuple[Rect, list[Rect]]:
    """The part of a window on screen, as a region: (keep, minus each cutout).

    `window` is its full rect, `frame` the part DWM actually draws (without
    the invisible resize border), and `covers` the windows above it, all in
    screen coordinates. Regions are relative to the window's rect, so the
    results are too.
    """
    left, top, _, _ = window

    def local(rect: Rect) -> Rect:
        return (rect[0] - left, rect[1] - top, rect[2] - left, rect[3] - top)

    fl, ft, fr, fb = frame
    cutouts = []
    for cl, ct, cr, cb in covers:
        cl, ct, cr, cb = max(cl, fl), max(ct, ft), min(cr, fr), min(cb, fb)
        if cl < cr and ct < cb:
            cutouts.append(local((cl, ct, cr, cb)))
    return local(frame), cutouts


@contextmanager
def _physical_pixels() -> Iterator[None]:
    """Work in physical pixels on this thread; Looptify isn't DPI-aware."""
    previous = _user32.SetThreadDpiAwarenessContext(
        _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    )
    try:
        yield
    finally:
        if previous:
            _user32.SetThreadDpiAwarenessContext(previous)


def _frame(hwnd: int) -> Rect:
    """The window's drawn bounds, without the invisible resize border."""
    rect = wintypes.RECT()
    if _dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
    ):
        return win32gui.GetWindowRect(hwnd)  # nonzero HRESULT: DWM can't say
    return (rect.left, rect.top, rect.right, rect.bottom)


def _covers(hwnd: int) -> bool:
    """Whether a window above Spotify hides what's beneath it.

    Hidden, minimized and cloaked windows cover nothing, and neither do
    click-through layered windows, which are overlays. Any other window
    counts as covering, even if it's partly see-through: wrongly counting it
    leaves a few pixels dark for a moment, but wrongly not counting it would
    let Spotify flash over the user's app.
    """
    if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
        return False
    overlay = win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
    if win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & overlay == overlay:
        return False
    cloaked = wintypes.DWORD()
    _dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
    )
    return not cloaked.value


def _pid(hwnd: int) -> int:
    return win32process.GetWindowThreadProcessId(hwnd)[1] if hwnd else 0


def _reclaim(hwnd: int) -> None:
    nudge = _Input(_INPUT_MOUSE, _MouseInput(0, 0, 0, _MOUSEEVENTF_MOVE, 0, 0))
    _user32.SendInput(1, ctypes.byref(nudge), ctypes.sizeof(_Input))
    try:
        win32gui.SetForegroundWindow(hwnd)
    except pywintypes.error:
        pass  # retried on a later pass


def _windows_above(hwnd: int) -> list[tuple[int, bool]]:
    found = []
    above = win32gui.GetWindow(hwnd, win32con.GW_HWNDPREV)
    while above:
        exstyle = win32gui.GetWindowLong(above, win32con.GWL_EXSTYLE)
        found.append((above, bool(exstyle & win32con.WS_EX_TOPMOST)))
        above = win32gui.GetWindow(above, win32con.GW_HWNDPREV)
    return found


def _has_region(hwnd: int) -> bool:
    region = _gdi32.CreateRectRgn(0, 0, 0, 0)
    try:
        return _user32.GetWindowRgn(hwnd, region) != 0  # 0 is ERROR: no region
    finally:
        _gdi32.DeleteObject(region)


def _hide(hwnd: int) -> bool:
    """Cut the window down to what's already on screen, so raising it shows nothing.

    It stays put and keeps rendering. Fully covered, the region is empty, and
    so is it if the windows above can't be measured: a moment of dark beats
    Spotify flashing over the user's app.
    """
    region = _gdi32.CreateRectRgn(0, 0, 0, 0)
    try:
        with _physical_pixels():
            covers = []
            above = win32gui.GetWindow(hwnd, win32con.GW_HWNDPREV)
            while above:
                if _covers(above):
                    covers.append(_frame(above))
                above = win32gui.GetWindow(above, win32con.GW_HWNDPREV)
            keep, cutouts = visible_part(
                win32gui.GetWindowRect(hwnd), _frame(hwnd), covers
            )
            _gdi32.SetRectRgn(region, *keep)
            for cutout in cutouts:
                hole = _gdi32.CreateRectRgn(*cutout)
                _gdi32.CombineRgn(region, region, hole, _RGN_DIFF)
                _gdi32.DeleteObject(hole)
    except pywintypes.error:
        _gdi32.SetRectRgn(region, 0, 0, 0, 0)
    if _user32.SetWindowRgn(hwnd, region, False):
        return True  # the system owns the region now
    _gdi32.DeleteObject(region)
    return False


def _unhide(hwnd: int, anchor: int | None) -> None:
    """Put the window back under `anchor` in the stack, then show it again."""
    if anchor and win32gui.IsWindow(anchor):
        win32gui.SetWindowPos(
            hwnd,
            anchor,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
        )
    _user32.SetWindowRgn(hwnd, None, True)


def reveal_spotify() -> None:
    """Clear a region left on Spotify's window by a run killed mid-press.

    Spotify's main window never has a region of its own (checked 2026-10-09),
    so any region found is ours.
    """
    hwnd = spotify_window()
    if hwnd and _has_region(hwnd):
        _user32.SetWindowRgn(hwnd, None, True)


class FocusGuard:
    """Hides Spotify for a press, and undoes the focus steal that follows."""

    def __init__(self, seconds: float = WATCH_SECONDS) -> None:
        self._seconds = seconds
        self.last_reclaim_ms: float | None = None

    def protect(self) -> threading.Thread:
        """Hide Spotify and start watching. Call just before pressing; returns at once."""
        original = win32gui.GetForegroundWindow()
        spotify = spotify_window()
        hidden, anchor = False, None
        if should_hide(
            spotify=spotify,
            foreground=original,
            minimized=bool(spotify and _user32.IsIconic(spotify)),
        ):
            anchor = restack_anchor(_windows_above(spotify))
            hidden = _hide(spotify)
        thread = threading.Thread(
            target=self._watch,
            args=(original, _pid(original), spotify_pids(), spotify if hidden else 0, anchor),
            name="looptify-focus",
            daemon=True,
        )
        thread.start()
        return thread

    def _watch(
        self,
        original: int,
        original_pid: int,
        spotify: set[int],
        hidden: int,
        anchor: int | None,
    ) -> None:
        start = time.perf_counter()
        end = start + self._seconds
        stolen_at: float | None = None
        last_try = float("-inf")
        try:
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
                if hidden and now - start >= HIDE_SECONDS and stolen_at is None:
                    _unhide(hidden, anchor)
                    hidden = 0
                time.sleep(0.001)
        finally:
            if hidden:
                _unhide(hidden, anchor)

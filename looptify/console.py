"""Console output setup.

The Windows console defaults to a legacy code page (cp1252 here), so printing a
track title containing anything outside Latin-1 — Japanese, Korean, Cyrillic,
emoji, and plenty of ordinary punctuation — raises UnicodeEncodeError and kills
the process. Spotify track names hit this constantly, so every entry point
reconfigures stdout before printing anything.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable

_CTRL_C_EVENT = 0
_CTRL_BREAK_EVENT = 1
_CTRL_CLOSE_EVENT = 2
_CTRL_LOGOFF_EVENT = 5
_CTRL_SHUTDOWN_EVENT = 6

_HANDLER_ROUTINE = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

# Windows calls the handler on its own thread and frees nothing for us, so the
# callback object has to outlive this function or it gets garbage collected and
# the process crashes when the console closes.
_installed_handlers: list = []


def on_console_close(cleanup: Callable[[], None]) -> bool:
    """Run `cleanup` when the console window is closed or the user logs off.

    Clicking the X on a console window terminates the process without raising
    KeyboardInterrupt, so `finally` blocks never run. Without this, closing the
    window during an ad would leave Spotify muted. Windows gives the handler a
    few seconds before killing the process, which is ample.

    Returns False if the handler could not be installed.
    """
    handled = {
        _CTRL_C_EVENT,
        _CTRL_BREAK_EVENT,
        _CTRL_CLOSE_EVENT,
        _CTRL_LOGOFF_EVENT,
        _CTRL_SHUTDOWN_EVENT,
    }

    def _handler(ctrl_type: int) -> bool:
        if ctrl_type in handled:
            try:
                cleanup()
            except Exception:
                pass
            # Ctrl+C stays with Python so KeyboardInterrupt still works;
            # the others we own, because the process is going away regardless.
            return ctrl_type != _CTRL_C_EVENT
        return False

    routine = _HANDLER_ROUTINE(_handler)
    _installed_handlers.append(routine)
    try:
        return bool(ctypes.windll.kernel32.SetConsoleCtrlHandler(routine, True))
    except (AttributeError, OSError):
        return False


def enable_unicode_output() -> None:
    """Switch stdout/stderr to UTF-8, replacing anything unrepresentable.

    `errors="replace"` matters as much as the encoding: a terminal font that
    cannot render a glyph should show a placeholder, never crash the looper
    mid-track.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                # A redirected or already-closed stream; printing still works.
                pass

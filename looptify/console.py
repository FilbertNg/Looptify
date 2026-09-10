"""Console setup: encoding, and cleanup when the window is closed."""

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

# Must outlive this module's functions or it gets collected and Windows
# crashes the process calling into freed memory.
_installed_handlers: list = []


def on_console_close(cleanup: Callable[[], None]) -> bool:
    """Run `cleanup` when the console window is closed. False if not installed.

    Clicking the X kills the process without raising KeyboardInterrupt, so
    `finally` never runs — which would leave Spotify muted mid-ad.
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
            # Leave Ctrl+C to Python so KeyboardInterrupt still works.
            return ctrl_type != _CTRL_C_EVENT
        return False

    routine = _HANDLER_ROUTINE(_handler)
    _installed_handlers.append(routine)
    try:
        return bool(ctypes.windll.kernel32.SetConsoleCtrlHandler(routine, True))
    except (AttributeError, OSError):
        return False


def enable_unicode_output() -> None:
    """Switch stdout/stderr to UTF-8 so non-Latin track names don't crash us.

    The Windows console defaults to a legacy code page, where printing a
    Japanese or Cyrillic title raises UnicodeEncodeError.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                # A redirected or already-closed stream; printing still works.
                pass

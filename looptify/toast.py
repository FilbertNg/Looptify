"""Corner notifications for arm/disarm.

tkinter rather than native Windows toasts: those need a registered
AppUserModelID and show nothing without one, which is a bad failure mode
for a feature whose job is reporting state.

Tk owns its interpreter per-thread, so one thread holds a hidden root and
takes work off a queue. A toast fades in, holds, fades out; a newer one
cuts the hold short and takes over once the old one has faded.
"""

from __future__ import annotations

import queue
import threading

_POLL_MS = 100
_FADE_STEP_MS = 16
_FADE_IN_STEP = 0.12  # ~130ms to fully visible
_FADE_OUT_STEP = 0.06  # ~250ms unhurried exit after the hold
_FADE_PREEMPT_STEP = 0.16  # ~100ms exit when a newer toast is waiting
_MAX_ALPHA = 0.96

_MARGIN = 24
_TASKBAR_ALLOWANCE = 64

_BG = "#181818"
_ACCENT_ON = "#1db954"  # Spotify green
_ACCENT_OFF = "#7a7a7a"
_FG = "#ffffff"
_FG_DIM = "#b3b3b3"


class ToastNotifier:
    """Shows brief messages in the corner of the screen.

    Degrades to doing nothing if tkinter is unavailable or fails to start —
    a missing notification must never take the looper down with it.
    """

    def __init__(self, enabled: bool = True, seconds: float = 3.0) -> None:
        self._enabled = enabled
        self._seconds = seconds
        self._queue: queue.Queue[tuple[str, str, bool] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._failed = False

        # Toast-thread state.
        self._window = None
        self._jobs: list[str] = []
        self._generation = 0
        self._exiting = False
        self._pending: tuple[str, str, bool] | None = None

    @property
    def active(self) -> bool:
        """True when notifications will actually be drawn."""
        return self._enabled and not self._failed

    def start(self) -> None:
        if not self._enabled:
            return
        self._thread = threading.Thread(
            target=self._run, name="looptify-toast", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def show(self, title: str, subtitle: str = "", positive: bool = True) -> None:
        """Queue a notification. Never raises."""
        if not self.active:
            return
        self._queue.put((title, subtitle, positive))

    def stop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout=2.0)

    # --- everything below runs on the toast thread only ---

    def _run(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            self._failed = True
            self._ready.set()
            return

        try:
            root = tk.Tk()
            root.withdraw()
        except Exception:
            # No display, no Tcl, a locked session — all survivable.
            self._failed = True
            self._ready.set()
            return

        self._tk = tk
        self._root = root
        self._ready.set()

        root.after(_POLL_MS, self._drain)
        try:
            root.mainloop()
        except Exception:
            self._failed = True

    def _drain(self) -> None:
        latest: tuple[str, str, bool] | None = None
        stopping = False
        try:
            while True:
                item = self._queue.get_nowait()
                if item is None:
                    stopping = True
                    break
                # Only the newest matters; older ones are already stale.
                latest = item
        except queue.Empty:
            pass

        if stopping:
            self._teardown()
            self._root.quit()
            return

        if latest is not None:
            try:
                self._render(*latest)
            except Exception:
                # One bad toast must not kill the thread.
                self._teardown()

        self._root.after(_POLL_MS, self._drain)

    def _cancel_jobs(self) -> None:
        for job in self._jobs:
            try:
                self._root.after_cancel(job)
            except Exception:
                pass
        self._jobs.clear()

    def _teardown(self) -> None:
        """Cancel anything pending and remove the window immediately."""
        self._generation += 1
        self._exiting = False
        self._cancel_jobs()
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None

    def _later(self, delay_ms: int, callback) -> None:
        self._jobs.append(self._root.after(delay_ms, callback))

    def _render(self, title: str, subtitle: str, positive: bool) -> None:
        """Queue this toast, letting anything on screen exit first."""
        self._pending = (title, subtitle, positive)

        if self._window is None:
            self._present_pending()
        elif not self._exiting:
            # Cut the hold short, but let the old toast fade rather than snap.
            self._begin_exit(_FADE_PREEMPT_STEP)
        # If it is already exiting, _pending is picked up when that finishes.

    def _present_pending(self) -> None:
        item, self._pending = self._pending, None
        if item is None:
            return
        try:
            self._build(*item)
        except Exception:
            self._teardown()

    def _build(self, title: str, subtitle: str, positive: bool) -> None:
        tk = self._tk

        self._teardown()
        generation = self._generation

        win = tk.Toplevel(self._root)
        win.overrideredirect(True)  # no title bar or border
        win.attributes("-topmost", True)
        win.configure(bg=_BG)
        try:
            win.attributes("-alpha", 0.0)  # faded in below
        except Exception:
            pass

        accent = _ACCENT_ON if positive else _ACCENT_OFF
        tk.Frame(win, bg=accent, width=4).pack(side="left", fill="y")

        body = tk.Frame(win, bg=_BG)
        body.pack(side="left", fill="both", expand=True, padx=(14, 18), pady=12)

        tk.Label(
            body, text=title, bg=_BG, fg=_FG,
            font=("Segoe UI", 11, "bold"), anchor="w", justify="left",
        ).pack(anchor="w")

        if subtitle:
            tk.Label(
                body, text=subtitle, bg=_BG, fg=_FG_DIM,
                font=("Segoe UI", 9), anchor="w", justify="left",
            ).pack(anchor="w", pady=(2, 0))

        # Size to content, then pin to the bottom-right above the taskbar.
        win.update_idletasks()
        width = win.winfo_reqwidth()
        height = win.winfo_reqheight()
        x = win.winfo_screenwidth() - width - _MARGIN
        y = win.winfo_screenheight() - height - _TASKBAR_ALLOWANCE
        win.geometry(f"{width}x{height}+{x}+{y}")

        self._window = win
        self._fade_in(generation, 0.0)

    def _stale(self, generation: int) -> bool:
        return generation != self._generation or self._window is None

    def _set_alpha(self, alpha: float) -> bool:
        try:
            self._window.attributes("-alpha", max(0.0, min(alpha, _MAX_ALPHA)))
            return True
        except Exception:
            return False

    def _current_alpha(self) -> float:
        try:
            return float(self._window.attributes("-alpha"))
        except Exception:
            return _MAX_ALPHA

    def _fade_in(self, generation: int, alpha: float) -> None:
        if self._stale(generation) or not self._set_alpha(alpha):
            return
        if alpha < _MAX_ALPHA:
            self._later(
                _FADE_STEP_MS,
                lambda: self._fade_in(generation, alpha + _FADE_IN_STEP),
            )
        else:
            self._later(
                int(self._seconds * 1000),
                lambda: self._begin_exit(_FADE_OUT_STEP),
            )

    def _begin_exit(self, step: float) -> None:
        """Start fading the current toast away, from wherever it is now."""
        if self._window is None:
            return
        self._cancel_jobs()
        self._generation += 1
        self._exiting = True
        self._fade_out(self._generation, self._current_alpha(), step)

    def _fade_out(self, generation: int, alpha: float, step: float) -> None:
        if self._stale(generation):
            return
        if alpha <= 0.0 or not self._set_alpha(alpha):
            self._teardown()
            # A toast queued while this one was leaving now gets its turn.
            self._present_pending()
            return
        self._later(
            _FADE_STEP_MS,
            lambda: self._fade_out(generation, alpha - step, step),
        )

"""Global hotkeys via RegisterHotKey.

RegisterHotKey needs a thread with a message pump, so the listener owns a
dedicated thread. All hotkeys share that one thread and pump — registering a
second key costs a table entry, not another thread. This approach needs no
administrator rights, unlike a low-level keyboard hook.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes  # not pulled in by `import ctypes` alone
import threading
from collections.abc import Callable, Mapping

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_VK_F1 = 0x70

_MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Turn 'ctrl+alt+l' into (modifier flags, virtual key code)."""
    tokens = [t.strip().lower() for t in spec.split("+") if t.strip()]
    if not tokens:
        raise ValueError(f"Empty hotkey: {spec!r}")

    modifiers = 0
    key: str | None = None
    for token in tokens:
        if token in _MODIFIERS:
            modifiers |= _MODIFIERS[token]
        elif key is None:
            key = token
        else:
            raise ValueError(
                f"Hotkey {spec!r} has more than one non-modifier key: "
                f"{key!r} and {token!r}"
            )

    if key is None:
        raise ValueError(f"Hotkey {spec!r} has no key, only modifiers")

    if len(key) == 1 and (key.isalpha() or key.isdigit()):
        return modifiers, ord(key.upper())
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return modifiers, _VK_F1 + int(key[1:]) - 1

    raise ValueError(
        f"Unrecognised key {key!r} in hotkey {spec!r}. Use a letter, a digit, "
        f"or F1-F24."
    )


class HotkeyListener:
    """Runs callbacks when any of its hotkeys is pressed, from its own thread.

    Takes a mapping of hotkey spec to callback, e.g. ``{"ctrl+alt+l": toggle}``.
    Extra bindings cost a table entry rather than another thread, since they
    all share this listener's message pump.
    """

    def __init__(self, bindings: Mapping[str, Callable[[], None]]) -> None:
        if not bindings:
            raise ValueError("HotkeyListener needs at least one binding")

        # Parse everything up front so a bad spec fails before any thread runs.
        self._bindings: dict[int, tuple[str, int, int, Callable[[], None]]] = {}
        for hotkey_id, (spec, callback) in enumerate(bindings.items(), start=1):
            modifiers, vk = parse_hotkey(spec)
            self._bindings[hotkey_id] = (spec, modifiers, vk, callback)

        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._error: Exception | None = None

    def start(self) -> None:
        """Start listening. Raises RuntimeError if a hotkey is already taken."""
        self._thread = threading.Thread(
            target=self._run, name="looptify-hotkey", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)
        if self._error is not None:
            raise self._error

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        registered: list[int] = []
        for hotkey_id, (spec, modifiers, vk, _) in self._bindings.items():
            if user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
                registered.append(hotkey_id)
                continue
            # Roll back, so a partial failure never leaves keys held.
            for done in registered:
                user32.UnregisterHotKey(None, done)
            self._error = RuntimeError(
                f"Could not register hotkey {spec!r} — another application "
                f"already owns it. Change it in config.toml."
            )
            self._ready.set()
            return

        self._ready.set()
        try:
            message = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) != 0:
                if message.message == _WM_HOTKEY:
                    binding = self._bindings.get(message.wParam)
                    if binding is not None:
                        binding[3]()
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)

    def stop(self) -> None:
        """Stop listening and release every hotkey."""
        if self._thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)

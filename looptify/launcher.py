"""Starts Spotify with the Chromium flags that expose its page to UI Automation.

A normal launch hides everything below the window frame. These three flags,
found by trial on 2026-10-08, expose the tracklist and keep it rendering
while Spotify is covered by other windows.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import psutil
import win32con
import win32gui
import win32process

REQUIRED_FLAGS = (
    "--force-renderer-accessibility=complete",
    "--enable-features=UiaProvider",
    "--disable-features=CalculateNativeWinOcclusion",
)

_PROCESS_NAME = "spotify.exe"


def is_main_process(cmdline: Sequence[str]) -> bool:
    """Chromium's helper processes carry --type=; the browser process doesn't."""
    return not any(arg.startswith("--type=") for arg in cmdline)


def has_required_flags(cmdline: Sequence[str]) -> bool:
    return all(flag in cmdline for flag in REQUIRED_FLAGS)


def _spotify_processes() -> list[psutil.Process]:
    return [
        proc
        for proc in psutil.process_iter(["name", "cmdline"])
        if (proc.info["name"] or "").lower() == _PROCESS_NAME
    ]


def spotify_pids() -> set[int]:
    """Every running Spotify.exe, main process and helpers alike."""
    return {proc.pid for proc in _spotify_processes()}


def needs_relaunch() -> bool:
    """True unless Spotify is running with every flag in REQUIRED_FLAGS."""
    # An unreadable command line comes back empty, which counts as unflagged.
    mains = [
        proc.info["cmdline"] or []
        for proc in _spotify_processes()
        if is_main_process(proc.info["cmdline"] or [])
    ]
    return not mains or not all(has_required_flags(cmd) for cmd in mains)


def _spotify_executable() -> Path | None:
    """The Store app alias first, then the standalone install."""
    for env, relative in (
        ("LOCALAPPDATA", r"Microsoft\WindowsApps\Spotify.exe"),
        ("APPDATA", r"Spotify\Spotify.exe"),
    ):
        root = os.environ.get(env)
        if root and (Path(root) / relative).exists():
            return Path(root) / relative
    return None


def _close_spotify(timeout: float) -> None:
    """Ask Spotify's windows to close, then force whatever is left."""
    procs = _spotify_processes()
    if not procs:
        return
    pids = {proc.pid for proc in procs}

    def close(hwnd: int, _: object) -> None:
        if (
            win32gui.IsWindowVisible(hwnd)
            and win32process.GetWindowThreadProcessId(hwnd)[1] in pids
        ):
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

    win32gui.EnumWindows(close, None)
    # Closing may only hide Spotify to the tray, so force it after the wait.
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=timeout)


@dataclass(frozen=True)
class LaunchResult:
    ok: bool
    message: str


def relaunch(close_timeout: float = 3.0, start_timeout: float = 20.0) -> LaunchResult:
    """Close Spotify if it's running, and start it with REQUIRED_FLAGS. Blocks."""
    exe = _spotify_executable()
    if exe is None:
        return LaunchResult(
            False, "couldn't find Spotify.exe; playlist modes will loop instead"
        )

    _close_spotify(close_timeout)
    # Detached, so closing Looptify's console doesn't take Spotify with it.
    subprocess.Popen(
        [str(exe), *REQUIRED_FLAGS],
        creationflags=subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )

    deadline = time.monotonic() + start_timeout
    while time.monotonic() < deadline:
        if not needs_relaunch():
            return LaunchResult(
                True, "Spotify restarted for playlist modes; press play to continue"
            )
        time.sleep(0.5)
    return LaunchResult(
        False,
        "Spotify didn't start with the playlist-mode flags; "
        "playlist modes will loop instead",
    )

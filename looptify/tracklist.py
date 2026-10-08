"""Drives Spotify's tracklist through UI Automation, from its own thread.

Spotify exposes its page only when started with `launcher.REQUIRED_FLAGS`.
Every UI Automation object stays on the worker thread that created it. The
poll loop talks to the worker through `send()`, `press()` and `status()`,
none of which wait on it.

Scrolling uses only ScrollIntoView: SetScrollPercent and Scroll both make
Spotify take the foreground (measured 2026-10-08, see docs/design).
"""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, replace

import comtypes
import comtypes.client

from looptify.focus import FocusGuard
from looptify.launcher import spotify_window
from looptify.planner import Action, Cancel, Locate, Prepare
from looptify.playlist import PageInfo, RowText, find_row, step_toward

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402  (generated above)

# Rows take ~0.2s to render after a scroll, so pause about that long per step.
TICK_SECONDS = 0.25
PAGE_REFRESH_SECONDS = 1.0
# Rows vanish when the list re-renders, so re-check a ready row this often.
READY_RECHECK_SECONDS = 2.0

# An element that vanished mid-read raises COMError, or ValueError from
# comtypes on a NULL pointer. Both mean "read again", not failure.
_STALE = (comtypes.COMError, ValueError)


@dataclass(frozen=True)
class TracklistStatus:
    """What the worker last saw. Replaced whole, so it's safe to read anywhere."""

    exposed: bool = False  # Spotify's page is visible to UI Automation
    page: PageInfo | None = None  # None: no tracklist on the open page
    ready_row: int | None = None  # the prepared row, rendered and pressable
    located: tuple[int, int | None] | None = None  # (Locate.seq, row or None)
    crashed: str | None = None


@dataclass(frozen=True)
class _Row:
    button: object  # play button: InvokePattern, ScrollItemPattern
    text: RowText


def _pattern(element, pattern_id: int, interface):
    unknown = element.GetCurrentPattern(pattern_id)
    return unknown.QueryInterface(interface) if unknown else None


def _invoke(button) -> None:
    pattern = _pattern(button, UIA.UIA_InvokePatternId, UIA.IUIAutomationInvokePattern)
    if pattern is None:
        raise ValueError("play button has no InvokePattern")
    pattern.Invoke()


def _scroll_into_view(walker, element) -> None:
    """ScrollIntoView on the element, or the nearest ancestor that supports it."""
    for _ in range(4):
        if not element:
            return
        pattern = _pattern(
            element, UIA.UIA_ScrollItemPatternId, UIA.IUIAutomationScrollItemPattern
        )
        if pattern is not None:
            pattern.ScrollIntoView()
            return
        element = walker.GetParentElement(element)


def _link_names(walker, cell) -> tuple[str, ...]:
    names = []
    child = walker.GetFirstChildElement(cell)
    while child:
        if child.CurrentControlType == UIA.UIA_HyperlinkControlTypeId:
            names.append(child.CurrentName or "")
        child = walker.GetNextSiblingElement(child)
    return tuple(names)


def _read_row(walker, element) -> tuple[int, _Row] | None:
    """A row's number, play button and text, or None for the header and non-songs."""
    number_cell = walker.GetFirstChildElement(element)
    if not number_cell:
        return None
    item = _pattern(
        number_cell, UIA.UIA_GridItemPatternId, UIA.IUIAutomationGridItemPattern
    )
    if item is None or item.CurrentRow < 1:
        return None
    button = walker.GetFirstChildElement(number_cell)
    if not button or button.CurrentControlType != UIA.UIA_ButtonControlTypeId:
        return None
    title_cell = walker.GetNextSiblingElement(number_cell)
    text = RowText("", ())
    if title_cell:
        text = RowText(title_cell.CurrentName or "", _link_names(walker, title_cell))
    return item.CurrentRow, _Row(button, text)


class TracklistWorker:
    """Owns Spotify's tracklist: finds rows, scrolls to them, presses play."""

    def __init__(self) -> None:
        self._commands: queue.SimpleQueue = queue.SimpleQueue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._status = TracklistStatus()
        self._thread: threading.Thread | None = None
        self._guard = FocusGuard()

        # Everything below belongs to the worker thread.
        self._uia = None
        self._grid = None
        self._page: PageInfo | None = None
        self._page_checked = float("-inf")
        self._seen: dict[int, RowText] = {}
        self._locate: Locate | None = None
        self._scan_down = False
        self._target: int | None = None
        self._ready: _Row | None = None
        self._ready_checked = float("-inf")

    @property
    def focus_reclaim_ms(self) -> float | None:
        """How long Spotify held focus after the last press, if it took it."""
        return self._guard.last_reclaim_ms

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="looptify-tracklist", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def status(self) -> TracklistStatus:
        with self._lock:
            return self._status

    def send(self, action: Action) -> None:
        self._commands.put(action)
        self._wake.set()

    def press(self) -> Future[bool]:
        """Press the prepared row's play button. Resolves True once pressed."""
        future: Future[bool] = Future()
        self._commands.put(future)
        self._wake.set()
        return future

    # ---- worker thread only ----

    def _publish(self, **changes) -> None:
        with self._lock:
            self._status = replace(self._status, **changes)

    def _run(self) -> None:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        try:
            self._uia = comtypes.client.CreateObject(
                UIA.CUIAutomation, interface=UIA.IUIAutomation
            )
            while not self._stop.is_set():
                self._drain()
                try:
                    self._tick()
                except _STALE:
                    self._forget_page()
                self._wake.wait(TICK_SECONDS)
                self._wake.clear()
        except Exception as exc:  # reported; the poll loop falls back to Loop
            self._publish(crashed=repr(exc))
        finally:
            self._grid = self._ready = self._uia = None
            comtypes.CoUninitialize()

    def _drain(self) -> None:
        while True:
            try:
                item = self._commands.get_nowait()
            except queue.Empty:
                return
            if isinstance(item, Future):
                item.set_result(self._press_safely())
            elif isinstance(item, Locate):
                self._locate = item
                self._scan_down = False
                self._set_target(None)
            elif isinstance(item, Prepare):
                self._set_target(item.row)
            elif isinstance(item, Cancel):
                self._locate = None
                self._set_target(None)

    def _set_target(self, row: int | None) -> None:
        self._target = row
        self._ready = None
        self._publish(ready_row=None)

    def _forget_page(self) -> None:
        self._grid = None
        self._ready = None
        self._page_checked = float("-inf")
        self._publish(ready_row=None)

    def _tick(self) -> None:
        now = time.monotonic()
        if now - self._page_checked >= PAGE_REFRESH_SECONDS:
            self._page_checked = now
            self._refresh_page()
        if self._grid is None or self._page is None:
            return
        if self._locate is not None:
            self._locate_step()
        elif self._target is not None:
            self._prepare_step(now)

    def _refresh_page(self) -> None:
        uia = self._uia
        hwnd = spotify_window()
        if not hwnd:
            self._show_page(None, exposed=False)
            return
        root = uia.ElementFromHandle(hwnd)
        document = root.FindFirst(
            UIA.TreeScope_Descendants,
            uia.CreatePropertyCondition(
                UIA.UIA_ControlTypePropertyId, UIA.UIA_DocumentControlTypeId
            ),
        )
        if not document:
            self._show_page(None, exposed=False)
            return
        # The sidebar's "Your Library" is a grid too, but in a navigation landmark.
        main = document.FindFirst(
            UIA.TreeScope_Subtree,
            uia.CreatePropertyCondition(
                UIA.UIA_LandmarkTypePropertyId, UIA.UIA_MainLandmarkTypeId
            ),
        )
        grid = None
        if main:
            grid = main.FindFirst(
                UIA.TreeScope_Descendants,
                uia.CreatePropertyCondition(
                    UIA.UIA_ControlTypePropertyId, UIA.UIA_DataGridControlTypeId
                ),
            )
        layout = (
            _pattern(grid, UIA.UIA_GridPatternId, UIA.IUIAutomationGridPattern)
            if grid
            else None
        )
        if layout is None:
            self._show_page(None, exposed=True)
            return
        self._grid = grid
        # Row 0 is the header.
        page = PageInfo(grid.CurrentName or "", max(0, layout.CurrentRowCount - 1))
        self._show_page(page, exposed=True)

    def _show_page(self, page: PageInfo | None, *, exposed: bool) -> None:
        if page is None:
            self._grid = None
        if page is None or self._page is None or page.name != self._page.name:
            self._seen.clear()
        if page != self._page:
            self._ready = None
        self._page = page
        ready_row = self._target if self._ready is not None else None
        self._publish(exposed=exposed, page=page, ready_row=ready_row)

    def _rows(self) -> dict[int, _Row]:
        """The rendered song rows by number, skipping any that vanish mid-read."""
        uia = self._uia
        found = self._grid.FindAll(
            UIA.TreeScope_Children,
            uia.CreatePropertyCondition(
                UIA.UIA_ControlTypePropertyId, UIA.UIA_DataItemControlTypeId
            ),
        )
        walker = uia.ControlViewWalker
        rows: dict[int, _Row] = {}
        for i in range(found.Length):
            try:
                read = _read_row(walker, found.GetElement(i))
            except _STALE:
                continue
            if read is not None:
                number, row = read
                rows[number] = row
                self._seen[number] = row.text
        return rows

    def _locate_step(self) -> None:
        """One step of finding the playing row: check what's been seen, else scan.

        The scan scrolls up to row 1, then down to the last row, one screen
        per tick, so commands still get through between steps.
        """
        locate = self._locate
        assert locate is not None and self._page is not None
        rows = self._rows()
        hit = find_row(self._seen, locate.title, locate.artist)
        if hit is not None:
            self._finish_locate(hit)
            return
        if not rows:
            return  # nothing rendered, e.g. minimized; try again next tick
        walker = self._uia.ControlViewWalker
        if not self._scan_down:
            if min(rows) <= 1:
                self._scan_down = True
            else:
                _scroll_into_view(walker, rows[min(rows)].button)
                return
        if max(rows) >= self._page.count:
            self._finish_locate(None)
            return
        _scroll_into_view(walker, rows[max(rows)].button)

    def _finish_locate(self, row: int | None) -> None:
        assert self._locate is not None
        self._publish(located=(self._locate.seq, row))
        self._locate = None

    def _prepare_step(self, now: float) -> None:
        if self._ready is not None and now - self._ready_checked < READY_RECHECK_SECONDS:
            return
        self._ready_checked = now
        rows = self._rows()
        row = rows.get(self._target)
        if row is not None:
            self._ready = row
            self._publish(ready_row=self._target)
            return
        self._ready = None
        self._publish(ready_row=None)
        edge = step_toward(rows.keys(), self._target)
        if edge is not None:
            _scroll_into_view(self._uia.ControlViewWalker, rows[edge].button)

    def _press_safely(self) -> bool:
        try:
            return self._press()
        except _STALE:
            return False

    def _press(self) -> bool:
        """Invoke the target's play button: the cached one, else one re-find."""
        target, row = self._target, self._ready
        if target is None or self._grid is None:
            return False
        guarded = False
        for _ in range(2):
            try:
                if row is None:
                    row = self._rows().get(target)
                    if row is None:
                        return False
                # Once only: a second guard would hide Spotify twice.
                if not guarded:
                    self._guard.protect()
                    guarded = True
                _invoke(row.button)
            except _STALE:
                row = None
                continue
            self._set_target(None)
            return True
        return False

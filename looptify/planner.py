"""Pure per-song planning for the playlist modes. No Windows APIs here.

Each poll, `step` compares what's playing and what Spotify shows against the
plan, decides which row comes next, and returns commands for the tracklist
worker: find the playing row, scroll the next one into range, or stop.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Literal

from looptify.playlist import Mode, PageInfo, ShuffleBag, choose_next, update_bag

# If a pressed row hasn't started by this long before the end, loop instead.
RESCUE_SECONDS = 0.4

TrackKey = tuple[str, str]  # (title, artist) as SMTC reports them


@dataclass(frozen=True)
class Locate:
    """Ask the worker which row holds `title` by `artist`."""

    seq: int
    title: str
    artist: str


@dataclass(frozen=True)
class Prepare:
    """Ask the worker to scroll `row` into range and keep it ready to press."""

    row: int


@dataclass(frozen=True)
class Cancel:
    """Ask the worker to stop locating and preparing."""


Action = Locate | Prepare | Cancel


@dataclass(frozen=True)
class Plan:
    """What the playlist modes know about the current song and the next one."""

    mode: Mode = Mode.LOOP
    track: TrackKey | None = None
    page: PageInfo | None = None
    current_row: int | None = None
    locate_seq: int = 0
    locating: bool = False
    target: int | None = None  # row to press at the mark; None means loop
    bag: ShuffleBag = ShuffleBag()
    pressed_row: int | None = None  # pressed, not yet seen playing


def _idle(plan: Plan) -> bool:
    return plan.mode is Mode.LOOP or plan.page is None or plan.track is None


def _choose(plan: Plan, rng: random.Random) -> tuple[Plan, list[Action]]:
    assert plan.page is not None
    bag = update_bag(plan.bag, plan.page, plan.current_row)
    target = choose_next(plan.mode, plan.page.count, plan.current_row, bag, rng)
    if target is None or target == plan.current_row:
        return replace(plan, bag=bag, target=None), [Cancel()]
    return replace(plan, bag=bag, target=target), [Prepare(target)]


def step(
    plan: Plan,
    *,
    mode: Mode,
    track: TrackKey | None,
    page: PageInfo | None,
    located: tuple[int, int | None] | None,
    rng: random.Random,
) -> tuple[Plan, list[Action]]:
    """Advance the plan by one poll.

    `track` is None during an ad. `located` is the worker's latest answer to a
    Locate, as (seq, row or None). Returns the new plan and the commands to
    send to the worker.
    """
    if mode is Mode.LOOP or page is None or track is None:
        busy = plan.target is not None or plan.locating
        idle = replace(
            plan,
            mode=mode,
            track=track,
            page=page,
            current_row=None,
            locating=False,
            target=None,
            pressed_row=None,
        )
        return idle, [Cancel()] if busy else []

    was_idle = _idle(plan)
    new = replace(plan, mode=mode, track=track, page=page)

    if (
        not was_idle
        and track != plan.track
        and plan.pressed_row is not None
        and page == plan.page
    ):
        # The row Looptify pressed has started; no need to look for it.
        started = replace(new, current_row=plan.pressed_row, pressed_row=None)
        return _choose(started, rng)

    if was_idle or track != plan.track or page != plan.page:
        seq = plan.locate_seq + 1
        searching = replace(
            new,
            current_row=None,
            locate_seq=seq,
            locating=True,
            target=None,
            pressed_row=None,
        )
        return searching, [Locate(seq, track[0], track[1])]

    if plan.locating:
        if located is not None and located[0] == plan.locate_seq:
            return _choose(replace(new, current_row=located[1], locating=False), rng)
        return new, []

    if mode != plan.mode:
        return _choose(new, rng)
    return new, []


def rechoose(plan: Plan, rng: random.Random) -> tuple[Plan, list[Action]]:
    """Pick again after the current song looped, so the random modes move on."""
    if _idle(plan) or plan.locating:
        return plan, []
    return _choose(plan, rng)


def mark_pressed(plan: Plan) -> Plan:
    """Remember that the target row was just pressed."""
    return replace(plan, pressed_row=plan.target)


def clear_press(plan: Plan) -> tuple[Plan, list[Action]]:
    """Forget a press that didn't take, and line the same row up again."""
    cleared = replace(plan, pressed_row=None)
    return cleared, [Prepare(plan.target)] if plan.target is not None else []


def should_rescue(plan: Plan, track: TrackKey | None, remaining: float) -> bool:
    """True when a pressed row hasn't started and the song is about to end."""
    return (
        plan.pressed_row is not None
        and track == plan.track
        and remaining <= RESCUE_SECONDS
    )


def fallback_reason(plan: Plan, ready_row: int | None) -> str | None:
    """Why a playlist mode has to loop this song instead of moving on, if it does."""
    if plan.mode is Mode.LOOP:
        return None
    if plan.page is None:
        return "no tracklist on the open Spotify page"
    if plan.locating:
        return "still finding the playing song in the tracklist (is Spotify minimized?)"
    if plan.target is None:
        return None  # the mode chose to repeat this song
    if ready_row != plan.target:
        return f"row {plan.target} isn't loaded yet (is Spotify minimized?)"
    return None


SkipVerdict = Literal["press", "wait", "drop"]


def skip_refusal(armed: bool, mode: Mode, plan: Plan) -> str | None:
    """Why a skip can't even be queued right now, if it can't.

    `mode` is the chosen mode; `plan.mode` is the one in effect, which is Loop
    when the tracklist worker has stopped.
    """
    if not armed:
        return "Looptify is deactivated."
    if mode is Mode.LOOP:
        return "Skipping works in In Order, Shuffle Loop and Pure Random."
    if plan.mode is Mode.LOOP:
        return "Playlist modes are unavailable this session."
    if plan.track is None:
        return "Nothing is playing that can be skipped."
    if plan.page is None:
        return "Open the playlist you're playing from in Spotify."
    if plan.page.count <= 1:
        return "The open playlist has only one song."
    return None


def prepare_skip(plan: Plan, rng: random.Random) -> tuple[Plan, list[Action]]:
    """Make sure a skip has another row to go to.

    Only Pure Random can choose to repeat the current song while the list
    holds others, and a skip means moving on, so draw again from the rest.
    """
    if _idle(plan) or plan.locating or plan.target is not None:
        return plan, []
    assert plan.page is not None
    others = [row for row in range(1, plan.page.count + 1) if row != plan.current_row]
    if not others:
        return plan, []
    target = rng.choice(others)
    return replace(plan, target=target), [Prepare(target)]


def skip_verdict(plan: Plan, skipping: TrackKey, ready_row: int | None) -> SkipVerdict:
    """What to do with a pending skip of `skipping` this poll."""
    if _idle(plan) or plan.track != skipping or plan.pressed_row is not None:
        return "drop"  # the song moved on, or the mode or page went away
    if plan.target is None:
        return "wait" if plan.locating else "drop"
    return "press" if ready_row == plan.target else "wait"


def skip_wait_reason(plan: Plan) -> str:
    """What a pending skip is waiting for, for the notification."""
    if plan.target is None:
        return "Will skip once the playing song is found in the list."
    return f"Will skip once row {plan.target} is loaded."

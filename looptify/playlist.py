"""Pure playlist-mode rules: which row plays next. No Windows APIs here."""

from __future__ import annotations

import random
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    """What happens just before a song ends."""

    LOOP = "loop"
    IN_ORDER = "in_order"
    SHUFFLE_LOOP = "shuffle_loop"
    PURE_RANDOM = "pure_random"


_CYCLE = (Mode.LOOP, Mode.IN_ORDER, Mode.SHUFFLE_LOOP, Mode.PURE_RANDOM)

MODE_LABELS = {
    Mode.LOOP: "Loop",
    Mode.IN_ORDER: "In Order",
    Mode.SHUFFLE_LOOP: "Shuffle Loop",
    Mode.PURE_RANDOM: "Pure Random",
}

MODE_BLURBS = {
    Mode.LOOP: "This song will loop before it ends.",
    Mode.IN_ORDER: "The next row plays before each song ends.",
    Mode.SHUFFLE_LOOP: "Random rows, no repeats until all have played.",
    Mode.PURE_RANDOM: "Any row, picked at random every time.",
}


def next_mode(mode: Mode) -> Mode:
    """The mode after this one in the hotkey cycle."""
    return _CYCLE[(_CYCLE.index(mode) + 1) % len(_CYCLE)]


@dataclass(frozen=True)
class PageInfo:
    """The tracklist Spotify is showing: its name and how many songs it holds."""

    name: str
    count: int


@dataclass(frozen=True)
class ShuffleBag:
    """Rows already played this Shuffle Loop cycle, for one playlist."""

    name: str = ""
    played: frozenset[int] = frozenset()


def update_bag(bag: ShuffleBag, page: PageInfo, current: int | None) -> ShuffleBag:
    """Record `current` as played, starting over for a new playlist or a full cycle.

    A full cycle restarts with the current row already in it, so the song that
    just played can't be drawn straight back.
    """
    played = bag.played if bag.name == page.name else frozenset()
    played = frozenset(row for row in played if 1 <= row <= page.count)
    if current is not None:
        played |= {current}
    if page.count > 0 and len(played) >= page.count:
        played = frozenset() if current is None else frozenset({current})
    return ShuffleBag(name=page.name, played=played)


def choose_next(
    mode: Mode,
    count: int,
    current: int | None,
    bag: ShuffleBag,
    rng: random.Random,
) -> int | None:
    """Pick the row to play next, or None to loop the current song.

    A result equal to `current` also means loop; callers treat the two alike.
    """
    if mode is Mode.LOOP or count <= 0:
        return None
    if mode is Mode.IN_ORDER:
        return 1 if current is None else current % count + 1
    if mode is Mode.PURE_RANDOM:
        return rng.randint(1, count)
    candidates = [
        row
        for row in range(1, count + 1)
        if row not in bag.played and row != current
    ]
    if not candidates:
        return current
    return rng.choice(candidates)


@dataclass(frozen=True)
class RowText:
    """What a tracklist row says: its title cell's text and its artist links."""

    title_cell: str
    artists: tuple[str, ...]


def find_row(rows: Mapping[int, RowText], title: str, artist: str) -> int | None:
    """Find the row for `title` by `artist`, as SMTC reports them. Lowest row wins.

    The title cell reads "{title} {artists}", sometimes with an explicit badge
    between, so match its start. SMTC reports only the first artist. A title
    that prefixes another title by the same artist can match the longer one;
    only songs the user started go through here, since Looptify knows the
    row of every song it pressed.
    """
    title = title.strip()
    if not title:
        return None
    for row in sorted(rows):
        text = rows[row]
        cell = text.title_cell.strip()
        if cell != title and not cell.startswith(title + " "):
            continue
        if not artist or not text.artists:
            return row
        if artist in text.artists or text.artists[0] in artist:
            return row
    return None


def step_toward(rendered: Collection[int], target: int) -> int | None:
    """Which rendered row to scroll into view to move toward `target`.

    None when `target` is already rendered, or nothing is.
    """
    if not rendered or target in rendered:
        return None
    if target > max(rendered):
        return max(rendered)
    if target < min(rendered):
        return min(rendered)
    return None

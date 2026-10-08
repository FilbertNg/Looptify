# Looptify — Playlist Modes Design

**Written:** 2026-10-08

> **This is a point-in-time design record, not maintained documentation.** It describes
> the playlist modes as designed on 2026-10-08, before implementation, and is kept for
> the reasoning — why Next is not used, why the tracklist is driven through UI
> Automation, and why Spotify has to be relaunched with Chromium flags. For how Looptify
> currently behaves, read the README.

## Problem

Looptify avoids the post-track ad by seeking the current song back to 0:00 just before
it ends, so the only thing it can do is play the same song forever.

Spotify Free also plays no ad when a *different* song is started by hand before the
current one finishes: hover a row's number in a tracklist and click the play button that
appears. The goal is to automate that, so Looptify can move through a playlist instead
of repeating one song.

## Goal

Three new modes alongside the existing loop, switched with a global hotkey:

| Mode | Next song |
|---|---|
| **Loop** | The same song (today's behaviour) |
| **In Order** | The next row of the open tracklist; after the last row, row 1 |
| **Shuffle Loop** | A random row not yet played this cycle; the cycle resets once every row has played |
| **Pure Random** | A uniformly random row, which may be the current one |

The switch must happen in the background: no visible window, no mouse movement, and no
lasting loss of keyboard focus.

## Non-goals

- Reordering, queueing, or editing playlists
- Remembering shuffle progress across Looptify restarts
- Driving Spotify while it is minimized (see Constraints)
- Injecting into, patching, or remote-debugging the Spotify client

## Feasibility findings

All measured on 2026-10-08 against the Microsoft Store build of Spotify
(`SpotifyAB.SpotifyMusic_1.301.234.0`), Windows 11 build 26200, on a 966-song
Liked Songs list.

### SMTC "Next" is not an option

SMTC reports `is_next_enabled`, `is_shuffle_enabled` and `is_repeat_enabled` as true,
so In Order and Shuffle Loop could in principle be built from Spotify's own Next button
plus its shuffle and repeat settings. The user confirmed that **Next does not avoid the
ad** — Spotify treats it as a skip. Only starting a song directly from its row does.

SMTC also carries no playlist information: `album_track_count` is 0, and there is no
track list, index, or count.

### Spotify's page is hidden from UI Automation by default

On a normal launch, UI Automation sees 15 elements, all window chrome. The page content
(rows, play buttons, song count) is absent. Neither setting the system screen-reader
flag (`SPI_SETSCREENREADER`) nor hit-testing with `ElementFromPoint` changes that, and
launching with plain `--force-renderer-accessibility` is not enough either.

Launching with **`--force-renderer-accessibility=complete --enable-features=UiaProvider`**
exposes the page: about 600 elements, a `DataGrid` named after the playlist, and a play
button per row.

| What | How it reads |
|---|---|
| Song count | `GridPattern.RowCount` = 967 (966 songs plus the header row); the page text also reads "966 songs" |
| Row number | `GridItemPattern.Row` on a row element; row 1 = the first song, matching Spotify's `#` column |
| Play button | The row's first button, named "Play {title} by {artists}" in an English UI |

The tracklist is virtualised: only about 30–55 rows around the scroll position exist at
any moment.

### Scrolling and pressing in the background

| Action | Moves rows? | Takes focus? |
|---|---|---|
| `ScrollPattern.SetScrollPercent` | Yes | **Yes** — Spotify comes to the front |
| `ScrollPattern.Scroll(LargeIncrement)` | Yes | **Yes** |
| `ScrollItemPattern.ScrollIntoView` on the edge row | Yes, one screen per step | No |
| `InvokePattern.Invoke` on a play button | Plays the row | **Yes**, briefly |
| Posted `WM_LBUTTONDBLCLK` to Spotify's window | Did not play | Yes |

Focus-taking happens inside Spotify, so it cannot be prevented from outside:
`LockSetForegroundWindow` does not stop it, and it happens even when the caller is
launched from Task Scheduler with no relation to the foreground app.

Chromium stops rendering a window it considers occluded, so with Spotify covered by
another window, `ScrollIntoView` stalls. Launching with
**`--disable-features=CalculateNativeWinOcclusion`** keeps it rendering. With all three
flags, Spotify covered by VS Code:

| Target | Scroll time | Focus during scroll | Focus taken by Invoke | Spotify visibly on top |
|---|---|---|---|---|
| Row 120 | 2.3 s, 4 steps | Never left VS Code | 3 ms, then reclaimed | No |
| Row 900 | 20.6 s, 45 steps | Never left VS Code | 5 ms, then reclaimed | No |

Focus was reclaimed by a zero-distance `SendInput` mouse move (which makes Looptify the
last-input process) followed by `SetForegroundWindow` on the user's window.

### Minimized Spotify cannot scroll

With Spotify minimized, `ScrollIntoView` does not load new rows, even with the
occlusion flag: six steps left the rendered range at rows 874–928. `Invoke` on a row
that is *already* rendered does work while minimized, and the song changes.

### Ads

The user observed no ads across every transition made during testing, and confirmed
that a background press on a row's play button avoids the ad like a manual click.

## Constraints

- Spotify must be launched with the three flags above. A normal launch exposes nothing.
- Spotify must show the tracklist being played from. On a page with no tracklist,
  Looptify loops instead.
- Spotify should not be minimized. Covered or behind other windows is fine. In In Order
  mode the next row is usually already rendered (the list was scrolled there for the
  current song), so In Order mostly works minimized; the random modes usually don't.
- Every song change takes keyboard focus for about 5 ms. A keypress landing in that
  window goes to Spotify, and a game in exclusive fullscreen may minimize.
- Scrolling far takes up to about 0.45 s per screen of rows, so the target has to be
  chosen and scrolled to well before the transition.

## Design

### Structure

The existing split is kept: decisions in pure modules, Windows calls in thin adapters.

```
looptify/
├── logic.py        pure, existing — the fire decision now means "transition", not just "loop"
├── playlist.py     NEW, pure — Mode, next-row choice per mode, shuffle bag
├── tracklist.py    NEW adapter — UI Automation worker thread: count, current row,
│                   scroll-to-row, press, focus watchdog
├── launcher.py     NEW adapter — detect Spotify's flags, (re)launch with them
├── smtc.py         unchanged — seek stays the fallback
├── hotkey.py       unchanged — a second hotkey is registered for mode cycling
└── cli.py          wiring
```

### Flow

1. A new track starts (SMTC reports a different title, artist or duration), whether the
   user or Looptify started it. Looptify takes the open tracklist's identity and count
   from the worker, finds the current row, and `playlist.py` picks the target row for
   the active mode. The worker starts scrolling to it.
2. At `lead_seconds` before the end, if the worker reports the target `ready`, Looptify
   presses it, with the focus watchdog running. Otherwise it seeks to 0:00 as today.
3. If SMTC shows no new track by 0.4 s before the end, Looptify seeks to 0:00.

The target is also re-chosen when the mode changes and when the open page changes.

In Loop mode the worker is never used, and Looptify behaves exactly as it does today.

### Mode rules (`playlist.py`)

Rows are numbered 1..N, with N = `RowCount − 1`. The module takes a `random.Random`, so
tests can seed it.

- **Loop** — target is the current song; transition is a seek.
- **In Order** — `current % N + 1`. If the current row is unknown, row 1.
- **Shuffle Loop** — uniform choice from the rows not in the bag and not the current
  row. The current row joins the bag when it starts playing. When every row is in the
  bag, the bag resets to just the current row, so the song that just played can't come
  straight back.
- **Pure Random** — uniform choice from 1..N. Drawing the current row means a seek, so
  the same song plays again.

In every mode, a target equal to the current row means a seek rather than a press. With
N = 1, every mode therefore loops the single song.

The shuffle bag is keyed by playlist identity (grid name plus N). A different identity
resets it. If N changes for the same name, rows above the new N are dropped from the
bag. The bag lives in memory only.

**Finding the current row.** When Looptify presses row *k* and SMTC then reports the
title it expected, the current row is *k*. When the user starts a song, the worker
matches SMTC's title and first artist against row names: first the rendered rows, then
the rest of the list in the background. If no row matches, the current row is unknown.

**Mode switching.** `mode` in config.toml sets the starting mode (default `"loop"`).
`mode_hotkey` (default `"ctrl+alt+shift+l"`; `ctrl+alt+m` was the first choice but another app owned it on the development machine) cycles Loop → In Order → Shuffle Loop →
Pure Random → Loop, and a toast names the new mode ("Mode: Shuffle Loop").

### Tracklist worker (`tracklist.py`)

The worker is a dedicated thread that initialises its own COM apartment and owns every
UI Automation object, since those must stay on the thread that created them. The poll
loop never blocks on it.

- **Commands:** `prepare(row)`, `press()`, `cancel()`.
- **Status:** `idle`, `scrolling`, `ready(row)`, or `failed(reason)`, read without
  blocking.
- **Page info:** about once a second the worker publishes `(name, N)` for the open
  tracklist, or "no tracklist".
- **Scrolling:** only `ScrollIntoView` on the rendered edge row nearest the target,
  repeated until the target row is rendered. `SetScrollPercent` is never used, because
  it takes focus.
- **Staying ready:** while `ready`, the target button is re-checked every 2 s, because
  rows disappear when the list re-renders. On `press()`, the cached button is tried
  first, then one quick re-find.
- **Locale independence:** rows are identified by `GridItemPattern.Row` and the row's
  first button, not by the "Play … by …" label text.
- **Stale elements:** a `COMError` or a null parent from a vanished element is expected
  during re-renders, and means "skip and retry", not failure.

### Focus watchdog

The watchdog runs only from just before a press until 1.5 s after it.

1. Record the current foreground window.
2. Poll the foreground window every millisecond.
3. If it changes to a window owned by a Spotify process, send a zero-distance
   `SendInput` mouse move, then call `SetForegroundWindow` on the recorded window.
4. Never reclaim when the recorded window was Spotify itself, when the recorded window
   no longer exists, or when focus moved to a non-Spotify window. That last case is the
   user switching apps.

### Launcher (`launcher.py`)

Whenever a playlist mode becomes active, at startup or by cycling into it, the launcher
reads the main Spotify process's command line (the `Spotify.exe` process without a
`--type=` argument).

If the three flags are missing, or Spotify is not running, it:
1. shows the toast "Restarting Spotify for playlist modes",
2. closes Spotify (`CloseMainWindow`, then a force kill after 3 s), and
3. starts it with the flags.

It looks for Spotify at the Store app-execution alias
`%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe` first, then the standalone install
`%APPDATA%\Spotify\Spotify.exe`. After a relaunch nothing is playing; Looptify waits,
as it does today ("waiting for Spotify...").

Loop mode never relaunches Spotify.

Flag detection is a pure function over the command-line string, so it is unit-tested.

### Fallbacks

Every fallback loops the current song by seeking, so an ad still never fires, and prints
one console line naming the reason.

| Situation | Behaviour |
|---|---|
| Flags missing and the relaunch failed | Playlist modes act as Loop; warned once |
| Open page has no tracklist (Home, Search, …) | Loop this song |
| Target not `ready` at the lead mark (minimized, very short song) | Loop this song; the worker keeps trying for the next |
| `press()` raised, or no new track by 0.4 s before the end | Seek immediately |
| Worker thread crashed | Logged; playlist modes disabled for the session |

### Configuration

| Key | Default | Meaning |
|---|---|---|
| `mode` | `"loop"` | Starting mode: `loop`, `in_order`, `shuffle_loop`, `pure_random` |
| `mode_hotkey` | `"ctrl+alt+shift+l"` | Cycles the mode |

An unknown `mode` value is a config error at startup, consistent with how unknown keys
are handled.

## Testing

**Automated (CI, no Spotify):**
- `playlist.py`: In Order wraps from N to 1. A seeded Shuffle Loop visits every row
  exactly once per cycle and never repeats across a cycle boundary. The bag resets on an
  identity change and is trimmed when N shrinks. Pure Random stays in 1..N. An unknown
  current row starts In Order at row 1.
- `logic.py`: when to prepare, when to press, when to fall back, and the 0.4 s deadline
  for seeking after a press with no track change.
- `launcher.py`: flag detection on real captured command lines.
- `config.py`: `mode` and `mode_hotkey` parsing and validation.

**Manual:** `scripts/verify_tracklist.py`, alongside the existing `verify_*.py`
scripts. It reads the count and the current row, scrolls to a given row, and with
`--play` presses it while timing the focus blip.

## Documentation

- **README:** a modes section; the mode hotkey; why Spotify is relaunched; the
  constraints above (minimized, focus blip, staying on the playlist page); and new
  troubleshooting rows.
- **Scope statement:** it currently says Looptify "sends documented Windows media
  commands and nothing else". It will now say that Looptify also uses Windows
  accessibility automation and launches Spotify with Chromium flags, and still never
  injects into, patches or modifies the client.
- **config.toml:** `mode` and `mode_hotkey`, documented.

## Risks

- **Spotify updates** may change the accessibility tree, the flags it honours, or how
  rows render. Identifying rows structurally (grid row index, first button) rather than
  by label limits the damage. The verify script exists to diagnose it quickly.
- **Focus reclaim** depends on Windows letting the last-input process set the
  foreground. If a future Windows build tightens that, Spotify would stay in front after
  each change. It would be visible, but not harmful.
- **Duplicate songs** in one list can make the user-started current row ambiguous. The
  first match is used. Rows Looptify pressed itself are always exact.
- Verified on one machine, one Spotify build, and Liked Songs only. Regular playlists and
  albums are expected to behave the same, since they use the same tracklist component,
  but are untested.

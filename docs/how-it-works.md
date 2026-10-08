# How Looptify Works

This document covers what happens under the hood: how Looptify knows when a song is
about to end, how it restarts or changes songs, how it detects ads, and the limitations
that follow from those choices. For installing and using Looptify, see the
[README](../README.md).

- [Overview](#overview)
- [Knowing when a song ends](#knowing-when-a-song-ends)
- [Restarting a song](#restarting-a-song)
- [Moving to another song](#moving-to-another-song)
- [Ad detection](#ad-detection)
- [Advanced settings](#advanced-settings)
- [Known limitations](#known-limitations)
- [Scope](#scope)
- [Code structure](#code-structure)

## Overview

Spotify Free plays its ad when a track *completes*. Looptify acts about 1.5 seconds
before that point, so a completion never happens:

- **Loop mode** seeks the current track back to 0:00.
- **In Order, Shuffle Loop and Pure Random** start a different song by pressing its play
  button in the open tracklist, which Spotify treats like a click by the user.

Spotify's Next button is deliberately not used: Spotify counts it as a skip, and the ad
still plays.

Looptify reads Spotify's playback state from **SMTC** (System Media Transport Controls),
the Windows API behind the media overlay. It makes no network calls and doesn't use the
Spotify Web API.

## Knowing when a song ends

### SMTC updates only every few seconds

SMTC pushes an update only when something happens, and on Spotify that is roughly every
**4.5 seconds** (the measured maximum staleness was 4.489 s). Firing at "1.5 seconds
remaining" based on the reported position alone could fire three seconds *after* the
track had already ended.

### Extrapolating the position

Every timeline update carries the time it was stamped. While playback is running, the true
position is the reported position plus the time elapsed since:

```python
estimated_position = position + (now − last_updated)
```

Measured against real playback, this predicts the position to within about **20 ms**
across a full 4.5-second gap, which is what makes a 1.5-second lead safe.

## Restarting a song

In Loop mode, Looptify seeks to 0:00 through SMTC. This is exact, and unlike the
previous-track button it doesn't depend on Spotify's "restart if past 3 seconds" rule. If
seeking isn't available, Looptify falls back to the previous-track command. Both target
Spotify's own media session, so neither can affect another media app.

Two guards stop one pass through the end of a song from firing repeatedly: a cooldown
(`cooldown_seconds`), and a requirement that playback is seen back near the start
(`restart_threshold_seconds`).

## Moving to another song

### Reaching the tracklist

Looptify presses play buttons through **Windows UI Automation**, the accessibility API
that screen readers use. Spotify only exposes its page to UI Automation when it's started
with these Chromium flags:

| Flag | Purpose |
|---|---|
| `--force-renderer-accessibility=complete` | Exposes the page content (rows, buttons, song count) |
| `--enable-features=UiaProvider` | Serves that content through UI Automation |
| `--disable-features=CalculateNativeWinOcclusion` | Keeps Spotify rendering while covered by other windows, which scrolling depends on |

When a playlist mode becomes active, Looptify checks Spotify's command line. If the flags
are missing, it closes Spotify and starts it again with them, using the Microsoft Store
app alias (`%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe`) or the standalone install
(`%APPDATA%\Spotify\Spotify.exe`). Loop mode never restarts Spotify.

The tracklist is the data grid inside the page's main landmark. The sidebar's library is
also a grid, but it sits in a navigation landmark. Rows are identified by their grid row
number and the first button in the row, not by their labels, so this works in any
Spotify display language.

### Choosing and preparing the next song

Spotify's tracklist is virtualized: only the 30–55 rows around the scroll position exist
at any moment. Reaching row 900 of 966 means scrolling there first, at roughly half a
second per screen of rows. Looptify therefore works ahead:

1. When a song starts, Looptify finds its row: either it's the row Looptify just pressed,
   or Looptify matches the title and first artist against the rows.
2. The active mode picks the next row:

   | Mode | Next row |
   |---|---|
   | In Order | The current row + 1, wrapping to row 1 after the last |
   | Shuffle Loop | A random row not yet played this cycle. A full cycle restarts with the song that just played excluded |
   | Pure Random | Any row, chosen uniformly. Drawing the current row repeats it |

3. A background worker scrolls that row into range while the song plays, and keeps
   re-checking it, because rows are rebuilt whenever the list re-renders.
4. At the 1.5-second mark, Looptify presses the row's play button.

Scrolling uses `ScrollItemPattern.ScrollIntoView`, one screen per step.
`ScrollPattern.SetScrollPercent` and `ScrollPattern.Scroll` are never used, because both
bring Spotify to the front.

### Staying in the background

Pressing a play button makes Spotify activate itself, and nothing outside Spotify can
prevent that. Disabling its windows and `LockSetForegroundWindow` both proved ineffective,
and Windows doesn't allow one app to DWM-cloak another app's window. Looptify limits the
effect in two ways:

- **Hiding the window.** For the moment of the press, Spotify's window is given an empty
  window region. It stays in place and keeps rendering, but covers no pixels, so being
  raised to the front shows nothing. About 0.8 seconds later the region is removed and
  Spotify is put back at its original place in the window stack. Spotify isn't hidden
  when it's minimized (nothing would show) or when it's the window in use (hiding it would
  itself cause a flicker). If Looptify is killed mid-press, it clears the region on its
  next start, on Ctrl+C, and when its console window is closed.
- **Returning focus.** A watchdog notices the moment Spotify takes the foreground. It
  sends a zero-distance mouse movement, which makes Looptify the last process to receive
  input and therefore allowed to set the foreground, then returns focus to the previous
  window. This takes 4–12 ms in testing. The mouse cursor doesn't move.

### Falling back

If the next song isn't ready at the 1.5-second mark, Looptify restarts the current song
instead, exactly as in Loop mode, and prints the reason in the console:

| Console message | Cause |
|---|---|
| `no tracklist on the open Spotify page` | Spotify is showing a page without a tracklist, such as Home or Search |
| `still finding the playing song in the tracklist` | The current song hasn't been located yet, usually because Spotify is minimized |
| `row N isn't loaded yet` | The next row hasn't been scrolled into range, usually because Spotify is minimized |
| `couldn't press the row` | The press failed |
| `the pressed row never started` | No new song had started 0.4 seconds before the end |

A playlist mode can fall back to looping, but it never lets a song finish, so the ad
still doesn't play.

## Ad detection

If an ad plays (for example, while Looptify is disarmed, or during a mid-session ad
break), Looptify mutes Spotify's audio sessions until music returns. The ad still plays,
silently. Looptify also clears any leftover mute on startup, so a run killed mid-ad can't
leave Spotify silent.

Two independent rules identify an ad; either one is enough. Both are based on captured
ads rather than assumptions.

### By artist

Spotify's own ads report `Spotify` as the artist:

```
title='Dengarkan musik tanpa iklan.'  artist='Spotify'  album=''  track_number=0
title='Nikmati musik tanpa iklan.'    artist='Spotify'  album=''  track_number=0
```

Both come from the same ad break. The titles vary and are localized, so the artist is the
reliable marker. Matching is **exact**: a substring match would also mute real releases
such as *Spotify Singles* and the *Spotify Sessions* EPs.

### By structure

A list of names can only catch Spotify's own ads, since third-party advertisers report
their own brand. Ads differ structurally from catalogue tracks, whoever made them:

| | Ad | Catalogue track |
|---|---|---|
| `album` | *(empty)* | `mosi mosi?` |
| `track_number` | `0` | `1` |
| `duration` | 14–30 s | 163 s |

Every catalogue track belongs to a release, so it has an album and a track number. Ads
have neither and are short. All three conditions must hold, so an untagged local file is
only muted if it's also very short. This rule needs no names, so it works in every
country and language.

### Capturing an ad

To record what an ad looks like in your market, set `log_tracks = true`, run Looptify
**disarmed**, and wait for an ad: a `[track] …` line prints its fields. Add its artist to
`ad_markers`, then set `log_tracks` back to `false`.

## Advanced settings

These settings are also in `config.toml`. The defaults suit most setups.

| Setting | Default | Purpose |
|---|---|---|
| `cooldown_seconds` | `3.0` | Minimum time between two actions |
| `restart_threshold_seconds` | `5.0` | Playback must be seen below this position before another action |
| `max_drift_seconds` | `10.0` | Ignore playback data older than this |
| `poll_interval` | `0.15` | How often Looptify checks playback, in seconds |
| `ad_markers` | `["Spotify"]` | Artist names that identify an ad, matched exactly |
| `detect_ads_by_structure` | `true` | Also detect ads by structure (no album, no track number, short) |
| `ad_max_duration_seconds` | `60.0` | Longest an ad can be, for the structural rule |
| `log_tracks` | `false` | Print every track change with its raw media fields |

Setting `ad_markers = []` and `detect_ads_by_structure = false` turns ad muting off.

## Known limitations

- **Crossfade.** With Crossfade enabled in Spotify, the next track starts *before* the
  current one ends. `lead_seconds` must be larger than the crossfade duration, or Looptify
  acts too late.
- **Minimized Spotify.** A minimized window stops rendering, so the tracklist can't scroll
  to rows that aren't already loaded, and Looptify repeats the current song instead. In
  Order usually still works, because the next row is normally already loaded. Spotify can
  be covered by other windows or on another monitor without any problem.
- **The tracklist on screen is the source.** Playlist modes take the next song from
  whatever tracklist Spotify is showing. Opening another playlist switches the source.
- **Keyboard focus during a press.** For the 4–12 ms that Spotify holds the focus, a key
  pressed in that instant goes to Spotify instead of the active app. A game running in
  exclusive fullscreen may minimize when it loses focus.
- **Launch flags.** Restarting Spotify manually drops the flags, and playlist modes then
  repeat the current song. Restarting Looptify restarts Spotify with them.
- **Finding a song you started yourself.** Looptify matches the title and first artist.
  With duplicate songs in a list, the first match is used. A title that is the start of
  another title by the same artist can match the longer one. Rows Looptify pressed itself
  are always exact.
- **Notifications.** The arm/disarm notification can take keyboard focus briefly when it
  appears.
- **Test coverage.** Looptify has been tested on one machine with the Microsoft Store build
  of Spotify, in one country. The playlist modes were built against a 966-song Liked Songs
  list. The standalone installer, other markets, and regular playlists and albums are
  expected to work but are untested. [Bug reports](https://github.com/FilbertNg/Looptify/issues)
  are welcome.

## Scope

In Loop mode, Looptify uses documented Windows media commands only. The playlist modes
also start Spotify with the Chromium flags listed above, press its play buttons through
UI Automation, and briefly change its window region and position in the window stack.

Looptify does **not** patch, modify or inject into the Spotify client, use remote
debugging, block or intercept network traffic, modify audio streams, or access your
account, credentials or library.

## Code structure

Every Windows API call lives in a thin adapter, and every decision lives in a pure module
with no Windows dependencies:

```
looptify/
├── logic.py     ← pure: position extrapolation, ad detection, when to act
├── playlist.py  ← pure: the modes, and which row comes next
├── planner.py   ← pure: when to locate, prepare and press a row
├── smtc.py         adapter: read playback state, seek
├── tracklist.py    adapter: read, scroll and press Spotify's tracklist
├── focus.py        adapter: hide Spotify during a press, return focus
├── launcher.py     adapter: start Spotify with the required flags
├── audio.py        adapter: mute Spotify
├── hotkey.py       adapter: global shortcuts
├── toast.py        adapter: on-screen notifications
├── console.py      adapter: console encoding, clean shutdown
└── cli.py          poll loop and wiring
```

This split lets the timing and playlist rules be tested against synthetic values, and lets
the test suite run in CI on a machine without Spotify. Adapters are checked with the
scripts in `scripts/` instead. See [CONTRIBUTING](../CONTRIBUTING.md) for the development
workflow, and [docs/design/](design/) for the original design records.

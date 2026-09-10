# Spotify Looper — Design

**Date:** 2026-09-10
**Status:** Approved for planning

## Problem

Spotify Free on Windows desktop plays an audio ad after a track finishes. Pressing the
previous-track button shortly before the track ends restarts it without registering a
completion, so no ad plays. Done by hand this means watching the progress bar and
catching the last second — easy to miss.

## Goal

A background Windows tool that watches Spotify's playback position and fires the
previous-track command ~1.5 s before the track ends, looping the current song
indefinitely while armed. Armed and disarmed with a global hotkey.

## Non-goals

- Playback control beyond loop and mute (no queue, playlist, or library manipulation)
- Network-level ad blocking or patching the Spotify client
- Cross-platform support (Windows only)
- Anything requiring Spotify Premium or a Spotify developer application

## Verified environment (2026-09-10)

| Fact | Value |
|---|---|
| OS | Windows 11 Home Single Language 10.0.26200 |
| Python | 3.12.9, pip 25.2 |
| Already installed | `pywin32` 310, `psutil` 7.0.0 |
| Spotify build | **Microsoft Store** (`SpotifyAB.SpotifyMusic_zpdnekdrzrea0`), process name `Spotify.exe` |
| Crossfade | **Off** — no crossfade key in the Store build's `LocalState/Spotify/prefs` |

The Store build runs in an AppContainer. This does not affect SMTC (a system-wide
broker) and should not affect per-process audio session lookup, since the process is
still a normal `Spotify.exe` with its own PID — but per-process mute is the one place
where the Store packaging could surprise us, so it is verified explicitly during
implementation.

## Approach

### Data source: Windows System Media Transport Controls

Spotify publishes playback state to SMTC — the OS media feed behind the Windows media
overlay. `GlobalSystemMediaTransportControlsSessionManager` exposes, per session:
title, artist, album, playback status, and timeline properties (position, end time,
last-updated timestamp).

Alternatives rejected:

- **Spotify Web API** (`GET /v1/me/player`) — requires OAuth app registration, adds
  100–300 ms network latency per poll, is rate limited, and its control endpoints
  (seek, previous) are Premium-only. SMTC would still be needed for the action.
- **Reading the UI** (UI Automation or OCR of the `0:43 / 3:17` text) — brittle against
  any Spotify redesign, and requires the window to be visible.

### Loop action

`TrySkipPreviousAsync()` on the Spotify SMTC session, rather than sending a global
`VK_MEDIA_PREV_TRACK` key, which Windows may route to a different media app. Because
the command fires near the end of the track (position well past 3 s), Spotify's
previous-track behaviour restarts the current track rather than moving back one.

At startup the tool probes `TryChangePlaybackPositionAsync(0)`. If Spotify honours it,
seeking to 0:00 is preferred — it is more precise and does not depend on Spotify's
"restart if past 3 s" rule. Skip-previous remains the fallback.

### Timing

SMTC does not tick. It pushes an update only on events (play, pause, seek, track
change), so a naive position read can be up to a second stale. Position is therefore
extrapolated:

```
estimated_position = Position + (now_utc - LastUpdatedTime)   # only while status == Playing
remaining          = EndTime - estimated_position
fire when remaining <= lead_seconds
```

Poll interval 150 ms. `lead_seconds` defaults to 1.5.

Guards:

- Act only when playback status is `Playing`.
- Ignore a session whose end time is zero or absent.
- After firing, enter a cooldown (default 3 s) and require the observed position to
  fall below 5 s before the next fire is allowed. Both conditions must hold. This
  prevents a double-fire within one pass.
- Never loop an item detected as an ad.

If the user later enables Crossfade, `lead_seconds` must exceed the crossfade duration,
since the next track begins before the current one ends. This is documented in the
config file rather than auto-detected.

### Ad handling

When the current SMTC item is detected as an ad, Spotify's audio session is muted via
per-process volume control (`pycaw`), and unmuted when a real track returns. Muting
applies whether or not the looper is armed. The ad still plays; it plays silently.

Ad detection is **derived from observed data, not guessed**. Spotify reports ads
through SMTC with distinguishing field values (typically a blank artist, or a title
such as "Advertisement"), but the exact fields vary by client version. Implementation
therefore includes a logging mode that records every SMTC field on track change; the
detector is written against a captured real ad.

Concretely: the config holds an `ad_markers` list, and an empty list means detection is
off and nothing is ever muted. The list ships empty. It is populated only after a real
ad has been captured through the logging mode, so the tool cannot mute real music on
the strength of a guess.

### Activation

Global hotkey **Ctrl+Alt+L** toggles ARMED / IDLE, registered with `RegisterHotKey`
via `pywin32` — no administrator rights and no low-level keyboard hook. The hotkey
runs on its own thread with a message pump. The combination is configurable.

While ARMED, whatever song is currently playing loops indefinitely; if the user
manually skips, the tool re-arms on the new track. While IDLE, tracks play out
normally.

## Structure

Windows-specific I/O is confined to thin adapters. All decision logic is pure functions
that can be unit-tested without Spotify running.

```
spotify_looper/
  logic.py     # PURE: extrapolate_position(), should_loop(), is_ad(), state machine
  smtc.py      # adapter: read session, skip-previous / seek
  audio.py     # adapter: per-process mute of Spotify.exe (pycaw)
  hotkey.py    # adapter: global hotkey via RegisterHotKey (pywin32)
  cli.py       # console loop: ARMED/IDLE, current track, live countdown to loop point
config.toml    # lead_seconds, hotkey, poll_interval, ad markers
tests/
  test_logic.py
run.bat
requirements.txt
```

| Unit | Does | Depends on |
|---|---|---|
| `logic.py` | Given a timeline snapshot and current state, decides whether to loop, mute, or do nothing | nothing (pure) |
| `smtc.py` | Returns a snapshot dataclass; issues loop commands | `winsdk` |
| `audio.py` | Mute/unmute Spotify by PID | `pycaw`, `psutil` |
| `hotkey.py` | Calls a callback when the hotkey fires | `pywin32` |
| `cli.py` | Wires the above; renders console status | all of the above |

The snapshot dataclass is the interface between the adapter and the logic, which is
what lets the timing rules be tested against synthetic values.

## Dependencies to add

- `winsdk` — SMTC bindings. If it does not install cleanly on Python 3.12, fall back to
  the official `winrt-runtime` + `winrt-Windows.Media.Control` projection packages.
- `pycaw` + `comtypes` — per-process audio session volume.

`pywin32` and `psutil` are already present.

## Testing

Unit-tested (pure, no Spotify required):

- `extrapolate_position` — including a paused session, where extrapolation must not
  advance, and a stale last-updated timestamp
- `should_loop` — fires inside the lead window, does not fire outside it, does not fire
  when paused, does not fire on unknown duration, does not fire twice within cooldown,
  re-arms after position returns to zero
- `is_ad` — against captured real SMTC field values, plus real tracks that must not
  match (notably tracks with unusual or missing metadata)

Manually verified (requires Spotify):

- SMTC snapshot reports a plausible position and duration for a playing track
- Skip-previous restarts the current track; whether seek-to-zero is honoured
- Per-process mute affects only Spotify, given the Store packaging
- Hotkey registers and toggles without administrator rights
- End-to-end: an armed track loops at the boundary with no ad

## Delivery

Console version first, with live status showing ARMED/IDLE, current track, and a
countdown to the loop point — so the timing logic can be observed and tuned directly.
A system-tray wrapper (`pystray`) is a possible follow-up, not part of this scope.

## Risks

| Risk | Mitigation |
|---|---|
| Spotify's SMTC position is coarser than assumed | Extrapolation plus a 1.5 s lead absorbs roughly a second of staleness; lead is configurable and tuned against observed behaviour |
| Ad detection false-positives, muting real music | Detector written only against captured real ad data; ships disabled until an ad is captured |
| Store-app packaging blocks per-process mute | Verified explicitly; if blocked, fall back to muting the whole session or dropping the mute feature |
| Looping still registers a play and triggers an ad | The manual version of this trick already works for the user, so the behaviour is established; confirmed end-to-end |
